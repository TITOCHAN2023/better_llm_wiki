#!/usr/bin/env python3
"""
commit_wiki.py — Checkpoint the wiki's truth-source state into git.

Run as the final step of any wiki-modifying op (compile / ingest / query
promote / audit processing). Stages only the canonical truth-source paths
(SCHEMA.md, INTEREST.md, wiki/, raw/, audit/, log/) — never `git add -A`,
so unrelated uncommitted work (.env, work-in-progress, etc.) is preserved.

Usage:
    python3 commit_wiki.py <wiki-root> [--message "..."] [--include-derived] [--dry-run]

Behavior:
  - If wiki-root is not under git → skip with a notice, exit 0.
  - If nothing to commit → skip silently, exit 0 (no empty commits).
  - Default commit message: parsed from the latest entry in log/<today>.md
    (e.g. "ingest: acc — ACC 论文 (touched 7 pages)"); falls back to a
    dir-summary checkpoint line if no log entry today.
  - --message overrides the auto-generated message.
  - --include-derived also stages graph/ and .query-index/wiki.db
    (default excludes these — they're regenerable; use only if you want
    versioned graph snapshots).
  - --dry-run shows what would be staged + the message, no actual commit.
  - After a successful commit, tags it `ckpt/<op>/<YYYYMMDD-HHMMSS>` (a
    lightweight tag — a ref only, history is untouched) so the checkpoint is
    listable and roll-back-able via rollback_wiki.py. Suppress with --no-tag.
  - Never runs `git push` — pushing is the user's decision.

Exit codes:
  0 — committed, or skipped cleanly (not in git, nothing to commit)
  2 — invalid wiki root or arguments
  3 — git command failed (e.g. pre-commit hook rejected the commit)
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path


TRUTH_PATHS = ["SCHEMA.md", "INTEREST.md", "wiki/", "raw/", "audit/", "log/"]
DERIVED_PATHS = ["graph/", ".query-index/wiki.db"]
LOG_ENTRY_RE = re.compile(r"^##\s+\[(\d{2}:\d{2})\]\s+([a-zA-Z0-9_-]+)\s+\|\s+(.+?)\s*$")


def is_git_repo(root: Path) -> bool:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--is-inside-work-tree"],
            capture_output=True, text=True, timeout=5,
        )
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return False
    return result.returncode == 0 and result.stdout.strip() == "true"


def wiki_is_gitignored(root: Path) -> bool:
    """True if the wiki/ dir is gitignored by an enclosing repo (treat as no-op)."""
    return _is_path_gitignored(root, "wiki")


def _is_path_gitignored(root: Path, rel: str) -> bool:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "check-ignore", "--quiet", rel],
            capture_output=True, check=False,
        )
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return False
    return result.returncode == 0


def existing_paths(root: Path, paths: list[str]) -> list[str]:
    out: list[str] = []
    for p in paths:
        target = root / p.rstrip("/")
        if target.exists():
            out.append(p)
    return out


def changed_under(root: Path, paths: list[str]) -> list[str]:
    """Return relative paths that git status reports as changed within `paths`."""
    if not paths:
        return []
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain", "-z",
             "--untracked-files=all", "--"] + paths,
            capture_output=True, text=True, timeout=10,
        )
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return []
    if result.returncode != 0:
        return []
    changed: list[str] = []
    for entry in result.stdout.split("\0"):
        if not entry or len(entry) < 4:
            continue
        path_part = entry[3:]
        if " -> " in path_part:
            path_part = path_part.split(" -> ", 1)[1]
        path_part = path_part.strip().strip('"')
        if path_part:
            changed.append(path_part)
    return changed


def latest_log_entry(root: Path) -> tuple[str, str] | None:
    """Return (op, title) of the most recent log entry today, or None."""
    today = date.today().strftime("%Y%m%d")
    log_path = root / "log" / f"{today}.md"
    if not log_path.exists():
        return None
    try:
        text = log_path.read_text(encoding="utf-8")
    except OSError:
        return None
    last: tuple[str, str] | None = None
    for line in text.splitlines():
        m = LOG_ENTRY_RE.match(line.strip())
        if m:
            last = (m.group(2), m.group(3))
    return last


def auto_message(root: Path, changed: list[str]) -> str:
    entry = latest_log_entry(root)
    if entry:
        op, title = entry
        return f"{op}: {title}"
    counts: dict[str, int] = {}
    for path in changed:
        head = path.split("/", 1)[0] if "/" in path else path
        counts[head] = counts.get(head, 0) + 1
    if counts:
        parts = ", ".join(f"{k} ({v})" for k, v in sorted(counts.items()))
        return f"wiki checkpoint: {parts}"
    return f"wiki checkpoint: {datetime.now().strftime('%Y-%m-%d %H:%M')}"


def run_git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True, text=True, check=False,
    )


def make_checkpoint_tag(root: Path, op: str) -> str | None:
    """Tag HEAD as `ckpt/<op>/<timestamp>` (lightweight). Best-effort — returns
    the tag name, or None if git tagging failed. A tag is a ref; it never
    rewrites history and is cheap to delete later (see rollback_wiki.py)."""
    op_slug = re.sub(r"[^A-Za-z0-9_-]", "-", op).strip("-") or "checkpoint"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = f"ckpt/{op_slug}/{stamp}"
    for suffix in ("", *(f"-{n}" for n in range(1, 20))):
        name = base + suffix
        if run_git(root, "tag", name).returncode == 0:
            return name
    return None


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Checkpoint a wiki's truth-source state into git.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("wiki_root", help="Path to the wiki root")
    parser.add_argument(
        "--message", "-m", default=None,
        help="Commit message (default: parsed from latest log/<today>.md entry)",
    )
    parser.add_argument(
        "--include-derived", action="store_true",
        help="Also stage graph/ and .query-index/wiki.db (default: truth-source only)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be staged + the message, don't actually commit",
    )
    parser.add_argument(
        "--no-tag", action="store_true",
        help="Don't tag the commit as ckpt/<op>/<timestamp> (default: tag it)",
    )
    args = parser.parse_args(argv)

    root = Path(args.wiki_root).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        print(f"error: wiki root not found: {root}", file=sys.stderr)
        return 2

    if not is_git_repo(root):
        print(f"skipped: {root} is not a git repository (commit is a no-op)", file=sys.stderr)
        return 0

    if wiki_is_gitignored(root):
        print(
            f"skipped: {root}/wiki is gitignored by the enclosing repo "
            "(this wiki is intentionally untracked — commit is a no-op)",
            file=sys.stderr,
        )
        return 0

    paths = TRUTH_PATHS + (DERIVED_PATHS if args.include_derived else [])
    paths = existing_paths(root, paths)
    if args.include_derived:
        # Warn if the derived paths are gitignored — `git add` will skip them silently
        # unless the user has uncommented those lines in .gitignore (or passes -f).
        derived_ignored = [
            p for p in DERIVED_PATHS
            if (root / p.rstrip("/")).exists() and _is_path_gitignored(root, p)
        ]
        if derived_ignored:
            print(
                f"note: --include-derived requested but these paths are gitignored "
                f"(uncomment them in .gitignore to track): {', '.join(derived_ignored)}",
                file=sys.stderr,
            )
    if not paths:
        print("skipped: no truth-source paths exist yet", file=sys.stderr)
        return 0

    changed = changed_under(root, paths)
    if not changed:
        print("skipped: nothing to commit (wiki state is clean)", file=sys.stderr)
        return 0

    message = args.message or auto_message(root, changed)

    if args.dry_run:
        print("would stage these paths:")
        for p in changed:
            print(f"  + {p}")
        print(f"would commit with message: {message!r}")
        return 0

    # Stage only the configured prefixes (unrelated uncommitted work stays put).
    add = run_git(root, "add", "--", *paths)
    if add.returncode != 0:
        print(f"error: git add failed:\n{add.stderr}", file=sys.stderr)
        return 3

    commit = run_git(root, "commit", "-m", message)
    if commit.returncode != 0:
        print(f"error: git commit failed:\n{commit.stdout}{commit.stderr}", file=sys.stderr)
        return 3

    show = run_git(root, "log", "-1", "--oneline")
    summary = show.stdout.strip() if show.returncode == 0 else "(couldn't show oneline)"
    print(f"✓ committed: {summary}")

    if not args.no_tag:
        op = (latest_log_entry(root) or ("checkpoint", ""))[0]
        tag = make_checkpoint_tag(root, op)
        if tag:
            print(f"  tagged {tag}")
        else:
            print("  note: couldn't create a checkpoint tag (commit is fine)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
