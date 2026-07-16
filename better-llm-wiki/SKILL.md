---
name: better-llm-wiki
description: >-
  Build and maintain a Karpathy-style LLM knowledge base for any research topic — a self-compiling
  markdown wiki where an Agent ingests raw sources, compiles
  cross-linked concept/entity/summary pages, answers queries against the
  corpus, evolves from discussion signals and user interests, lints the graph for health, and audits in-context human feedback
  filed from the local web viewer or written manually. Use when (1) scaffolding a
  new knowledge base for any research topic, (2) ingesting
  articles/papers/PDFs/web pages into raw/, (3) compiling or restructuring
  wiki articles from existing raw material, (4) answering questions
  against the wiki and filing durable answers back, (5) updating INTEREST.md and discussion-derived increments, (6) running lint
  passes for dead links / orphan pages / coverage gaps / audit shape,
  (7) processing human feedback from the audit/ directory and applying
  corrections. Not for general note-taking, daily journals, or non-wiki
  markdown note systems.
---

# LLM Wiki — Karpathy Knowledge Base Pattern

> **Links:** every link in every output is a standard CommonMark MD link. Intra-wiki links use the **content-root anchor** — a leading `/` that resolves at the wiki content root — `[Page](/path/to/page.md)`. The *same* form works everywhere (inside wiki files and in chat replies), so there is nothing file-relative to compute and **`../` is meaningless** — never write it. Wrap paths containing spaces in `<...>` — `[A B](</concepts/A B.md>)`.

