# Graph JSON Reference

This document only explains the JSON content of `.graph` files.

It does not define rendering, layout, interaction, animation, or frontend architecture.

## General Rules

- Every `.graph` file is JSON.
- `schema` is currently `1`.
- `.graph` files are generated artifacts. Do not edit them by hand.
- Markdown remains the source of truth.
- `id` is the stable unique key for a node.
- File names and titles are not unique.

## Common Node Fields

```json
{
  "id": "wiki/concepts/HyLo.md",
  "kind": "concept",
  "title": "HyLo",
  "displayName": "HyLo",
  "shortName": "HyLo",
  "qualifiedName": "concepts / HyLo",
  "breadcrumb": ["wiki", "concepts", "HyLo"],
  "tags": ["hybrid-architecture"],
  "summary": "A short description.",
  "degree": 16,
  "inbound": 8,
  "outbound": 8,
  "d": 1
}
```

Fields:

- `id`: stable node ID.
- `kind`: node category, such as `concept`, `entity`, `summary`, `page`, `index`, `section`, `raw_source`, or `log`.
- `title`: page title from frontmatter, H1, or file stem.
- `displayName`: default human label.
- `shortName`: compact label.
- `qualifiedName`: contextual label, useful when names repeat.
- `breadcrumb`: path-like context.
- `tags`: page tags.
- `summary`: short page summary.
- `degree`: inbound plus outbound count.
- `inbound`: inbound edge count.
- `outbound`: outbound edge count.
- `d`: depth or recency value. Defaults to `1`.

## Common Edge Fields

```json
{
  "s": "wiki/concepts/HyLo.md",
  "t": "wiki/entities/Mamba.md",
  "k": "links_to",
  "w": 1,
  "d": 1
}
```

Fields:

- `s`: source node ID.
- `t`: target node ID.
- `k`: edge kind.
- `w`: edge weight. Defaults to `1`.
- `d`: depth or recency value. Defaults to `1`.

Common edge kinds:

- `links_to`
- `touched`
- `contains`
- `listed_in`
- `ingested_as`
- `mentions`
- `derived_from`

## `graph/manifest.graph`

Top-level index for global graph files.

```json
{
  "schema": 1,
  "generatedAt": "2026-04-29T16:21:56+08:00",
  "root": "demo-wiki",
  "nodesFile": "nodes.graph",
  "adjacencyFile": "adjacency.graph",
  "edgeShards": ["edges-000.graph"],
  "recentFile": "recent.graph",
  "navigationFile": "navigation.graph",
  "lineageFile": "lineage.graph",
  "settingsFile": "settings.graph",
  "statsFile": "stats.graph",
  "issuesFile": "issues.graph",
  "viewFile": "view.graph"
}
```

Fields:

- `schema`: protocol version.
- `generatedAt`: generation timestamp.
- `root`: wiki root path used when generated.
- `nodesFile`: node catalog filename.
- `adjacencyFile`: adjacency filename.
- `edgeShards`: edge shard filenames.
- `recentFile`: recent activity graph filename.
- `navigationFile`: index/navigation graph filename.
- `lineageFile`: source lineage graph filename.
- `settingsFile`: graph settings filename.
- `statsFile`: graph stats filename.
- `issuesFile`: graph issues filename.
- `viewFile`: optional view-state filename.

## `graph/nodes.graph`

Global node catalog.

Shape:

```json
[
  {
    "id": "wiki/concepts/HyLo.md",
    "kind": "concept",
    "title": "HyLo",
    "displayName": "HyLo",
    "shortName": "HyLo",
    "qualifiedName": "concepts / HyLo",
    "breadcrumb": ["wiki", "concepts", "HyLo"],
    "fileName": "HyLo.md",
    "parentName": "concepts",
    "tags": ["hybrid-architecture"],
    "summary": "A short description.",
    "degree": 16,
    "inbound": 8,
    "outbound": 8
  }
]
```

Additional fields:

- `fileName`: backing Markdown filename.
- `parentName`: parent folder name.

