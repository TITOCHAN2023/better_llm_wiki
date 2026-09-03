import * as d3drag from "d3-drag";
import * as d3force from "d3-force";
import * as d3sel from "d3-selection";
import * as d3zoom from "d3-zoom";

export type GraphView = "knowledge" | "recent" | "navigation" | "lineage" | "local";

export interface GraphNode extends d3force.SimulationNodeDatum {
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

export interface GraphEdge extends d3force.SimulationLinkDatum<GraphNode> {
  source: string | GraphNode;
  target: string | GraphNode;
  kind: string;
  weight: number;
  depth: number;
}

export interface GraphData {
  nodes: GraphNode[];
  edges: GraphEdge[];
  meta: {
    view: GraphView;
    source: "compiled" | "markdown-fallback";
    generatedAt: string | null;
    availableViews: GraphView[];
    layout: string | null;
    stats: Record<string, unknown>;
  };
}

export interface GraphOptions {
  selectedId?: string | null;
  centerId?: string | null;
  compact?: boolean;
  labelMode?: "priority" | "all";
  minWidth?: number;
  minHeight?: number;
  openOnClick?: boolean;
  onNodeSelect?: (node: GraphNode | null) => void;
  onNodeOpen?: (node: GraphNode) => void;
}

export interface GraphController {
  destroy(): void;
  fit(): void;
  focusNode(id: string): void;
  reset(): void;
}

const KIND_TONES: Record<string, string> = {
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

let graphInstanceSequence = 0;

/**
 * Render one normalized graph shape. The server absorbs protocol/version
 * differences; this function only deals with layout and interaction.
 */
export function renderGraph(
  svgEl: SVGSVGElement,
  data: GraphData,
  opts: GraphOptions = {},
): GraphController {
  const svg = d3sel.select(svgEl);
  svg.selectAll("*").remove();
  const instanceKey = sanitizeToken(svgEl.id || `graph-${++graphInstanceSequence}`);
  const glowId = `${instanceKey}-soft-glow`;
  const arrowId = `${instanceKey}-arrow`;
  svg.style("--graph-soft-glow", `url(#${glowId})`);

  const dimensions = () => ({
    width: Math.max(svgEl.clientWidth, opts.minWidth ?? 640),
    height: Math.max(svgEl.clientHeight, opts.minHeight ?? 480),
  });
  let { width, height } = dimensions();
  svg.attr("viewBox", `0 0 ${width} ${height}`);

  const defs = svg.append("defs");
  const glow = defs
    .append("filter")
    .attr("id", glowId)
    .attr("x", "-100%")
    .attr("y", "-100%")
    .attr("width", "300%")
    .attr("height", "300%");
  glow.append("feGaussianBlur").attr("stdDeviation", 5).attr("result", "blur");
  const merge = glow.append("feMerge");
  merge.append("feMergeNode").attr("in", "blur");
  merge.append("feMergeNode").attr("in", "SourceGraphic");

  defs
    .append("marker")
    .attr("id", arrowId)
    .attr("viewBox", "0 -5 10 10")
    .attr("refX", 17)
    .attr("refY", 0)
    .attr("markerWidth", 4)
    .attr("markerHeight", 4)
    .attr("orient", "auto")
    .append("path")
    .attr("d", "M0,-4L8,0L0,4")
    .attr("fill", "context-stroke");

  const root = svg.append("g").attr("class", "graph-root");
  const linkLayer = root.append("g").attr("class", "links");
  const nodeLayer = root.append("g").attr("class", "nodes");

  const nodes = data.nodes.map((node) => ({ ...node }));
  const links = data.edges.map((edge) => ({ ...edge }));
  const nodeById = new Map(nodes.map((node) => [node.id, node]));
  const centerId = opts.centerId ?? null;
  const incoming = new Set<string>();
  const outgoing = new Set<string>();
  const adjacency = new Map<string, Set<string>>(nodes.map((node) => [node.id, new Set()]));
  for (const edge of links) {
    const source = edgeId(edge.source);
    const target = edgeId(edge.target);
    adjacency.get(source)?.add(target);
    adjacency.get(target)?.add(source);
    if (centerId && target === centerId) incoming.add(source);
    if (centerId && source === centerId) outgoing.add(target);
  }

  seedPositions(nodes, width, height, centerId, links);
  const centerNode = centerId ? nodeById.get(centerId) : undefined;
  if (centerNode) {
    centerNode.fx = width / 2;
    centerNode.fy = height / 2;
  }
  const relationFor = (id: string): "center" | "incoming" | "outgoing" | "both" | "other" => {
    if (id === centerId) return "center";
    if (incoming.has(id) && outgoing.has(id)) return "both";
    if (incoming.has(id)) return "incoming";
    if (outgoing.has(id)) return "outgoing";
    return "other";
  };
  const targetY = (node: GraphNode): number => {
    if (!centerId) return height / 2;
    const relation = relationFor(node.id);
    if (relation === "incoming") return height * 0.24;
    if (relation === "outgoing") return height * 0.76;
    return height / 2;
  };
  const radius = (node: GraphNode) => {
    const base = Math.min(17, 5.5 + Math.sqrt(Math.max(1, node.degree)) * 1.55);
    return node.id === centerId ? Math.max(12, base) : base;
  };
  const linkDistance = opts.compact
    ? 86
    : data.meta.view === "local"
      ? 155
      : data.meta.view === "navigation"
        ? 118
        : 96;
  const charge = opts.compact ? -190 : data.meta.view === "local" ? -520 : -260;

  const simulation = d3force
    .forceSimulation<GraphNode>(nodes)
    .force(
      "link",
      d3force
        .forceLink<GraphNode, GraphEdge>(links)
        .id((node) => node.id)
        .distance((edge) => linkDistance + Math.min(54, 12 * Math.log2(edge.weight + 1)))
        .strength(0.34),
    )
    .force("charge", d3force.forceManyBody<GraphNode>().strength(charge).distanceMax(720))
    .force("center", d3force.forceCenter(width / 2, height / 2))
    .force(
      "collision",
      d3force.forceCollide<GraphNode>().radius((node) => radius(node) + 20).strength(0.92),
    )
    .force("x", d3force.forceX<GraphNode>(width / 2).strength(opts.compact ? 0.055 : 0.028))
    .force(
      "y",
      d3force.forceY<GraphNode>((node) => targetY(node)).strength(opts.compact ? 0.09 : 0.028),
    )
    .alphaDecay(0.035)
    .velocityDecay(0.38);

  const linkSelection = linkLayer
    .selectAll<SVGPathElement, GraphEdge>("path")
    .data(links)
    .enter()
    .append("path")
    .attr("class", (edge) => {
      const source = edgeId(edge.source);
      const target = edgeId(edge.target);
      const relation = target === centerId ? "incoming" : source === centerId ? "outgoing" : "other";
      return `link edge-${sanitizeToken(edge.kind)} relation-${relation}`;
    })
    .attr("fill", "none")
    .attr("marker-end", `url(#${arrowId})`)
    .attr("stroke-opacity", (edge) => 0.1 + 0.38 * edge.depth)
    .attr("stroke-width", (edge) => 0.7 + Math.min(2.2, Math.log2(edge.weight + 1) * 0.65));

  const priorityLabels = new Set(
    opts.labelMode === "all"
      ? nodes.map((node) => node.id)
      : [...nodes]
          .sort((a, b) => b.degree - a.degree)
          .slice(0, Math.max(8, Math.min(18, Math.ceil(nodes.length * 0.14))))
          .map((node) => node.id),
  );

  const nodeSelection = nodeLayer
    .selectAll<SVGGElement, GraphNode>("g.node")
    .data(nodes)
    .enter()
    .append("g")
    .attr("class", (node) => {
      const selected = node.id === opts.selectedId ? " selected" : "";
      const labeled = priorityLabels.has(node.id) ? " labeled" : "";
      const relation = centerId ? ` relation-${relationFor(node.id)}` : "";
      return `node kind-${sanitizeToken(node.kind)}${relation}${selected}${labeled}`;
    })
    .attr("role", "button")
    .attr("tabindex", 0)
    .attr("aria-label", (node) => `${node.displayName}, ${node.kind}, ${node.degree} connections`);

  nodeSelection.append("title").text((node) => {
    const context = node.qualifiedName && node.qualifiedName !== node.displayName
      ? `\n${node.qualifiedName}`
      : "";
    return `${node.displayName}${context}\n${node.degree} connections`;
  });

  nodeSelection
    .append("circle")
    .attr("class", "node-aura")
    .attr("r", (node) => radius(node) + 7)
    .attr("fill", (node) => toneFor(node.kind));

  nodeSelection
    .append("circle")
    .attr("class", "node-core")
    .attr("r", radius)
    .attr("fill", (node) => toneFor(node.kind));

  nodeSelection
    .append("circle")
    .attr("class", "node-center")
    .attr("r", (node) => Math.max(1.8, radius(node) * 0.28));

  nodeSelection
    .append("text")
    .attr("class", "node-label")
    .attr("dy", (node) => radius(node) + 16)
    .attr("text-anchor", "middle")
    .text((node) => truncate(node.shortName || node.displayName, 28));

  const dragBehavior = d3drag
    .drag<SVGGElement, GraphNode>()
    .on("start", (event, node) => {
      if (node.id === centerId) return;
      if (!event.active) simulation.alphaTarget(0.14).restart();
      node.fx = node.x;
      node.fy = node.y;
    })
    .on("drag", (event, node) => {
      if (node.id === centerId) return;
      node.fx = event.x;
      node.fy = event.y;
    })
    .on("end", (event, node) => {
      if (node.id === centerId) return;
      if (!event.active) simulation.alphaTarget(0);
      node.fx = null;
      node.fy = null;
    });
  nodeSelection.call(dragBehavior);

  const zoomBehavior = d3zoom
    .zoom<SVGSVGElement, unknown>()
    .scaleExtent([0.16, 5])
    .on("zoom", (event) => root.attr("transform", event.transform.toString()));
  svg.call(zoomBehavior);
  svg.on("dblclick.zoom", null);
  svg.on("click", () => {
    selectNode(null);
    opts.onNodeSelect?.(null);
  });

  nodeSelection
    .on("mouseenter", (_event, node) => highlightNeighborhood(node.id))
    .on("mouseleave", () => highlightNeighborhood(null))
    .on("click", (event, node) => {
      event.stopPropagation();
      if (opts.openOnClick && node.id !== centerId && node.navigable) {
        opts.onNodeOpen?.(node);
        return;
      }
      selectNode(node.id);
      opts.onNodeSelect?.(node);
    })
    .on("dblclick", (event, node) => {
      event.stopPropagation();
      if (node.navigable) opts.onNodeOpen?.(node);
    })
    .on("keydown", (event, node) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        if (opts.openOnClick && node.id !== centerId && node.navigable) {
          opts.onNodeOpen?.(node);
          return;
        }
        selectNode(node.id);
        opts.onNodeSelect?.(node);
      }
    });

