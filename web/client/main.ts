import mermaid from "mermaid";
import type { AuditEntry } from "audit-shared";
import { installFeedbackUI } from "./feedback.js";
import {
  renderGraph,
  type GraphController,
  type GraphData,
  type GraphNode,
  type GraphView,
} from "./graph.js";

interface PageResponse {
  path: string;
  title: string | null;
  html: string;
  raw: string;
  frontmatter: Record<string, unknown> | null;
}

type Theme = "light" | "dark";

const THEME_STORAGE_KEY = "llm-wiki-theme";

function initialTheme(): Theme {
  return document.documentElement.dataset.theme === "light" ? "light" : "dark";
}

const state = {
  currentPath: "wiki/index.md" as string,
  rawMarkdown: "" as string,
  author: "me" as string,
  graphController: null as GraphController | null,
  localGraphController: null as GraphController | null,
  localGraphRequest: 0,
  graphData: null as GraphData | null,
  graphView: "knowledge" as GraphView,
  hiddenGraphKinds: new Set<string>(),
  selectedGraphNode: null as GraphNode | null,
  theme: initialTheme(),
};

const MERMAID_THEME_VARIABLES: Record<Theme, Record<string, string>> = {
  dark: {
    background: "#111111",
    primaryColor: "#313131",
    primaryTextColor: "#ededed",
    primaryBorderColor: "#bcbcbc",
    secondaryColor: "#454545",
    secondaryTextColor: "#ededed",
    secondaryBorderColor: "#a4a4a4",
    tertiaryColor: "#585858",
    tertiaryTextColor: "#ededed",
    tertiaryBorderColor: "#949494",
    lineColor: "#7f7f84",
    textColor: "#ededed",
    mainBkg: "#313131",
    nodeBorder: "#bcbcbc",
    clusterBkg: "#181818",
    clusterBorder: "#454545",
    titleColor: "#ededed",
    edgeLabelBackground: "#181818",
    actorBkg: "#313131",
    actorBorder: "#bcbcbc",
    actorTextColor: "#ededed",
    actorLineColor: "#7f7f84",
    signalColor: "#ededed",
    signalTextColor: "#ededed",
    labelBoxBkgColor: "#313131",
    labelBoxBorderColor: "#bcbcbc",
    labelTextColor: "#ededed",
    loopTextColor: "#ededed",
    noteBkgColor: "#d8d8d2",
    noteTextColor: "#111111",
    noteBorderColor: "#b8b8b2",
    activationBkgColor: "#454545",
    activationBorderColor: "#bcbcbc",
    stateBkg: "#313131",
    stateBorder: "#bcbcbc",
    specialStateColor: "#f0f0f0",
  },
  light: {
    background: "#ffffff",
    primaryColor: "#f4f4f1",
    primaryTextColor: "#20201d",
    primaryBorderColor: "#6b6b65",
    secondaryColor: "#e9e9e4",
    secondaryTextColor: "#20201d",
    secondaryBorderColor: "#7b7b74",
    tertiaryColor: "#deded8",
    tertiaryTextColor: "#20201d",
    tertiaryBorderColor: "#8a8a83",
    lineColor: "#777770",
    textColor: "#20201d",
    mainBkg: "#f4f4f1",
    nodeBorder: "#6b6b65",
    clusterBkg: "#fafaf8",
    clusterBorder: "#c7c7c0",
    titleColor: "#20201d",
    edgeLabelBackground: "#ffffff",
    actorBkg: "#f4f4f1",
    actorBorder: "#6b6b65",
    actorTextColor: "#20201d",
    actorLineColor: "#777770",
    signalColor: "#20201d",
    signalTextColor: "#20201d",
    labelBoxBkgColor: "#f4f4f1",
    labelBoxBorderColor: "#6b6b65",
    labelTextColor: "#20201d",
    loopTextColor: "#20201d",
    noteBkgColor: "#fff3bd",
    noteTextColor: "#20201d",
    noteBorderColor: "#b7942d",
    activationBkgColor: "#e9e9e4",
    activationBorderColor: "#6b6b65",
    stateBkg: "#f4f4f1",
    stateBorder: "#6b6b65",
    specialStateColor: "#4b4b46",
  },
};

