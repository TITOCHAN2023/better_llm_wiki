#!/usr/bin/env python3
"""
ingest_scan.py — Scope a fresh ingest's 1-hop neighborhood as an audit reading list.

The closing incremental `lint` after an ingest only loads the changed pages,
so a new page is never reviewed against the existing pages it relates to.
This script fills that gap: it auto-detects which pages just changed, expands
one hop along the link graph (both directions), and hands the resulting page
set to the agent as an **audit scope**.

The script deliberately does NOT judge contradictions itself — computing the
scope is the deterministic part; deciding whether the pages actually conflict
is a semantic call left to the agent (string-matching numeric claims would
mis-flag e.g. `7B` vs `70B` as contradictions). Output is advisory only and
is never written into `audit/`.

Usage:
    python3 ingest_scan.py <wiki-root> [--seed wiki/...md ...] [--depth N]
                          [--format text|json] [--write]
                          [--audited-state-file PATH]

Seed auto-detection (in order, first non-empty wins; --seed always overrides):
  1. `git status` + stat-cache-vs-last-lint (union) — the same set
     incremental `lint_wiki.py` just touched, including post-`git pull`
     and post-checkout deltas.
  2. `graph/recent.graph` — last ingest's target pages.
  3. Explicit `--seed wiki/...md` (repeatable) — manual override.

Sliding-window loop (incremental mode) — a queue / BFS worklist:
    Pass `--audited-state-file <path>` to make every invocation a *round*.
    The file persists the set of nodes already *enqueued* — i.e. changed or
    fixed and processed in an earlier round (`enqueued`). Each round's seeds
    are the just-changed nodes (the ones popped this round); the script audits
    only the edges from a seed to a neighbor that has NOT been enqueued before
    (`new_edges`), then merges this round's seeds into the file.

    An edge back toward an already-enqueued node is shielded: that relationship
    was reconciled when the earlier node was processed, and since every fix
    aligns the wrong side to the originating node as the source of truth, it
    stays transitively consistent — no version/hash bookkeeping needed. Delete
    the file (or use a fresh path) to start a new loop.

    The enqueued-set guarantees each node is processed at most once, so the
    loop terminates in at most (node-count) rounds — it cannot oscillate. Stop
    when the report says `Converged`, when seeds are empty, or if the
    8-iteration safety cap trips (escalate to human if you somehow hit it).

Examples:
    python3 ingest_scan.py ./my-wiki                            # auto-seed, one-shot
    python3 ingest_scan.py ./my-wiki --seed wiki/concepts/Foo.md
    python3 ingest_scan.py ./my-wiki --depth 2 --write          # widen to 2 hops, save
    python3 ingest_scan.py ./my-wiki --audited-state-file /tmp/wiki-audit.json
        # call repeatedly inside the loop; state file accumulates audited (page, edge) sets

When --write is set, the report lands at <wiki-root>/outputs/audit-cr/ingest-scan.md.

Exit codes:
  0 — scope produced (or no seeds detected; the latter is informational, not an error)
  2 — invalid wiki root, missing graph/, or bad --depth
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# Canonical wiki link literal. Two anchor forms are accepted:
#   • OKF content-root `/…md` — leading `/` == the content root (`wiki/`).
#   • legacy root-relative `wiki/…md`.
# Two shapes each: `<… with spaces.md>` (angle-bracketed, may contain spaces and
# parens, as the skill mandates for space-bearing paths) OR a bare `…md`.
# Group 1 captures the bracketed inner path; group 2 the bare path. Callers
# normalize the leading-`/` form to `wiki/…` via canonical_link_target().
WIKI_MD_LINK_RE = re.compile(r"<((?:/|wiki/)[^<>]*?\.md)>|((?:/|wiki/)[^\s`<>\)]*?\.md)")


def canonical_link_target(captured: str) -> str:
    """Normalize a captured link path to a `wiki/…` page id.
    `/entities/X.md` (content-root anchor) → `wiki/entities/X.md`;
    a `wiki/…` path is returned unchanged."""
    return "wiki" + captured if captured.startswith("/") else captured


def load_graph_json(root: Path, name: str) -> Any:
    try:
        return json.loads((root / "graph" / name).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def changed_wiki_seeds_from_git(root: Path) -> list[str] | None:
    """Auto-detect seeds: uncommitted `wiki/**/*.md` from `git status`.

    Returns the list of changed wiki pages, or None if the wiki isn't under git
    (so callers can fall back). This mirrors what the incremental lint default
    sees, so the seed set is the same set of pages the just-finished
    `lint <root>` just touched — no flag-passing required.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain", "--", "wiki/"],
            capture_output=True, text=True, timeout=5,
        )
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return None
    if result.returncode != 0:
        return None
    seeds: set[str] = set()
    for raw_line in result.stdout.splitlines():
        if len(raw_line) < 4:
            continue
        path = raw_line[3:].strip()
        # Renames look like "R  old -> new" — take the new path.
        if " -> " in path:
            path = path.split(" -> ", 1)[1].strip()
        # Strip surrounding quotes git uses for paths with unusual chars.
        if path.startswith('"') and path.endswith('"'):
            path = path[1:-1]
        if path.startswith("wiki/") and path.endswith(".md") and not path.endswith(".md.graph"):
            if (root / path).is_file():
                seeds.add(path)
    return sorted(seeds)