  function selectNode(id: string | null): void {
    nodeSelection.classed("selected", (node) => node.id === id);
    if (id) highlightNeighborhood(id);
    else highlightNeighborhood(null);
  }

  function highlightNeighborhood(id: string | null): void {
    if (!id) {
      nodeSelection.classed("muted-node", false).classed("neighbor", false);
      linkSelection.classed("muted-edge", false).classed("active-edge", false);
      return;
    }
    const neighbors = adjacency.get(id) ?? new Set<string>();
    nodeSelection
      .classed("muted-node", (node) => node.id !== id && !neighbors.has(node.id))
      .classed("neighbor", (node) => neighbors.has(node.id));
    linkSelection
      .classed("muted-edge", (edge) => edgeId(edge.source) !== id && edgeId(edge.target) !== id)
      .classed("active-edge", (edge) => edgeId(edge.source) === id || edgeId(edge.target) === id);
  }

  simulation.on("tick", () => {
    linkSelection.attr("d", (edge) => curvedPath(edge));
    nodeSelection.attr("transform", (node) => `translate(${node.x ?? 0},${node.y ?? 0})`);
  });

  const fit = (): void => {
    if (!nodes.length) return;
    const xs = nodes.map((node) => node.x ?? width / 2);
    const ys = nodes.map((node) => node.y ?? height / 2);
    const minX = Math.min(...xs) - 56;
    const maxX = Math.max(...xs) + 56;
    const minY = Math.min(...ys) - 56;
    const maxY = Math.max(...ys) + 56;
    const spanX = Math.max(1, maxX - minX);
    const spanY = Math.max(1, maxY - minY);
    const scale = Math.max(0.18, Math.min(1.65, 0.88 / Math.max(spanX / width, spanY / height)));
    const centerX = (minX + maxX) / 2;
    const centerY = (minY + maxY) / 2;
    const transform = d3zoom.zoomIdentity
      .translate(width / 2 - centerX * scale, height / 2 - centerY * scale)
      .scale(scale);
    svg.call(zoomBehavior.transform, transform);
  };

