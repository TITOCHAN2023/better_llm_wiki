# Audit Guide — human feedback on wiki content

The `audit/` directory is the human feedback surface. One file per feedback, YAML frontmatter + markdown body. Feedback is produced by the web viewer or written manually and **consumed by the AI during the `audit` operation**.

## Why it exists

AI-written content is wrong sometimes. Raw sources contradict each other. Feedback in chat is lost the moment the conversation ends. The audit directory gives corrections a permanent, location-anchored home that every tool (web viewer, AI, lint script, manual editors) understands.

## Directory layout

```
<wiki-root>/audit/
├── 20260409-143022-example-system-size.md    ← open feedback
├── 20260409-150110-rag-definition.md      ← open feedback
└── resolved/
    ├── 20260408-110505-typo-gemma.md      ← processed, with resolution
    └── 20260407-180012-rejected-scope.md  ← rejected, with rationale
```

- `audit/*.md` — open feedback, not yet processed.
- `audit/resolved/*.md` — processed feedback. Nothing ever gets deleted; rejections stay with their rationale.

## File format

Filename: `YYYYMMDD-HHMMSS-<short-slug>.md`. The prefix is the creation timestamp (local time); the slug is a human-readable hint derived from the selected text or the comment.

```markdown
---
id: 20260409-143022-a1b2
target: tech/Example_System.md
type: warn
start: [45, 1]
end: [45, 42]
author: lewis
source: web-viewer
created: 2026-04-09T14:30:22+08:00
status: open
---

# Comment

实际应该是 ~1,800 个文件，参考 2026-03-31 commit abc123 的 tree。
`find . -type f | wc -l` 当时是 1817。这个数字直接影响下面几个估算。

# Resolution

<!-- Filled in when the audit is processed and moved to resolved/ -->
```

### Frontmatter fields

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | string | yes | Unique id: `YYYYMMDD-HHMMSS-<4hex>`. Must match filename prefix. |
| `target` | string | yes | Path relative to wiki root. Must be a file that exists (lint check). |
| `type` | enum | yes | One of `info`, `suggest`, `warn`, `error`. |
| `start` | `[int, int]` | yes | `[line, col]`, both 1-indexed. Inclusive start position of the selection. |
| `end` | `[int, int]` | yes | `[line, col]`, both 1-indexed. Inclusive end position of the selection. For a single-character or caret position, `end == start`. |
| `author` | string | yes | Free text. The web viewer prefills the logged-in user; manual edits can write any stable author string. |
| `source` | enum | yes | One of `web-viewer`, `manual`, `gateway`, `cli`. |
| `created` | ISO 8601 | yes | Timestamp with timezone. |
| `status` | enum | yes | `open` for files in `audit/`, `resolved` for files in `audit/resolved/`. |

### Type semantics

- **info** — "worth noting but not wrong". Example: additional context, alternate phrasing.
- **suggest** — "consider this". Example: reword, reorganize.
- **warn** — "something looks off". Example: stale number, ambiguous sentence.
- **error** — "this is wrong". Example: factual mistake, broken link, wrong attribution.

The AI should process `error` and `warn` first, then `suggest`, then `info`.

## Coordinate strategy

Audits anchor by **(line, column) coordinates**, not by surrounding text.

- `start` and `end` are 1-indexed `[line, col]` pairs identifying the inclusive start and end position of the user's selection.
- A caret-only position (no text selected) is expressed as `end == start`.
- Coordinates count Unicode code points (the same units a typical editor reports for selection ranges), not bytes. The browser editor sets these values directly from the selection model.

Coordinates are fragile to upstream edits — any insert/delete before the selection point shifts them. The trade-off is intentional: the wire format stays simple and fully deterministic. Drift is handled at the read side:

- During the `audit` op, if `start` exceeds the current file's line count, or the line is too short for the recorded column, the anchor is stale.
- Stale anchors are **never silently dropped**. The agent surfaces the discrepancy to the user and asks whether to re-anchor (by re-selecting in the viewer), reject the audit, or defer it for later.

The intent is that audits get processed quickly — same-day, often same-session — so the drift window is small in practice.

## Processing workflow (the `audit` op)

See `SKILL.md` → "The five operations" → `audit` for the canonical version. In short:

