#!/usr/bin/env python3
"""
scaffold.py — Bootstrap a new LLM Wiki directory structure.

Usage:
    python3 scaffold.py <wiki-root> "<Topic Title>" [--lang <code>] [--force]

Examples:
    python3 scaffold.py ~/wikis/ai-research "AI Research"
    python3 scaffold.py ~/wikis/ai-research "AI Research" --lang zh

The --lang flag sets the Language field in SCHEMA.md. The agent reads this
field at session start and writes all wiki content in that language.
Default: en.

If <wiki-root> already contains a scaffolded wiki (SCHEMA.md, wiki/, audit/,
INTEREST.md present), the script refuses to run and lists what it found.
Pass --force to overwrite anyway — that will clobber template files in place,
so use it only when you genuinely want to re-scaffold and have committed or
backed up anything custom you've added.

Creates:
    <wiki-root>/
    ├── SCHEMA.md          (schema template)
    ├── INTEREST.md        (user interest profile for query/discussion steering)
    ├── .gitignore         (excludes derived layers by default; tweak to taste)
    ├── graph/             (derived graph artifacts written by lint)
    ├── .graph-cache/      (incremental graph compile cache written by lint)
    ├── log/
    │   └── YYYYMMDD.md    (first day's log with scaffold entry)
    ├── audit/
    │   ├── .gitkeep
    │   └── resolved/
    │       └── .gitkeep
    ├── raw/
    │   ├── articles/
    │   ├── papers/
    │   ├── notes/
    │   └── refs/
    ├── wiki/
    │   └── index.md       (task-oriented entry points; domain folders are
    │                       created on demand during ingest, each with its
    │                       own index.md — pages are classified by their
    │                       frontmatter `type`, not by folder)
    └── outputs/
        ├── queries/
        └── discussions/
"""

import os
import sys
from datetime import date, datetime