function configureMermaid(theme: Theme): void {
  mermaid.initialize({
    startOnLoad: false,
    theme: "base",
    securityLevel: "loose",
    fontFamily: "Inter, system-ui, sans-serif",
    themeVariables: MERMAID_THEME_VARIABLES[theme],
  });
}

configureMermaid(state.theme);

async function main() {
  try {
    const cfg = await fetch("/api/config").then((r) => r.json());
    if (cfg.author) state.author = cfg.author;
  } catch {}

  updateThemeControl();
  document.getElementById("btn-theme")!.addEventListener("click", () => {
    void applyTheme(state.theme === "dark" ? "light" : "dark", true);
  });
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", (event) => {
    if (readStoredTheme() === null) void applyTheme(event.matches ? "dark" : "light", false);
  });

  // Initial page.
  const initial = new URL(window.location.href).searchParams.get("page") ?? "wiki/index.md";
  await loadPage(initial);

  window.addEventListener("popstate", (e) => {
    const p = (e.state && e.state.page) || new URL(window.location.href).searchParams.get("page") || "wiki/index.md";
    void loadPage(p);
  });

  // Intercept wikilinks so navigation stays in the SPA.
  document.getElementById("page-content")!.addEventListener("click", (e) => {
    const target = (e.target as HTMLElement).closest("a.wikilink") as HTMLAnchorElement | null;
    if (!target) return;
    const href = target.getAttribute("href") ?? "";
    const u = new URL(href, window.location.href);
    const page = u.searchParams.get("page");
    if (page) {
      e.preventDefault();
      navigateToPage(page);
    }
  });

  // Refresh audits button.
  document.getElementById("btn-refresh")!.addEventListener("click", () => {
    void loadAudits(state.currentPath);
  });

  // Graph workbench.
  const graphOverlay = document.getElementById("graph-overlay")!;
  const graphSvg = document.getElementById("graph-svg") as unknown as SVGSVGElement;
  const graphLoading = document.getElementById("graph-loading")!;
  const graphEmpty = document.getElementById("graph-empty")!;
  const graphSearch = document.getElementById("graph-search") as HTMLInputElement;
  const graphSearchResults = document.getElementById("graph-search-results")!;

  const openNode = (node: GraphNode) => {
    if (!node.navigable || !node.path) return;
    closeGraph();
    navigateToPage(node.path);
  };

  const setSelectedNode = (node: GraphNode | null) => {
    state.selectedGraphNode = node;
    renderGraphInspector(node);
  };

  const renderCurrentGraph = () => {
    state.graphController?.destroy();
    state.graphController = null;
    if (!state.graphData) return;

    const visibleNodes = state.graphData.nodes.filter((node) => !state.hiddenGraphKinds.has(node.kind));
    const visibleIds = new Set(visibleNodes.map((node) => node.id));
    const visibleData: GraphData = {
      ...state.graphData,
      nodes: visibleNodes,
      edges: state.graphData.edges.filter((edge) => {
        const source = typeof edge.source === "string" ? edge.source : edge.source.id;
        const target = typeof edge.target === "string" ? edge.target : edge.target.id;
        return visibleIds.has(source) && visibleIds.has(target);
      }),
    };

    graphEmpty.classList.toggle("hidden", visibleNodes.length > 0);
    if (visibleNodes.length === 0) return;
    state.graphController = renderGraph(graphSvg, visibleData, {
      selectedId: state.selectedGraphNode?.id,
      centerId: visibleData.meta.view === "local" ? state.currentPath : null,
      labelMode: visibleData.meta.view === "local" ? "all" : "priority",
      onNodeSelect: setSelectedNode,
      onNodeOpen: openNode,
    });
  };

  const loadGraphView = async (view: GraphView) => {
    state.graphView = view;
    state.hiddenGraphKinds.clear();
    setSelectedNode(null);
    graphSearch.value = "";
    graphSearchResults.classList.add("hidden");
    graphLoading.classList.remove("hidden");
    graphEmpty.classList.add("hidden");
    state.graphController?.destroy();
    state.graphController = null;
    graphSvg.replaceChildren();
    setActiveGraphView(view);

    try {
      const params = new URLSearchParams({ view, path: state.currentPath });
      const response = await fetch(`/api/graph?${params}`);
      if (!response.ok) throw new Error(`Graph request failed: ${response.status}`);
      const data = (await response.json()) as GraphData;
      state.graphData = data;
      updateGraphOverview(data);
      renderKindFilters(data, () => renderCurrentGraph());
      updateGraphViewAvailability(data.meta.availableViews);
      renderCurrentGraph();
    } catch (error) {
      console.error(error);
      state.graphData = null;
      graphEmpty.classList.remove("hidden");
      graphEmpty.querySelector("strong")!.textContent = "Could not load graph";
      graphEmpty.querySelector("span")!.textContent = "Check the compiled .graph files and try again.";
    } finally {
      graphLoading.classList.add("hidden");
    }
  };

  const openGraph = async () => {
    graphOverlay.classList.remove("hidden");
    await new Promise((r) => requestAnimationFrame(() => r(null)));
    await loadGraphView(state.graphView);
  };
  const closeGraph = () => {
    graphOverlay.classList.add("hidden");
    state.graphController?.destroy();
    state.graphController = null;
    graphSearchResults.classList.add("hidden");
  };

  document.getElementById("btn-graph")!.addEventListener("click", () => {
    if (graphOverlay.classList.contains("hidden")) void openGraph();
    else closeGraph();
  });
  document.getElementById("graph-close")!.addEventListener("click", closeGraph);
  document.getElementById("graph-fit")!.addEventListener("click", () => state.graphController?.fit());
  document.getElementById("graph-reset")!.addEventListener("click", () => state.graphController?.reset());
  document.getElementById("local-graph-expand")!.addEventListener("click", () => {
    state.graphView = "local";
    void openGraph();
  });
  document.getElementById("graph-open-page")!.addEventListener("click", () => {
    if (state.selectedGraphNode) openNode(state.selectedGraphNode);
  });
  document.querySelectorAll<HTMLButtonElement>("[data-graph-view]").forEach((button) => {
    button.addEventListener("click", () => {
      const view = button.dataset.graphView as GraphView;
      if (!button.disabled) void loadGraphView(view);
    });
  });

  graphSearch.addEventListener("input", () => {
    renderGraphSearchResults(graphSearch.value, (node) => {
      graphSearch.value = node.displayName;
      graphSearchResults.classList.add("hidden");
      state.graphController?.focusNode(node.id);
    });
  });
  graphSearch.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      const first = graphSearchResults.querySelector<HTMLButtonElement>("button[data-node-id]");
      first?.click();
    }
    if (event.key === "Escape") {
      graphSearch.value = "";
      graphSearchResults.classList.add("hidden");
    }
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !graphOverlay.classList.contains("hidden")) {
      closeGraph();
    }
    if (e.key === "/" && !graphOverlay.classList.contains("hidden") && !isEditableFocused()) {
      e.preventDefault();
      graphSearch.focus();
    }
    if ((e.key === "g" || e.key === "G") && !isEditableFocused()) {
      e.preventDefault();
      if (graphOverlay.classList.contains("hidden")) void openGraph();
      else closeGraph();
    }
  });

  function renderGraphSearchResults(query: string, onSelect: (node: GraphNode) => void): void {
    graphSearchResults.replaceChildren();
    const needle = query.trim().toLocaleLowerCase();
    if (!needle || !state.graphData) {
      graphSearchResults.classList.add("hidden");
      return;
    }
    const matches = state.graphData.nodes
      .filter((node) => !state.hiddenGraphKinds.has(node.kind))
      .filter((node) => {
        const haystack = [node.displayName, node.qualifiedName, node.id, ...node.tags]
          .join(" ")
          .toLocaleLowerCase();
        return haystack.includes(needle);
      })
      .sort((a, b) => b.degree - a.degree)
      .slice(0, 8);

    for (const node of matches) {
      const button = document.createElement("button");
      button.type = "button";
      button.dataset.nodeId = node.id;
      button.setAttribute("role", "option");
      const title = document.createElement("strong");
      title.textContent = node.displayName;
      const context = document.createElement("span");
      context.textContent = node.qualifiedName;
      button.append(title, context);
      button.addEventListener("click", () => onSelect(node));
      graphSearchResults.append(button);
    }
    if (matches.length === 0) {
      const empty = document.createElement("p");
      empty.textContent = "No matching nodes";
      graphSearchResults.append(empty);
    }
    graphSearchResults.classList.remove("hidden");
  }

  // Feedback UI.
  installFeedbackUI({
    getState: () => ({ currentPath: state.currentPath, rawMarkdown: state.rawMarkdown, author: state.author }),
    onCreated: async () => {
      await loadAudits(state.currentPath);
    },
  });
}