1. `python3 scripts/audit_cr.py <wiki-root> --open --write` → build `outputs/audit-cr/contradiction-register.md`, the prioritized correction/contradiction cleanup queue.
2. `python3 scripts/audit_review.py <wiki-root> --open` → get a grouped list.
3. For each open audit:
   - Read the file, use the anchor to locate the range in the target.
   - Decide: accept / partial / reject / defer.
   - Apply edits in the target file when accepting or partially accepting.
   - For deferred audits, add the question to `SCHEMA.md` "Open research questions" with a reference to the audit id.
   - Append a `# Resolution` section to the audit file.
   - Flip `status: open` → `status: resolved` in the frontmatter.
   - Move the file to `audit/resolved/`.
   - Append a `## [HH:MM] audit | resolved <id> — <one-liner>` entry to `log/YYYYMMDD.md`.
4. After processing, run `python3 scripts/audit_cr.py <wiki-root> --all --write` to refresh the contradiction register with both open and resolved history.
5. Run `python3 scripts/lint_wiki.py <wiki-root> --full`. The lint script refuses to run the full check while open `audit/*.md` files remain, so unresolved user feedback cannot be silently skipped. (Incremental lint — the default, no flag — does not enforce this gate; the gate is on full lint specifically, which rebuilds the global graph.) Do not delete audit files; clear the inbox by moving every processed item to `audit/resolved/`.
6. Review any contradiction/staleness markers reported by lint or written into `graph/issues.graph`; reconcile true conflicts, file new audits for human decisions, or move unresolved uncertainty to `SCHEMA.md` Open research questions.

## Resolution section format

```markdown
# Resolution

2026-04-10 · accepted.
Fixed the file count (was "~1,900", corrected to "~1,800" per commit abc123).
Updated: tech/Example_System.md lines 47–48.
Log: [log/20260410 · 14:30 audit](log/20260410.md)
```

Fields:
- Date · decision (`accepted`, `partial`, `rejected`, `deferred`).
- 1–3 sentences on what you did and why.
- Which files were touched (for non-trivial edits).
- Pointer to the log entry.

For `rejected` audits: explain **why** — most often "out of scope per SCHEMA.md" or "contradicts more authoritative source X". Rejected audits still move to `resolved/` so they're not processed again, but they remain visible in case the scope changes.

## Sidecar protocol (external writers)

External tools that don't have direct access to the canonical `audit/` directory — for example the mino_server `auditWrite` gateway, which writes into a sandboxed VFS — drop audits as **JSON sidecar files** under `wiki/`:

- target: `wiki/concepts/Foo.md`
- sidecar: `wiki/concepts/Foo.md.audit`

Discovery glob: `wiki/**/*.md.audit`. The location is informational (typically next to the target page); lint doesn't care where exactly under `wiki/` it sits.

### Format

The sidecar file is JSON. The top level is either a single audit object or an array of objects (one file can carry multiple entries):

```json
[
  {
    "id": "20260525-180000-abcd",
    "target": "wiki/concepts/Foo.md",
    "type": "warn",
    "start": [3, 1],
    "end": [3, 5],
    "author": "lewis",
    "source": "web-viewer",
    "created": "2026-05-25T18:00:00+08:00",
    "status": "open",
    "comment": "Free-form markdown body. Can contain --- horizontal rules,\n```yaml\n---\nnested: frontmatter\n---\n``` code blocks, anything."
  },
  {
    "id": "20260525-181500-ef01",
    "target": "wiki/concepts/Foo.md",
    "type": "suggest",
    "start": [12, 4],
    "end": [14, 22],
    "author": "tito",
    "source": "manual",
    "created": "2026-05-25T18:15:00+08:00",
    "status": "open",
    "comment": "Another note on the same target, spanning lines 12–14."
  }
]
```

### Why JSON wire format vs. custom delimiter

The wire format is consumed by web frontends, backend gateways, and lint. All three already have standard JSON parsers — no custom delimiters, no escape rules, no collisions with markdown horizontal rules in the `comment` body. The LLM-readable canonical form (`audit/<id>-<slug>.md`) is still YAML frontmatter + markdown body; lint does the JSON → canonical translation during ingest.

### Ingest behavior