def scaffold(root: str, title: str, lang: str = "en") -> None:
    today = date.today()
    today_iso = today.isoformat()
    today_compact = today.strftime("%Y%m%d")
    now_hm = datetime.now().strftime("%H:%M")

    dirs = [
        "raw/articles",
        "raw/papers",
        "raw/notes",
        "raw/refs",
        "wiki",  # domain folders are created on demand; pages classified by `type`
        "outputs/queries",
        "outputs/discussions",
        "log",
        "audit",
        "audit/resolved",
        "graph",
        ".graph-cache",
    ]

    for d in dirs:
        os.makedirs(os.path.join(root, d), exist_ok=True)
    print(f"✓ Created directory tree under {root}/")

    _write(root, "audit/.gitkeep", "")
    _write(root, "audit/resolved/.gitkeep", "")

    gitignore = """\
# === Derived caches — regenerable, not committed by default ===
.graph-cache/
.query-index/
wiki/**/*.md.graph
outputs/audit-cr/

# === Optional: comment out a line to start committing that layer ===

# Versioned graph snapshots (uncomment "graph/" to skip them)
graph/

# Query/discussion sinks (uncomment to skip — keep them if you want history)
outputs/queries/
outputs/discussions/
"""
    _write(root, ".gitignore", gitignore)
    print("✓ Created .gitignore (derived layers excluded by default)")

    schema_md = f"""# {title} Knowledge Base

> Schema document — read at the start of every session together with `wiki/index.md`.
> Update after every major compile, ingest batch, or structural change.

## Scope

What this wiki covers:
- <describe the topic area>

What this wiki deliberately excludes:
- <describe out-of-scope areas>

## Operations

This wiki follows the better-llm-wiki skill's five operations: `compile`, `ingest`, `query`, `lint`, `audit`.
Every operation appends an entry to `log/YYYYMMDD.md`.

## Naming & structure conventions

Every `wiki/` page carries a `type` in its frontmatter — **that field, not the
folder, classifies the page** (the scripts read `type` first). Directories are
domain-driven: group pages by subject and drop an `index.md` in each folder as
its overview (progressive disclosure). Split a folder once a topic exceeds
~1200 words.

A sensible default layout for a research wiki (optional, not enforced):
- `wiki/concepts/` — `type: concept`, Title Case noun phrases.
- `wiki/entities/` — `type: entity`, proper names (people, tools, papers, orgs).
- `wiki/summaries/` — `type: summary`, kebab-case source slug.

Use whatever domain folders fit the topic (`wiki/models/`, `wiki/datasets/`, …);
just set a `type` and link the page from `index.md`.

**Links** between pages use the content-root anchor: `[Title](/concepts/Foo.md)` —
the leading `/` resolves at the wiki content root (no `wiki/` prefix, no `../`,
never file-relative). Wrap space-bearing paths in angle brackets:
`[A B](</concepts/A B.md>)`.

Frontmatter required on every `wiki/` page: `title`, `type`, `created`, `updated`, `sources`, `tags`. Only `type` drives classification; the rest drive display, lineage, and maintenance.

`log/YYYYMMDD.md` is an operation log and must not use frontmatter; its first line must be `# YYYY-MM-DD`. Audit files use their own YAML frontmatter schema.

### Diagrams and formulas
- All diagrams are **mermaid**. No ASCII art.
- All formulas are **KaTeX** (inline `$...$` or block `$$...$$`).

### Raw file policy
- Small text sources → copy into `raw/<subfolder>/`.
- Large binaries → create a pointer file at `raw/refs/<slug>.md` with `kind: ref` and `external_path` fields. Do not copy the binary.

### Graph protocol
- `lint` validates the wiki and compiles `.graph` artifacts from Markdown links.
- `graph/` stores global graph files for frontends. Do not edit by hand.
- `wiki/**/*.md.graph` may appear as page-local graph caches. Do not edit by hand.
- `.graph-cache/` stores incremental graph compile state. It may be deleted and regenerated.

## Skill entry points

Maintain `wiki/index.md` as task-oriented navigation. Prefer headings that begin with "I want to ..."; every wiki page should appear exactly once.

### I want to understand the core ideas
*(none)*

### I want to identify people, tools, papers, and organizations
*(none)*

### I want to inspect sources
*(none)*

## Open research questions

- <What do you want to understand better?>
- <What are the key open questions in this domain?>

## Research gaps

Sources to ingest:
- [ ] <URL or paper title> — why it's relevant

## Audit backlog

*(none — run `python3 scripts/audit_review.py <wiki-root> --open` to refresh)*

## Notes for the LLM

- Language: {lang}
- Tone: <neutral, academic, conversational, ...>
- Depth: <survey-level | deep technical>
- Handling contradictions: state both, cite each, add to Open Research Questions.
"""
    _write(root, "SCHEMA.md", schema_md)
    print("✓ Created SCHEMA.md")

    interest_md = f"""# Interest Profile — {title}

> Dynamic steering file. Read with `SCHEMA.md` and `wiki/index.md` at session start.
> Update after meaningful discussions, repeated user questions, or explicit preference changes.

## Current Focus

- <topic the user is actively exploring> | weight: 3 | evidence: <question/date>

## Recurring Interests

- <stable long-term theme> | weight: 2 | evidence: <repeated questions or explicit preference>

## Positive Signals

- <method, benchmark, tradeoff, author, system, or paper family the user responds to> | weight: 1.5

## Downrank / Less Relevant

- <topic that is currently out of scope or lower priority> | weight: 1

## Source Preferences

- <preferred source type, e.g. primary papers, benchmarks, implementation notes> | weight: 1

## Exploration Queue

- [ ] <source/topic to inspect next> — why it matches the current interest profile

## Discussion Signals

- {today_iso} — scaffold — initial empty interest profile.

## Update Rules

- Add a signal only when the user explicitly states a preference, asks repeated related questions, or spends a discussion comparing alternatives.
- Prefer short evidence-backed bullets over broad personality guesses.
- Decay stale focus by moving it from Current Focus to Recurring Interests or Downrank.
- `query_wiki.py` uses this file as a soft ranking hint, never as a hard filter.
"""
    _write(root, "INTEREST.md", interest_md)
    print("✓ Created INTEREST.md")

    log_md = f"""# {today_iso}

## [{now_hm}] scaffold | Initialized {title} knowledge base
- Created directory tree (raw/, wiki/, log/, audit/, outputs/)
- Reserved graph/ and .graph-cache/ for lint-owned graph artifacts
- Created SCHEMA.md schema template
- Created INTEREST.md interest profile template
- Created wiki/index.md task-oriented entry point skeleton
- Language: {lang}
"""
    _write(root, f"log/{today_compact}.md", log_md)
    print(f"✓ Created log/{today_compact}.md")

    index_md = f"""---
title: Index — {title}
type: index
created: {today_iso}
updated: {today_iso}
sources: []
tags: []
---

# Index — {title}

> One-sentence scope of the wiki.

## 🔖 Navigation
- [I want to understand the core ideas](#i-want-to-understand-the-core-ideas) · [I want to identify people, tools, papers, and organizations](#i-want-to-identify-people-tools-papers-and-organizations) · [I want to inspect sources](#i-want-to-inspect-sources) · [Open Questions](#open-questions)

## I want to understand the core ideas

*(none yet)*

## I want to identify people, tools, papers, and organizations

*(none yet)*

## I want to inspect sources

*(none yet)*

## Open Questions

- <First research question>
"""
    _write(root, "wiki/index.md", index_md)
    print("✓ Created wiki/index.md")

    print(f"""
✅ Wiki scaffolded at: {root}/   (language: {lang})

Next steps:
  1. Fill in SCHEMA.md — define scope and naming conventions
     Fill in INTEREST.md — define current focus and ranking preferences
  2. Add sources to raw/ (use your browser clipper or save markdown manually)
  3. Run ingest: tell your LLM agent "ingest raw/<file>.md"
  4. Preview query context: python3 scripts/query_wiki.py {root} "what does the wiki say about X?"
     (query auto-builds or refreshes .query-index/wiki.db)
  5. Ask questions: "what does the wiki say about X?"
  6. Capture durable discussion signals:
     python3 scripts/evolve_wiki.py {root} --title "..." --summary "..." --interest "..."
  7. Run lint after every write (incremental by default — auto-detects changes
     via the union of `git status` (uncommitted edits) and stat-cache vs last
     lint (catches `git pull` / branch-checkout deltas)):
       python3 scripts/lint_wiki.py {root}
     Add --full only for periodic full-graph checks (or after renames/deletes):
       python3 scripts/lint_wiki.py {root} --full
     (the --full audit-inbox gate stops first if audit/*.md has open feedback)
  8. Checkpoint the op's state into git (run as the FINAL step, after log):
       python3 scripts/commit_wiki.py {root}
     (no-op if not in git; commits only SCHEMA.md / INTEREST.md / wiki/ / raw/ /
      audit/ / log/; commit message auto-derived from log/<today>.md's last entry)
  9. Build audit CR queue:   python3 scripts/audit_cr.py {root} --open --write
 10. Process feedback:       python3 scripts/audit_review.py {root} --open
""")


