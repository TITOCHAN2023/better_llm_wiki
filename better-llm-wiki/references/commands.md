# Command cheatsheet

One entrypoint does everything. `<root>` is always the wiki folder (the one holding `wiki/`, `raw/`, `log/`).

```
python3 <skill>/scripts/wiki.py <command> <root> [options]
```

`<skill>` is the folder that holds this skill (contains `SKILL.md` and `scripts/`). Run `python3 <skill>/scripts/wiki.py help` any time.

## The write loop — do this EVERY time you change `wiki/`

| Step | Command | Why |
|------|---------|-----|
| 1. Look before you write | `wiki.py query <root> "<topic>"` | find existing pages, don't duplicate or invent |
| 2. Edit `wiki/` pages | (your edits) | links are `[Title](/concepts/Foo.md)` — leading `/`, **never `../`** |
| 3. Health-check | `wiki.py lint <root>` | fix everything it prints before moving on |
| 4. Checkpoint | `wiki.py commit <root>` | save state into git (last step) |

If you renamed/deleted pages or are about to publish: `wiki.py lint <root> --full`.

## Intent → command

| I want to… | Command |
|------------|---------|
| Start a new wiki | `wiki.py scaffold <root> "My Topic" --lang zh` |
| Answer a question from the wiki | `wiki.py query <root> "how does X work?"` |
| List recently-updated pages | `wiki.py query <root> --recent 30 --sort updated` |
| Find pages near a known one | `wiki.py query <root> --related /concepts/Foo.md --depth 1` |
| Health-check after writing | `wiki.py lint <root>` |
| Full health-check (renames/publish) | `wiki.py lint <root> --full` |
| Save changes into git | `wiki.py commit <root>` |
| Audit a fresh ingest's neighborhood | `wiki.py ingest-scan <root> --audited-state-file /tmp/scan-<name>.json` (re-run until it says Converged) |
| Record a durable discussion point | `wiki.py evolve <root> --title "…" --summary "…" --page /concepts/Foo.md` |
| See human feedback waiting | `wiki.py audit-review <root> --open` |
| Build correction register | `wiki.py audit-cr <root> --open --write` |
| Undo the last checkpoint | `wiki.py rollback <root> --list` then `wiki.py rollback <root> --undo <ref>` |
| Modernize an old wiki's links | `wiki.py migrate-okf <root>` (add `--dry-run` first) |

## Two rules the linter enforces (so just follow them)

1. **Links start with `/`** and point at a `.md` under the content root: `[Vaswani](/entities/Vaswani.md)`. Wrap spaces in `<…>`: `[A B](</concepts/A B.md>)`. Never write `../`, never write a `wiki/` prefix.
2. **Every `wiki/` page has frontmatter** with at least `type:` (`concept` / `entity` / `summary` / `paper` / …). The folder does not decide the type — the frontmatter does.

## Scale (large wikis)

- Keep each wiki as its **own git repo** (run `wiki.py commit` after each change). Then `lint` and `query` detect changes via git in O(changes) — they stay fast and low-memory no matter how big the wiki grows.
- Bulk source material goes in `raw/` (not counted toward size limits). `wiki/` holds short, linked, curated pages.
- `lint --full` (whole-graph) is intentionally bounded and will refuse above ~80k pages — that is expected; use plain `lint` (incremental) + `query` at that scale. Knobs: `LLM_WIKI_MAX_PAGES_HARD`, etc.