## `graph/edges-*.graph`

Global edge shard.

Shape:

```json
[
  {
    "s": "wiki/concepts/HyLo.md",
    "t": "wiki/entities/Mamba.md",
    "k": "links_to",
    "w": 1,
    "d": 1
  }
]
```

`edgeShards` in `manifest.graph` lists all shard files.

## `graph/adjacency.graph`

Neighbor lookup by node ID.

Shape:

```json
{
  "wiki/concepts/HyLo.md": {
    "in": [
      {
        "from": "wiki/index.md",
        "kind": "links_to"
      }
    ],
    "out": [
      {
        "to": "wiki/entities/Mamba.md",
        "kind": "links_to"
      }
    ]
  }
}
```

Fields:

- Object key: node ID.
- `in`: inbound neighbors.
- `in[].from`: source node ID.
- `in[].kind`: edge kind.
- `out`: outbound neighbors.
- `out[].to`: target node ID.
- `out[].kind`: edge kind.

## `wiki/**/*.md.graph`

Page-local sidecar for one Markdown page.

Shape:

```json
{
  "schema": 1,
  "id": "wiki/concepts/HyLo.md",
  "kind": "concept",
  "title": "HyLo",
  "displayName": "HyLo",
  "shortName": "HyLo",
  "qualifiedName": "concepts / HyLo",
  "breadcrumb": ["wiki", "concepts", "HyLo"],
  "fileName": "HyLo.md",
  "parentName": "concepts",
  "hash": "sha256:...",
  "tags": ["hybrid-architecture"],
  "summary": "A short description.",
  "ego": {
    "in": [
      {
        "from": "wiki/index.md",
        "kind": "links_to"
      }
    ],
    "out": [
      {
        "to": "wiki/entities/Mamba.md",
        "kind": "links_to"
      }
    ]
  },
  "stats": {
    "inbound": 8,
    "outbound": 8,
    "degree": 16
  }
}
```

Fields:

- `hash`: content hash of the backing Markdown page.
- `ego`: one-hop local graph.
- `ego.in`: inbound links.
- `ego.out`: outbound links.
- `stats`: local degree counts.

## `graph/recent.graph`

Recent operation graph.

This file is date-window based, not count-limited. Lint includes log entries from the last 21 days and groups touched pages into:

- `recent`: 0-7 days ago, connected from the synthetic `recent` node.
- `lastweek`: 7-14 days ago, connected from the synthetic `lastweek` node.
- `floating`: 14-21 days ago, included as standalone page nodes with no synthetic edge.

Shape:

```json
{
  "schema": 1,
  "window": {
    "kind": "relative_day_buckets",
    "anchorDate": "2026-05-15",
    "ranges": {
      "recent": { "fromDaysAgo": 0, "toDaysAgo": 7, "node": "recent" },
      "lastweek": { "fromDaysAgo": 7, "toDaysAgo": 14, "node": "lastweek" },
      "floating": { "fromDaysAgo": 14, "toDaysAgo": 21, "node": null }
    },
    "historyDays": 21,
    "datesByBucket": {
      "recent": [],
      "lastweek": [],
      "floating": ["2026-04-29"]
    },
    "availableLogDays": 1,
    "includedLogDays": 1,
    "entryLimit": null,
    "touchedNodeLimit": null
  },
  "entries": [
    {
      "id": "log-entry:20260429-14-55-ingest-example",
      "date": "2026-04-29",
      "time": "14:55",
      "op": "ingest",
      "title": "example ingest",
      "logPath": "log/20260429.md",
      "anchor": "14-55-ingest-example",
      "ageDays": 16,
      "bucket": "floating",
      "body": "- touched `wiki/concepts/HyLo.md`",
      "touched": ["wiki/concepts/HyLo.md"],
      "nodes": [
        {
          "id": "wiki/concepts/HyLo.md",
          "displayName": "HyLo",
          "qualifiedName": "concepts / HyLo",
          "kind": "concept"
        }
      ]
    }
  ],
  "nodes": [
    {
      "id": "recent",
      "kind": "recent",
      "displayName": "Recent 7 days",
      "qualifiedName": "Recent activity / 0-7 days",
      "entries": [],
      "d": 1
    },
    {
      "id": "lastweek",
      "kind": "recent",
      "displayName": "Last week",
      "qualifiedName": "Recent activity / 7-14 days",
      "entries": [],
      "d": 0.58
    },
    {
      "id": "wiki/concepts/HyLo.md",
      "kind": "concept",
      "displayName": "HyLo",
      "bucket": "floating",
      "buckets": ["floating"],
      "entries": [
        {
          "id": "log-entry:20260429-14-55-ingest-example",
          "date": "2026-04-29",
          "time": "14:55",
          "op": "ingest",
          "title": "example ingest",
          "logPath": "log/20260429.md",
          "anchor": "14-55-ingest-example",
          "ageDays": 16,
          "bucket": "floating",
          "d": 0.3
        }
      ],
      "d": 0.3
    }
  ],
  "edges": []
}
```

