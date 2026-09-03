import fs from "node:fs";
import path from "node:path";
import type { Request, Response } from "express";
import type { ServerConfig } from "../config.js";

export const GRAPH_VIEWS = ["knowledge", "recent", "navigation", "lineage", "local"] as const;
export type GraphView = (typeof GRAPH_VIEWS)[number];

export interface GraphNode {
  id: string;
  label: string;
  path: string | null;
  group: string;
  kind: string;
  degree: number;
  inbound: number;
  outbound: number;
  title: string | null;
  displayName: string;
  shortName: string;
  qualifiedName: string;
  breadcrumb: string[];
  tags: string[];
  summary: string;
  sourceUrls: string[];
  depth: number;
  navigable: boolean;
}

export interface GraphEdge {
  source: string;
  target: string;
  kind: string;
  weight: number;
  depth: number;
}

export interface GraphMeta {
  view: GraphView;
  source: "compiled" | "markdown-fallback";
  generatedAt: string | null;
  availableViews: GraphView[];
  layout: string | null;
  stats: Record<string, unknown>;
}

export interface GraphData {
  nodes: GraphNode[];
  edges: GraphEdge[];
  meta: GraphMeta;
}

interface GraphManifest {
  generatedAt?: string;
  nodesFile?: string;
  edgeShards?: string[];
  recentFile?: string;
  navigationFile?: string;
  lineageFile?: string;
  settingsFile?: string;
  statsFile?: string;
}

interface GraphSettings {
  hiddenKinds?: string[];
  defaultView?: string;
  views?: string[];
  layout?: Record<string, { engine?: string }>;
}

interface CompactNode {
  id?: unknown;
  kind?: unknown;
  title?: unknown;
  displayName?: unknown;
  shortName?: unknown;
  qualifiedName?: unknown;
  breadcrumb?: unknown;
  fileName?: unknown;
  parentName?: unknown;
  tags?: unknown;
  summary?: unknown;
  sourceUrls?: unknown;
  degree?: unknown;
  inbound?: unknown;
  outbound?: unknown;
  d?: unknown;
  page?: unknown;
}

interface CompactEdge {
  s?: unknown;
  t?: unknown;
  k?: unknown;
  w?: unknown;
  d?: unknown;
}

interface MultiGraph {
  nodes?: unknown;
  edges?: unknown;
}

interface SidecarGraph extends CompactNode {
  ego?: {
    in?: Array<{ from?: unknown; kind?: unknown }>;
    out?: Array<{ to?: unknown; kind?: unknown }>;
  };
  stats?: {
    inbound?: unknown;
    outbound?: unknown;
    degree?: unknown;
  };
}