def changed_wiki_seeds_from_stat_cache(root: Path) -> list[str]:
    """Auto-detect seeds: pages whose mtime/size diverges from `.graph-cache/md-file-stats.graph`.

    This is what catches `git pull` and branch-checkout deltas — the working
    tree is clean (so `git status` returns nothing) but pages on disk no
    longer match the snapshot the last lint recorded. The cache is the same
    file `lint_wiki.py` writes; if it doesn't exist yet we return [] and let
    the caller fall back further down the chain.
    """
    cache_path = root / ".graph-cache" / "md-file-stats.graph"
    if not cache_path.is_file():
        return []
    try:
        old = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(old, dict):
        return []

    wiki_dir = root / "wiki"
    if not wiki_dir.is_dir():
        return []

    seeds: set[str] = set()
    for path in wiki_dir.rglob("*.md"):
        if not path.is_file() or path.name.endswith(".md.graph"):
            continue
        rel = path.relative_to(root).as_posix()
        try:
            stat = path.stat()
        except OSError:
            continue
        old_meta = old.get(rel)
        if not isinstance(old_meta, dict):
            seeds.add(rel)
            continue
        if old_meta.get("size") != stat.st_size or old_meta.get("mtimeNs") != stat.st_mtime_ns:
            seeds.add(rel)
    # Deleted pages can't seed a 1-hop scope (the page is gone), so we drop
    # them here; lint's deleted-page reporter is the right surface for those.
    return sorted(seeds)


def latest_ingest_seeds(root: Path) -> list[str]:
    """Pages touched by the most recent ingest, read from graph/recent.graph."""
    data = load_graph_json(root, "recent.graph")
    if not isinstance(data, dict):
        return []
    edges = data.get("edges")
    if not isinstance(edges, list):
        return []
    hits: list[tuple[str, str, str]] = []  # (date, time, target)
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        target = edge.get("t")
        entries = edge.get("entries")
        if not target or not isinstance(entries, list):
            continue
        for entry in entries:
            if isinstance(entry, dict) and entry.get("op") == "ingest":
                hits.append((str(entry.get("date") or ""), str(entry.get("time") or ""), str(target)))
    if not hits:
        return []
    best = max((date, time) for date, time, _ in hits)
    return sorted({target for date, time, target in hits if (date, time) == best})


def page_outlinks(root: Path, rel: str) -> set[str]:
    """Outgoing wiki-md link targets in a page body.

    Used only as a hop-1 fallback for brand-new seed pages that a stale
    adjacency.graph (built by the last full lint) doesn't list yet.
    """
    try:
        text = (root / rel).read_text(encoding="utf-8")
    except OSError:
        return set()
    return {
        canonical_link_target(m.group(1) or m.group(2))
        for m in WIKI_MD_LINK_RE.finditer(text)
    }