`lint_wiki.py` runs an **ingest pass before the preflight gate**: every `wiki/**/*.md.audit` is split into its constituent blocks, each block is rendered as canonical YAML frontmatter + body and written to `audit/<id>-<slug>.md`, and the sidecar is removed. Downstream tooling (`audit_review.py`, `audit_cr.py`, manual processing) never sees the sidecar form.

**Robustness contract**: when a sidecar contains a malformed block (missing `id`, unparseable header line, or no delimiter at all), the sidecar is **preserved on disk** with a stderr warning. User feedback is never silently dropped, even when the writer-side tool has a bug. Fix the sidecar manually or re-run the writer; lint will pick it up next time.

### Authoring an audit by hand

To write an audit without a UI:

1. Open the target wiki page (`wiki/<...>.md`) and find the start and end coordinates of the selection. Use any editor that reports line/col, or count manually (lines and columns are both 1-indexed; columns count Unicode characters, not bytes).
2. Generate an id: `YYYYMMDD-HHMMSS-<4 random hex>` (e.g. `20260526-160500-1a2b`).
3. Write a JSON file at `wiki/<target>.md.audit` following the format above. Required fields: `id`, `target`, `type`, `start`, `end`, `author`, `source` (use `"manual"` for hand-written), `created`, `status: "open"`, `comment`.
4. Run `python3 scripts/lint_wiki.py <wiki-root>` (incremental, the default). The audit sidecar will be ingested into `audit/<id>-<slug>.md`; the open-audit preflight gate then blocks any subsequent `--full` lint until the audit is processed.

## Tooling

- **`scripts/lint_wiki.py`** first drains `wiki/**/*.md.audit` sidecars into `audit/`, then blocks full lint when open `audit/*.md` files exist; after the inbox is clear, it checks MD scale limits before loading all pages, validates audit file shape and targets, and writes contradiction/staleness markers to `graph/issues.graph`.
- **`scripts/audit_review.py`** lists and groups audits.
- **`scripts/audit_cr.py`** builds the periodic correction/contradiction register from human-filed `audit/*.md`. It scans open/resolved audits, detects high-risk signals (`error`, `warn`, deferred/rejected resolutions, contradiction/stale wording), clusters them by target and anchor, and writes `outputs/audit-cr/contradiction-register.md` when `--write` is set.
- **`scripts/ingest_scan.py`** is the machine-driven counterpart to that human-feedback flow: it seeds from freshly-ingested pages (auto-detected via the union of `git status` and the stat cache vs the last lint, then `graph/recent.graph`, then explicit `--seed`), expands **one hop** along the link graph (both directions), and prints the seeds plus their directly-linked neighbors as an **audit scope**. It does not judge contradictions itself — that semantic call is left to the agent, which reads the listed pages and files real audits by hand. The output is advisory only and is **never** written into `audit/`. The script emits **one** 1-hop window per invocation; the agent runs it in a **sliding-window loop** (scan → audit → fix → re-scan) with `--audited-state-file <path>`, which persists the set of already-enqueued (processed) nodes across calls as a queue/BFS worklist. Each round shows only the *new pages* and *new edges* since the previous one, so nothing is read twice; an edge back toward an already-processed node is shielded. Stop when the report says `✅ Converged` (new pages and new edges both empty), when seeds are empty, or after 8 iterations as a runaway guard — see SKILL.md `ingest` step 9.
- **`scripts/rollback_wiki.py`** is the safety net when an audit "fix" turns out to be wrong and has polluted the wiki. Every write op checkpoints into git and is tagged `ckpt/<op>/<timestamp>` (see `commit_wiki.py`); `rollback_wiki.py <root> --list` shows the last 30 days of checkpoints and `--undo <tag>` surgically reverts just that one audit's **content** as a new forward commit — later audits are kept, `log/` is never rewound (the trail records that the rollback happened), and the undo is itself re-revertible. Use `--to <ref>` to restore the whole content tree to a clean point. It refuses to run on a dirty tree and never rewrites history.
- **`web/`** writes audit files from the local web viewer on selection.
- **`audit-shared/`** — TypeScript library implementing the schema, anchor algorithm, id generator, and YAML (de)serialization used by the web server and any future audit-writing tools.