def _write(root: str, path: str, content: str) -> None:
    full = os.path.join(root, path)
    os.makedirs(os.path.dirname(full) or ".", exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(content)


def existing_wiki_artifacts(root: str) -> list[str]:
    """Return the list of scaffold-owned files/dirs already present under root.

    Tests for the markers a real wiki would have so we don't refuse for an
    empty directory or one containing only unrelated files. Order matters:
    the first hits are the ones most likely to surface in a user's mental
    model ("did I already scaffold here?").
    """
    markers = ["SCHEMA.md", "INTEREST.md", "wiki/index.md", "audit", "log"]
    return [m for m in markers if os.path.exists(os.path.join(root, m))]


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    root_arg = sys.argv[1]
    title_arg = sys.argv[2]
    lang_arg = "en"
    force = "--force" in sys.argv
    if "--lang" in sys.argv:
        idx = sys.argv.index("--lang")
        if idx + 1 < len(sys.argv):
            lang_arg = sys.argv[idx + 1].strip().lower()

    found = existing_wiki_artifacts(root_arg)
    if found and not force:
        print(
            f"error: {root_arg} already contains a scaffolded wiki:",
            file=sys.stderr,
        )
        for marker in found:
            print(f"    {marker}", file=sys.stderr)
        print(
            "\nRefusing to overwrite. Options:\n"
            "  - Pick a fresh directory.\n"
            "  - Commit/back up your wiki, then re-run with --force to re-scaffold.\n"
            "  - If you only want to update INTEREST.md / SCHEMA.md, edit them by hand.",
            file=sys.stderr,
        )
        sys.exit(2)

    scaffold(root_arg, title_arg, lang_arg)
