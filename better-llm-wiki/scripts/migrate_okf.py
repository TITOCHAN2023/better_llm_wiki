#!/usr/bin/env python3
"""
migrate_okf.py — Modernize an existing wiki to the OKF-style representation.

Two in-place transforms over `wiki/**/*.md`:

  1. Link anchor: rewrite the legacy root-relative form `](wiki/…md)` to the
     OKF content-root anchor `](/…md)`. The leading `/` resolves at the wiki
     content root; the `wiki/` segment becomes redundant. Both the bare and the
     angle-bracketed (`<wiki/… with spaces.md>`) shapes are handled, and any
     `#anchor` suffix is preserved.
  2. Frontmatter `type`: ensure every page declares a `type`. If the field is
     missing it is inferred from the legacy bucket (`concepts/`→concept,
     `entities/`→entity, `summaries/`→summary, `index.md`→index, else page) so
     that classification survives once folders are free to move.

This migration is OPTIONAL. The scripts recognize both the new `/…md` anchor
and the legacy `wiki/…md` form, so an un-migrated wiki keeps working — this
tool just canonicalizes it. It is idempotent: re-running changes nothing.

Only `wiki/**/*.md` content is touched. Operational streams (`log/`, `audit/`,
`outputs/`) and `raw/` are left untouched — their `wiki/`, `raw/`, `log/`
references still resolve, and rewriting historical records is not this tool's
job.

Usage:
    python3 migrate_okf.py <wiki-root> [--dry-run]

    --dry-run   Report what would change without writing.
"""

import argparse
import posixpath
import re
import sys
from pathlib import Path

# An MD link whose href starts with the legacy `wiki/` prefix. Two shapes:
# angle-bracketed (may contain spaces) or bare (no whitespace/parens). The
# `wiki/` prefix itself is captured so we can replace exactly it with `/`.
LEGACY_LINK_RE = re.compile(
    r"""
    (?P<pre>!?\[[^\]\n]*\]\(\s*)
    (?:
        <(?P<wiki_br>wiki)/(?P<br_rest>[^>\n]+?\.md(?:\#[^>\n]*)?)>
        |
        (?P<wiki_bare>wiki)/(?P<bare_rest>[^()\s]+?\.md(?:\#[^()\s]*)?)
    )
    (?P<post>\s*\))
    """,
    re.VERBOSE,
)

FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


def rewrite_links(text: str) -> tuple[str, int]:
    """Return (new_text, num_links_rewritten)."""
    count = 0

    def repl(m: re.Match) -> str:
        nonlocal count
        count += 1
        if m.group("br_rest") is not None:
            return f"{m.group('pre')}</{m.group('br_rest')}>{m.group('post')}"
        return f"{m.group('pre')}/{m.group('bare_rest')}{m.group('post')}"

    return LEGACY_LINK_RE.sub(repl, text), count


def infer_type(rel_id: str) -> str:
    if rel_id == "wiki/index.md":
        return "index"
    parts = rel_id.split("/")
    if len(parts) >= 3:
        return {"concepts": "concept", "entities": "entity", "summaries": "summary"}.get(
            parts[1], "page"
        )
    return "page"


def ensure_type(text: str, rel_id: str) -> tuple[str, bool]:
    """Add a `type:` line to the frontmatter if absent. Returns (new_text, added)."""
    m = FRONTMATTER_RE.match(text)
    inferred = infer_type(rel_id)
    if not m:
        # No frontmatter at all — prepend a minimal one rather than fabricate fields.
        return f"---\ntype: {inferred}\n---\n{text}", True
    body = m.group(1)
    for line in body.split("\n"):
        if line.split(":", 1)[0].strip() == "type" and ":" in line:
            return text, False  # already declared
    new_body = f"type: {inferred}\n{body}"
    return text[: m.start()] + f"---\n{new_body}\n---\n" + text[m.end():], True


def main() -> int:
    parser = argparse.ArgumentParser(description="Modernize a wiki to OKF-style representation.")
    parser.add_argument("wiki_root", help="Path to the wiki root containing wiki/")
    parser.add_argument("--dry-run", action="store_true", help="Report changes without writing")
    args = parser.parse_args()

    root = Path(args.wiki_root).expanduser().resolve()
    wiki_dir = root / "wiki"
    if not wiki_dir.is_dir():
        print(f"error: missing wiki/ directory under {root}", file=sys.stderr)
        return 2

    total_links = 0
    types_added = 0
    files_changed = 0
    for md in sorted(wiki_dir.rglob("*.md")):
        if md.name.endswith(".md.graph"):
            continue
        rel_id = md.relative_to(root).as_posix()
        original = md.read_text(encoding="utf-8")
        text, n_links = rewrite_links(original)
        text, added_type = ensure_type(text, rel_id)
        if text == original:
            continue
        files_changed += 1
        total_links += n_links
        types_added += 1 if added_type else 0
        marks = []
        if n_links:
            marks.append(f"{n_links} link(s)→/")
        if added_type:
            marks.append(f"+type:{infer_type(rel_id)}")
        print(f"  {rel_id}  [{', '.join(marks)}]")
        if not args.dry_run:
            md.write_text(text, encoding="utf-8")

    verb = "would change" if args.dry_run else "changed"
    print(
        f"\n{'DRY-RUN — ' if args.dry_run else ''}{verb} {files_changed} file(s): "
        f"{total_links} link(s) rewritten to /-anchor, {types_added} type field(s) added."
    )
    if not args.dry_run and files_changed:
        print("Next: run  lint_wiki.py <wiki-root> --full  to rebuild the graph and confirm health.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