function isEditableFocused(): boolean {
  const el = document.activeElement;
  if (!el) return false;
  const tag = el.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || (el as HTMLElement).isContentEditable;
}

const GRAPH_VIEW_COPY: Record<GraphView, string> = {
  knowledge: "All compiled pages and the links between them.",
  recent: "Pages touched in the latest activity window, weighted by recency.",
  navigation: "The index structure as sections, pages and containment links.",
  lineage: "How raw sources flow into summaries and durable knowledge pages.",
  local: "The current page and its one-hop incoming and outgoing links.",
};

function setActiveGraphView(view: GraphView): void {
  document.querySelectorAll<HTMLButtonElement>("[data-graph-view]").forEach((button) => {
    button.classList.toggle("active", button.dataset.graphView === view);
  });
  document.getElementById("graph-canvas-view")!.textContent = view.toUpperCase();
  document.getElementById("graph-view-description")!.textContent = GRAPH_VIEW_COPY[view];
}

function updateGraphViewAvailability(availableViews: GraphView[]): void {
  const available = new Set(availableViews);
  document.querySelectorAll<HTMLButtonElement>("[data-graph-view]").forEach((button) => {
    const view = button.dataset.graphView as GraphView;
    button.disabled = !available.has(view);
    button.title = button.disabled ? "This compiled graph is not available" : "";
  });
}

