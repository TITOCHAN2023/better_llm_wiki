#!/usr/bin/env python3
"""
evolve_wiki.py — Capture discussion-derived wiki evolution signals.

This script gives the self-evolution loop a durable, testable write path:
it can save a discussion increment under outputs/discussions/, append
interest/downrank signals to INTEREST.md, and log the operation.

Usage:
    python3 evolve_wiki.py <wiki-root> --title "Discussion title" --summary "What changed"

Examples:
    python3 evolve_wiki.py ./my-wiki \
      --title "ACC verification preferences" \
      --summary "User prefers implementation risks over leaderboard claims." \
      --page wiki/concepts/Agent Context Compilation.md \
      --interest "verification failure modes | weight: 3 | evidence: 2026-05-25 discussion" \
      --downrank "leaderboard-only summaries | weight: 2 | evidence: user rejected shallow coverage"

Exit codes:
  0 — evolution signals captured
  2 — invalid wiki root or options
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path


def slugify(text: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", text.strip().lower()).strip("-")
    return slug[:80] or "discussion"


def today_parts() -> tuple[str, str]:
    now = datetime.now().astimezone()
    return now.date().isoformat(), now.strftime("%H:%M")


def ensure_interest_file(root: Path) -> Path:
    """Create INTEREST.md if missing, using the same template scaffold.py writes.

    Keep this template byte-equivalent to scaffold._interest_template aside from
    the first Discussion Signals bullet (which records the creator path). The
    two paths are intentionally redundant so an old wiki without INTEREST.md
    can still acquire a structurally identical profile from the evolve flow.
    """
    path = root / "INTEREST.md"
    if path.exists():
        return path
    title = root.name
    today, _ = today_parts()
    path.write_text(
        f"""# Interest Profile — {title}

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

- {today} — evolve — initial empty interest profile.

## Update Rules

- Add a signal only when the user explicitly states a preference, asks repeated related questions, or spends a discussion comparing alternatives.
- Prefer short evidence-backed bullets over broad personality guesses.
- Decay stale focus by moving it from Current Focus to Recurring Interests or Downrank.
- `query_wiki.py` uses this file as a soft ranking hint, never as a hard filter.
""",
        encoding="utf-8",
    )
    return path


def read_body(args: argparse.Namespace) -> str:
    parts: list[str] = []
    if args.summary:
        parts.append(args.summary.strip())
    if args.body_file:
        body_path = Path(args.body_file).expanduser()
        if args.body_file == "-":
            body = sys.stdin.read()
        else:
            body = body_path.read_text(encoding="utf-8")
        if body.strip():
            parts.append(body.strip())
    return "\n\n".join(parts).strip()


def validate_pages(root: Path, pages: list[str]) -> list[str]:
    valid: list[str] = []
    for raw in pages:
        rel = raw.strip()
        if not rel:
            continue
        if not rel.endswith(".md"):
            raise ValueError(f"--page must be a .md path: {rel}")
        # Accept both the OKF content-root anchor (`/concepts/X.md`) and the
        # legacy root-relative form (`wiki/concepts/X.md`); normalize to the
        # on-disk `wiki/…` id.
        if rel.startswith("/"):
            rel = "wiki" + rel
        elif not rel.startswith("wiki/"):
            raise ValueError(
                f"--page must be a content-root (/…md) or wiki-root-relative (wiki/…md) path: {rel}"
            )
        target = root / rel
        if not target.exists() or not target.is_file():
            raise ValueError(f"--page target does not exist: {rel}")
        valid.append(rel)
    return sorted(set(valid))


def unique_path(base_dir: Path, stem: str) -> Path:
    today, _ = today_parts()
    candidate = base_dir / f"{today}-{stem}.md"
    if not candidate.exists():
        return candidate
    index = 2
    while True:
        candidate = base_dir / f"{today}-{stem}-{index}.md"
        if not candidate.exists():
            return candidate
        index += 1


def md_link(label: str, href: str) -> str:
    rendered = f"<{href}>" if any(ch.isspace() for ch in href) else href
    return f"[{label}]({rendered})"


def write_discussion(root: Path, title: str, body: str, pages: list[str]) -> Path:
    out_dir = root / "outputs" / "discussions"
    out_dir.mkdir(parents=True, exist_ok=True)
    today, _ = today_parts()
    out = unique_path(out_dir, slugify(title))
    page_lines = "\n".join(f"- {md_link(Path(page).stem, page)}" for page in pages) if pages else "- (none)"
    text = f"""---
title: "{title.replace('"', '\\"')}"
type: discussion
created: {today}
updated: {today}
sources: []
tags: [discussion, self-evolution]
---

# {title}

## Summary

{body or "(no summary provided)"}

## Grounded Pages

{page_lines}

