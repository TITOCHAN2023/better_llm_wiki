import mermaid from "mermaid";
import type { AuditEntry } from "audit-shared";
import { renderTree } from "./tree.js";
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

const state = {
  currentPath: "wiki/index.md" as string,
  rawMarkdown: "" as string,
  author: "me" as string,
  graphController: null as GraphController | null,
  graphData: null as GraphData | null,
  graphView: "knowledge" as GraphView,
  hiddenGraphKinds: new Set<string>(),
  selectedGraphNode: null as GraphNode | null,
};

// ── Mermaid with Catppuccin Mocha palette ──────────────────────────────────
mermaid.initialize({
  startOnLoad: false,
  theme: "base",
  securityLevel: "loose",
  fontFamily: "Inter, system-ui, sans-serif",
  themeVariables: {
    // canvas
    background: "#11111b",
    // nodes
    primaryColor: "#313244",
    primaryTextColor: "#cdd6f4",
    primaryBorderColor: "#b4befe",
    secondaryColor: "#45475a",
    secondaryTextColor: "#cdd6f4",
    secondaryBorderColor: "#89b4fa",
    tertiaryColor: "#585b70",
    tertiaryTextColor: "#cdd6f4",
    tertiaryBorderColor: "#94e2d5",
    // edges & text
    lineColor: "#7f849c",
    textColor: "#cdd6f4",
    mainBkg: "#313244",
    nodeBorder: "#b4befe",
    clusterBkg: "#181825",
    clusterBorder: "#45475a",
    titleColor: "#cdd6f4",
    edgeLabelBackground: "#181825",
    // sequence
    actorBkg: "#313244",
    actorBorder: "#b4befe",
    actorTextColor: "#cdd6f4",
    actorLineColor: "#7f849c",
    signalColor: "#cdd6f4",
    signalTextColor: "#cdd6f4",
    labelBoxBkgColor: "#313244",
    labelBoxBorderColor: "#b4befe",
    labelTextColor: "#cdd6f4",
    loopTextColor: "#cdd6f4",
    noteBkgColor: "#f9e2af",
    noteTextColor: "#11111b",
    noteBorderColor: "#f9e2af",
    activationBkgColor: "#45475a",
    activationBorderColor: "#b4befe",
    // state
    stateBkg: "#313244",
    stateBorder: "#b4befe",
    specialStateColor: "#f38ba8",
  },
});

async function main() {
  try {
    const cfg = await fetch("/api/config").then((r) => r.json());
    if (cfg.author) state.author = cfg.author;
  } catch {}

  // Tree.
  const tree = await fetch("/api/tree").then((r) => r.json());
  renderTree(document.getElementById("tree")!, tree, (path) => {
    void loadPage(path);
    history.pushState({ page: path }, "", `/?page=${encodeURIComponent(path)}`);
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
      void loadPage(page);
      history.pushState({ page }, "", `/?page=${encodeURIComponent(page)}`);
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
    void loadPage(node.path);
    history.pushState({ page: node.path }, "", `/?page=${encodeURIComponent(node.path)}`);
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

  const openButton = document.getElementById("graph-open-page") as HTMLButtonElement;
  openButton.disabled = !node.navigable;
  openButton.classList.toggle("hidden", !node.navigable);
}

function graphKindTone(kind: string): string {
  const tones: Record<string, string> = {
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
  return tones[kind] ?? "#8b8b94";
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

    // Render mermaid blocks.
    const mermaidNodes = pageEl.querySelectorAll("pre.mermaid-block code.language-mermaid");
    for (let i = 0; i < mermaidNodes.length; i++) {
      const code = mermaidNodes[i] as HTMLElement;
      const pre = code.parentElement as HTMLElement;
      const source = code.textContent ?? "";
      const id = `mermaid-${Date.now()}-${i}`;
      try {
        const { svg } = await mermaid.render(id, source);
        const container = document.createElement("div");
        container.className = "mermaid-block";
        container.innerHTML = svg;
        const srcLine = pre.getAttribute("data-source-line");
        if (srcLine) container.setAttribute("data-source-line", srcLine);
        pre.replaceWith(container);
      } catch (err) {
        console.error("mermaid render failed", err);
      }
    }

    // Tree selection highlight.
    document.querySelectorAll("#tree a.active").forEach((el) => el.classList.remove("active"));
    const link = document.querySelector(`#tree a[data-path="${cssEscape(data.path)}"]`);
    if (link) link.classList.add("active");

    // Title chip.
    const titleEl = document.getElementById("wiki-title")!;
    titleEl.textContent = data.title ?? data.path;

    await loadAudits(data.path);
    pageEl.scrollTop = 0;
    (document.querySelector("main") as HTMLElement | null)?.scrollTo({ top: 0 });
  } catch (err) {
    console.error(err);
    pageEl.innerHTML = `<p class="loading">Error loading page.</p>`;
  }
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

function cssEscape(s: string): string {
  return s.replace(/["\\]/g, "\\$&");
}

void main();
