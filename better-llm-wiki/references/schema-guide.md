# SCHEMA.md Schema Guide

`SCHEMA.md` (also read as `AGENTS.md` by some tools) is the **schema document** for a wiki topic. It tells the LLM agent the scope, conventions, current state, and open questions — every session should start by reading it together with `INTEREST.md` and `wiki/index.md`.

## Why it matters

Without a schema, the LLM creates inconsistent page names, overlapping articles, and drifts from the wiki's intended scope. With a well-maintained schema, the LLM becomes a disciplined, consistent wiki maintainer.

**Co-evolve it with the wiki** — update after every major compile, ingest batch, or structural change.

## Full template

```markdown
# <Topic Title> Knowledge Base

> Schema document — read at the start of every session together with INTEREST.md and wiki/index.md.

## Scope

What this wiki covers:
- <bullet list of included areas>

What this wiki deliberately excludes:
- <bullet list of out-of-scope areas>

## Operations

This wiki follows the better-llm-wiki skill's five operations: `compile`, `ingest`, `query`, `lint`, `audit`.
Every operation appends an entry to `log/YYYYMMDD.md`.

Discussion-derived self-evolution updates `INTEREST.md` and may write notes under `outputs/discussions/`.

For crash resistance, keep `wiki/` as bounded synthesized Markdown and move bulky evidence into `raw/`; `lint_wiki.py` has MD scale guards before full graph compilation. For contradiction resistance, resolve audit feedback first and treat lint's contradiction/staleness markers as a cleanup queue, not as durable truth.

Frontmatter is required for generated wiki content pages under `wiki/`. `log/YYYYMMDD.md` is an operation log and must not use frontmatter; its first line must be `# YYYY-MM-DD`. Audit files use their own YAML frontmatter schema.

## Naming conventions

### Pages

Every page is classified by its frontmatter `type`, not by its folder. Directories are domain-driven (group by subject, one `index.md` per folder). The layout below is a sensible **default** for a research wiki — not a requirement; use whatever domain folders fit the topic and set the right `type`.

- **Concept pages** (`type: concept`, default `wiki/concepts/`): Title Case noun phrases. E.g., "Market Making Strategy", not "market making" or "MarketMakingStrategy".
- **Folder-split concepts** (`wiki/concepts/<topic>/`): used when a topic would exceed ~1200 words as a single page. Contains `index.md` + one file per aspect.
- **Entity pages** (`type: entity`, default `wiki/entities/`): Proper names. E.g., "Andrej Karpathy", "OpenAI", "Avellaneda-Stoikov Model".
- **Summary pages** (`type: summary`, default `wiki/summaries/`): kebab-case source slug. E.g., "karpathy-llm-wiki-gist".

### Links
- Always use standard MD links with the content-root anchor: `[Page Title](/path.md)`. The leading `/` resolves at the wiki content root — no `wiki/` prefix, no `../`.
- Wrap paths with spaces in angle brackets: `[Andrej Karpathy](</entities/Andrej Karpathy.md>)`.
- For folder-split pages, link to the index file: `[Foo](/concepts/Foo/index.md)`.
- Link the first mention of every entity or concept. Do not link the same page more than twice per article.

### Frontmatter
Every generated wiki page under `wiki/` has YAML frontmatter:
```yaml
---
title: <Page Title>
type: <concept | entity | summary | paper | index | …>
created: YYYY-MM-DD
updated: YYYY-MM-DD
sources: [list of raw/ slugs this page draws from]
tags: [relevant tags]
---
```

`type` is the one field that drives classification (scripts read it directly). `concept`, `entity`, `summary`, and `index` are the common values, but it is free-form — use a `type` that fits your domain (`paper`, `dataset`, `runbook`, …). Use `type: index` for `wiki/index.md` and any folder-level `index.md`. This requirement does not apply to `log/YYYYMMDD.md`, `audit/*.md`, raw source files, or `SCHEMA.md`. Log files intentionally start directly with the date H1.

### Diagrams and formulas
- All diagrams are **mermaid**. No ASCII art.
- All formulas are **KaTeX** (inline `$...$` or block `$$...$$`).

### Raw file policy
- Small text sources → copy into `raw/<subfolder>/`.
- Large binaries → create a pointer file at `raw/refs/<slug>.md` with `kind: ref` frontmatter and an `external_path` field. Do not copy the binary.

### Graph protocol
- `lint` owns the `.graph` protocol. It validates the wiki and compiles graph artifacts from Markdown links.
- `graph/` contains global graph artifacts for frontends. Do not edit by hand.
- `wiki/**/*.md.graph` may exist as page-local graph caches. Do not edit by hand.
- `.graph-cache/` contains incremental compile state. It may be deleted and regenerated.
- Read `references/graph-guide.md` before changing the graph protocol.

## Skill entry points

Maintain `wiki/index.md` as a task-oriented navigation surface. Prefer headings that begin with "I want to ..." and list each wiki page exactly once under the entry point where a reader would naturally look for it.

Paths are relative to `SCHEMA.md`, which lives at wiki-root, so they include the `wiki/` prefix.

### I want to understand the core ideas
- [<Concept Title>](/concepts/<Concept Title>.md) — one-line summary
- [<Topic>](/concepts/<Topic>/index.md) — (folder-split) one-line summary
    - [<aspect-1>](/concepts/<Topic>/<aspect-1>.md) — ...

### I want to identify people, tools, papers, and organizations
- [<Entity Name>](/entities/<Entity Name>.md) — one-line summary

### I want to inspect sources
- [<slug>](/summaries/<slug>.md) — source title (date)

## Open research questions

- <Questions that should drive future ingest/query work>
- <Things the wiki currently doesn't cover well>
- <Contradictions or gaps noticed between articles>

## Research gaps

Sources to ingest:
- [ ] <URL or paper title> — why it's relevant

## Audit backlog

Count of open audits per target (filled in after running `audit_review.py --open`):
- <file> — N open
- ...

## Notes for the LLM

- Language: <ISO 639-1 code — e.g. en, zh, ja, ko. **All wiki content must be written in this language.** Set by `scaffold.py --lang`; do not change without user consent.>
- Tone, depth level, how to handle contradictions, etc.
```

## What makes a good schema

**Good scope definition** prevents sprawl. A wiki about "LLM memory techniques" should exclude "LLM training" even though they're related.

**Explicit naming conventions** keep links from breaking. If you decide concept pages use Title Case, enforce it — a broken link is an orphan.

**Maintained entry points** let the LLM know what already exists before creating a new page. The most common error is creating duplicate articles with slightly different names.

**Open research questions** give the LLM direction. Without them, the LLM defaults to ingesting the most obvious sources and missing your actual questions.

**Audit backlog** surfaces what the human has flagged as wrong. The AI should glance at it at the start of every session to decide whether to run an `audit` op before ingesting new material.

## Update cadence

- After every new wiki page: add it exactly once under the right "I want to ..." entry point in `wiki/index.md`.
- After every ingest batch: update "Sources to ingest" checklist.
- After every lint pass: update "Research gaps".
- After every audit pass: refresh the "Audit backlog" counts.
- Monthly: review scope, prune stale research questions.