def bfs_neighborhood(
    adjacency: dict[str, Any],
    seeds: list[str],
    depth: int,
    extra_edges: dict[str, set[str]],
) -> set[str]:
    """Undirected BFS over the link graph: neighbors = in.from ∪ out.to ∪ extra."""
    visited = set(seeds)
    frontier = set(seeds)
    for _ in range(max(0, depth)):
        nxt: set[str] = set()
        for node in frontier:
            neighbors: set[str] = set(extra_edges.get(node, set()))
            entry = adjacency.get(node)
            if isinstance(entry, dict):
                for item in entry.get("in", []) or []:
                    if isinstance(item, dict) and item.get("from"):
                        neighbors.add(str(item["from"]))
                for item in entry.get("out", []) or []:
                    if isinstance(item, dict) and item.get("to"):
                        neighbors.add(str(item["to"]))
            nxt |= neighbors - visited
        if not nxt:
            break
        visited |= nxt
        frontier = nxt
    return visited


def scope_edges(
    adjacency: dict[str, Any],
    extra_edges: dict[str, set[str]],
    scope: set[str],
) -> set[frozenset[str]]:
    """All undirected edges (frozenset of two endpoints) that lie within `scope`."""
    edges: set[frozenset[str]] = set()
    for node in scope:
        candidates: set[str] = set(extra_edges.get(node, set()))
        entry = adjacency.get(node)
        if isinstance(entry, dict):
            for item in entry.get("in", []) or []:
                if isinstance(item, dict) and item.get("from"):
                    candidates.add(str(item["from"]))
            for item in entry.get("out", []) or []:
                if isinstance(item, dict) and item.get("to"):
                    candidates.add(str(item["to"]))
        for other in candidates:
            if other != node and other in scope:
                edges.add(frozenset({node, other}))
    return edges


def scan_ingest(root: Path, seeds: list[str], depth: int) -> dict[str, Any]:
    adjacency = load_graph_json(root, "adjacency.graph")
    if not isinstance(adjacency, dict):
        adjacency = {}
    seed_set = set(seeds)
    extra_edges = {seed: page_outlinks(root, seed) for seed in seeds}
    scope = bfs_neighborhood(adjacency, list(seeds), depth, extra_edges)
    edges = scope_edges(adjacency, extra_edges, scope)
    return {
        "seeds": sorted(seed_set),
        "depth": depth,
        "neighbors": sorted(scope - seed_set),
        "scope": sorted(scope),
        "edges": sorted(sorted(list(e)) for e in edges),
    }


# --- enqueued-state file: the queue/BFS visited-set persisted across loop rounds ---

def load_enqueued_state(path: Path | None) -> set[str]:
    """Load the set of already-enqueued nodes from a JSON state file. Missing → empty set."""
    if path is None or not path.is_file():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    if not isinstance(data, dict):
        return set()
    return set(str(n) for n in (data.get("enqueued", []) or []))


def save_enqueued_state(path: Path, enqueued: set[str]) -> None:
    payload = {"enqueued": sorted(enqueued)}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def compute_increment(result: dict[str, Any], enqueued: set[str]) -> None:
    """Mutate result in place with the queue-model increment.

    `enqueued` is the set of nodes popped in prior rounds. This round's seeds
    are the newly-changed nodes. We audit an edge only if it is incident to a
    seed (the change touched it) AND its other endpoint has NOT been enqueued
    before — an edge back toward an already-processed node is shielded because
    the fix that produced it already reconciled that relationship against the
    source of truth. Neighbor-neighbor edges (touching no seed) are left alone;
    this ingest's change did not touch them.
    """
    seeds = set(result["seeds"])
    scope = set(result["scope"])
    edges_this_round = {frozenset(pair) for pair in result["edges"]}

    new_edges: set[frozenset[str]] = set()
    for edge in edges_this_round:
        others = edge - seeds  # endpoints that are not a seed this round
        if len(others) == len(edge):
            continue  # neighbor-neighbor edge: not incident to any seed, skip
        # others is the non-seed endpoint (empty when both ends are fresh seeds).
        if others & enqueued:
            continue  # neighbor already processed in an earlier round → shielded
        new_edges.add(edge)

    new_seeds = seeds - enqueued
    # A page is "new" (worth reading) if it's a never-seen seed OR an endpoint
    # of an edge we're auditing this round.
    new_pages = set(new_seeds)
    for edge in new_edges:
        new_pages.update(edge)
    new_pages &= scope

    result["enqueued_input"] = len(enqueued)
    result["new_seeds"] = sorted(new_seeds)
    result["new_edges"] = sorted(sorted(list(e)) for e in new_edges)
    result["new_pages"] = sorted(new_pages)
    result["converged"] = not new_pages and not new_edges


