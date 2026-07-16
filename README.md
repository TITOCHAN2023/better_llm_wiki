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

## How it works

**Compile, don't retrieve.** RAG chops sources into chunks and re-retrieves them
by similarity on every question — the model never builds a lasting understanding.
Here the agent instead acts like a meticulous editor: it reads a source once,
writes it into short cross-linked pages, and from then on *reasons over the wiki*.
Each source typically touches 5–15 pages (a summary, some concept pages, a few
entity pages) and every reference becomes a real markdown link, so the corpus
grows into a navigable graph rather than a pile of fragments.

**The representation is just files (OKF).** Three moving parts, nothing else:

- `raw/` — immutable source text (the agent reads it, never rewrites it; not
  counted toward size budgets).
- `wiki/` — the generated knowledge: short pages (~400–1200 words) classified by
  a frontmatter `type`, linked with content-root anchors `[…](/concepts/Foo.md)`.
- `SCHEMA.md` + `INTEREST.md` — the wiki's scope/conventions, and a soft
  steering profile that nudges query ranking toward what you care about.

Derived layers (`.query-index/` SQLite, `graph/` graph files) are rebuilt on
demand and can be deleted at any time. Markdown is the only source of truth.

**The anti-hallucination loop.** The most common failure of an AI writing a wiki
is inventing facts or link targets. Two hard rules prevent it: *read before you
write* (`query` the wiki + open the `raw/` source you're grounding in), and *lint
after you write* (a script checks dead links, orphans, missing index entries,
banned `../`, frontmatter, and cross-page numeric conflicts). The agent fixes
everything lint reports before the edit is considered done.

**It audits itself.** After an ingest, `ingest_scan.py` expands one hop around
the pages you just touched and hands back that neighborhood as a reading list.
The agent reads it and reconciles contradictions (stale numbers, clashing
definitions); each fix becomes the seed of the next round, and the window slides
outward until nothing new appears — a fixpoint. Machine-checkable problems are
hard errors; judgment calls are surfaced for the agent (or a human, via the
`audit/` inbox) to decide.

## The five operations

Every action on the wiki is one of `compile`, `ingest`, `query`, `lint`, `audit`,
plus a lightweight self-evolution pass after substantive discussions. Each
appends an entry to the day's log. See `better-llm-wiki/SKILL.md` for the full
protocol, and `better-llm-wiki/references/commands.md` for a copy-paste command
cheatsheet.

## Layout

```
better-llm-wiki/     ← the skill: SKILL.md + scripts/ + references/
demo-wiki/           ← a sample knowledge base (LLM-research topic) with graph output
web/                 ← local Node preview server (renders mermaid/KaTeX, files audits)
audit-shared/        ← TypeScript library for the audit file schema
demo-graph-viewer.html ← zero-dependency local .graph viewer
```

## Scripts

- **`wiki.py`** — one entrypoint for everything: `python3 wiki.py <command> <root> [opts]` forwards to the scripts below. `python3 wiki.py help` prints the command list and the write loop. You never need to remember the individual filenames.
- **`scaffold.py`** — bootstrap a new wiki (`SCHEMA.md`, `INTEREST.md`, `wiki/`, `log/`, `audit/`, …).
- **`index_wiki.py`** — compile markdown into a local SQLite query index for long-running wikis.
- **`query_wiki.py`** — rank candidate pages for agent answers by keyword match, graph relevance, recency, centrality, and `INTEREST.md` soft preferences.
- **`evolve_wiki.py`** — capture discussion-derived increments and update `INTEREST.md`.
- **`lint_wiki.py`** — validate the wiki (dead links, orphans, index coverage, audit shape) and compile the `.graph` artifacts. Incremental by default.
- **`ingest_scan.py`** — scope a fresh ingest's 1-hop neighborhood as a sliding-window audit reading list.
- **`audit_cr.py`** — build a correction/contradiction register from open and resolved audits.
- **`commit_wiki.py`** / **`rollback_wiki.py`** — checkpoint the truth-source state into git, and roll a checkpoint back.
- **`migrate_okf.py`** — canonicalize an existing wiki to the OKF-style representation (rewrite `wiki/…` links to `/…`, backfill `type`). Optional and idempotent — the scripts recognize both forms.

## Scaling — from tens of MB to tens of GB without crashing

The size budget counts only `wiki/` markdown (bulk sources live in `raw/`, which
is not counted). The design keeps the *daily* operations cheap no matter how big
the corpus gets, and refuses the *expensive* one before it can OOM:

- **Change-detection is O(changes), not O(corpus).** When a wiki is its own git
  repo, incremental `lint` and `query` find what changed via `git status` +
  `git diff <last-linted-commit> HEAD` — no whole-corpus scan. Measured: cold
  incremental lint holds flat at **28 MB whether the wiki has 20k or 50k pages**;
  a repeat query on an unchanged 50k-page wiki dropped from 147 MB to **30 MB**.
  Because memory is decoupled from corpus size, tens of GB is a disk question,
  not a RAM one.
- **The query index carries lookups at scale.** `index_wiki.py` builds a SQLite
  + FTS5 index by streaming (bounded memory), reindexing only changed pages;
  `query_wiki.py` searches it with `LIMIT`, so it never loads the whole corpus.
- **Whole-graph `lint --full` is intentionally bounded.** Its peak memory scales
  with pages loaded (~30 KB/page), so it warns at 25k pages and refuses above
  80k (env-overridable) with guidance to use incremental lint + query instead —
  it degrades gracefully rather than crashing.
- **Self-pruning.** Oversized pages split into folders, near-duplicates merge,
  cold material moves to `raw/`, and `INTEREST.md` keeps the active surface small.

Keep each wiki as its own git repo (run `wiki.py commit` after each change) so
the O(changes) fast paths engage.

## Quick start

```bash
SKILL=better-llm-wiki/scripts          # the skill's scripts dir
WIKI=<your knowledge path>/my-wiki      # your wiki root

# 1. Scaffold a wiki (once)
python3 $SKILL/wiki.py scaffold $WIKI "My Research Topic" --lang en
git -C $WIKI init && python3 $SKILL/wiki.py commit $WIKI   # make it its own git repo

# 2. Add a source, then tell your agent: "ingest raw/articles/my-article.md"
cp my-article.md $WIKI/raw/articles/

# 3. Ask questions (auto-builds/refreshes the query index)
python3 $SKILL/wiki.py query $WIKI "what does the wiki say about X?"

# 4. Lint after every write (incremental by default; --full for a periodic deep check)
python3 $SKILL/wiki.py lint $WIKI

# 5. Checkpoint into git (final step of any write op)
python3 $SKILL/wiki.py commit $WIKI
```

`python3 $SKILL/wiki.py help` lists every command and the canonical write loop.

To use it as an agent skill, copy `better-llm-wiki/` into your skill directory,
or paste `better-llm-wiki/SKILL.md` into your agent's context.