function updateGraphOverview(data: GraphData): void {
  document.getElementById("graph-stat-nodes")!.textContent = data.nodes.length.toLocaleString();
  document.getElementById("graph-stat-edges")!.textContent = data.edges.length.toLocaleString();
  const source = data.meta.source === "compiled" ? "Compiled .graph" : "Markdown fallback";
  const date = data.meta.generatedAt ? new Date(data.meta.generatedAt) : null;
  const generated = date && !Number.isNaN(date.valueOf())
    ? ` · ${date.toLocaleDateString(undefined, { month: "short", day: "numeric" })}`
    : "";
  document.getElementById("graph-source-label")!.textContent = `${source}${generated}`;
  document.getElementById("graph-view-description")!.textContent = GRAPH_VIEW_COPY[data.meta.view];
  document.getElementById("graph-canvas-view")!.textContent = data.meta.view.toUpperCase();
  const empty = document.getElementById("graph-empty")!;
  empty.querySelector("strong")!.textContent = "No graph data";
  empty.querySelector("span")!.textContent = "This view has no compiled nodes yet.";
}

function renderKindFilters(data: GraphData, onChange: () => void): void {
  const container = document.getElementById("graph-kind-filters")!;
  container.replaceChildren();
  const counts = new Map<string, number>();
  for (const node of data.nodes) counts.set(node.kind, (counts.get(node.kind) ?? 0) + 1);
  const sorted = [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  for (const [kind, count] of sorted) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "graph-kind-filter";
    button.dataset.kind = kind;
    const dot = document.createElement("span");
    dot.className = "graph-kind-dot";
    dot.style.background = graphKindTone(kind);
    const label = document.createElement("span");
    label.textContent = kind.replace(/_/g, " ");
    const value = document.createElement("b");
    value.textContent = String(count);
    button.append(dot, label, value);
    button.addEventListener("click", () => {
      if (state.hiddenGraphKinds.has(kind)) state.hiddenGraphKinds.delete(kind);
      else state.hiddenGraphKinds.add(kind);
      button.classList.toggle("excluded", state.hiddenGraphKinds.has(kind));
      if (state.selectedGraphNode?.kind === kind && state.hiddenGraphKinds.has(kind)) {
        state.selectedGraphNode = null;
        renderGraphInspector(null);
      }
      onChange();
    });
    container.append(button);
  }
}