  const focusNode = (id: string): void => {
    const node = nodeById.get(id);
    if (!node) return;
    selectNode(id);
    opts.onNodeSelect?.(node);
    const scale = 1.55;
    const transform = d3zoom.zoomIdentity
      .translate(width / 2 - (node.x ?? width / 2) * scale, height / 2 - (node.y ?? height / 2) * scale)
      .scale(scale);
    svg.call(zoomBehavior.transform, transform);
  };

  const reset = (): void => {
    for (const node of nodes) {
      node.fx = null;
      node.fy = null;
    }
    seedPositions(nodes, width, height, centerId, links);
    const center = centerId ? nodeById.get(centerId) : undefined;
    if (center) {
      center.fx = width / 2;
      center.fy = height / 2;
    }
    simulation.alpha(0.9).restart();
    svg.call(zoomBehavior.transform, d3zoom.zoomIdentity);
    window.setTimeout(() => fit(), 560);
  };

  const resizeObserver = new ResizeObserver(() => {
    const next = dimensions();
    if (next.width === width && next.height === height) return;
    width = next.width;
    height = next.height;
    svg.attr("viewBox", `0 0 ${width} ${height}`);
    simulation.force("center", d3force.forceCenter(width / 2, height / 2));
    simulation.force("x", d3force.forceX<GraphNode>(width / 2).strength(opts.compact ? 0.055 : 0.028));
    simulation.force(
      "y",
      d3force.forceY<GraphNode>((node) => targetY(node)).strength(opts.compact ? 0.09 : 0.028),
    );
    if (centerNode) {
      centerNode.fx = width / 2;
      centerNode.fy = height / 2;
    }
    simulation.alpha(0.24).restart();
  });
  resizeObserver.observe(svgEl);

