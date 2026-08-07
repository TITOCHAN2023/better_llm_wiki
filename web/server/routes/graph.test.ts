import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { buildGraph } from "./graph.js";

function makeWiki(): string {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "llm-wiki-graph-"));
  fs.mkdirSync(path.join(root, "wiki", "concepts"), { recursive: true });
  fs.mkdirSync(path.join(root, "graph"), { recursive: true });
  return root;
}

function writeJson(root: string, relativePath: string, value: unknown): void {
  const full = path.join(root, relativePath);
  fs.mkdirSync(path.dirname(full), { recursive: true });
  fs.writeFileSync(full, `${JSON.stringify(value, null, 2)}\n`, "utf-8");
}

test("knowledge view reads compiled nodes and compact edge shards", (t) => {
  const root = makeWiki();
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  writeJson(root, "graph/manifest.graph", {
    generatedAt: "2026-08-07T12:00:00+08:00",
    nodesFile: "nodes.graph",
    edgeShards: ["edges-000.graph"],
    settingsFile: "settings.graph",
    statsFile: "stats.graph",
  });
  writeJson(root, "graph/nodes.graph", [
    { id: "wiki/concepts/A.md", kind: "concept", displayName: "Alpha", summary: "First" },
    { id: "wiki/concepts/B.md", kind: "entity", displayName: "Beta" },
    { id: "wiki/concepts/Q.md", kind: "query", displayName: "Hidden" },
  ]);
  writeJson(root, "graph/edges-000.graph", [
    { s: "wiki/concepts/A.md", t: "wiki/concepts/B.md", k: "links_to", w: 2, d: 0.8 },
    { s: "wiki/concepts/A.md", t: "wiki/concepts/Q.md", k: "links_to" },
  ]);
  writeJson(root, "graph/settings.graph", { hiddenKinds: ["query"], layout: { knowledge: { engine: "forceatlas2" } } });
  writeJson(root, "graph/stats.graph", { pages: 3, edges: 2 });

  const graph = buildGraph(root, "knowledge");
  assert.equal(graph.meta.source, "compiled");
  assert.equal(graph.meta.layout, "forceatlas2");
  assert.deepEqual(graph.nodes.map((node) => node.displayName), ["Alpha", "Beta"]);
  assert.deepEqual(graph.edges, [
    { source: "wiki/concepts/A.md", target: "wiki/concepts/B.md", kind: "links_to", weight: 2, depth: 0.8 },
  ]);
  assert.equal(graph.nodes[0]?.outbound, 1);
  assert.equal(graph.nodes[1]?.inbound, 1);
});

test("local view normalizes a page sidecar and enriches neighbor metadata", (t) => {
  const root = makeWiki();
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  writeJson(root, "graph/manifest.graph", { nodesFile: "nodes.graph" });
  writeJson(root, "graph/nodes.graph", [
    { id: "wiki/concepts/A.md", kind: "concept", displayName: "Alpha" },
    { id: "wiki/concepts/B.md", kind: "summary", displayName: "Beta summary", tags: ["demo"] },
  ]);
  writeJson(root, "wiki/concepts/A.md.graph", {
    schema: 1,
    id: "wiki/concepts/A.md",
    kind: "concept",
    displayName: "Alpha",
    summary: "Local center",
    ego: {
      in: [],
      out: [{ to: "wiki/concepts/B.md", kind: "mentions" }],
    },
  });

  const graph = buildGraph(root, "local", "/concepts/A.md");
  assert.equal(graph.nodes.length, 2);
  assert.equal(graph.nodes.find((node) => node.id.endsWith("B.md"))?.displayName, "Beta summary");
  assert.equal(graph.nodes.find((node) => node.id.endsWith("A.md"))?.summary, "Local center");
  assert.equal(graph.edges[0]?.kind, "mentions");
});

test("knowledge view keeps root-anchored links in the markdown fallback", (t) => {
  const root = makeWiki();
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  fs.rmSync(path.join(root, "graph"), { recursive: true, force: true });
  fs.writeFileSync(path.join(root, "wiki", "index.md"), "# Index\n\n[Alpha](/concepts/A.md)\n", "utf-8");
  fs.writeFileSync(path.join(root, "wiki", "concepts", "A.md"), "# Alpha\n", "utf-8");

  const graph = buildGraph(root, "knowledge");
  assert.equal(graph.meta.source, "markdown-fallback");
  assert.equal(graph.nodes.length, 2);
  assert.deepEqual(graph.edges.map((edge) => [edge.source, edge.target]), [
    ["wiki/index.md", "wiki/concepts/A.md"],
  ]);
});