Fields:

- `window`: recent graph inclusion window.
- `window.kind`: currently `relative_day_buckets`.
- `window.anchorDate`: date used to calculate relative age.
- `window.ranges`: bucket definitions. Ranges are half-open except the final history boundary: `0 <= recent < 7`, `7 <= lastweek < 14`, `14 <= floating <= 21`.
- `window.historyDays`: maximum log age included.
- `window.datesByBucket`: included log dates grouped by bucket, newest first.
- `window.entryLimit`: always `null`; entries are not capped inside the included days.
- `window.touchedNodeLimit`: always `null`; touched page nodes are not capped.
- `entries`: full log-entry records.
- `entries[].ageDays`: age relative to `window.anchorDate`.
- `entries[].bucket`: `recent`, `lastweek`, or `floating`.
- `entries[].touched`: page IDs touched by that log entry.
- `entries[].nodes`: touched page metadata copied into the entry.
- `nodes`: graph nodes, including synthetic `recent`/`lastweek` nodes and touched page nodes. Log files and log entries are not graph nodes.
- `nodes[].bucket`: page node's primary time bucket. Synthetic nodes do not use this field.
- `nodes[].buckets`: all time buckets that touched this page within the 21-day window.
- `edges`: aggregated `recent -> page` and `lastweek -> page` edges. Floating pages have no edge.
- `edges[].entries`: log entries that touched that page.
- `edges[].w`: number of recent entries represented by the edge.
- `d`: recency depth. Newer dates are closer to `1`; older dates are smaller. For a merged edge, `d` is the newest/strongest represented entry depth.
- Do not emit `log/YYYYMMDD.md#entry-heading` as an edge endpoint or node id; that shape looks like a file path with an anchor and can break file-path indexes.

## `graph/navigation.graph`

Graph compiled from `wiki/index.md`.

Shape:

```json
{
  "schema": 1,
  "root": "wiki/index.md",
  "nodes": [
    {
      "id": "nav:wiki/index.md",
      "kind": "index",
      "displayName": "Index",
      "qualifiedName": "Navigation / Index",
      "page": "wiki/index.md"
    },
    {
      "id": "nav:wiki/index.md#concepts",
      "kind": "section",
      "displayName": "Concepts",
      "qualifiedName": "Navigation / Concepts",
      "level": 2,
      "page": "wiki/index.md"
    },
    {
      "id": "wiki/concepts/HyLo.md",
      "kind": "concept",
      "displayName": "HyLo",
      "qualifiedName": "concepts / HyLo"
    }
  ],
  "edges": [
    {
      "s": "nav:wiki/index.md",
      "t": "nav:wiki/index.md#concepts",
      "k": "contains",
      "w": 1,
      "d": 1
    },
    {
      "s": "nav:wiki/index.md#concepts",
      "t": "wiki/concepts/HyLo.md",
      "k": "listed_in",
      "w": 1,
      "d": 1
    }
  ]
}
```