function renderGraphInspector(node: GraphNode | null): void {
  const empty = document.getElementById("graph-inspector-empty")!;
  const detail = document.getElementById("graph-inspector-detail")!;
  empty.classList.toggle("hidden", Boolean(node));
  detail.classList.toggle("hidden", !node);
  if (!node) return;

  document.getElementById("graph-inspector-kind")!.textContent = node.kind.replace(/_/g, " ").toUpperCase();
  document.getElementById("graph-inspector-title")!.textContent = node.displayName;
  document.getElementById("graph-inspector-path")!.textContent = node.qualifiedName || node.id;
  const summary = document.getElementById("graph-inspector-summary")!;
  summary.textContent = node.summary || "No summary is available in this graph artifact.";
  summary.classList.toggle("muted", !node.summary);
  document.getElementById("graph-inspector-in")!.textContent = String(node.inbound);
  document.getElementById("graph-inspector-out")!.textContent = String(node.outbound);
  document.getElementById("graph-inspector-degree")!.textContent = String(node.degree);

  const tags = document.getElementById("graph-inspector-tags")!;
  tags.replaceChildren();
  for (const value of node.tags.slice(0, 8)) {
    const tag = document.createElement("span");
    tag.textContent = value;
    tags.append(tag);
  }

  const sources = document.getElementById("graph-inspector-sources")!;
  const sourceLinks = document.getElementById("graph-inspector-source-links")!;
  sourceLinks.replaceChildren();
  sources.classList.toggle("hidden", node.sourceUrls.length === 0);
  const baseLabels = node.sourceUrls.map((url) => graphSourceLabel(url));
  const labelTotals = new Map<string, number>();
  const labelSeen = new Map<string, number>();
  for (const label of baseLabels) labelTotals.set(label, (labelTotals.get(label) ?? 0) + 1);
  for (const [index, url] of node.sourceUrls.entries()) {
    const link = document.createElement("a");
    link.href = url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.title = url;
    const label = document.createElement("span");
    const baseLabel = baseLabels[index] ?? "Original source";
    const ordinal = (labelSeen.get(baseLabel) ?? 0) + 1;
    labelSeen.set(baseLabel, ordinal);
    label.textContent = (labelTotals.get(baseLabel) ?? 0) > 1
      ? `${baseLabel} ${ordinal}`
      : baseLabel;
    const arrow = document.createElement("span");
    arrow.textContent = "↗";
    link.append(label, arrow);
    sourceLinks.append(link);
  }

  const openButton = document.getElementById("graph-open-page") as HTMLButtonElement;
  openButton.disabled = !node.navigable;
  openButton.classList.toggle("hidden", !node.navigable);
}

function graphSourceLabel(value: string): string {
  try {
    const url = new URL(value);
    if (url.pathname.includes("/minutes/")) return "Meeting minutes";
    if (url.pathname.includes("/docx/")) return "Lark document";
    if (url.pathname.includes("/wiki/")) return "Lark wiki";
    if (url.hostname === "arxiv.org") return "arXiv paper";
    if (url.hostname === "github.com") return "GitHub source";
    return url.hostname.replace(/^www\./, "");
  } catch {
    return "Original source";
  }
}

function graphKindTone(kind: string): string {
  const tones: Record<string, string> = {
    index: "var(--graph-kind-index)",
    concept: "var(--graph-kind-concept)",
    synthesis: "var(--graph-kind-synthesis)",
    entity: "var(--graph-kind-entity)",
    summary: "var(--graph-kind-summary)",
    raw_source: "var(--graph-kind-raw-source)",
    recent: "var(--graph-kind-recent)",
    section: "var(--graph-kind-section)",
    page: "var(--graph-kind-page)",
  };
  return tones[kind] ?? "var(--graph-kind-other)";
}

function navigateToPage(path: string): void {
  void loadPage(path);
  history.pushState({ page: path }, "", `/?page=${encodeURIComponent(path)}`);
}