  window.setTimeout(() => fit(), nodes.length > 120 ? 1000 : 680);

  return {
    destroy() {
      resizeObserver.disconnect();
      simulation.stop();
      svg.on(".zoom", null).selectAll("*").remove();
    },
    fit: () => fit(),
    focusNode,
    reset,
  };
}

function seedPositions(
  nodes: GraphNode[],
  width: number,
  height: number,
  centerId: string | null = null,
  edges: GraphEdge[] = [],
): void {
  if (centerId) {
    const incoming = new Set<string>();
    const outgoing = new Set<string>();
    for (const edge of edges) {
      const source = edgeId(edge.source);
      const target = edgeId(edge.target);
      if (target === centerId) incoming.add(source);
      if (source === centerId) outgoing.add(target);
    }
    const groups: Record<"incoming" | "outgoing" | "both" | "other", GraphNode[]> = {
      incoming: [],
      outgoing: [],
      both: [],
      other: [],
    };
    for (const node of nodes) {
      if (node.id === centerId) {
        node.x = width / 2;
        node.y = height / 2;
        node.vx = 0;
        node.vy = 0;
        continue;
      }
      const isIncoming = incoming.has(node.id);
      const isOutgoing = outgoing.has(node.id);
      const relation = isIncoming && isOutgoing
        ? "both"
        : isIncoming
          ? "incoming"
          : isOutgoing
            ? "outgoing"
            : "other";
      groups[relation].push(node);
    }
    const placeBand = (items: GraphNode[], centerY: number, spreadY: number): void => {
      items.sort((a, b) => hashString(a.id) - hashString(b.id));
      const count = Math.max(1, items.length);
      items.forEach((node, index) => {
        const angle = ((index + 0.5) / count) * Math.PI * 2;
        node.x = width / 2 + Math.cos(angle) * Math.max(54, width * 0.34);
        node.y = centerY + Math.sin(angle) * spreadY;
        node.vx = 0;
        node.vy = 0;
      });
    };
    placeBand(groups.incoming, height * 0.24, Math.max(42, height * 0.105));
    placeBand(groups.outgoing, height * 0.76, Math.max(42, height * 0.105));
    placeBand(groups.both, height / 2, Math.max(50, height * 0.16));
    placeBand(groups.other, height / 2, Math.max(56, height * 0.2));
    return;
  }
  const ring = Math.max(130, Math.min(width, height) * 0.34);
  for (const node of nodes) {
    const hash = hashString(node.id);
    const angle = ((hash % 4096) / 4096) * Math.PI * 2;
    const radial = 0.42 + ((hash >>> 12) % 1000) / 1650;
    node.x = width / 2 + Math.cos(angle) * ring * radial;
    node.y = height / 2 + Math.sin(angle) * ring * radial;
    node.vx = 0;
    node.vy = 0;
  }
}

function curvedPath(edge: GraphEdge): string {
  const source = edge.source as GraphNode;
  const target = edge.target as GraphNode;
  if (source.x == null || source.y == null || target.x == null || target.y == null) return "";
  const dx = target.x - source.x;
  const dy = target.y - source.y;
  const distance = Math.max(1, Math.hypot(dx, dy));
  const bend = Math.min(42, distance * 0.11);
  const mx = (source.x + target.x) / 2 - (dy / distance) * bend;
  const my = (source.y + target.y) / 2 + (dx / distance) * bend;
  return `M${source.x},${source.y} Q${mx},${my} ${target.x},${target.y}`;
}

function edgeId(value: string | GraphNode): string {
  return typeof value === "string" ? value : value.id;
}

function toneFor(kind: string): string {
  return KIND_TONES[kind] ?? "var(--graph-kind-other)";
}

function sanitizeToken(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9_-]+/g, "-") || "other";
}

function truncate(value: string, max: number): string {
  return value.length <= max ? value : `${value.slice(0, max - 1)}…`;
}

function hashString(value: string): number {
  let hash = 2166136261;
  for (let i = 0; i < value.length; i++) {
    hash ^= value.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}
