#!/usr/bin/env python3
"""
rollback_wiki.py — List and safely roll back wiki write/audit checkpoints.

Every write op checkpoints the wiki into git (commit_wiki.py) and tags it
`ckpt/<op>/<YYYYMMDD-HHMMSS>`. This tool reviews those checkpoints and undoes a
bad one WITHOUT ever rewriting history, resetting hard, or pushing:

    python3 rollback_wiki.py <wiki-root> --list [--days 30]
    python3 rollback_wiki.py <wiki-root> --undo <ref> [--dry-run]
    python3 rollback_wiki.py <wiki-root> --to   <ref> [--dry-run]
    python3 rollback_wiki.py <wiki-root> --prune-tags [--days 30] [--dry-run]

Modes:
  --list         Show checkpoints from the last N days (default 30): time, tag,
                 short hash, subject — newest first.
  --undo <ref>   Surgically undo exactly that one checkpoint's content changes
                 by 3-way applying its reverse patch as a new forward commit.
                 Every later checkpoint is kept, and the undo is itself
                 re-revertible. This is the safe default for "an audit polluted
                 the wiki, take just that change back".
  --to <ref>     Restore the whole content tree (SCHEMA.md / INTEREST.md /
                 wiki/ / raw/ / audit/) to that checkpoint's exact state and
                 commit it as a new forward "rollback to <ref>" checkpoint.
                 Use when many bad rounds accumulated and you want a clean point.
  --prune-tags   Delete ckpt/* TAGS older than N days (default 30). Tags only —
                 the commit history they pointed at is never touched, so you can
                 still reach those commits by hash.

Both --undo and --to roll back CONTENT only and never rewind `log/`: the log is
an append-only audit trail, so its forward record — including the fact that a
rollback happened — is always preserved (and reverting a middle log append
would just conflict with later ones). Add a fresh log entry for the rollback.

<ref> may be a checkpoint tag (from --list) OR any git commit hash, so even
un-tagged historical commits can be undone.

Both modes refuse to run if the truth-source tree has uncommitted changes
(commit or stash them first) — so a rollback can never silently eat work in
progress. Nothing here ever runs `git reset --hard`, rewrites history, or pushes.

Exit codes:
  0 — success (listed / undone / restored / pruned / dry-run)
  2 — invalid wiki root / args / not a git repo / dirty tree / bad ref
  3 — git operation failed (e.g. the undo overlapped a later change and left
      conflict markers — resolve them and commit, or `git checkout -- <paths>`)
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from commit_wiki import TRUTH_PATHS, is_git_repo, run_git, wiki_is_gitignored  # noqa: E402

TAG_PREFIX = "ckpt/"
# The empty tree — used as the "parent" when reverting the very first commit.
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
# We roll back CONTENT only. log/ is an append-only audit trail: rewinding it
# would erase the record of what happened (including that a rollback occurred),
# and reverting a middle append reliably conflicts with later appends. So a
# rollback never touches log/ — a fresh log entry documents it going forward.
CONTENT_PATHS = [p for p in TRUTH_PATHS if p != "log/"]


def _fetch_checkpoints(root: Path) -> list[tuple[int, str, str, str]]:
    """Return [(unix_time, tag, short_hash, subject)] for all ckpt/* tags, newest first."""
    res = run_git(
        root, "for-each-ref", "--sort=-creatordate",
        "--format=%(creatordate:unix)%09%(refname:short)%09%(objectname:short)%09%(contents:subject)",
        f"refs/tags/{TAG_PREFIX}",
    )
    if res.returncode != 0:
        return []
    rows: list[tuple[int, str, str, str]] = []
    for line in res.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        try:
            ts = int(parts[0])
        except ValueError:
            continue
        rows.append((ts, parts[1], parts[2], parts[3]))
    return rows


def _cutoff(days: int) -> float:
    return datetime.now().timestamp() - days * 86400


def cmd_list(root: Path, days: int) -> int:
    cutoff = _cutoff(days)
    rows = [r for r in _fetch_checkpoints(root) if r[0] >= cutoff]
    if not rows:
        print(f"No checkpoint tags in the last {days} days.")
        print("(Tags are created going forward by commit_wiki.py; older commits "
              "are still reachable by hash and can be passed to --undo/--to.)")
        return 0
    print(f"Checkpoints in the last {days} days (newest first):\n")
    print(f"{'when':<17}  {'hash':<9}  tag")
    for ts, tag, sha, subject in rows:
        when = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
        print(f"{when:<17}  {sha:<9}  {tag}")
        print(f"{'':<17}  {'':<9}  └ {subject}")
    print(f"\nUndo one:  rollback_wiki.py {root} --undo <tag-or-hash>")
    return 0


def _resolve_commit(root: Path, ref: str) -> str | None:
    res = run_git(root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    sha = res.stdout.strip()
    return sha or None


def _truth_tree_dirty(root: Path) -> bool:
    res = run_git(root, "status", "--porcelain", "--", *TRUTH_PATHS)
    return bool(res.stdout.strip())


def _guard(root: Path, ref: str) -> str | None:
    """Shared precheck for --undo/--to. Returns the resolved commit sha, or None
    on failure (after printing the reason)."""
    if _truth_tree_dirty(root):
        print("error: the wiki has uncommitted truth-source changes. Commit or "
              "stash them first — rollback won't run over a dirty tree.", file=sys.stderr)
        return None
    sha = _resolve_commit(root, ref)
    if not sha:
        print(f"error: '{ref}' is not a valid tag or commit in this repo.", file=sys.stderr)
        return None
    return sha


def _parent_or_empty(root: Path, sha: str) -> str:
    res = run_git(root, "rev-parse", "--verify", "--quiet", f"{sha}^")
    return res.stdout.strip() or EMPTY_TREE


def cmd_undo(root: Path, ref: str, dry_run: bool) -> int:
    sha = _guard(root, ref)
    if sha is None:
        return 2
    # Reverse patch = the diff that turns this commit's content back into its
    # parent's, restricted to content paths (log/ excluded by design).
    parent = _parent_or_empty(root, sha)
    diff = run_git(root, "diff", "--binary", sha, parent, "--", *CONTENT_PATHS)
    if diff.returncode != 0:
        print(f"error: couldn't compute the reverse patch:\n{diff.stderr}", file=sys.stderr)
        return 3
    if not diff.stdout.strip():
        print(f"nothing to undo — checkpoint {sha[:9]} changed no content files "
              "(it only touched log/, or was empty).")
        return 0
    if dry_run:
        files = run_git(root, "diff", "--name-only", sha, parent, "--", *CONTENT_PATHS).stdout.split()
        print(f"would revert {sha[:9]}'s changes to {len(files)} content file(s), "
              f"3-way applied, log/ left intact:")
        for f in files:
            print(f"  ~ {f}")
        return 0
    applied = subprocess.run(
        ["git", "-C", str(root), "apply", "--3way", "--index"],
        input=diff.stdout, capture_output=True, text=True,
    )
    if applied.returncode != 0:
        print(applied.stderr, file=sys.stderr)
        print("\nthe undo overlaps a later change and left conflict markers. Resolve "
              "the listed files, `git add` them and commit; or run "
              f"`git checkout -- {' '.join(CONTENT_PATHS)}` to abort (the tree was "
              "clean before this ran, so that fully backs it out).", file=sys.stderr)
        return 3
    commit = run_git(root, "commit", "-m", f"rollback: undo {ref} ({sha[:9]}) — content reverted, log kept")
    if commit.returncode != 0:
        print(f"error: commit failed:\n{commit.stdout}{commit.stderr}", file=sys.stderr)
        return 3
    head = run_git(root, "log", "-1", "--oneline").stdout.strip()
    print(f"✓ undid {sha[:9]} — new HEAD: {head}")
    print("  (a forward commit; itself re-revertible, and log/ history is intact)")
    return 0


def cmd_to(root: Path, ref: str, dry_run: bool) -> int:
    sha = _guard(root, ref)
    if sha is None:
        return 2
    if dry_run:
        print(f"would restore content paths to {sha[:9]} and commit a new "
              f"'rollback to {ref}' checkpoint (log/ kept). Paths: {', '.join(CONTENT_PATHS)}")
        return 0
    # Make the content subtree exactly match <sha>: drop current index entries,
    # repopulate from the target, then clean files that were added after it.
    # log/ is left untouched so the audit trail keeps its full forward record.
    run_git(root, "rm", "-r", "--cached", "--quiet", "--ignore-unmatch", "--", *CONTENT_PATHS)
    for path in CONTENT_PATHS:
        run_git(root, "checkout", sha, "--", path)  # per-path: missing-at-<sha> paths just no-op
    run_git(root, "clean", "-fdq", "--", *CONTENT_PATHS)
    if not _truth_tree_dirty(root):
        # Nothing changed — HEAD already matched the target. Undo the index churn.
        run_git(root, "reset", "--quiet", "HEAD", "--", *CONTENT_PATHS)
        print(f"already at {sha[:9]} for the content tree — nothing to roll back.")
        return 0
    run_git(root, "add", "--", *CONTENT_PATHS)
    commit = run_git(root, "commit", "-m", f"rollback: restore truth-source to {ref} ({sha[:9]})")
    if commit.returncode != 0:
        print(f"error: commit failed:\n{commit.stdout}{commit.stderr}", file=sys.stderr)
        return 3
    head = run_git(root, "log", "-1", "--oneline").stdout.strip()
    print(f"✓ rolled truth-source back to {sha[:9]} — new HEAD: {head}")
    return 0


def cmd_prune_tags(root: Path, days: int, dry_run: bool) -> int:
    cutoff = _cutoff(days)
    stale = [(tag, sha) for ts, tag, sha, _ in _fetch_checkpoints(root) if ts < cutoff]
    if not stale:
        print(f"No checkpoint tags older than {days} days.")
        return 0
    verb = "would delete" if dry_run else "deleting"
    print(f"{verb} {len(stale)} tag(s) older than {days} days (history untouched):")
    for tag, sha in stale:
        print(f"  - {tag} ({sha})")
        if not dry_run:
            run_git(root, "tag", "-d", tag)
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="List and safely roll back wiki checkpoints (git-native, no history rewrite).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("wiki_root", help="Path to the wiki root")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--list", action="store_true", help="List recent checkpoints")
    mode.add_argument("--undo", metavar="REF", help="Revert one checkpoint (tag or commit)")
    mode.add_argument("--to", metavar="REF", help="Restore whole truth-source tree to a checkpoint")
    mode.add_argument("--prune-tags", action="store_true", help="Delete ckpt/* tags older than --days")
    parser.add_argument("--days", type=int, default=30, help="Window for --list / --prune-tags (default: 30)")
    parser.add_argument("--dry-run", action="store_true", help="Show what would happen, change nothing")
    args = parser.parse_args(argv)

    root = Path(args.wiki_root).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        print(f"error: wiki root not found: {root}", file=sys.stderr)
        return 2
    if not is_git_repo(root):
        print(f"error: {root} is not a git repository — no checkpoints to roll back.", file=sys.stderr)
        return 2
    if wiki_is_gitignored(root):
        print(f"error: {root}/wiki is gitignored by the enclosing repo — it isn't "
              "version-tracked here, so there's nothing to roll back.", file=sys.stderr)
        return 2
    if args.days < 0:
        print("error: --days must be >= 0", file=sys.stderr)
        return 2

    if args.list:
        return cmd_list(root, args.days)
    if args.undo:
        return cmd_undo(root, args.undo, args.dry_run)
    if args.to:
        return cmd_to(root, args.to, args.dry_run)
    if args.prune_tags:
        return cmd_prune_tags(root, args.days, args.dry_run)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