async function loadLocalGraph(targetPath: string): Promise<void> {
  const requestId = ++state.localGraphRequest;
  const stage = document.querySelector<HTMLElement>(".local-graph-stage")!;
  const svg = document.getElementById("local-graph-svg") as unknown as SVGSVGElement;
  const loading = document.getElementById("local-graph-loading")!;
  const empty = document.getElementById("local-graph-empty")!;
  const count = document.getElementById("local-graph-count")!;
  const inbound = document.getElementById("local-graph-in")!;
  const outbound = document.getElementById("local-graph-out")!;

  state.localGraphController?.destroy();
  state.localGraphController = null;
  svg.replaceChildren();
  stage.classList.remove("has-graph");
  loading.classList.remove("hidden");
  empty.classList.add("hidden");
  count.textContent = "Current page";
  inbound.textContent = "—";
  outbound.textContent = "—";

  try {
    const params = new URLSearchParams({ view: "local", path: targetPath });
    const response = await fetch(`/api/graph?${params}`);
    if (!response.ok) throw new Error(`Local graph request failed: ${response.status}`);
    const data = (await response.json()) as GraphData;
    if (requestId !== state.localGraphRequest || targetPath !== state.currentPath) return;

    const center = data.nodes.find((node) => node.id === targetPath || node.path === targetPath);
    const centerId = center?.id ?? targetPath;
    const inboundCount = center?.inbound ?? 0;
    const outboundCount = center?.outbound ?? 0;
    inbound.textContent = String(inboundCount);
    outbound.textContent = String(outboundCount);
    count.textContent = `${inboundCount} in · ${outboundCount} out`;

    if (!center || data.edges.length === 0) {
      empty.classList.remove("hidden");
      empty.querySelector("strong")!.textContent = center ? "No connections" : "No local graph";
      empty.querySelector("span")!.textContent = center
        ? "No page links point in or out yet."
        : "Run wiki lint to compile this page sidecar.";
      return;
    }

    stage.classList.add("has-graph");
    state.localGraphController = renderGraph(svg, data, {
      centerId,
      selectedId: centerId,
      compact: true,
      labelMode: "all",
      minWidth: 300,
      minHeight: 520,
      openOnClick: true,
      onNodeOpen: (node) => {
        if (node.navigable && node.path) navigateToPage(node.path);
      },
    });
  } catch (error) {
    if (requestId !== state.localGraphRequest) return;
    console.error(error);
    empty.classList.remove("hidden");
    empty.querySelector("strong")!.textContent = "Could not load connections";
    empty.querySelector("span")!.textContent = "Check the page-local .graph sidecar.";
  } finally {
    if (requestId === state.localGraphRequest) loading.classList.add("hidden");
  }
}

async function loadPage(pathArg: string): Promise<void> {
  const pageEl = document.getElementById("page-content")!;
  pageEl.innerHTML = '<p class="loading">Loading</p>';

  try {
    const res = await fetch(`/api/page?path=${encodeURIComponent(pathArg)}`);
    if (!res.ok) {
      pageEl.innerHTML = `<p class="loading">Failed to load <code>${escapeHtml(pathArg)}</code>: ${res.status}</p>`;
      return;
    }
    const data: PageResponse = await res.json();
    state.currentPath = data.path;
    state.rawMarkdown = data.raw;

    pageEl.innerHTML = data.html;

    await renderMermaidBlocks(pageEl);

    // Title chip.
    const titleEl = document.getElementById("wiki-title")!;
    titleEl.textContent = data.title ?? data.path;

    await Promise.all([loadAudits(data.path), loadLocalGraph(data.path)]);
    pageEl.scrollTop = 0;
    (document.querySelector("main") as HTMLElement | null)?.scrollTo({ top: 0 });
  } catch (err) {
    console.error(err);
    pageEl.innerHTML = `<p class="loading">Error loading page.</p>`;
  }
}

let mermaidRenderSequence = 0;

async function renderMermaidBlocks(pageEl: HTMLElement): Promise<void> {
  const mermaidNodes = pageEl.querySelectorAll("pre.mermaid-block code.language-mermaid");
  if (mermaidNodes.length === 0) return;
  await document.fonts.ready;
  for (const node of Array.from(mermaidNodes)) {
    const code = node as HTMLElement;
    const pre = code.parentElement as HTMLElement;
    const source = code.textContent ?? "";
    const container = document.createElement("div");
    container.className = "mermaid-block";
    container.dataset.mermaidSource = source;
    const srcLine = pre.getAttribute("data-source-line");
    if (srcLine) container.setAttribute("data-source-line", srcLine);
    pre.replaceWith(container);
    try {
      await renderMermaidContainer(container, source);
    } catch (err) {
      container.replaceWith(pre);
      console.error("mermaid render failed", err);
    }
  }
}

