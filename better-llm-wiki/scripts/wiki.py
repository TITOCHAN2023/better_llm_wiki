#!/usr/bin/env python3
"""wiki.py — one entrypoint for the whole LLM-wiki skill.

You do NOT need to remember the individual script filenames. Run:

    python3 wiki.py <command> <wiki-root> [options]

Commands, in the order you normally use them:

  scaffold      <root> "<Title>" [--lang zh]   create a new wiki (once)
  query         <root> "<question>"            find pages to read — ALWAYS before writing
  lint          <root> [--full]                health-check — ALWAYS after any write
  commit        <root>                         checkpoint into git — the LAST step of a write
  ingest-scan   <root> --audited-state-file <p>  sliding-window audit loop after an ingest
  evolve        <root> --title "<t>" ...       capture a durable discussion signal
  audit-cr      <root> --open --write          build the contradiction/correction register
  audit-review  <root> --open                  list human feedback waiting in audit/
  rollback      <root> --list | --undo <ref>   undo a checkpoint
  migrate-okf   <root> [--dry-run]             modernize an old wiki's links to /-anchor
  index         <root> [--rebuild]             (re)build the query index (query does this for you)

THE CANONICAL WRITE LOOP (do these in order every time you change wiki/):
  1. python3 wiki.py query  <root> "<what you're about to write about>"   # read first, don't duplicate
  2. ...edit wiki/ pages...  (links look like [Title](/concepts/Foo.md) — leading slash, never ../)
  3. python3 wiki.py lint    <root>                                        # fix everything it reports
  4. python3 wiki.py commit  <root>                                        # checkpoint

Every path argument <root> is the wiki root (the folder that holds wiki/, raw/, log/…).
Run `python3 wiki.py <command> --help` for one command's full options.
"""
import subprocess
import sys
from pathlib import Path

# command name → underlying script. Kebab-case commands map to the real files
# so callers never need the filenames or the *_wiki.py suffix pattern.
SCRIPTS = {
    "scaffold": "scaffold.py",
    "query": "query_wiki.py",
    "lint": "lint_wiki.py",
    "commit": "commit_wiki.py",
    "ingest-scan": "ingest_scan.py",
    "evolve": "evolve_wiki.py",
    "audit-cr": "audit_cr.py",
    "audit-review": "audit_review.py",
    "rollback": "rollback_wiki.py",
    "migrate-okf": "migrate_okf.py",
    "index": "index_wiki.py",
}


def main() -> int:
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    command = args[0]
    if command not in SCRIPTS:
        print(f"error: unknown command {command!r}\n", file=sys.stderr)
        print("valid commands: " + ", ".join(SCRIPTS), file=sys.stderr)
        print("run `python3 wiki.py help` for the full list and the write loop.", file=sys.stderr)
        return 2
    script = Path(__file__).with_name(SCRIPTS[command])
    return subprocess.run([sys.executable, str(script), *args[1:]]).returncode


if __name__ == "__main__":
    sys.exit(main())