const MD_LINK_RE =
  /!?\[[^\]\n]*\]\(\s*(?:<([^>\n]+?\.md(?:#[^>\n]*)?)>|([^()\s]+?\.md(?:#[^()\s]*)?))\s*\)/g;
const EXTERNAL_URL_RE = /^[a-z][a-z0-9+.\-]*:/i;

export function buildGraph(
  wikiRoot: string,
  view: GraphView = "knowledge",
  currentPath = "wiki/index.md",
): GraphData {
  const manifest = readGraphJson<GraphManifest>(wikiRoot, "manifest.graph") ?? {};
  const settingsName = safeGraphName(manifest.settingsFile) ?? "settings.graph";
  const settings = readGraphJson<GraphSettings>(wikiRoot, settingsName) ?? {};
  const statsName = safeGraphName(manifest.statsFile) ?? "stats.graph";
  const stats = readGraphJson<Record<string, unknown>>(wikiRoot, statsName) ?? {};
  const availableViews = detectAvailableViews(wikiRoot, manifest, currentPath);
  const layout = settings.layout?.[view]?.engine ?? null;

  const meta: GraphMeta = {
    view,
    source: "compiled",
    generatedAt: typeof manifest.generatedAt === "string" ? manifest.generatedAt : null,
    availableViews,
    layout,
    stats,
  };

  if (view === "local") {
    const local = buildLocalGraph(wikiRoot, currentPath);
    if (local) return { ...local, meta };
    return { nodes: [], edges: [], meta };
  }

  if (view === "knowledge") {
    const compiled = buildKnowledgeGraph(wikiRoot, manifest, settings);
    if (compiled) return { ...compiled, meta };
    return { ...buildMarkdownFallback(wikiRoot), meta: { ...meta, source: "markdown-fallback" } };
  }

  const fileByView: Record<Exclude<GraphView, "knowledge" | "local">, string> = {
    recent: safeGraphName(manifest.recentFile) ?? "recent.graph",
    navigation: safeGraphName(manifest.navigationFile) ?? "navigation.graph",
    lineage: safeGraphName(manifest.lineageFile) ?? "lineage.graph",
  };
  const multi = readGraphJson<MultiGraph>(wikiRoot, fileByView[view]);
  if (!multi) return { nodes: [], edges: [], meta };
  return { ...normalizeMultiGraph(multi), meta };
}

function buildKnowledgeGraph(
  wikiRoot: string,
  manifest: GraphManifest,
  settings: GraphSettings,
): Pick<GraphData, "nodes" | "edges"> | null {
  const nodesName = safeGraphName(manifest.nodesFile) ?? "nodes.graph";
  const rawNodes = readGraphJson<unknown>(wikiRoot, nodesName);
  if (!Array.isArray(rawNodes)) return null;

  const shardNames = Array.isArray(manifest.edgeShards)
    ? manifest.edgeShards.map(safeGraphName).filter((v): v is string => Boolean(v))
    : ["edges-000.graph"];
  const rawEdges: unknown[] = [];
  for (const shard of shardNames) {
    const value = readGraphJson<unknown>(wikiRoot, shard);
    if (Array.isArray(value)) rawEdges.push(...value);
  }

  const hiddenKinds = new Set(Array.isArray(settings.hiddenKinds) ? settings.hiddenKinds : []);
  const normalized = normalizeMultiGraph({ nodes: rawNodes, edges: rawEdges });
  const nodes = normalized.nodes.filter((node) => !hiddenKinds.has(node.kind));
  const visible = new Set(nodes.map((node) => node.id));
  const edges = normalized.edges.filter((edge) => visible.has(edge.source) && visible.has(edge.target));
  return withComputedDegrees(nodes, edges);
}

function buildLocalGraph(
  wikiRoot: string,
  currentPath: string,
): Pick<GraphData, "nodes" | "edges"> | null {
  const docPath = normalizeDocumentPath(currentPath);
  if (!docPath) return null;
  const sidecarPath = `${docPath}.graph`;
  const full = path.resolve(wikiRoot, sidecarPath);
  if (!isInside(wikiRoot, full) || !fs.existsSync(full) || !fs.statSync(full).isFile()) return null;

  const sidecar = readJsonFile<SidecarGraph>(full);
  if (!sidecar || (!sidecar.ego && typeof sidecar.id !== "string")) return null;

  const catalog = readGlobalNodeCatalog(wikiRoot);
  const centerId = asString(sidecar.id) || docPath;
  const centerSource: CompactNode = { ...sidecar, id: centerId };
  const nodeIds = new Set<string>([centerId]);
  const edges: GraphEdge[] = [];

  for (const item of sidecar.ego?.out ?? []) {
    const target = asString(item.to);
    if (!target || target === centerId) continue;
    nodeIds.add(target);
    edges.push({ source: centerId, target, kind: asString(item.kind) || "links_to", weight: 1, depth: 1 });
  }
  for (const item of sidecar.ego?.in ?? []) {
    const source = asString(item.from);
    if (!source || source === centerId) continue;
    nodeIds.add(source);
    edges.push({ source, target: centerId, kind: asString(item.kind) || "links_to", weight: 1, depth: 1 });
  }

  const nodes = Array.from(nodeIds, (id) => {
    if (id === centerId) return normalizeNode(centerSource);
    return normalizeNode(catalog.get(id) ?? { id });
  });
  return withComputedDegrees(nodes, dedupeEdges(edges));
}

function normalizeMultiGraph(value: MultiGraph): Pick<GraphData, "nodes" | "edges"> {
  const rawNodes = Array.isArray(value.nodes) ? value.nodes : [];
  const rawEdges = Array.isArray(value.edges) ? value.edges : [];
  const nodes = rawNodes
    .filter((item): item is CompactNode => isRecord(item) && typeof item.id === "string")
    .map(normalizeNode);
  const edges = rawEdges
    .filter((item): item is CompactEdge => isRecord(item))
    .map(normalizeEdge)
    .filter((edge): edge is GraphEdge => edge !== null);
  return withComputedDegrees(nodes, dedupeEdges(edges));
}

function normalizeNode(raw: CompactNode): GraphNode {
  const id = asString(raw.id);
  const displayName =
    asString(raw.displayName) || asString(raw.title) || asString(raw.shortName) || deriveLabel(id);
  const shortName = asString(raw.shortName) || displayName;
  const kind = asString(raw.kind) || deriveKind(id);
  const candidatePath = asString(raw.page) || (looksNavigable(id) ? stripAnchor(id) : "");
  const breadcrumb = Array.isArray(raw.breadcrumb)
    ? raw.breadcrumb.filter((part): part is string => typeof part === "string")
    : deriveBreadcrumb(id);
  const tags = Array.isArray(raw.tags)
    ? raw.tags.filter((tag): tag is string => typeof tag === "string")
    : [];
  const sourceUrls = Array.isArray(raw.sourceUrls)
    ? raw.sourceUrls.filter((value): value is string => isSafeHttpUrl(value))
    : [];
  const inbound = asFiniteNumber(raw.inbound, 0);
  const outbound = asFiniteNumber(raw.outbound, 0);
  const degree = asFiniteNumber(raw.degree, inbound + outbound);
  return {
    id,
    label: shortName,
    path: candidatePath || null,
    group: kind,
    kind,
    degree,
    inbound,
    outbound,
    title: asString(raw.title) || displayName,
    displayName,
    shortName,
    qualifiedName: asString(raw.qualifiedName) || breadcrumb.join(" / ") || displayName,
    breadcrumb,
    tags,
    summary: asString(raw.summary),
    sourceUrls: Array.from(new Set(sourceUrls)),
    depth: clamp01(asFiniteNumber(raw.d, 1)),
    navigable: Boolean(candidatePath),
  };
}

function normalizeEdge(raw: CompactEdge): GraphEdge | null {
  const source = asString(raw.s);
  const target = asString(raw.t);
  if (!source || !target || source === target) return null;
  return {
    source,
    target,
    kind: asString(raw.k) || "links_to",
    weight: Math.max(1, asFiniteNumber(raw.w, 1)),
    depth: clamp01(asFiniteNumber(raw.d, 1)),
  };
}

function withComputedDegrees(
  nodes: GraphNode[],
  edges: GraphEdge[],
): Pick<GraphData, "nodes" | "edges"> {
  const counts = new Map<string, { inbound: number; outbound: number }>();
  for (const node of nodes) counts.set(node.id, { inbound: 0, outbound: 0 });
  for (const edge of edges) {
    const source = counts.get(edge.source);
    const target = counts.get(edge.target);
    if (source) source.outbound += 1;
    if (target) target.inbound += 1;
  }
  return {
    nodes: nodes.map((node) => {
      const count = counts.get(node.id) ?? { inbound: 0, outbound: 0 };
      return {
        ...node,
        inbound: count.inbound,
        outbound: count.outbound,
        degree: count.inbound + count.outbound,
      };
    }),
    edges,
  };
}

function dedupeEdges(edges: GraphEdge[]): GraphEdge[] {
  const seen = new Set<string>();
  return edges.filter((edge) => {
    const key = `${edge.source}\u0000${edge.target}\u0000${edge.kind}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function readGlobalNodeCatalog(wikiRoot: string): Map<string, CompactNode> {
  const manifest = readGraphJson<GraphManifest>(wikiRoot, "manifest.graph") ?? {};
  const name = safeGraphName(manifest.nodesFile) ?? "nodes.graph";
  const raw = readGraphJson<unknown>(wikiRoot, name);
  const out = new Map<string, CompactNode>();
  if (!Array.isArray(raw)) return out;
  for (const item of raw) {
    if (!isRecord(item) || typeof item.id !== "string") continue;
    out.set(item.id, item as CompactNode);
  }
  return out;
}

function detectAvailableViews(
  wikiRoot: string,
  manifest: GraphManifest,
  currentPath: string,
): GraphView[] {
  const out: GraphView[] = ["knowledge"];
  const files: Array<[GraphView, string]> = [
    ["recent", safeGraphName(manifest.recentFile) ?? "recent.graph"],
    ["navigation", safeGraphName(manifest.navigationFile) ?? "navigation.graph"],
    ["lineage", safeGraphName(manifest.lineageFile) ?? "lineage.graph"],
  ];
  for (const [view, name] of files) {
    if (fs.existsSync(path.join(wikiRoot, "graph", name))) out.push(view);
  }
  const docPath = normalizeDocumentPath(currentPath);
  if (docPath && fs.existsSync(path.join(wikiRoot, `${docPath}.graph`))) out.push("local");
  return out;
}

function readGraphJson<T>(wikiRoot: string, name: string): T | null {
  const safeName = safeGraphName(name);
  if (!safeName) return null;
  return readJsonFile<T>(path.join(wikiRoot, "graph", safeName));
}

function readJsonFile<T>(file: string): T | null {
  try {
    const stat = fs.statSync(file);
    if (!stat.isFile() || stat.size > 64 * 1024 * 1024) return null;
    return JSON.parse(fs.readFileSync(file, "utf-8")) as T;
  } catch {
    return null;
  }
}

function safeGraphName(value: unknown): string | null {
  if (typeof value !== "string" || !value.endsWith(".graph")) return null;
  const normalized = path.posix.normalize(value);
  if (normalized.startsWith("../") || normalized.includes("/") || path.posix.isAbsolute(normalized)) {
    return null;
  }
  return normalized;
}

function normalizeDocumentPath(value: string): string | null {
  const stripped = stripAnchor(value).replace(/^\/+/, "").replace(/\.graph$/, "");
  if (!stripped || path.posix.isAbsolute(stripped)) return null;
  const normalized = path.posix.normalize(stripped.startsWith("wiki/") ? stripped : `wiki/${stripped}`);
  if (normalized.startsWith("../") || !normalized.endsWith(".md")) return null;
  return normalized;
}

function buildMarkdownFallback(wikiRoot: string): Pick<GraphData, "nodes" | "edges"> {
  const wikiDir = path.join(wikiRoot, "wiki");
  if (!fs.existsSync(wikiDir)) return { nodes: [], edges: [] };
  const files = collectMdFiles(wikiDir);
  const nodes = new Map<string, GraphNode>();
  for (const file of files) {
    const rel = path.relative(wikiRoot, file).split(path.sep).join("/");
    const text = fs.readFileSync(file, "utf-8");
    const title = extractTitle(text) ?? deriveLabel(rel);
    nodes.set(rel, normalizeNode({ id: rel, title, displayName: title }));
  }

  const edges: GraphEdge[] = [];
  for (const file of files) {
    const source = path.relative(wikiRoot, file).split(path.sep).join("/");
    const text = fs.readFileSync(file, "utf-8");
    MD_LINK_RE.lastIndex = 0;
    let match: RegExpExecArray | null;
    while ((match = MD_LINK_RE.exec(text))) {
      const href = (match[1] ?? match[2] ?? "").trim();
      const target = resolveGraphTarget(wikiRoot, href);
      if (!target || target === source || !nodes.has(target)) continue;
      edges.push({ source, target, kind: "links_to", weight: 1, depth: 1 });
    }
  }
  return withComputedDegrees(Array.from(nodes.values()), dedupeEdges(edges));
}

function resolveGraphTarget(wikiRoot: string, href: string): string | null {
  const pathPart = stripAnchor(href).trim();
  if (!pathPart || EXTERNAL_URL_RE.test(pathPart)) return null;
  const decoded = safeDecode(pathPart);
  const candidate = decoded.startsWith("/")
    ? `wiki/${decoded.slice(1)}`
    : decoded.startsWith("wiki/") || decoded.startsWith("raw/")
      ? decoded
      : `wiki/${decoded}`;
  const normalized = path.posix.normalize(candidate);
  if (normalized.startsWith("../") || path.posix.isAbsolute(normalized)) return null;
  const full = path.resolve(wikiRoot, normalized);
  if (!isInside(wikiRoot, full) || !fs.existsSync(full) || !fs.statSync(full).isFile()) return null;
  return path.relative(wikiRoot, full).split(path.sep).join("/");
}

function collectMdFiles(dir: string): string[] {
  const out: string[] = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (entry.name.startsWith(".")) continue;
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) out.push(...collectMdFiles(full));
    else if (entry.isFile() && entry.name.endsWith(".md")) out.push(full);
  }
  return out;
}

function extractTitle(text: string): string | null {
  const frontmatter = /^---\n([\s\S]*?)\n---/.exec(text);
  const frontmatterTitle = frontmatter ? /^title:\s*(.+)$/m.exec(frontmatter[1] ?? "") : null;
  if (frontmatterTitle) return frontmatterTitle[1]!.trim().replace(/^["']|["']$/g, "");
  const heading = /^#\s+(.+?)\s*$/m.exec(text);
  return heading?.[1] ?? null;
}

function deriveLabel(id: string): string {
  const clean = stripAnchor(id).replace(/\.graph$/i, "");
  const last = clean.split("/").pop() || clean || "Untitled";
  return last.replace(/\.md$/i, "");
}

function deriveKind(id: string): string {
  if (id === "recent" || id === "lastweek") return "recent";
  if (id.startsWith("nav:")) return id.includes("#") ? "section" : "index";
  if (id.startsWith("raw/")) return "raw_source";
  const parts = stripAnchor(id).split("/").filter(Boolean);
  const segment = parts[0] === "wiki" ? parts[1] : parts[0];
  if (!segment) return "page";
  const singular: Record<string, string> = {
    concepts: "concept",
    entities: "entity",
    summaries: "summary",
  };
  return singular[segment] ?? (segment === "index.md" ? "index" : "page");
}

function deriveBreadcrumb(id: string): string[] {
  return stripAnchor(id)
    .replace(/^nav:/, "")
    .replace(/\.md$/i, "")
    .split(/[\/#]/)
    .filter(Boolean);
}

function looksNavigable(id: string): boolean {
  const clean = stripAnchor(id);
  return /^(wiki|raw|log)\/.+\.md$/i.test(clean);
}

function stripAnchor(value: string): string {
  return value.split("#", 1)[0] ?? value;
}

function safeDecode(value: string): string {
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
}

function isInside(root: string, candidate: string): boolean {
  const rel = path.relative(root, candidate);
  return rel === "" || (!rel.startsWith("..") && !path.isAbsolute(rel));
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function asString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function isSafeHttpUrl(value: unknown): value is string {
  if (typeof value !== "string") return false;
  try {
    const parsed = new URL(value);
    return parsed.protocol === "https:" || parsed.protocol === "http:";
  } catch {
    return false;
  }
}

function asFiniteNumber(value: unknown, fallback: number): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function clamp01(value: number): number {
  return Math.max(0, Math.min(1, value));
}

export function handleGraph(cfg: ServerConfig) {
  return (req: Request, res: Response) => {
    const rawView = typeof req.query.view === "string" ? req.query.view : "knowledge";
    if (!GRAPH_VIEWS.includes(rawView as GraphView)) {
      res.status(400).json({ error: "unknown graph view", view: rawView, supported: GRAPH_VIEWS });
      return;
    }
    const currentPath = typeof req.query.path === "string" ? req.query.path : "wiki/index.md";
    res.json(buildGraph(cfg.wikiRoot, rawView as GraphView, currentPath));
  };
}