## Promotion Notes

- Promote into `wiki/` only if this remains durable beyond the current discussion.
"""
    out.write_text(text, encoding="utf-8")
    return out


def append_to_section(path: Path, section: str, bullets: list[str]) -> None:
    if not bullets:
        return
    text = path.read_text(encoding="utf-8")
    heading_re = re.compile(rf"^(##\s+{re.escape(section)}\s*)$", re.MULTILINE | re.IGNORECASE)
    match = heading_re.search(text)
    block = "\n".join(f"- {bullet.strip()}" for bullet in bullets if bullet.strip())
    if not block:
        return
    if not match:
        if not text.endswith("\n"):
            text += "\n"
        text += f"\n## {section}\n\n{block}\n"
        path.write_text(text, encoding="utf-8")
        return

    next_heading = re.search(r"^##\s+", text[match.end():], re.MULTILINE)
    insert_at = len(text) if next_heading is None else match.end() + next_heading.start()
    prefix = text[:insert_at].rstrip()
    suffix = text[insert_at:].lstrip("\n")
    rendered = f"{prefix}\n{block}\n"
    if suffix:
        rendered += "\n" + suffix
    path.write_text(rendered, encoding="utf-8")


def append_log(root: Path, title: str, discussion_path: Path | None, signals: int) -> None:
    log_dir = root / "log"
    log_dir.mkdir(parents=True, exist_ok=True)
    today, hm = today_parts()
    log_name = today.replace("-", "") + ".md"
    log_path = log_dir / log_name
    if not log_path.exists():
        log_path.write_text(f"# {today}\n", encoding="utf-8")
    rel = discussion_path.relative_to(root).as_posix() if discussion_path else ""
    line = f"\n## [{hm}] evolve | {slugify(title)} — captured discussion signal"
    detail = f"\n\n- Signals: {signals}"
    if rel:
        detail += f"\n- Discussion: [{discussion_path.name}]({rel})"
    with log_path.open("a", encoding="utf-8") as f:
        f.write(line + detail + "\n")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Capture discussion-derived wiki evolution signals.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("wiki_root", help="Path to the wiki root")
    parser.add_argument("--title", required=True, help="Short title for the discussion/evolution event")
    parser.add_argument("--summary", default="", help="Short durable summary of the discussion")
    parser.add_argument("--body-file", help="Read additional discussion body from a file, or '-' for stdin")
    parser.add_argument("--page", action="append", default=[], help="Grounding wiki page. Repeatable.")
    parser.add_argument("--interest", action="append", default=[], help="Positive interest bullet to append. Repeatable.")
    parser.add_argument("--recurring", action="append", default=[], help="Recurring interest bullet to append. Repeatable.")
    parser.add_argument("--downrank", action="append", default=[], help="Downrank / less relevant bullet to append. Repeatable.")
    parser.add_argument("--source", action="append", default=[], help="Source preference bullet to append. Repeatable.")
    parser.add_argument("--explore", action="append", default=[], help="Exploration queue bullet to append. Repeatable.")
    parser.add_argument("--signal", action="append", default=[], help="Discussion signal bullet to append. Repeatable.")
    parser.add_argument("--no-discussion", action="store_true", help="Only update INTEREST.md/log; do not write outputs/discussions")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    root = Path(args.wiki_root).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        print(f"error: wiki root does not exist or is not a directory: {root}", file=sys.stderr)
        return 2
    if not (root / "wiki").exists():
        print(f"error: missing wiki/ directory under {root}", file=sys.stderr)
        return 2

    try:
        pages = validate_pages(root, args.page)
        body = read_body(args)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    interest_path = ensure_interest_file(root)
    today, _ = today_parts()
    discussion_path: Path | None = None
    if not args.no_discussion:
        discussion_path = write_discussion(root, args.title, body, pages)

    signal_bullets = list(args.signal)
    if body:
        signal_bullets.append(f"{today} — discussion — {args.title} | evidence: {body.splitlines()[0][:120]}")

    append_to_section(interest_path, "Current Focus", args.interest)
    append_to_section(interest_path, "Recurring Interests", args.recurring)
    append_to_section(interest_path, "Downrank / Less Relevant", args.downrank)
    append_to_section(interest_path, "Source Preferences", args.source)
    append_to_section(interest_path, "Exploration Queue", args.explore)
    append_to_section(interest_path, "Discussion Signals", signal_bullets)

    signals = sum(len(v) for v in (args.interest, args.recurring, args.downrank, args.source, args.explore, signal_bullets))
    append_log(root, args.title, discussion_path, signals)

    if discussion_path:
        print(f"Wrote {discussion_path.relative_to(root)}")
    print(f"Updated {interest_path.relative_to(root)} ({signals} signal(s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