def _md_link(path: str) -> str:
    return path if " " not in path else f"<{path}>"


def render_ingest_scan(result: dict[str, Any]) -> str:
    now = datetime.now(timezone.utc)
    seeds = result["seeds"]
    neighbors = result["neighbors"]
    scope = result["scope"]
    new_pages = result.get("new_pages", seeds)  # falls back if increment wasn't computed
    new_edges = result.get("new_edges", result.get("edges", []))
    enqueued_in = result.get("enqueued_input", 0)
    converged = result.get("converged", False)
    incremental_mode = "new_edges" in result

    lines: list[str] = []
    lines.append("# Ingest-Scan Audit Scope")
    lines.append("")
    lines.append(f"Generated: {now.isoformat(timespec='seconds')}")
    lines.append(
        f"Depth: `{result['depth']}` hop · seeds: `{len(seeds)}` · "
        f"neighbors: `{len(neighbors)}` · scope: `{len(scope)}` · edges in scope: `{len(result['edges'])}`"
    )
    if incremental_mode:
        lines.append(
            f"Enqueued so far (input): `{enqueued_in}` node(s) · "
            f"this round adds: `{len(new_pages)}` pages to read, `{len(new_edges)}` edges to audit"
        )
    lines.append("")

    if incremental_mode and converged:
        lines.append("## ✅ Converged — nothing new to audit this round")
        lines.append(
            "All pages in scope and all edges among them have been audited in earlier rounds. "
            "**Stop the sliding-window loop.** No further reading needed."
        )
        lines.append("")
        return "\n".join(lines) + "\n"

    if incremental_mode:
        lines.append("## Incremental — read these pages and audit these edges")
        lines.append("**Only the items listed below are new this round.** Skip anything not listed.")
        lines.append("")
        lines.append("### New pages (first time in scope)")
        if new_pages:
            for rel in new_pages:
                lines.append(f"- [{rel}]({_md_link(rel)})")
        else:
            lines.append("- (none — every page in scope was audited in earlier rounds)")
        lines.append("")
        lines.append("### New edges to audit (page-pair relationships not yet reviewed)")
        if new_edges:
            for pair in new_edges:
                a, b = pair[0], pair[1]
                lines.append(f"- [{a}]({_md_link(a)}) ↔ [{b}]({_md_link(b)})")
        else:
            lines.append("- (none — no new pairs since the previous round)")
        lines.append("")
        lines.append("### Full scope (reference)")
        for seed in seeds:
            lines.append(f"- **seed**: [{seed}]({_md_link(seed)})")
        for rel in neighbors:
            lines.append(f"- neighbor: [{rel}]({_md_link(rel)})")
        lines.append("")
    else:
        lines.append("## Seeds — pages the latest ingest created or changed")
        for seed in seeds:
            lines.append(f"- [{seed}]({_md_link(seed)})")
        lines.append("")
        lines.append("## Neighbors — existing pages linked to/from the seeds (1 hop, in + out)")
        if neighbors:
            for rel in neighbors:
                lines.append(f"- [{rel}]({_md_link(rel)})")
        else:
            lines.append("- (none — the seeds have no graph neighbors yet)")
        lines.append("")

    lines.append("## What to do (agent)")
    lines.append(
        "Read the **new pages** above and **audit the new edges** for contradictions the latest "
        "edits may have introduced — stale numbers, conflicting definitions, version/parameter "
        "mismatches, claims that no longer agree, or duplicated concepts that should be merged."
    )
    lines.append("")
    lines.append(
        "This is a **semantic review** — judge with the actual page text, not string matching. "
        "For each real conflict, reconcile the pages or file an audit on the wrong side; if the "
        "neighborhood is consistent, note it in the log and move on. Do not auto-write `audit/*.md` "
        "from this report."
    )
    lines.append("")
    lines.append("## Sliding window — re-run this script after fixes")
    lines.append(
        "Fixing a page above usually edits **new** pages, which are then the next round's seeds. "
        "A single pass is never enough on its own. After you finish auditing this round, "
        "**re-run `ingest_scan.py` with the same `--audited-state-file <path>`** — the script "
        "automatically records what you just audited and shows only the next round's increment."
    )
    lines.append("")
    lines.append("Stop when **any** of:")
    lines.append("- this round reports `Converged` (no new pages, no new edges), **or**")
    lines.append("- this round's `seeds` is empty (no working-tree changes), **or**")
    lines.append("- **8 iterations elapsed** (hard runaway cap — if you hit it, the wiki has a "
                 "deeper structural conflict; log it and escalate to human).")
    lines.append("")
    return "\n".join(lines) + "\n"


