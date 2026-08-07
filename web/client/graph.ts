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
  index: "#ffffff",
  concept: "#f4f4f5",
  synthesis: "#e4e4e7",
  entity: "#d4d4d8",
  summary: "#a1a1aa",
  raw_source: "#71717a",
  recent: "#ffffff",
  section: "#8b8b94",
  page: "#b7b7bd",
};

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

  const dimensions = () => ({
    width: Math.max(svgEl.clientWidth, 640),
    height: Math.max(svgEl.clientHeight, 480),
  });
  let { width, height } = dimensions();
  svg.attr("viewBox", `0 0 ${width} ${height}`);

  const defs = svg.append("defs");
  const glow = defs
    .append("filter")
    .attr("id", "graph-soft-glow")
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
    .attr("id", "graph-arrow")
    .attr("viewBox", "0 -5 10 10")
    .attr("refX", 17)
    .attr("refY", 0)
    .attr("markerWidth", 4)
    .attr("markerHeight", 4)
    .attr("orient", "auto")
    .append("path")
    .attr("d", "M0,-4L8,0L0,4")
    .attr("fill", "#6f6f78");

  const root = svg.append("g").attr("class", "graph-root");
  const linkLayer = root.append("g").attr("class", "links");
  const nodeLayer = root.append("g").attr("class", "nodes");

  const nodes = data.nodes.map((node) => ({ ...node }));
  const links = data.edges.map((edge) => ({ ...edge }));
  const nodeById = new Map(nodes.map((node) => [node.id, node]));
  const adjacency = new Map<string, Set<string>>(nodes.map((node) => [node.id, new Set()]));
  for (const edge of links) {
    const source = edgeId(edge.source);
    const target = edgeId(edge.target);
    adjacency.get(source)?.add(target);
    adjacency.get(target)?.add(source);
  }

  seedPositions(nodes, width, height);
  const radius = (node: GraphNode) => Math.min(17, 5.5 + Math.sqrt(Math.max(1, node.degree)) * 1.55);
  const linkDistance = data.meta.view === "local" ? 155 : data.meta.view === "navigation" ? 118 : 96;
  const charge = data.meta.view === "local" ? -520 : -260;

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
    .force("x", d3force.forceX(width / 2).strength(0.028))
    .force("y", d3force.forceY(height / 2).strength(0.028))
    .alphaDecay(0.035)
    .velocityDecay(0.38);

  const linkSelection = linkLayer
    .selectAll<SVGPathElement, GraphEdge>("path")
    .data(links)
    .enter()
    .append("path")
    .attr("class", (edge) => `link edge-${sanitizeToken(edge.kind)}`)
    .attr("fill", "none")
    .attr("marker-end", "url(#graph-arrow)")
    .attr("stroke-opacity", (edge) => 0.1 + 0.38 * edge.depth)
    .attr("stroke-width", (edge) => 0.7 + Math.min(2.2, Math.log2(edge.weight + 1) * 0.65));

  const priorityLabels = new Set(
    [...nodes]
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
      return `node kind-${sanitizeToken(node.kind)}${selected}${labeled}`;
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
      if (!event.active) simulation.alphaTarget(0.14).restart();
      node.fx = node.x;
      node.fy = node.y;
    })
    .on("drag", (event, node) => {
      node.fx = event.x;
      node.fy = event.y;
    })
    .on("end", (event, node) => {
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
    seedPositions(nodes, width, height);
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
    simulation.force("x", d3force.forceX(width / 2).strength(0.028));
    simulation.force("y", d3force.forceY(height / 2).strength(0.028));
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

function seedPositions(nodes: GraphNode[], width: number, height: number): void {
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
  return KIND_TONES[kind] ?? "#8b8b94";
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