> **Experimental skill — iterating.**
> Inspired by [Karpathy's llm-wiki Gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)

## Core idea

Instead of RAG (re-retrieving raw docs on every query), the LLM **compiles** raw sources into a persistent, cross-linked wiki. Every ingest, query, discussion, lint, and audit pass makes the wiki richer. Knowledge compounds — and the human stays in the loop via a structured feedback channel instead of ad-hoc corrections that get lost.

- **You** own: sourcing raw material, asking good questions, steering direction, revealing what you care about, filing feedback on anything the AI got wrong.
- **LLM** owns: all writing, cross-referencing, filing, bookkeeping, and acting on your feedback.

The wiki is a living artifact with **five operations** — `compile`, `ingest`, `query`, `lint`, `audit` — plus a lightweight self-evolution loop after meaningful discussions. Every session starts by reading `SCHEMA.md`, `INTEREST.md`, and `wiki/index.md`.

## Directory layout

```
<wiki-root>/
├── SCHEMA.md          ← Schema: scope, conventions, current articles, gaps
├── INTEREST.md        ← User interest profile that steers discovery/ranking
├── graph/             ← Derived global graph artifacts (written by lint)
├── .graph-cache/      ← Incremental graph-compile cache (written by lint)
├── .query-index/      ← Derived SQLite query index (written by index_wiki.py)
├── log/               ← Per-day operation log (one file per day)
│   ├── 20260409.md
│   └── 20260410.md
├── audit/             ← Human feedback inbox (one file per comment)
│   ├── 20260409-143022-example-system-size.md
│   └── resolved/      ← Processed feedback, archived with resolution notes
├── raw/               ← Immutable source documents (LLM reads, never writes)
│   ├── articles/
│   ├── papers/
│   ├── notes/
│   └── refs/          ← Pointer files for large binaries kept outside raw/
├── wiki/              ← LLM-generated knowledge (LLM writes, you read)
│   ├── index.md       ← Task-oriented entry points — every page listed exactly once
│   └── <domain>/      ← Domain folders, created on demand, each with its own
│                        index.md (progressive disclosure). Pages are classified
│                        by their frontmatter `type` (concept / entity / summary /
│                        paper / …), NOT by folder. `concepts/ entities/ summaries/`
│                        is a fine default layout for a research wiki — not required.
└── outputs/
    ├── queries/       ← Query answers (promote durable ones to wiki/)
    ├── discussions/   ← Discussion-derived increments and synthesis notes
    └── audit-cr/      ← Derived contradiction/correction register reports
```

`SCHEMA.md` is the **schema file** — the single most important configuration. It tells the LLM the wiki's scope, naming conventions, current article list, open questions, and research gaps. Read `references/schema-guide.md` for what to put in it. Read it at the start of every session.

`INTEREST.md` is the **steering file**. It records current focus, recurring interests, downranked topics, source preferences, and discussion signals. `query_wiki.py` reads it by default as a soft ranking hint. It never overrides the user's explicit question and can be bypassed with `--no-interest`.

The graph protocol is documented in `references/graph-guide.md`. Markdown remains the source of truth; `.graph` files are derived artifacts for local/global knowledge-graph views.

## Core principles

Five rules govern everything below. If a future instruction contradicts one, flag it to the user before acting.

### 0. Links are standard MD, anchored at the content root

Every intra-wiki reference — in every file this skill produces (wiki pages, summaries, query outputs, audit resolutions, log entries) and in every chat reply to the user — uses standard CommonMark link syntax with a **content-root anchor**: a leading `/` that resolves at the wiki content root (the `wiki/` directory on disk).

```markdown
[Page Name](/concepts/Page Name.md)          ← wrong: has a space, must be bracketed
[Page Name](</concepts/Page Name.md>)        ← correct
[Vaswani](/entities/Vaswani.md)
```

**One rule, everywhere.** The leading `/` means "from the content root," so the *same* path is written identically inside a deeply-nested wiki file and in a chat reply — there is no "current file" to anchor against, nothing relative to compute. Because every path starts at the root, **`../` is meaningless — never write it** (a link that begins `../` or omits the leading `/` is a bug the linter flags).

| Wrote this | Meaning |
|---|---|
| `[X](/entities/X.md)` | ✅ content-root anchor → `<wiki-root>/wiki/entities/X.md` |
| `[X](../entities/X.md)` | ❌ file-relative hop — meaningless, flagged |
| `[X](wiki/entities/X.md)` | ⚠️ legacy form — still resolves, but write the `/` form in new content |
| `[X](entities/X.md)` | ❌ missing leading `/` — ambiguous, flagged |

Any path containing spaces is wrapped in angle brackets: `[Agents SDK](</entities/OpenAI Agents SDK.md>)`.

> **Design note (OKF-aligned):** this is the [Open Knowledge Format](https://cloud.google.com/blog/products/data-analytics/how-the-open-knowledge-format-can-improve-data-sharing) convention — the file path *is* the identity, and links are plain root-anchored paths. It replaces the old `wiki/…` prefix + `../` ban. The scripts still recognize the legacy `wiki/…` form, so old wikis keep working; run `scripts/migrate_okf.py <wiki-root>` to canonicalize one to the `/` form.

**Citation blocks at the end of a chat reply** are titled "References" or "Related pages"; every bullet is an MD link with a content-root path:

```markdown
## References
- [OpenAI Agents SDK (Python)](</entities/OpenAI Agents SDK (Python).md>)
- [Agent Handoff](/concepts/Agent Handoff.md)
- [Tracing (Agents SDK)](</concepts/Tracing (Agents SDK).md>)
```

### 1. Divide and conquer

A single concept page should **never** try to cover a complex topic end-to-end. Target: **400–1200 words per page**. When a topic would blow past that:

- Create a subfolder: `wiki/concepts/<topic>/`
- Put a short index page at `wiki/concepts/<topic>/index.md` — definition, list of sub-pages, one-line summaries
- Put each aspect in its own file: `wiki/concepts/<topic>/<aspect>.md`
- In `wiki/index.md`, show the hierarchy via indented bullets

Example layout (from a real wiki):
```
wiki/tech/example-system/
├── index.md                         (overview + links to sub-pages)
├── Example_System_Architecture.md
├── Example_System_Agent_Framework.md
├── Example_System_Bridge_System.md
├── Example_System_Query_Engine.md
├── Example_System_Skills_Plugins.md
├── Example_System_State_Management.md
└── Example_System_Tool_System.md
```

One fat file covering all seven aspects would be unreadable and unlinkable. Seven focused files + an index page give you navigation, selective reading, clean backlinks, and small audit targets.

### 2. Mermaid for diagrams, KaTeX for formulas

- **Any flow, sequence, hierarchy, or state diagram** must be written in mermaid — never ASCII art. ASCII boxes rot fast and are impossible to annotate.
  ````
  ```mermaid
  flowchart LR
      A[raw/article.md] --> B[summary]
      B --> C[concept page]
      C --> D[index.md]
  ```
  ````
- **Any formula** must be written in KaTeX: inline `$f(x) = \sum_i w_i x_i$` or block `$$...$$`.

Both render in the web viewer, and the source markdown remains portable to other KaTeX/Mermaid-capable preview tools.

### 3. Raw file policy

Small text-based sources (md, txt, small pdfs, small images) → copy into `raw/<subfolder>/`.

Large binaries (videos, model weights, installers, datasets, large PDFs >10 MB) → **do not copy**. Instead:

- Create a pointer file at `raw/refs/<slug>.md` with:
  ```yaml
  ---
  kind: ref
  external_path: /Volumes/external/models/llama-3-70b/
  size: ~140 GB
  ---
  ```
  followed by a short description of what it is and why it matters to this wiki.
- Since `raw/` is outside the content root, it is not reachable via the `/` anchor. Reference large-binary pointers from generated files via the root-relative legacy form `raw/refs/<slug>.md` (the linter still accepts `raw/`, `log/`, `audit/` prefixes). Inside `wiki/` files, reference the summary instead: `[slug](/summaries/<slug>.md)`.

This keeps the wiki repo git-friendly and portable.

### 4. Frontmatter belongs to wiki pages

Frontmatter is required for generated wiki content pages under `wiki/`, including `wiki/index.md`. These pages are durable knowledge nodes, so scripts and graph tooling use their metadata (`title`, `type`, `created`, `updated`, `sources`, `tags`) for display labels, classification, lineage, and maintenance.

Do **not** add frontmatter to `log/YYYYMMDD.md`. Log files are operation streams, not wiki pages; their first line must be the date H1 (`# YYYY-MM-DD`) so lint, grep workflows, and recent-activity graph compilation can read them directly.

Audit files are a separate case: `audit/*.md` use their own YAML frontmatter schema, documented in `references/audit-guide.md`.

### 5. Respect the wiki language

All wiki content — page titles, headings, body text, index entries, log descriptions, query answers — must be written in the language specified by `SCHEMA.md`'s **`Language`** field (under "Notes for the LLM"). The only exceptions are:
- Operation names (`compile`, `ingest`, `query`, `lint`, `audit`)
- YAML frontmatter keys
- Directory and file names (keep ASCII-safe kebab-case / Title Case per naming conventions)
- The SKILL.md itself

When scaffolding a new wiki, **ask the user which language to use** before running `scaffold.py`. Pass it via `--lang`:
```bash
python3 scripts/scaffold.py <wiki-root> "<Topic>" --lang zh
```
If the user doesn't specify, default to `en`. The scaffold sets the `Language` field in `SCHEMA.md` and generates all templates in the chosen language.

### 6. Audit is the human feedback surface

The wiki is AI-written; it will be wrong sometimes. The raw sources are human-written; they will contradict each other. The `audit/` directory is how humans correct both without losing the corrections in chat history.

- Humans file feedback via the web viewer or by writing audit files manually. Each feedback is one file in `audit/` with YAML frontmatter (target, type, [line,col] start/end coords) and a markdown body.
- The AI **must** periodically run the `audit` op — never silently ignore `audit/*.md` files.
- When feedback is applied, the file moves to `audit/resolved/` with a `# Resolution` section appended and a log entry recorded in `log/YYYYMMDD.md`.

See `references/audit-guide.md` for the full file format and processing workflow.

### 7. Read before write, lint after write — the anti-hallucination loop

The most common failure mode is the agent writing wiki/ content from memory and inventing link targets or facts. Two hard rules prevent it:

**Before writing any wiki/ content:**
1. Run `python3 <skill-path>/scripts/query_wiki.py <wiki-root> "<topic>" --limit 10` to discover existing pages on the topic. Link to them; do not duplicate.
2. Open the `raw/` source(s) you are grounding the page in. Cite what is actually in raw/, not what you remember.
3. If the wiki has no page and raw/ has no source on this topic, **stop and tell the user** instead of fabricating from general knowledge.

**After any wiki/ write (every time, no exceptions):**
```bash
python3 <skill-path>/scripts/lint_wiki.py <wiki-root>
```
- **Default lint is incremental** — the script detects which pages you touched itself (union of `git status` for uncommitted edits and stat-cache vs the last lint, which also catches `git pull` and branch-checkout deltas) and only re-lints those, refreshing the page-local `.md.graph` sidecars. You do not pass `--changed`; the script is self-aware. Cost on a 1000-page wiki: < 100 ms.
- If lint reports any issue, fix it **before** logging the operation as complete. The fix is the point — skipping the close-out is how hallucinated link targets, mis-titled pages, and forgotten `index.md` entries reach production.

**Add `--full` for a periodic deep check** only when:
- you renamed or deleted pages (cross-page graph changed)
- you are about to push / publish / hand off
- a periodic health check is scheduled (e.g. weekly)

For in-session writes, the default (no flag) is always what you run.

---

## The five operations

Every action on the wiki is one of these five. Each appends an entry to the current day's log file (`log/YYYYMMDD.md`).

### 1. `compile`

(Re)structure wiki content from existing `raw/` material — including splitting oversized pages, merging near-duplicates, and rebuilding `index.md`.

**When to run**: after a big ingest batch, when an existing page has outgrown 1200 words, when `index.md` no longer reflects reality, or when the user says "clean up the wiki".

**Steps**:
1. Read `SCHEMA.md`, `INTEREST.md`, `wiki/index.md`, and every file in the target subtree.
2. For each page over ~1200 words: plan a split into `wiki/concepts/<topic>/` with an index + sub-pages. Confirm the plan with the user before writing.
3. For each pair of near-duplicate pages: propose a merge. Confirm, then rewrite.
4. Regenerate `wiki/index.md` so every page is listed exactly once.
5. **Link self-check**: grep every written/modified `wiki/` file for `](../` and for hrefs that lack a leading `/` — replace either with the content-root form `[..](/…md)` immediately. This is a hard rule, not a guideline.
6. **Close with lint**: run `python3 <skill-path>/scripts/lint_wiki.py <wiki-root>` (incremental by default). Fix every reported issue before moving on. After renames/deletes specifically, add `--full` so the global graph rebuilds.
7. Log: `## [HH:MM] compile | <what you did — files touched, splits, merges>`
8. **Checkpoint state into git** (final step, after the log entry is written): run `python3 <skill-path>/scripts/commit_wiki.py <wiki-root>`. Stages only the truth-source paths (`SCHEMA.md` / `INTEREST.md` / `wiki/` / `raw/` / `audit/` / `log/`) — never `git add -A`, so unrelated WIP stays put. Commit message auto-derives from the log entry just written. No-op if the wiki isn't in git.

### 2. `ingest`

Add a new source. **One source typically touches 5–15 wiki pages.**

**Two paths, pick by wiki size:**

- **Lite path (small wiki, < ~50 pages, or solo personal notes)** — steps 1–8 + 10 + 11. Skip the 1-hop ingest-scan (step 9) and the audit register (`audit_cr.py`) until the wiki is large enough that the agent can't hold the link neighborhood in working memory.
- **Full path (team wiki, > ~50 pages, multiple contributors)** — all steps below. The ingest-scan in step 9 catches new-vs-existing conflicts that incremental lint never reviews together.

**Steps**:
1. Save source to the right subfolder:
   - web article → `raw/articles/<slug>.md`
   - paper → `raw/papers/<slug>.md` (extracted text for big PDFs)
   - note → `raw/notes/<slug>.md`
   - large binary → `raw/refs/<slug>.md` pointer file (see raw file policy)
2. Read the source in full.
3. Create `wiki/summaries/<slug>.md` (200–400 words — key takeaways, not a rewrite; see `references/article-guide.md`).
4. Create or update relevant concept pages in `wiki/concepts/`. Respect divide-and-conquer: if a concept page would exceed 1200 words, split instead of cramming.
5. Create or update entity pages in `wiki/entities/` for any new people / tools / papers / organizations referenced.
6. Update `wiki/index.md` so the new pages appear under the right "I want to ..." entry point.
7. **Link self-check**: grep every written/modified `wiki/` file for `](../` and for hrefs that lack a leading `/` — replace either with the content-root form `[..](/…md)` immediately.
8. **Close with lint**: run `python3 <skill-path>/scripts/lint_wiki.py <wiki-root>`. Fix every reported issue (dead links, missing index entry, wrong title, sources ref to a file that wasn't actually written) before moving on.
9. **Scope the neighborhood for audit — sliding-window loop** (advisory, never blocks): the script computes one window; the *loop* is your job. A fix in this window typically edits new pages, which become the next window's seeds — so a single pass is never enough on its own. **The script runs the loop as a queue/BFS worklist: the state file records which nodes it has already *enqueued* (processed)**, so each iteration only shows what's *new* this round — an edge back toward an already-processed node is shielded (that relationship was reconciled when the earlier node was processed, and every fix aligns to the source-of-truth node, so it stays consistent).

   **Setup once per loop**: pick a state file path (e.g. `/tmp/ingest-audit-<wiki-name>.json`) and delete it if it exists — that starts a fresh loop.

   **Per iteration**:
   - Run `python3 <skill-path>/scripts/ingest_scan.py <wiki-root> --audited-state-file <path>`. The script **auto-detects seeds** — union of `git status` (uncommitted `wiki/*.md`) and the stat cache vs the last lint (post-`git pull` / post-checkout deltas), then `graph/recent.graph`, then explicit `--seed <page>` if you want to narrow manually. It expands **one hop** along the link graph (both directions), diffs against the enqueued-node set in the state file, and prints **only the new pages and new edges** introduced this round. It then records this round's seeds as enqueued in the state file automatically.
   - **The script does not judge contradictions — you do**: read the listed new pages and audit the listed new edges for conflicts (stale numbers, clashing definitions, version/parameter mismatches, concepts that should be merged). Reconcile the pages or file an audit; if consistent, note it and continue.

   **Stop when any of**:
   - The report says **`✅ Converged`** (`new_pages == ∅` AND `new_edges == ∅` — every page and every page-pair in the current scope has already been audited in earlier rounds), **or**
   - This iteration's `seeds == ∅` (no working-tree changes since the last lint, and no ingest history), **or**
   - **8 iterations elapsed** (hard cap to prevent runaway loops — the total edge count is bounded by `pages × pages`, so convergence is theoretically guaranteed; hitting this means the agent kept editing in ways that genuinely expand the graph and a human should look at why).

   After the loop ends, delete the state file (or just leave it — it'll be reset next ingest).

   **Lite path: skip the whole loop** on a small wiki (< ~50 pages) or when the ingest touched only orphan pages with no graph neighbors yet. It exists to catch new-vs-existing conflicts; if there's effectively no "existing" yet, you're only re-reading what you just wrote.
10. Log: `## [HH:MM] ingest | <slug> — <one-line description> (touched N pages)`
11. **Checkpoint state into git** (final step): run `python3 <skill-path>/scripts/commit_wiki.py <wiki-root>`. Stages truth-source dirs only (auto-detects what changed via git); message auto-derives from this ingest's log entry. No-op if not in git.

### 3. `query`

Answer a question **grounded in the wiki**, not general knowledge.

Before synthesis, run the query helper to get an agent-sized reading list. It is a discovery tool, not the final answer writer:

```bash
python3 <your_path_to_skill>/scripts/query_wiki.py <wiki-root> "<question>"
```

`query_wiki.py` reads `<wiki-root>/.query-index/wiki.db` by default. If the index is missing or stale relative to `wiki/**/*.md`, it auto-runs:

```bash
python3 <your_path_to_skill>/scripts/index_wiki.py <wiki-root>
```

For a deliberate full refresh, run `index_wiki.py --rebuild` first, or add `--rebuild-index` to the query command. Use `--no-index` only for debugging small wikis.

`index_wiki.py` is stat-first incremental indexing: it scans metadata for `wiki/**/*.md`, deletes removed DB rows, and reads/chunks only new or changed pages. Unchanged pages stay in SQLite/FTS without being read again.

`query_wiki.py` also reads `INTEREST.md` by default. Interest terms are a soft boost for ranking and exploration; they do not hide explicit query matches. Use `--no-interest` when you need a neutral, interest-free result list.

Useful query patterns:
- **Question / keyword query**: `python3 scripts/query_wiki.py <wiki-root> "RAG vs llm-wiki tradeoffs" --limit 12`
- **Time query**: `python3 scripts/query_wiki.py <wiki-root> --since 14d --sort updated` or `--recent 30`
- **Relevance from a known page**: `python3 scripts/query_wiki.py <wiki-root> --related /concepts/Foo.md --depth 2`
- **Hybrid query**: `python3 scripts/query_wiki.py <wiki-root> "long context" --related HyLo --depth 1`
- **Machine-readable context**: add `--format json`; for a shell-readable reading queue use `--paths-only`
- **Neutral search**: add `--no-interest` to ignore `INTEREST.md`

The helper ranks pages by lexical match, graph proximity, centrality, and recency. Its SQLite index stores page metadata, bounded chunks, FTS5 rows, and page links so query does not scan every Markdown file as the wiki grows. Use it to choose what to read; then read the selected pages yourself and synthesize.

**Steps**:
1. Read `SCHEMA.md`, `INTEREST.md`, and `wiki/index.md`. Scan the "I want to ..." entry points for relevant pages and note current interest hints.
2. Run `scripts/query_wiki.py` with the user's question and any obvious seed page (`--related`) or time window (`--since`, `--recent`) implied by the request. The query helper refreshes the local SQLite index when Markdown files changed.
3. Read the top-ranked pages in full; follow only the most relevant one-hop MD links. If the query helper returns low/no hits, inspect `wiki/index.md` once more before declaring a gap.
4. If the wiki doesn't have enough material, say so and suggest what to ingest next instead of making something up.
5. Synthesize the answer. Two outputs, each with its own path convention:
   - **In the chat reply to the user**: cite pages with content-root MD links, e.g. `[Page Name](</concepts/Page Name.md>)`. The leading `/` anchors at the wiki content root. Any path with spaces is wrapped in `<...>`.
   - **In the saved query file** at `outputs/queries/<slug>.md`: use the same content-root links, e.g. `[Page Name](</concepts/Page Name.md>)`.
6. Save to `outputs/queries/<YYYY-MM-DD>-<question-slug>.md`.
7. If the discussion reveals a stable user preference, update `INTEREST.md` with a short evidence-backed bullet.
8. If the answer is durable (a comparison, analysis, or new synthesis) → promote a cleaned-up version to a `type: concept` page under an appropriate `wiki/` domain folder. Links inside that new page use content-root paths (e.g. `/entities/Name.md`, never `../entities/Name.md`). Add to `index.md`.
9. **If you wrote to wiki/ in step 8**: close with `python3 <skill-path>/scripts/lint_wiki.py <wiki-root>`. Fix any reported issue before logging.
10. Log: `## [HH:MM] query | <question-slug>` (and a separate `## [HH:MM] promote | ...` line if promoted).
11. **Checkpoint state into git** (final step, only if step 8 wrote to `wiki/`): run `python3 <skill-path>/scripts/commit_wiki.py <wiki-root>`. Skip when the query produced no wiki/ writes — the commit_wiki script itself will detect "nothing to commit" and no-op, but skipping the call is fine too.

### Self-evolution from discussions

After a substantial conversation about wiki material, do a small self-evolution pass before closing the task. This pass should be conservative: capture only durable knowledge or clear user-interest signals, not every casual aside.

**Discussion increments**:
- If the conversation produced a new comparison, critique, hypothesis, or synthesis grounded in existing pages, save it to `outputs/discussions/<YYYY-MM-DD>-<slug>.md`.
- If the increment is durable and generally useful, promote it into the relevant `wiki/concepts/` page or create a new focused concept page. **Any promotion that touches wiki/ must close with `lint_wiki.py <wiki-root>`** (incremental by default) — same rule as principle #7.
- Link every touched page with content-root `/…` links and update `wiki/index.md` when a new page is created.
- Prefer the helper when only discussion/interest capture is needed:
  ```bash
  python3 scripts/evolve_wiki.py <wiki-root> \
    --title "<short discussion title>" \
    --summary "<durable synthesis>" \
    --page /concepts/Foo.md \
    --interest "<topic or preference> | weight: 2 | evidence: <date/question>"
  ```

**Interest updates**:
- Update `INTEREST.md` when the user explicitly says they care about a topic, repeatedly asks related questions, rejects a direction as irrelevant, or spends time comparing tradeoffs.
- Put short-lived active directions under `Current Focus`; stable themes under `Recurring Interests`; unwanted or low-priority areas under `Downrank / Less Relevant`; next sources under `Exploration Queue`.
- Each interest bullet needs evidence, such as a date, query, or discussion note. Avoid broad personality guesses.
- Interest affects query ranking only as a soft boost. The user's explicit query remains primary.
- `evolve_wiki.py` appends discussion signals and interest bullets without promoting them into canonical `wiki/` pages. Promote separately only after the synthesis is durable.

### 4. `lint`

Health check. Every lint operation must run the skill's own script; do not substitute a manual checklist or a different local script unless the user explicitly asks for that override.

**Default — incremental** (what you use after every in-session wiki/ write, per principle #7):

```bash
python3 <your_path_to_skill>/scripts/lint_wiki.py <wiki-root>
```

The script detects what changed itself (no `--changed` flag needed) and only re-lints those pages.

**Full mode** — opt in with `--full`, only when you renamed/deleted pages, are about to push/publish, or want a periodic global health check:

```bash
python3 <your_path_to_skill>/scripts/lint_wiki.py <wiki-root> --full
```

If you find yourself reaching for `--full` after a small edit, you're probably wasting time — the incremental default covers it.

Hard rule: `lint_wiki.py` first checks the open audit inbox (`audit/*.md`, excluding `audit/resolved/`). If any open audit files exist, full lint and graph compilation stop. Process the user's audit feedback first, move every accepted/rejected/deferred item to `audit/resolved/` with a `# Resolution`, and make sure the `audit/` root contains no open `.md` files. Do not delete audit files; clear the inbox by moving them to `audit/resolved/`. Only then refresh `audit_cr.py --all --write` and run lint again.

Crash guard: after the audit inbox is clear, lint checks the MD-only corpus size before reading every page. Defaults: warn after 50MB of `wiki/**/*.md`, stop before full graph lint after 1GB, warn on pages over 512KB, and stop on pages over 16MB. If the guard stops, split oversized pages, archive cold material, move evidence-heavy text to `raw/`, and rely on `query_wiki.py`/`.query-index/wiki.db` for discovery until the wiki pages are bounded again. Override only deliberately with `LLM_WIKI_MD_WARN_MB`, `LLM_WIKI_MD_HARD_MB`, `LLM_WIKI_PAGE_WARN_KB`, or `LLM_WIKI_PAGE_HARD_MB`.

Incremental rule (the default): without `--full`, lint detects modified `wiki/**/*.md` files itself — as the union of `git status` (uncommitted edits) and `.graph-cache/md-file-stats.graph` (stat cache vs the last lint). The stat-cache half catches `git pull` and branch-checkout deltas, which leave the working tree clean but the files on disk changed; using either signal alone silently drops those pages from incremental lint. It stats the corpus but reads only changed pages, runs local link/source/marker checks, and writes the page-local `.md.graph` sidecar for every linted `.md`. It intentionally skips global graph compilation, orphan checks, and full index coverage. The full-corpus hard limit does not block incremental lint; only oversized changed pages do. Use `--full` after deletes/renames, before publishing, or whenever global graph/index freshness matters.

The script reports:
- **Open audit preflight** — blocks full lint while user feedback is waiting in `audit/*.md`
- **MD scale guard** — prevents a large wiki from crashing full lint/graph compilation by stopping before all pages are loaded
- **Banned `../` paths** — wiki-internal links that use `../` instead of the content-root `/…` anchor (hard error)
- **Dead links** — `[text](path.md)` whose resolved target doesn't exist
- **Orphan pages** — pages with no inbound links
- **Missing index entries** — pages not listed in `wiki/index.md`
- **Frequently-linked missing pages** — a link target referenced 3+ times but no page exists
- **Legacy link residue** — pre-MD link syntax still present in an older wiki; `scripts/migrate_wikilinks.py` converts it to standard MD
- **log/ shape** — stray files or wrong filenames in `log/`
- **audit/ shape** — malformed YAML frontmatter in `audit/*.md`
- **Audit target resolution** — every open audit's `target` file must exist
- **Contradiction/staleness markers and heuristic claim conflicts** — marker words such as `待核实`, `矛盾`, `contradiction`, or `outdated`, plus fuzzy cross-page numeric claims (e.g. the same subject reported with two different context-window or parameter-count values), are all reported as non-fatal heuristic signals and written to `graph/issues.graph`. They do not affect lint exit code; subject normalization is loose, so model variants (`7B` vs `70B`) and unrelated metrics can collide. Treat them as a review queue — reconcile, file an audit, or move uncertainty to `SCHEMA.md` Open research questions.

`lint` also owns graph compilation:
- Rebuild page-local `wiki/**/*.md.graph` sidecars for changed pages
- Rebuild global `graph/*.graph` artifacts for full/local graph views
- Rebuild `graph/recent.graph` from the latest 10 operation log entries
- Rebuild `graph/navigation.graph` from `wiki/index.md`
- Rebuild `graph/lineage.graph` from raw sources, `sources`, and summary links
- Rebuild `graph/settings.graph` with graph-viewer defaults and type-affinity hints
- Maintain `.graph-cache/*` for incremental recompilation
- Write scale and contradiction marker payloads to `graph/issues.graph` and aggregate counts to `graph/stats.graph`
- Remove legacy `*.agraph` artifacts from older graph protocol drafts
- Treat `.graph` files as derived artifacts; never edit them by hand

Graph artifacts are mode-scoped:
- Incremental mode (the default, no flag) writes only page-local `.md.graph` sidecars for the changed Markdown lint scope.
- Full lint rewrites only dirty page sidecars; global graph files use atomic semantic writes so unchanged payloads are not rewritten merely because timestamps would change.

Read `references/graph-guide.md` before changing this protocol.

For each issue, propose a fix, confirm with the user, then apply. Log: `## [HH:MM] lint | <N> issues found, <M> fixed`.

### 5. `audit`

Process human feedback from `audit/`.

**Steps**:
1. Run `python3 scripts/audit_cr.py <wiki-root> --open --write` to build the correction/contradiction cleanup queue at `outputs/audit-cr/contradiction-register.md`.
2. Run `python3 scripts/audit_review.py <wiki-root> --open` to get a grouped list by target file.
3. For each open audit, read the file. Use the `start` and `end` `[line, col]` coordinates to locate the exact range in the target file. If those coords no longer point at a valid range (file shorter than `start[0]`, or line too short for the recorded column), the anchor is stale — surface the discrepancy to the user instead of guessing.
4. Decide the action:
   - **Accept**: apply the correction to the target file.
   - **Partially accept**: apply what makes sense, note the rest in the resolution.
   - **Reject**: explain why in the resolution — the feedback may be based on a misreading of scope or a contradictory source.
   - **Defer**: add to `SCHEMA.md` "Open research questions", explain why source evidence is missing, and treat the audit as resolved/deferred so the open inbox is cleared.
5. For every accepted, partially accepted, rejected, or deferred audit, append a `# Resolution` section to the audit file:
   ```markdown
   # Resolution

   2026-04-10 · accepted.
   Fixed the file count (was "~1,900", corrected to "~1,800" per commit abc123).
   Updated: tech/Example_System.md lines 47–48.
   ```
6. Flip `status: open` → `status: resolved`, then move the file from `audit/` to `audit/resolved/`. Filename unchanged.
7. Log per resolved audit:
   ```
   ## [HH:MM] audit | resolved 20260409-143022-a1b2 — <one-line what>
   ```
8. Re-run `python3 scripts/audit_cr.py <wiki-root> --all --write` after processing to refresh the contradiction register. Then run `lint` (incremental, the default) to verify the audit-resolved edits don't leave dead links or orphans; run `lint --full` instead if any audit caused a rename or delete.
9. Never delete audit files. Rejected ones still go to `resolved/` with the rejection rationale in their resolution section — that's valuable history.
10. **Checkpoint state into git** (final step): run `python3 <skill-path>/scripts/commit_wiki.py <wiki-root>`. Captures the resolved-audit moves + any `wiki/` edits + the log entries written above, in a single commit named after the latest log entry.

`audit_cr.py` is the periodic cleanup helper for contradiction risk. It scans open/resolved audit files, detects high-risk correction signals (`error`, `warn`, `deferred`, `rejected`, contradiction/stale wording), clusters them by target and anchor, and writes a durable queue under `outputs/audit-cr/`. It does not edit wiki pages or move audit files; the agent still performs the actual resolution.

`scripts/ingest_scan.py` is a second, machine-driven entry point distinct from human feedback. Instead of reading `audit/`, it **auto-detects seeds** from freshly-ingested pages — the union of `git status` (uncommitted `wiki/*.md`) and the stat cache vs the last lint (post-`git pull` / post-checkout deltas), the same union incremental lint uses; falling back to `graph/recent.graph` if neither signal fires; and overridable with explicit `--seed`. It then expands **one hop** along the link graph (both directions) and prints the seeds plus their directly-linked neighbors as an **audit scope** — closing the blind spot that incremental lint leaves (it only loads changed pages, so new-vs-existing pages are never reviewed together). The script deliberately **does not judge contradictions itself**: computing the scope is the deterministic part; deciding whether the pages actually conflict is a semantic call left to the agent, which reads them and reconciles or files audits by hand. Output is **advisory only and never written into `audit/`** (that would trip the open-audit lint gate). The script emits **one** 1-hop window per invocation; the **sliding-window loop** (audit → fix → re-scan) is driven by the agent in the `ingest` op. With `--audited-state-file <path>` the script **persists the set of already-enqueued (processed) nodes across calls** as a queue/BFS worklist — each invocation reports only the *increment* since the last call (`new_pages`, `new_edges`), and converges when both are empty. Each node is enqueued at most once, so the loop terminates in at most (node-count) rounds and cannot oscillate; an 8-iteration cap exists as a runaway sanity check. See step 9 for the loop body.

See `references/audit-guide.md` for the full audit file format.

---

## Tooling

| Tool | Purpose |
|------|---------|
| **`web/`** | Local Node.js server — preview the wiki with mermaid/math rendered; select → feedback → `audit/` |
| `scripts/scaffold.py` | Bootstrap a new wiki directory tree |
| `scripts/index_wiki.py` | Compile Markdown into `.query-index/wiki.db` with documents, chunks, FTS5, and links |
| `scripts/query_wiki.py` | Agent discovery: keyword, time-window, graph-related, and JSON/path query planning |
| `scripts/evolve_wiki.py` | Capture discussion increments, update `INTEREST.md`, and append an evolve log entry |
| `scripts/lint_wiki.py` | Ten-pass health check |
| `scripts/audit_review.py` | Group open/resolved audits by target file |
| `scripts/audit_cr.py` | Build the periodic contradiction/correction register under `outputs/audit-cr/` from human-filed `audit/*.md` |
| `scripts/ingest_scan.py` | Scope a fresh ingest's 1-hop neighborhood as an audit reading list (auto-detects seeds via `git status` ∪ stat-cache vs last lint → `recent.graph` → optional `--seed` override). With `--audited-state-file` it persists the enqueued-node set across calls and reports only the per-round increment — drives the sliding-window loop to natural convergence. |
| `scripts/commit_wiki.py` | Checkpoint the wiki's truth-source state into git as the final step of any write op. Stages only SCHEMA.md / INTEREST.md / wiki/ / raw/ / audit/ / log/ (never `git add -A`); auto-derives the commit message from the latest `log/<today>.md` entry; tags each commit `ckpt/<op>/<timestamp>` for rollback (suppress with `--no-tag`). No-op outside git. |
| `scripts/rollback_wiki.py` | List and safely roll back checkpoints. `--list [--days 30]` shows recent checkpoints; `--undo <tag/commit>` surgically reverts one checkpoint's **content** as a new forward commit (keeps later audits, re-revertible); `--to <ref>` restores the whole content tree to a checkpoint; `--prune-tags` drops old `ckpt/*` tags. Never rewinds `log/`, never rewrites history / resets hard / pushes. Refuses on a dirty tree. |
| [qmd](https://github.com/tobi/qmd) | Optional local semantic search (useful at >100 pages) |

The web viewer and manual audit files use the **same format** with the **same anchor algorithm**, so feedback can be processed consistently regardless of how it was created.

## Starting a new wiki

**Before scaffolding, ask the user which language to use.** Then run:

```bash
python3 scripts/scaffold.py <wiki-root> "<Topic Title>" --lang <code>
```

Supported: `en` (English, default), `zh` (Chinese). Any other ISO 639-1 code (e.g. `ja`, `ko`, `fr`) sets the Language field; templates fall back to English.

Creates the full tree (including `log/<today>.md`, `audit/`, `audit/resolved/`, `outputs/discussions/`), a language-appropriate `SCHEMA.md`, an `INTEREST.md` profile, and a `wiki/index.md` with localized task-oriented entry points.

After scaffolding:
1. Fill in `SCHEMA.md` — define scope, naming conventions, initial research questions.
2. Fill in `INTEREST.md` — define current focus, recurring interests, and source preferences.
3. Start ingesting sources.
4. Ask questions to build up `outputs/queries/`; promote durable answers and discussion increments.
5. Run `lint` periodically.
6. Run `audit` whenever new feedback accumulates.

All subsequent content follows the language set in `SCHEMA.md` — see principle 5.

## `wiki/index.md` format

The LLM rebuilds `index.md` on every compile and touches it on every ingest. Organize it by user intent, using "I want to ..." entry points instead of a taxonomy-only category list. Links use the content-root anchor (leading `/`):

```markdown
---
title: Index — <Topic>
type: index
created: YYYY-MM-DD
updated: YYYY-MM-DD
sources: []
tags: []
---

# Index — <Topic>

> One-sentence scope of the wiki.

## 🔖 Navigation
- [I want to understand the core ideas](#i-want-to-understand-the-core-ideas) · [I want to identify people, tools, papers, and organizations](#i-want-to-identify-people-tools-papers-and-organizations) · [I want to inspect sources](#i-want-to-inspect-sources) · [Open Questions](#open-questions)

## I want to understand the core ideas
- [Foo](/concepts/Foo.md) — one-line summary
- [Bar](/concepts/Bar/index.md) — (folder-split) one-line summary
    - [aspect-1](/concepts/Bar/aspect-1.md) — ...
    - [aspect-2](/concepts/Bar/aspect-2.md) — ...

## I want to identify people, tools, papers, and organizations
- [Andrej Karpathy](</entities/Andrej Karpathy.md>) — AI researcher, author of the llm-wiki pattern

## I want to inspect sources
- 2026-04-09 — [llm-wiki-gist](/summaries/llm-wiki-gist.md) — Karpathy's original Gist

## Open Questions
- Q1: ...
```

Rules:
- **Every bullet in `index.md` is a clickable MD link — never plain text.** `index.md` exists so the user and the agent can jump to any page in one click; a bullet like `- RuView` with no link is a dead weight entry that defeats the whole point. If you cannot produce a working path, the page does not exist yet — list it under "Open Questions" instead of the catalog.
- Every wiki page must appear exactly once in `index.md`. `lint` enforces this.
- Folder-split concepts show hierarchy via indented bullets — the parent's `index.md` link stays the primary entry; sub-pages are nested under it, also as MD links (see the `Bar` example above).
- Entry-point headings should be written as user tasks, preferably beginning with "I want to ...". Concepts, entities, and summaries can be mixed under the same task when that is how a reader would naturally look for them.
- Paths use the content-root anchor — `/concepts/Foo.md`, `</entities/Andrej Karpathy.md>`, etc. The leading `/` resolves at the wiki content root; this is the same convention used by all files under `wiki/`. Use `<...>` angle brackets around any path containing spaces.
- `index.md` + `SCHEMA.md` together are what the AI reads at session start.

## `log/` format

See `references/log-guide.md` for full details. Minimum:

- One file per day: `log/YYYYMMDD.md`
- No YAML frontmatter in log files. The first line must be the H1 date, e.g. `# 2026-04-09`; `lint_wiki.py` enforces this because recent-activity graph compilation reads log files directly.
- H1 = the date; H2 per entry with `## [HH:MM] <op> | <one-line description>`
- Ops: `compile`, `ingest`, `query`, `lint`, `audit`, `promote`, `split`, `scaffold`

Quick grep across history: `grep -rh "^## \[" log/ | tail -20`.

## Active skill sync

When a project vendors this skill's `scripts/` or `references/` into a wiki root, record the last synced skill state in `<wiki-root>/.better-llm-wiki-sync`:

```json
{"source":"better-llm-wiki","synced_at":"YYYY-MM-DDTHH:MM:SS","skill_version":"<commit-or-package-version>"}
```

At session start, read `SCHEMA.md`, `wiki/index.md`, and this sync marker. If the marker is missing or the source skill version differs, sync the vendored `scripts/` and `references/`, then update the marker. If no commit or package version is available, use a content hash of the copied files as `skill_version`.

## Use cases

- **Research deep-dive** — reading papers/articles on a topic over weeks; the wiki evolves with your understanding, and the audit trail keeps AI mistakes from silently accumulating
- **Personal wiki** — journal entries, notes, ideas compiled into a personal encyclopedia; comment on anything you disagree with later, the AI corrects it
- **Team knowledge base** — fed by Slack threads, meeting notes, docs; team members file corrections through the web viewer
- **Reading companion** — filing each book chapter as you go; builds a rich companion wiki by the end

## References

- `references/schema-guide.md` — What to put in `SCHEMA.md`
- `references/article-guide.md` — How to write good wiki articles (length, links, mermaid, math, divide-and-conquer)
- `references/log-guide.md` — The `log/` folder convention
- `references/audit-guide.md` — Audit file format, anchor strategy, processing workflow
- `references/tooling-tips.md` — local editor/browser workflow, qmd, and web preview tips
