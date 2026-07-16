# better-llm-wiki

A self-compiling markdown knowledge base for LLM agents. Instead of RAG
(re-retrieving raw documents on every query), an agent **compiles** raw sources
into a persistent, cross-linked wiki. Every ingest, query, discussion, lint, and
audit pass makes the wiki richer — knowledge compounds, and a human stays in the
loop through a structured feedback channel.

Inspired by [Andrej Karpathy's llm-wiki Gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)
and aligned with the [Open Knowledge Format (OKF)](https://cloud.google.com/blog/products/data-analytics/how-the-open-knowledge-format-can-improve-data-sharing):
plain markdown is the source of truth, a file's path is its identity, and links
between pages are ordinary root-anchored markdown links.

## Why this shape

- **Markdown is the truth.** Human-readable, git-friendly, portable. No database
  to migrate, no vendor format to escape.
- **The path is the identity; the links are the graph.** Pages link each other
  with a content-root anchor — `[Attention](/concepts/Attention.md)` — a leading
  `/` that resolves at the wiki content root. One rule everywhere; no `../` to
  compute, no prefix to remember.
- **`type` classifies, not the folder.** Each page declares a `type` in its
  frontmatter (`concept` / `entity` / `summary` / `paper` / …). Directories are
  domain-driven, so the structure follows the subject, not a fixed taxonomy.
- **The human stays in the loop.** An `audit/` inbox captures corrections as
  durable files instead of losing them in chat history; the agent must process
  them before the wiki is considered healthy.

## The five operations

Every action on the wiki is one of `compile`, `ingest`, `query`, `lint`, `audit`,
plus a lightweight self-evolution pass after substantive discussions. Each
appends an entry to the day's log. See `better-llm-wiki/SKILL.md` for the full
protocol.

## Layout

```
better-llm-wiki/     ← the skill: SKILL.md + scripts/ + references/
demo-wiki/           ← a sample knowledge base (LLM-research topic) with graph output
web/                 ← local Node preview server (renders mermaid/KaTeX, files audits)
audit-shared/        ← TypeScript library for the audit file schema
demo-graph-viewer.html ← zero-dependency local .graph viewer
```

## Scripts

- **`scaffold.py`** — bootstrap a new wiki (`SCHEMA.md`, `INTEREST.md`, `wiki/`, `log/`, `audit/`, …).
- **`index_wiki.py`** — compile markdown into a local SQLite query index for long-running wikis.
- **`query_wiki.py`** — rank candidate pages for agent answers by keyword match, graph relevance, recency, centrality, and `INTEREST.md` soft preferences.
- **`evolve_wiki.py`** — capture discussion-derived increments and update `INTEREST.md`.
- **`lint_wiki.py`** — validate the wiki (dead links, orphans, index coverage, audit shape) and compile the `.graph` artifacts. Incremental by default.
- **`ingest_scan.py`** — scope a fresh ingest's 1-hop neighborhood as a sliding-window audit reading list.
- **`audit_cr.py`** — build a correction/contradiction register from open and resolved audits.
- **`commit_wiki.py`** / **`rollback_wiki.py`** — checkpoint the truth-source state into git, and roll a checkpoint back.
- **`migrate_okf.py`** — canonicalize an existing wiki to the OKF-style representation (rewrite `wiki/…` links to `/…`, backfill `type`). Optional and idempotent — the scripts recognize both forms.

## Quick start

```bash
# 1. Scaffold a wiki
python3 better-llm-wiki/scripts/scaffold.py <your knowledge path>/my-wiki "My Research Topic"

# 2. Add a source
cp my-article.md <your knowledge path>/my-wiki/raw/articles/
#    Then tell your agent: "ingest raw/articles/my-article.md"

# 3. Ask questions (auto-builds/refreshes .query-index/wiki.db)
python3 better-llm-wiki/scripts/query_wiki.py <your knowledge path>/my-wiki "what does the wiki say about X?"

# 4. Lint after every write (incremental by default)
python3 better-llm-wiki/scripts/lint_wiki.py <your knowledge path>/my-wiki
#    Periodic full check (rebuilds the global graph; gated on open audit feedback):
python3 better-llm-wiki/scripts/lint_wiki.py <your knowledge path>/my-wiki --full

# 5. Checkpoint into git (final step of any write op)
python3 better-llm-wiki/scripts/commit_wiki.py <your knowledge path>/my-wiki
```

To use it as an agent skill, copy `better-llm-wiki/` into your skill directory,
or paste `better-llm-wiki/SKILL.md` into your agent's context.