async function renderMermaidContainer(container: HTMLElement, source: string): Promise<void> {
  const id = `mermaid-${Date.now()}-${mermaidRenderSequence++}`;
  const { svg, bindFunctions } = await mermaid.render(id, source, container);
  container.innerHTML = svg;
  bindFunctions?.(container);
}

async function rerenderMermaidBlocks(): Promise<void> {
  const containers = document.querySelectorAll<HTMLElement>("#page-content .mermaid-block[data-mermaid-source]");
  if (containers.length === 0) return;
  await document.fonts.ready;
  for (const container of Array.from(containers)) {
    const source = container.dataset.mermaidSource;
    if (!source) continue;
    try {
      await renderMermaidContainer(container, source);
    } catch (err) {
      console.error("mermaid theme render failed", err);
    }
  }
}

async function applyTheme(theme: Theme, persist: boolean): Promise<void> {
  state.theme = theme;
  document.documentElement.dataset.theme = theme;
  document.documentElement.style.colorScheme = theme;
  if (persist) {
    try { localStorage.setItem(THEME_STORAGE_KEY, theme); } catch {}
  }
  configureMermaid(theme);
  updateThemeControl();
  await rerenderMermaidBlocks();
}

function readStoredTheme(): Theme | null {
  try {
    const value = localStorage.getItem(THEME_STORAGE_KEY);
    return value === "light" || value === "dark" ? value : null;
  } catch {
    return null;
  }
}

function updateThemeControl(): void {
  const button = document.getElementById("btn-theme");
  const icon = document.getElementById("theme-icon");
  if (!button || !icon) return;
  const light = state.theme === "light";
  const label = light ? "Switch to dark mode" : "Switch to light mode";
  icon.textContent = light ? "☾" : "☀";
  button.setAttribute("aria-label", label);
  button.setAttribute("aria-pressed", String(light));
  button.setAttribute("title", label);
}

async function loadAudits(targetPath: string): Promise<void> {
  const el = document.getElementById("audit-list")!;
  el.innerHTML = '<p class="loading">Loading</p>';
  try {
    const res = await fetch(`/api/audit?target=${encodeURIComponent(targetPath)}&mode=open`);
    const data: { entries: AuditEntry[] } = await res.json();
    if (data.entries.length === 0) {
      el.innerHTML = '<p class="muted" style="padding: 4px 6px; font-size: 12.5px;">No open audits for this page.</p>';
      return;
    }
    el.innerHTML = data.entries.map((e) => renderAuditItem(e)).join("");
    el.querySelectorAll("button[data-resolve]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const id = btn.getAttribute("data-resolve")!;
        const note = window.prompt("Resolution note (optional):", "") ?? "";
        const r = await fetch(`/api/audit/${id}/resolve`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ resolution: note }),
        });
        if (r.ok) await loadAudits(targetPath);
        else alert("Failed to resolve.");
      });
    });
  } catch (err) {
    el.innerHTML = '<p class="muted" style="padding: 4px 6px;">Failed to load audits.</p>';
    console.error(err);
  }
}

function renderAuditItem(e: AuditEntry): string {
  const body = e.body
    .replace(/^#\s*Comment\s*/i, "")
    .split(/^#\s*Resolution/im)[0]!
    .replace(/<!--[\s\S]*?-->/g, "")
    .trim();
  const when = new Date(e.created).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
  return `
    <div class="audit-item sev-${e.severity}" data-id="${e.id}">
      <div class="audit-head">
        <span class="sev-pill sev-${e.severity}">${e.severity}</span>
        <span class="author">${escapeHtml(e.author)}</span>
      </div>
      <div class="audit-body">${escapeHtml(body)}</div>
      <div class="audit-meta">${e.id} · ${when}</div>
      <div class="audit-actions"><button type="button" data-resolve="${e.id}">mark resolved</button></div>
    </div>`;
}

function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, (ch) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch] ?? ch),
  );
}

void main();