Fields:

- `root`: index page ID.
- `kind: "index"`: root index node.
- `kind: "section"`: heading from `wiki/index.md`.
- `level`: Markdown heading level.
- `page`: source page that produced the navigation node.
- `contains`: index-to-section edge.
- `listed_in`: section-to-page edge.

## `graph/lineage.graph`

Source lineage graph.

Shape:

```json
{
  "schema": 1,
  "nodes": [
    {
      "id": "raw/papers/example.md",
      "kind": "raw_source",
      "displayName": "Example Paper",
      "qualifiedName": "raw/papers/example.md"
    },
    {
      "id": "wiki/summaries/example.md",
      "kind": "summary",
      "displayName": "Example summary"
    },
    {
      "id": "wiki/concepts/HyLo.md",
      "kind": "concept",
      "displayName": "HyLo"
    }
  ],
  "edges": [
    {
      "s": "raw/papers/example.md",
      "t": "wiki/summaries/example.md",
      "k": "ingested_as",
      "w": 1,
      "d": 1
    },
    {
      "s": "wiki/summaries/example.md",
      "t": "wiki/concepts/HyLo.md",
      "k": "mentions",
      "w": 1,
      "d": 1
    }
  ]
}
```

Fields:

- `raw_source`: node from `raw/`.
- `ingested_as`: raw source to generated wiki page.
- `mentions`: summary page to linked wiki page.
- `derived_from`: wiki page to another wiki page listed as a source.

## `graph/settings.graph`

Advisory graph metadata.

Shape:

```json
{
  "schema": 1,
  "hiddenKinds": ["query"],
  "defaultView": "knowledge",
  "views": ["knowledge", "recent", "navigation", "lineage"],
  "layout": {
    "knowledge": {
      "engine": "forceatlas2",
      "edgeWeightInfluence": 1
    }
  },
  "typeAffinity": {
    "entity": {
      "concept": 1.2,
      "entity": 0.8
    }
  }
}
```

Fields:

- `hiddenKinds`: node kinds that should usually be hidden.
- `defaultView`: default graph view name.
- `views`: available graph view names.
- `layout`: advisory metadata keyed by view name.
- `typeAffinity`: advisory relation strength by node kind.

## `graph/stats.graph`

Aggregate graph stats.

Shape:

```json
{
  "pages": 10,
  "edges": 49,
  "orphans": 0,
  "deadLinks": 0,
  "byKind": {
    "concept": 6,
    "entity": 2,
    "page": 1,
    "summary": 1
  }
}
```

Fields:

- `pages`: wiki page node count.
- `edges`: global edge count.
- `orphans`: orphan page count.
- `deadLinks`: dead-link count.
- `byKind`: node count by kind.

## `graph/issues.graph`

Machine-readable graph issues.

Shape:

```json
{
  "orphans": [],
  "deadLinks": [
    {
      "source": "wiki/index.md",
      "target": "wiki/missing.md"
    }
  ],
  "missingIndexEntries": []
}
```

Fields:

- `orphans`: page IDs with no inbound links.
- `deadLinks`: unresolved links.
- `deadLinks[].source`: source page ID.
- `deadLinks[].target`: unresolved target path.
- `missingIndexEntries`: page IDs missing from `wiki/index.md`.

## `graph/view.graph`

Optional frontend-owned view state.

Shape:

```json
{
  "positions": {
    "wiki/concepts/HyLo.md": {
      "x": 120,
      "y": 80
    }
  },
  "zoom": 0.9,
  "pan": {
    "x": 0,
    "y": 0
  }
}
```

Fields:

- `positions`: node positions keyed by node ID.
- `zoom`: saved zoom value.
- `pan`: saved pan offset.

`lint` does not rewrite `view.graph`.

## `.graph-cache/*`

Internal compiler cache.

Common files:

- `page-hashes.graph`
- `reverse-links.graph`
- `dirty.graph`

These files are implementation details for `lint` and are not part of the display protocol.