def emit_ingest_scan_json(result: dict[str, Any]) -> None:
    print(json.dumps(result, ensure_ascii=False, indent=2))


def detect_seeds(root: Path) -> tuple[list[str], str]:
    """Run the seed-detection fallback chain. Returns (seeds, source_label)."""
    git_seeds = changed_wiki_seeds_from_git(root) or []
    cache_seeds = changed_wiki_seeds_from_stat_cache(root)
    fresh = sorted(set(git_seeds) | set(cache_seeds))
    if fresh:
        if git_seeds and cache_seeds:
            return fresh, "git status + stat cache (uncommitted + post-pull/checkout)"
        if cache_seeds:
            return fresh, "stat cache (post-pull / checkout vs last lint)"
        return fresh, "git status (uncommitted wiki/*.md)"
    recent = latest_ingest_seeds(root)
    if recent:
        return recent, "graph/recent.graph (latest ingest)"
    return [], ""


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scope a fresh ingest's 1-hop neighborhood for agent audit.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("wiki_root", help="Path to the wiki root containing wiki/ and graph/")
    parser.add_argument(
        "--seed",
        action="append",
        dest="seeds",
        default=[],
        metavar="WIKI/PATH.md",
        help="Seed page (repeatable). Skips auto-detection.",
    )
    parser.add_argument("--depth", type=int, default=1, help="Neighborhood hops, both directions (default: 1)")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--write", action="store_true", help="Also write outputs/audit-cr/ingest-scan.md")
    parser.add_argument(
        "--audited-state-file",
        metavar="PATH",
        help=(
            "JSON file tracking pages and edges already audited in this loop. "
            "Read at start, auto-updated after the run with this round's scope. "
            "Pass the same path each round; delete it to begin a fresh loop."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    root = Path(args.wiki_root).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        print(f"error: wiki root does not exist or is not a directory: {root}", file=sys.stderr)
        return 2
    if not (root / "graph").exists():
        print(
            f"error: missing graph/ directory under {root}; run `lint_wiki.py {root} --full` first",
            file=sys.stderr,
        )
        return 2
    if args.depth < 0:
        print("error: --depth must be >= 0", file=sys.stderr)
        return 2

    if args.seeds:
        seeds = list(args.seeds)
        seed_source = "explicit --seed"
    else:
        seeds, seed_source = detect_seeds(root)

    if not seeds:
        print(
            "no seeds detected — `git status` is clean, the stat cache matches "
            "the last lint (so nothing was pulled or checked out since), "
            "`graph/recent.graph` has no ingest entries, and no --seed was "
            "passed. Pass --seed <wiki/...md> explicitly if you want to scope "
            "the scan manually.",
            file=sys.stderr,
        )
        return 0

    print(f"# seeds from: {seed_source} ({len(seeds)} page(s))", file=sys.stderr)
    result = scan_ingest(root, seeds, args.depth)

    state_path = Path(args.audited_state_file).expanduser().resolve() if args.audited_state_file else None
    if state_path is not None:
        enqueued = load_enqueued_state(state_path)
        # Compute the increment against PRIOR rounds' enqueued set, then enqueue
        # this round's seeds (order matters: a fresh seed-seed edge must audit
        # once before either endpoint is marked enqueued).
        compute_increment(result, enqueued)
        merged = enqueued | set(result["seeds"])
        save_enqueued_state(state_path, merged)
        print(
            f"# queue state: {state_path} ({len(merged)} node(s) enqueued)",
            file=sys.stderr,
        )

    if args.format == "json":
        emit_ingest_scan_json(result)
    else:
        report = render_ingest_scan(result)
        print(report, end="")
        if args.write:
            out = root / "outputs" / "audit-cr" / "ingest-scan.md"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(report, encoding="utf-8")
            print(f"\nWrote {out.relative_to(root)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
