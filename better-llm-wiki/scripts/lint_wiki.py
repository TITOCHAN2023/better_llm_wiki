#!/usr/bin/env python3
"""
lint_wiki.py — Health check for an LLM Wiki.

Usage:
    python3 lint_wiki.py <wiki-root> [--full]

By default the script runs in **incremental mode**: it detects changed wiki
Markdown files itself and only re-lints those — no global graph rebuild, no
orphan/missing-index pass. This is the every-write closer used by ingest /
promote / audit flows.

Detection takes the union of two signals so nothing slips through:
  - `git status` catches uncommitted edits in the working tree
  - `.graph-cache/md-file-stats.graph` catches anything that changed since
    the last successful lint — including content pulled in via `git pull`,
    branch checkouts, or stashed work that was popped after a commit. The
    cache is refreshed whenever lint finishes.

Pass `--full` for the periodic full check (rebuilds graph/, runs orphan + missing
index + cross-page contradiction passes; honors the corpus scale hard limit).

Example:
    python3 lint_wiki.py <your knowledge path>/wikis/ai-research        # incremental (default)
    python3 lint_wiki.py <your knowledge path>/wikis/ai-research --full # periodic full lint

Checks:
  0a. Audit sidecar ingest — drain wiki/**/*.md.audit sidecars (written by
      external tools such as mino_server gateway auditWrite) into
      audit/<id>-<slug>.md and remove the sidecar.
  0b. Open audit preflight — if audit/*.md contains open feedback, stop and
      process the audit inbox before running the full lint/graph compile.
  1. MD scale guard — if wiki/**/*.md is too large for safe full-graph lint,
     stop before reading the corpus and ask the agent to split/archive/index.
  2. Banned ../ paths — links are wiki-root-relative; any href containing
     '../' is a convention violation regardless of whether the target
     file exists.
  3. Missing root prefix — inside wiki/ files, links must start with a
     valid wiki-root segment (wiki/, log/, raw/, audit/) so they stay
     stable across tools and renderers.
  4. Dead links — [text](path.md) whose resolved target doesn't exist
  5. Malformed link URLs — [text](url with spaces.md) that must be wrapped in
     <...> per CommonMark. Silently dropped by strict parsers (including the
     web viewer) — they'd otherwise manifest as phantom orphan / missing-index
     warnings with no hint at the root cause.
  6. Orphan pages — wiki pages with no inbound links
  7. Missing index entries — wiki pages not listed in wiki/index.md
  8. Frequently-missing targets — hrefs referenced 3+ times but no file exists
  9. Residual wikilinks — leftover [[...]] syntax (run migrate_wikilinks.py)
  10. log/ shape — every file matches YYYYMMDD.md and has the right H1
  11. audit/ shape — every audit/*.md parses as a valid AuditEntry
  12. Audit targets — every open audit's `target` file must exist
  13. Dead `sources:` refs — every wiki page's `sources:` frontmatter entry
      must resolve to a file under raw/ or wiki/ (external URLs skipped).
      Silently dropped by build_lineage_graph otherwise — typos vanish.
  14. Contradiction/staleness markers and heuristic numeric claim conflicts —
      non-fatal signals such as "待核实", "contradiction", or "outdated", plus
      fuzzy cross-page numeric claims (e.g. two different context-window
      values for the same subject). Written to graph/issues.graph for the
      agent to verify; they do not affect lint exit code.
  15. Legacy `.agraph` cleanup — remove old generated artifacts superseded by
      `.graph`.

Link conventions enforced:
  - All intra-wiki links use standard MD syntax: [text](relative/path.md).
  - Paths are wiki-root-relative and must begin with one of:
      wiki/   — wiki pages (concepts/entities/summaries/index)
      log/    — daily activity logs
      raw/    — source materials (papers, articles, references)
      audit/  — audit entries
    Examples: wiki/concepts/Foo.md, log/20260519.md, raw/papers/x.md.
    `../` is banned.
  - Paths with spaces may be wrapped in angle brackets: [text](<path with spaces.md>).

Exit codes:
  0 — no issues found
  1 — issues found (printed to stdout)
"""

import bisect
import argparse
import hashlib
import json
import os
import posixpath
import re
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path


# Matches [text](href) and ![alt](src). href/src may be wrapped in <...>
# (that's how paths containing spaces travel in strict CommonMark).
# We only keep those whose path ends with `.md` (anchor stripped downstream).
MD_LINK_RE = re.compile(
    r"""
    !?\[[^\]\n]*\]\(
        \s*
        (?:
            <(?P<bracketed>[^>\n]+?\.md(?:\#[^>\n]*)?)>
            |
            (?P<bare>[^()\s]+?\.md(?:\#[^()\s]*)?)
        )
        \s*
    \)
    """,
    re.VERBOSE,
)
MD_LINK_WITH_TEXT_RE = re.compile(
    r"""
    (?P<bang>!)?\[(?P<label>[^\]\n]*)\]\(
        \s*
        (?:
            <(?P<bracketed>[^>\n]+?\.md(?:\#[^>\n]*)?)>
            |
            (?P<bare>[^()\s]+?\.md(?:\#[^()\s]*)?)
        )
        \s*
    \)
    """,
    re.VERBOSE,
)
# Detects `[text](url with spaces.md)` — URL contains unescaped whitespace but
# is not wrapped in <...>. Strict CommonMark drops these silently (see
# MD_LINK_RE's `bare` branch, which forbids whitespace), producing phantom
# orphan / missing-index warnings downstream. This pattern explicitly hunts
# for the malformed shape so we can flag it with a root-cause message.
MALFORMED_LINK_RE = re.compile(
    r"""
    !?\[[^\]\n]*\]\(
        \s*
        (?!<)                               # skip the legal bracketed form
        (?P<url>
            [^)<>\n]*?                      # URL body (no parens/angles/newlines)
            \s                              # at least one raw whitespace char
            [^)<>\n]*?
            \.md
            (?:\#[^)<>\n]*)?                # optional #anchor
        )
        \s*
    \)
    """,
    re.VERBOSE,
)
# Detects raw `../foo/bar.md` path literals anywhere in the file, including
# malformed markdown links, JSON payloads, code blocks, and prose. This is the
# hard-rule check for "never write file-relative parent hops inside wiki/".
BANNED_PARENT_PATH_RE = re.compile(
    r"""
    (?P<path>
        \.\./
        [^<>"'\n)]*?
        \.md
        (?:\#[^<>"'\n)]*)?
    )
    """,
    re.VERBOSE,
)
# Detects raw absolute filesystem paths to markdown files, e.g.
# `/Users/name/wiki/entities/Foo.md`. These are banned inside wiki/ files;
# paths must be written relative to the wiki/ root instead.
ABSOLUTE_MD_PATH_RE = re.compile(
    r"""
    (?<![A-Za-z0-9._-])
    (?P<path>
        /
        [^<>"'\n)]*?
        \.md
        (?:\#[^<>"'\n)]*)?
    )
    """,
    re.VERBOSE,
)
# Leftover legacy wikilink — kept only to warn users to migrate.
RESIDUAL_WIKILINK_RE = re.compile(r"\[\[[^\]\n]+?\]\]")
LOG_FILENAME_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})\.md$")
FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
EXTERNAL_URL_RE = re.compile(r"^[a-z][a-z0-9+.\-]*:", re.IGNORECASE)
LOG_ENTRY_RE = re.compile(r"^## \[(?P<time>\d{2}:\d{2})\]\s+(?P<op>[a-zA-Z0-9_-]+)\s+\|\s+(?P<title>.+?)\s*$")
WIKI_MD_LITERAL_RE = re.compile(r"(?P<path>wiki/[^\s`<>\)]*?\.md)")
BACKTICK_WIKI_MD_LITERAL_RE = re.compile(r"`(?P<path>wiki/[^`\n]+?\.md)`")

AUDIT_REQUIRED_FIELDS = {
    "id", "target", "type", "start", "end",
    "author", "source", "created", "status",
}
VALID_AUDIT_TYPES = {"info", "suggest", "warn", "error"}
VALID_STATUSES = {"open", "resolved"}
VALID_SOURCES = {"web-viewer", "manual", "gateway", "cli"}
EXPECTED_LINK_SECTIONS = {
    "used in",
    "related pages",
    "related concepts",
    "links into the wiki",
    "sub-pages",
    "concepts introduced / referenced",
}
INDEX_LINK_SECTIONS = {
    "concepts",
    "entities",
    "summaries",
    "summaries (chronological)",
}
CONTRADICTION_MARKER_RE = re.compile(
    r"""
    \b(?:contradict(?:ion|s|ory)?|inconsistent|stale|outdated)\b
    |
    \b(?:needs\s+verification|needs\s+source|todo:?\s*verify|verify\s+later|fact[-\s]?check)\b
    |
    矛盾|不一致|过时|陈旧|待核实|需验证|需要验证|事实错误|口径不一致
    """,
    re.IGNORECASE | re.VERBOSE,
)
NUMERIC_CLAIM_RE = re.compile(
    r"""
    (?P<subject>
        [A-Z][A-Za-z0-9_.+-]*(?:\s+[A-Z][A-Za-z0-9_.+-]*){0,8}
        |
        [\u3400-\u9fffA-Za-z0-9_.+-]{2,40}
    )
    \s+
    (?P<predicate>
        supports?|uses?|has|requires?|is|are|was|were|equals?|reaches?|achieves?
        |
        支持|使用|采用|需要|要求|是|为|等于|达到|拥有
    )
    \s+
    (?P<value>
        [^.\n;。；]{0,40}?
        \d+(?:\.\d+)?\s*(?:k|m|b|kb|mb|gb|tb|tokens?|token|context\s+window|参数|上下文|窗口|%|％)
        [^.\n;。；]{0,40}
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)
CLAIM_KIND_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("context_window", re.compile(r"context\s+window|上下文|窗口", re.IGNORECASE)),
    ("parameters", re.compile(r"parameters?|params?|参数", re.IGNORECASE)),
    ("tokens", re.compile(r"tokens?|token", re.IGNORECASE)),
    ("storage", re.compile(r"\b(?:kb|mb|gb|tb)\b", re.IGNORECASE)),
    ("percent", re.compile(r"%|％")),
)


def env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


MD_WARN_BYTES = env_int("LLM_WIKI_MD_WARN_MB", 50) * 1024 * 1024
MD_HARD_BYTES = env_int("LLM_WIKI_MD_HARD_MB", 1024) * 1024 * 1024
MD_PAGE_WARN_BYTES = env_int("LLM_WIKI_PAGE_WARN_KB", 512) * 1024
MD_PAGE_HARD_BYTES = env_int("LLM_WIKI_PAGE_HARD_MB", 16) * 1024 * 1024

# Page-COUNT guard. Full-graph lint holds per-page structures (preloaded text,
# node/edge/adjacency/reverse-link maps, sidecar payloads) in memory at once, so
# its peak RSS scales with the number of pages READ this run — roughly ~30 KB per
# page measured on synthetic corpora — NOT with total bytes. A wiki of many tiny
# pages therefore OOMs long before the byte hard-stop. These bound the count of
# pages a single run will load. Above the hard limit the run refuses and points
# the user at the incremental + query/index workflow (the intended path at scale).
MD_MAX_PAGES_WARN = env_int("LLM_WIKI_MAX_PAGES_WARN", 25000)
MD_MAX_PAGES_HARD = env_int("LLM_WIKI_MAX_PAGES_HARD", 80000)


def extract_md_link_hrefs(text: str) -> list[str]:
    """Return every MD-link href pointing at a .md file (anchor stripped).
    External URLs (http:, mailto:, etc.) are filtered out."""
    out: list[str] = []
    for m in MD_LINK_RE.finditer(text):
        href = (m.group("bracketed") or m.group("bare") or "").strip()
        if "#" in href:
            href = href.split("#", 1)[0]
        if not href or EXTERNAL_URL_RE.match(href):
            continue
        out.append(href)
    return out


def build_line_starts(text: str) -> list[int]:
    """Return 0-based offsets where each line starts."""
    starts = [0]
    idx = text.find("\n")
    while idx != -1:
        starts.append(idx + 1)
        idx = text.find("\n", idx + 1)
    return starts


def line_number_from_offset(line_starts: list[int], offset: int) -> int:
    """Convert a 0-based offset into a 1-based line number."""
    return bisect.bisect_right(line_starts, offset)


def extract_md_link_hrefs_with_lines(
    text: str,
    line_starts: list[int],
) -> list[tuple[int, str]]:
    """Return every MD-link href pointing at a .md file with its 1-based line."""
    out: list[tuple[int, str]] = []
    for m in MD_LINK_RE.finditer(text):
        href = (m.group("bracketed") or m.group("bare") or "").strip()
        if "#" in href:
            href = href.split("#", 1)[0]
        if not href or EXTERNAL_URL_RE.match(href):
            continue
        line_no = line_number_from_offset(line_starts, m.start())
        out.append((line_no, href))
    return out


def format_bytes(value: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(max(0, value))
    for unit in units:
        if size < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{value} B"


def collect_wiki_md_scale(wiki_path: Path) -> dict[str, object]:
    files: list[Path] = []
    largest: list[tuple[int, Path]] = []
    stats: dict[str, dict[str, int]] = {}
    stat_errors: list[tuple[Path, str]] = []
    total_bytes = 0

    for path in sorted(wiki_path.rglob("*.md")):
        resolved = path.resolve()
        try:
            stat = resolved.stat()
        except OSError as exc:
            stat_errors.append((resolved, str(exc)))
            continue
        size = stat.st_size
        files.append(resolved)
        total_bytes += size
        largest.append((size, resolved))
        stats[resolved.relative_to(wiki_path.parent).as_posix()] = {
            "size": size,
            "mtimeNs": stat.st_mtime_ns,
        }

    largest.sort(key=lambda item: item[0], reverse=True)
    return {
        "files": files,
        "totalBytes": total_bytes,
        "largest": largest[:20],
        "stats": stats,
        "largePages": [(size, path) for size, path in largest if size >= MD_PAGE_WARN_BYTES],
        "hugePages": [(size, path) for size, path in largest if size >= MD_PAGE_HARD_BYTES],
        "statErrors": stat_errors,
    }


def scale_issue_payload(root_path: Path, scale: dict[str, object]) -> dict[str, object]:
    large_pages = scale.get("largePages", [])
    if not isinstance(large_pages, list):
        large_pages = []
    return {
        "totalBytes": int(scale.get("totalBytes") or 0),
        "fileCount": len(scale.get("files", [])) if isinstance(scale.get("files"), list) else 0,
        "warnBytes": MD_WARN_BYTES,
        "hardBytes": MD_HARD_BYTES,
        "pageWarnBytes": MD_PAGE_WARN_BYTES,
        "pageHardBytes": MD_PAGE_HARD_BYTES,
        "largePages": [
            {"path": path.relative_to(root_path).as_posix(), "bytes": size}
            for size, path in large_pages[:50]
        ],
    }


def print_scale_preflight(
    root_path: Path,
    scale: dict[str, object],
    *,
    total_hard_stop: bool = True,
    page_scope: set[Path] | None = None,
) -> bool:
    stat_errors = scale.get("statErrors", [])
    if isinstance(stat_errors, list) and stat_errors:
        print(f"\n🔴 MD scale preflight could not stat {len(stat_errors)} file(s):")
        for path, error in stat_errors[:20]:
            print(f"   {path.relative_to(root_path)} — {error}")
        if len(stat_errors) > 20:
            print(f"   … and {len(stat_errors) - 20} more")
        return False

    total_bytes = int(scale.get("totalBytes") or 0)
    files = scale.get("files", [])
    file_count = len(files) if isinstance(files, list) else 0
    huge_pages = scale.get("hugePages", [])
    large_pages = scale.get("largePages", [])
    if not isinstance(huge_pages, list):
        huge_pages = []
    if not isinstance(large_pages, list):
        large_pages = []
    if page_scope is not None:
        huge_pages = [(size, path) for size, path in huge_pages if path in page_scope]
        large_pages = [(size, path) for size, path in large_pages if path in page_scope]

    # Pages actually loaded this run: the changed scope when incremental,
    # else the whole corpus. Memory scales with THIS count, so the page-count
    # guard applies in both modes (it also catches a cold first lint that has
    # no stat cache yet and would otherwise read-all).
    read_count = len(page_scope) if page_scope is not None else file_count

    hard_reasons: list[str] = []
    if read_count >= MD_MAX_PAGES_HARD:
        hard_reasons.append(
            f"{read_count} page(s) would be loaded this run "
            f"(hard stop {MD_MAX_PAGES_HARD}; peak RSS scales ~30 KB/page)"
        )
    if total_hard_stop and total_bytes >= MD_HARD_BYTES:
        hard_reasons.append(
            f"wiki/**/*.md totals {format_bytes(total_bytes)} "
            f"(hard stop {format_bytes(MD_HARD_BYTES)})"
        )
    if huge_pages:
        hard_reasons.append(
            f"{len(huge_pages)} page(s) exceed {format_bytes(MD_PAGE_HARD_BYTES)}"
        )

    if hard_reasons:
        print("\n🔴 MD scale guard stopped this lint before reading the corpus:")
        for reason in hard_reasons:
            print(f"   {reason}")
        for size, path in huge_pages[:10]:
            print(f"   huge page: {path.relative_to(root_path)} — {format_bytes(size)}")
        print("   fix: split oversized pages, archive cold material, or keep raw/source bulk outside wiki/.")
        print("   use query/index workflows for large corpora; full graph lint is intentionally bounded.")
        print(
            "   knobs: LLM_WIKI_MAX_PAGES_HARD, LLM_WIKI_MAX_PAGES_WARN, "
            "LLM_WIKI_MD_HARD_MB, LLM_WIKI_MD_WARN_MB, "
            "LLM_WIKI_PAGE_HARD_MB, LLM_WIKI_PAGE_WARN_KB"
        )
        return False

    if total_bytes >= MD_WARN_BYTES or large_pages or read_count >= MD_MAX_PAGES_WARN:
        print(
            f"\n🟡 MD scale warning: {file_count} file(s), "
            f"{format_bytes(total_bytes)} total"
        )
        if read_count >= MD_MAX_PAGES_WARN:
            print(
                f"   {read_count} page(s) loaded this run "
                f"(warn {MD_MAX_PAGES_WARN}, hard {MD_MAX_PAGES_HARD}) — "
                f"prefer incremental lint + query/index at this scale"
            )
        if total_bytes >= MD_WARN_BYTES:
            print(f"   warn threshold: {format_bytes(MD_WARN_BYTES)}")
        if large_pages:
            print(f"   large pages over {format_bytes(MD_PAGE_WARN_BYTES)}:")
            for size, path in large_pages[:10]:
                print(f"   {path.relative_to(root_path)} — {format_bytes(size)}")
            if len(large_pages) > 10:
                print(f"   … and {len(large_pages) - 10} more")
        print("   keep pages small and move bulk evidence into raw/ or source-specific summaries.")
    else:
        print(f"✅ MD scale OK ({file_count} file(s), {format_bytes(total_bytes)})")
    return True


def md_stat_cache_path(root_path: Path) -> Path:
    return root_path / ".graph-cache" / "md-file-stats.graph"


def write_md_stat_cache(root_path: Path, scale: dict[str, object]) -> None:
    stats = scale.get("stats")
    if isinstance(stats, dict):
        atomic_write_json(md_stat_cache_path(root_path), stats)


def git_changed_wiki_files(root_path: Path) -> tuple[list[Path], list[str], str] | None:
    try:
        # If wiki/ is ignored by the enclosing repo (common for vendored
        # demo wikis or sub-tree knowledge bases), git status will silently
        # report no changes regardless of actual edits. Defer to stat cache.
        ignored = subprocess.run(
            ["git", "-C", str(root_path), "check-ignore", "--quiet", "wiki"],
            check=False, capture_output=True,
        )
        if ignored.returncode == 0:
            return None
        result = subprocess.run(
            [
                "git", "-C", str(root_path),
                "status", "--porcelain=v1", "-z", "--untracked-files=all",
                "--", "wiki",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None

    changed: list[Path] = []
    deleted: list[str] = []
    entries = [entry for entry in result.stdout.split("\0") if entry]
    idx = 0
    while idx < len(entries):
        raw_entry = entries[idx]
        idx += 1
        if len(raw_entry) < 4:
            continue
        status = raw_entry[:2]
        rel = raw_entry[3:].strip()
        if status[0] in {"R", "C"} or status[1] in {"R", "C"}:
            idx += 1  # porcelain -z stores the old path as the next NUL entry.
        if not rel.startswith("wiki/") or not rel.endswith(".md"):
            continue
        path = (root_path / rel).resolve()
        if "D" in status and not path.exists():
            deleted.append(rel)
        elif path.exists():
            changed.append(path)

    return sorted(set(changed)), sorted(set(deleted)), "git status"


def cached_changed_wiki_files(
    root_path: Path,
    scale: dict[str, object],
) -> tuple[list[Path], list[str], str]:
    stats = scale.get("stats")
    if not isinstance(stats, dict):
        return [], [], "stat cache"

    old = load_json_file(md_stat_cache_path(root_path), {})
    if not isinstance(old, dict):
        old = {}

    changed: list[Path] = []
    deleted = sorted(str(rel) for rel in old.keys() if str(rel) not in stats)
    for rel, meta in stats.items():
        if not isinstance(meta, dict):
            continue
        old_meta = old.get(rel)
        if not isinstance(old_meta, dict):
            changed.append((root_path / str(rel)).resolve())
            continue
        if old_meta.get("size") != meta.get("size") or old_meta.get("mtimeNs") != meta.get("mtimeNs"):
            changed.append((root_path / str(rel)).resolve())

    return sorted(set(changed)), deleted, "stat cache"


def changed_wiki_files(
    root_path: Path,
    scale: dict[str, object],
) -> tuple[list[Path], list[str], str]:
    """Return the union of git-status and stat-cache change detection.

    Neither signal alone is complete:
      - `git status` misses anything already committed — notably content
        pulled in via `git pull` or brought in by a branch switch. After
        such an event the working tree is clean, but the wiki has changed
        relative to the last lint.
      - Stat cache misses brand-new files when the cache is empty (first run)
        and can't tell a deletion from a removed entry on its own.

    Taking the union is conservative — at worst we re-lint a file that didn't
    really change, which is cheap and idempotent. Under-reporting (the old
    git-only behaviour) silently dropped post-pull changes from incremental
    lint, which then propagated into `ingest_scan.py` and the
    page-local `.md.graph` sidecars going stale.
    """
    cache_changed, cache_deleted, _ = cached_changed_wiki_files(root_path, scale)
    git_result = git_changed_wiki_files(root_path)
    if git_result is None:
        return cache_changed, cache_deleted, "stat cache"

    git_changed, git_deleted, _ = git_result
    changed = sorted({p for p in git_changed} | {p for p in cache_changed})
    deleted = sorted(set(git_deleted) | set(cache_deleted))

    if git_changed and cache_changed:
        source = "git status + stat cache"
    elif cache_changed and not git_changed:
        # Working tree clean but cache says files moved — typical post-pull /
        # post-checkout state. Surface the cause so the operator isn't
        # confused by lint flagging changes git doesn't see.
        source = "stat cache (post-pull / checkout)"
    else:
        source = "git status"
    return changed, deleted, source


def extract_contradiction_markers(text: str) -> list[tuple[int, str, str]]:
    out: list[tuple[int, str, str]] = []
    in_fence = False
    in_frontmatter = text.split("\n", 1)[0].rstrip("\r") == "---"
    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        stripped = raw_line.strip()
        if in_frontmatter:
            if line_no > 1 and stripped == "---":
                in_frontmatter = False
            continue
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence or not stripped or stripped.startswith("#"):
            continue
        match = CONTRADICTION_MARKER_RE.search(raw_line)
        if not match:
            continue
        excerpt = re.sub(r"\s+", " ", stripped)
        out.append((line_no, match.group(0), excerpt[:180]))
    return out


def claim_kind(value: str) -> str:
    for kind, pattern in CLAIM_KIND_PATTERNS:
        if pattern.search(value):
            return kind
    return "numeric"


def normalize_claim_subject(subject: str) -> str:
    return re.sub(r"\s+", " ", subject.strip().lower())


def normalize_claim_predicate(predicate: str) -> str:
    raw = predicate.strip().lower()
    aliases = {
        "support": "supports",
        "supports": "supports",
        "supported": "supports",
        "支持": "supports",
        "use": "uses",
        "uses": "uses",
        "used": "uses",
        "使用": "uses",
        "采用": "uses",
        "has": "has",
        "拥有": "has",
        "require": "requires",
        "requires": "requires",
        "需要": "requires",
        "要求": "requires",
        "is": "is",
        "are": "is",
        "was": "is",
        "were": "is",
        "是": "is",
        "为": "is",
        "equals": "equals",
        "equal": "equals",
        "等于": "equals",
        "reaches": "reaches",
        "reach": "reaches",
        "achieves": "reaches",
        "achieve": "reaches",
        "达到": "reaches",
    }
    return aliases.get(raw, raw)


def normalize_claim_value(value: str) -> str:
    compact = re.sub(r"\s+", "", value.strip().lower())
    compact = compact.replace("，", ",").replace("％", "%")
    match = re.search(r"\d+(?:\.\d+)?\s*(?:k|m|b|kb|mb|gb|tb|tokens?|token|参数|上下文|窗口|%)?", value, re.IGNORECASE)
    if not match:
        return compact
    return re.sub(r"\s+", "", match.group(0).lower())


def extract_numeric_claims(text: str) -> list[tuple[int, str, str, str, str, str]]:
    out: list[tuple[int, str, str, str, str, str]] = []
    in_fence = False
    in_frontmatter = text.split("\n", 1)[0].rstrip("\r") == "---"
    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        stripped = raw_line.strip()
        if in_frontmatter:
            if line_no > 1 and stripped == "---":
                in_frontmatter = False
            continue
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence or not stripped or stripped.startswith("#"):
            continue
        for match in NUMERIC_CLAIM_RE.finditer(stripped):
            subject = normalize_claim_subject(match.group("subject"))
            predicate = normalize_claim_predicate(match.group("predicate"))
            value_text = match.group("value").strip()
            kind = claim_kind(value_text)
            value = normalize_claim_value(value_text)
            excerpt = re.sub(r"\s+", " ", stripped)[:180]
            out.append((line_no, subject, predicate, kind, value, excerpt))
    return out


def find_contradictory_claims(claims: list[dict[str, object]]) -> list[dict[str, object]]:
    grouped: dict[tuple[str, str, str], dict[str, list[dict[str, object]]]] = defaultdict(lambda: defaultdict(list))
    for claim in claims:
        key = (
            str(claim["subject"]),
            str(claim["predicate"]),
            str(claim["kind"]),
        )
        grouped[key][str(claim["value"])].append(claim)

    contradictions: list[dict[str, object]] = []
    for (subject, predicate, kind), by_value in grouped.items():
        if len(by_value) < 2:
            continue
        values = sorted(by_value)
        evidence = []
        for value in values:
            for claim in by_value[value][:3]:
                evidence.append({
                    "value": value,
                    "source": claim["source"],
                    "line": claim["line"],
                    "excerpt": claim["excerpt"],
                })
        contradictions.append({
            "subject": subject,
            "predicate": predicate,
            "kind": kind,
            "values": values,
            "evidence": evidence,
        })
    contradictions.sort(key=lambda item: (str(item["subject"]), str(item["predicate"]), str(item["kind"])))
    return contradictions


def extract_malformed_link_urls(
    text: str,
    line_starts: list[int],
) -> list[tuple[int, str]]:
    """Return every `[text](url)` where the URL has raw whitespace but is not
    wrapped in <...>. Each item is (line_number, offending_url).
    These are CommonMark-invalid and invisible to MD_LINK_RE — we surface them
    so users aren't debugging phantom orphan warnings."""
    out: list[tuple[int, str]] = []
    for m in MALFORMED_LINK_RE.finditer(text):
        url = m.group("url").strip()
        if EXTERNAL_URL_RE.match(url):
            continue
        line_no = line_number_from_offset(line_starts, m.start())
        out.append((line_no, url))
    return out


def extract_banned_parent_paths(
    text: str,
    line_starts: list[int],
) -> list[tuple[int, str]]:
    """Return every raw `../...md` path literal, regardless of context.
    Each item is (line_number, offending_path)."""
    out: list[tuple[int, str]] = []
    for m in BANNED_PARENT_PATH_RE.finditer(text):
        path = (m.group("path") or "").strip()
        if not path:
            continue
        line_no = line_number_from_offset(line_starts, m.start())
        out.append((line_no, path))
    return out


def extract_absolute_md_paths(
    text: str,
    line_starts: list[int],
) -> list[tuple[int, str]]:
    """Return every raw absolute `/...md` path literal.
    External URLs are filtered out elsewhere; this scanner is for filesystem
    paths embedded in markdown, JSON payloads, code blocks, or prose."""
    out: list[tuple[int, str]] = []
    for m in ABSOLUTE_MD_PATH_RE.finditer(text):
        path = (m.group("path") or "").strip()
        if not path or EXTERNAL_URL_RE.match(path):
            continue
        line_no = line_number_from_offset(line_starts, m.start())
        out.append((line_no, path))
    return out


def has_md_link(text: str) -> bool:
    """True if the text contains at least one markdown link to anything."""
    return re.search(r"!\[[^\]\n]*\]\(|\[[^\]\n]+\]\(", text) is not None


def extract_non_link_bullets(
    text: str,
    rel_path: Path,
) -> list[tuple[int, str, str]]:
    """Return bullets inside link-expected sections that do not contain MD links.
    Each item is (line_number, section_title, bullet_text)."""
    out: list[tuple[int, str, str]] = []
    current_section = ""
    in_fence = False

    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        stripped = raw_line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue

        heading = re.match(r"^(#{1,6})\s+(.+?)\s*$", stripped)
        if heading:
            current_section = heading.group(2).strip().lower()
            continue

        bullet = re.match(r"^\s*[-*]\s+(.*)$", raw_line)
        if not bullet:
            continue

        bullet_text = bullet.group(1).strip()
        if not bullet_text:
            continue
        if bullet_text.startswith("[ ]") or bullet_text.startswith("[x]") or bullet_text.startswith("[X]"):
            continue

        expects_link = False
        if rel_path.as_posix() == "wiki/index.md":
            expects_link = current_section in INDEX_LINK_SECTIONS
        else:
            expects_link = current_section in EXPECTED_LINK_SECTIONS

        if expects_link and not has_md_link(bullet_text):
            out.append((line_no, current_section, bullet_text))

    return out


# Top-level directories that markdown links inside the wiki may legally
# reference. All link paths are wiki-root-relative; they must begin with one
# of these segments. Anything else is either a relative-path hack (`../`),
# a missing-prefix typo (`concepts/Foo.md`), or a pointer outside the
# wiki tree.
VALID_ROOT_PREFIXES: tuple[str, ...] = ("wiki/", "log/", "raw/", "audit/")


def resolve_href(root_path: Path, href: str) -> Path | None:
    """Resolve a wiki-internal href to an absolute Path.

    Two accepted forms:
      • OKF content-root anchor `/foo/bar.md` — the leading `/` anchors at the
        content root (`<root>/wiki/`), so `/entities/X.md` → `wiki/entities/X.md`.
      • Legacy root-relative `wiki/…`, `log/…`, `raw/…`, `audit/…`.
    `../` traversal is meaningless under both forms and never resolves.
    Returns the resolved absolute Path if the file exists under the wiki
    root, else None."""
    if href.startswith("/"):
        # Content-root anchor: leading `/` == `<root>/wiki/`.
        joined = posixpath.normpath("wiki" + href)
    else:
        joined = posixpath.normpath(href)
        if joined.startswith("../") or joined == "..":
            return None
        if not any(joined.startswith(p) for p in VALID_ROOT_PREFIXES):
            return None
    target = root_path / Path(joined)
    root_resolved = root_path.resolve()
    if target.exists() and target.is_file():
        try:
            target.resolve().relative_to(root_resolved)
        except ValueError:
            return None
        return target.resolve()
    return None


def canonicalize_href_with_wiki_prefix(href: str) -> str | None:
    """Default auto-suggest for a missing root prefix — prepend `wiki/`
    since it's the most common case. Returns None for paths that already
    start with a valid root segment or use ../ traversal."""
    joined = posixpath.normpath(href)
    if joined in (".", ".."):
        return None
    if joined.startswith("../"):
        return None
    if any(joined.startswith(p) for p in VALID_ROOT_PREFIXES):
        return None
    return posixpath.join("wiki", joined)


def canonicalize_absolute_path(root_path: Path, abs_path: str) -> str | None:
    """Convert an absolute filesystem markdown path into root-relative
    `wiki/...` form when it points inside `<root>/wiki/`."""
    try:
        p = Path(abs_path).resolve()
    except OSError:
        return None
    wiki_dir = (root_path / "wiki").resolve()
    try:
        return posixpath.join("wiki", p.relative_to(wiki_dir).as_posix())
    except ValueError:
        return None


def parse_frontmatter(text: str) -> dict | None:
    """Minimal YAML-ish frontmatter parser. Handles the flat key:value fields
    and one-level lists/arrays actually used by audit files. Does not handle
    arbitrary YAML — intentional, to avoid a pyyaml dependency."""
    m = FRONTMATTER_RE.match(text)
    if not m:
        return None
    body = m.group(1)
    result: dict = {}
    i = 0
    lines = body.split("\n")
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#"):
            i += 1
            continue
        if ":" not in line:
            i += 1
            continue
        key, _, rest = line.partition(":")
        key = key.strip()
        val = rest.strip()
        if val.startswith("[") and val.endswith("]"):
            inner = val[1:-1].strip()
            if not inner:
                result[key] = []
            else:
                parts = [p.strip() for p in inner.split(",")]
                parsed: list = []
                for p in parts:
                    if p.isdigit() or (p.startswith("-") and p[1:].isdigit()):
                        parsed.append(int(p))
                    else:
                        parsed.append(p.strip('"').strip("'"))
                result[key] = parsed
        elif val.startswith('"') and val.endswith('"'):
            result[key] = val[1:-1].replace("\\n", "\n").replace('\\"', '"')
        elif val.startswith("'") and val.endswith("'"):
            result[key] = val[1:-1]
        else:
            result[key] = val
        i += 1
    return result


def strip_frontmatter(text: str) -> str:
    m = FRONTMATTER_RE.match(text)
    if not m:
        return text
    return text[m.end():]


def extract_page_title(text: str, fallback: str) -> str:
    fm = parse_frontmatter(text) or {}
    title = fm.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()
    for line in strip_frontmatter(text).splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return fallback


def extract_page_summary(text: str) -> str:
    body = strip_frontmatter(text)
    lines: list[str] = []
    in_fence = False
    for raw_line in body.splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if not stripped:
            if lines:
                break
            continue
        if stripped.startswith("#"):
            continue
        if stripped.startswith("![") and stripped.endswith(")"):
            continue
        lines.append(stripped)
        if len(" ".join(lines)) >= 200:
            break
    summary = re.sub(r"\s+", " ", " ".join(lines)).strip()
    return summary[:200].rstrip() if summary else ""


def ensure_string_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def infer_page_kind(rel_id: str, frontmatter: dict) -> str:
    page_type = frontmatter.get("type")
    if isinstance(page_type, str) and page_type.strip():
        return page_type.strip()
    parts = Path(rel_id).parts
    if len(parts) >= 3:
        group = parts[1]
        if group == "concepts":
            return "concept"
        if group == "entities":
            return "entity"
        if group == "summaries":
            return "summary"
    return "page"


def stem_without_md(filename: str) -> str:
    return filename[:-3] if filename.endswith(".md") else filename


def path_segment_name(segment: str) -> str:
    return stem_without_md(segment)


def build_display_metadata(rel_id: str, title: str) -> dict[str, object]:
    parts = rel_id.split("/")
    file_name = parts[-1] if parts else rel_id
    stem = stem_without_md(file_name)
    parent_name = parts[-2] if len(parts) >= 2 else ""

    if file_name == "index.md":
        if parent_name and parent_name != "wiki":
            fallback = path_segment_name(parent_name)
        else:
            fallback = title or "Index"
    else:
        fallback = stem

    display_name = title.strip() if title.strip() else fallback
    short_name = display_name

    breadcrumb_parts: list[str] = []
    for part in parts:
        if part == "index.md":
            continue
        breadcrumb_parts.append(path_segment_name(part))
    if file_name != "index.md":
        breadcrumb_parts[-1] = display_name
    elif not breadcrumb_parts:
        breadcrumb_parts = [display_name]
    elif breadcrumb_parts[-1] == "wiki":
        breadcrumb_parts.append(display_name)

    qualified_parts = breadcrumb_parts[1:] if breadcrumb_parts and breadcrumb_parts[0] == "wiki" else breadcrumb_parts
    if not qualified_parts:
        qualified_parts = [display_name]
    qualified_name = " / ".join(qualified_parts)

    return {
        "displayName": display_name,
        "shortName": short_name,
        "qualifiedName": qualified_name,
        "breadcrumb": breadcrumb_parts,
        "fileName": file_name,
        "parentName": path_segment_name(parent_name) if parent_name else "",
    }


def load_json_file(path: Path, default: object) -> object:
    if not path.exists():
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def atomic_write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if path.exists():
        try:
            if path.read_text(encoding="utf-8") == rendered:
                return
        except OSError:
            pass
    tmp = path.with_name(f"{path.name}.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(rendered)
    tmp.replace(path)


def strip_volatile_json_keys(value: object, volatile_keys: set[str]) -> object:
    if isinstance(value, dict):
        return {
            key: strip_volatile_json_keys(item, volatile_keys)
            for key, item in value.items()
            if key not in volatile_keys
        }
    if isinstance(value, list):
        return [strip_volatile_json_keys(item, volatile_keys) for item in value]
    return value


def atomic_write_json_semantic(path: Path, data: object, volatile_keys: set[str]) -> None:
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            if strip_volatile_json_keys(existing, volatile_keys) == strip_volatile_json_keys(data, volatile_keys):
                return
        except (OSError, json.JSONDecodeError):
            pass
    atomic_write_json(path, data)


def canonicalize_graph_href(root_path: Path, href: str) -> str | None:
    if "../" in href or href == "..":
        if href.startswith("../wiki/"):
            candidate = posixpath.normpath(href[3:])
        else:
            return None
    else:
        candidate = posixpath.normpath(href)
        if not any(candidate.startswith(p) for p in VALID_ROOT_PREFIXES):
            suggested = canonicalize_href_with_wiki_prefix(candidate)
            if suggested is None:
                return None
            candidate = suggested
    resolved = resolve_href(root_path, candidate)
    if resolved is None:
        return None
    return resolved.relative_to(root_path).as_posix()


def canonicalize_graph_literal(root_path: Path, literal: str) -> str | None:
    normalized = posixpath.normpath(literal.strip())
    if not normalized.startswith("wiki/"):
        return None
    resolved = resolve_href(root_path, normalized)
    if resolved is None:
        return None
    return resolved.relative_to(root_path).as_posix()


def canonicalize_raw_literal(root_path: Path, literal: str) -> str | None:
    normalized = posixpath.normpath(literal.strip())
    if not normalized.startswith("raw/"):
        return None
    target = root_path / Path(normalized)
    if not target.exists() or not target.is_file():
        return None
    rel = target.resolve().relative_to(root_path).as_posix()
    return rel


def add_graph_edge(
    adjacency: dict[str, dict[str, list[dict[str, str]]]],
    edge_map: dict[tuple[str, str, str], dict[str, object]],
    source: str,
    target: str,
    kind: str,
) -> None:
    key = (source, target, kind)
    if key in edge_map:
        edge_map[key]["w"] = int(edge_map[key]["w"]) + 1
        return
    edge_map[key] = {"s": source, "t": target, "k": kind, "w": 1, "d": 1}
    adjacency[source]["out"].append({"to": target, "kind": kind})
    adjacency[target]["in"].append({"from": source, "kind": kind})


def dedupe_edges(edges: list[dict[str, object]]) -> list[dict[str, object]]:
    merged: dict[tuple[str, str, str], dict[str, object]] = {}
    for edge in edges:
        key = (str(edge["s"]), str(edge["t"]), str(edge["k"]))
        if key not in merged:
            merged[key] = {**edge, "w": int(edge.get("w", 1))}
        else:
            merged[key]["w"] = int(merged[key].get("w", 1)) + int(edge.get("w", 1))
            merged[key]["d"] = max(float(merged[key].get("d", 1)), float(edge.get("d", 1)))
    return sorted(merged.values(), key=lambda item: (str(item["s"]), str(item["t"]), str(item["k"])))


def build_graph_settings() -> dict[str, object]:
    return {
        "schema": 1,
        "hiddenKinds": ["query"],
        "defaultView": "knowledge",
        "views": ["knowledge", "recent", "navigation", "lineage"],
        "typeAffinity": {
            "entity": {"entity": 0.8, "concept": 1.2, "summary": 1.0, "synthesis": 1.0, "query": 0.8, "index": 0.8},
            "concept": {"entity": 1.2, "concept": 0.8, "summary": 1.0, "synthesis": 1.2, "query": 1.0, "index": 0.8},
            "summary": {"entity": 1.0, "concept": 1.0, "summary": 0.5, "synthesis": 1.0, "query": 0.8, "index": 0.6},
            "synthesis": {"entity": 1.0, "concept": 1.2, "summary": 1.0, "synthesis": 0.8, "query": 1.0, "index": 0.8},
            "query": {"entity": 0.8, "concept": 1.0, "summary": 0.8, "synthesis": 1.0, "query": 0.5, "index": 0.5},
            "index": {"entity": 0.8, "concept": 0.8, "summary": 0.6, "synthesis": 0.8, "query": 0.5, "index": 0.5},
        },
        "layout": {
            "knowledge": {"engine": "forceatlas2", "edgeWeightInfluence": 1.0},
            "recent": {"engine": "bipartite"},
            "navigation": {"engine": "tree"},
            "lineage": {"engine": "dag"},
        },
    }


def extract_md_links_with_text(text: str) -> list[tuple[str, str]]:
    links: list[tuple[str, str]] = []
    for m in MD_LINK_WITH_TEXT_RE.finditer(text):
        if m.group("bang"):
            continue
        href = (m.group("bracketed") or m.group("bare") or "").strip()
        if "#" in href:
            href = href.split("#", 1)[0]
        if not href or EXTERNAL_URL_RE.match(href):
            continue
        label = (m.group("label") or "").strip()
        links.append((label, href))
    return links


def slug_text(text: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", text.strip().lower()).strip("-")
    return slug or "section"


def build_navigation_graph(root_path: Path, nodes: dict[str, dict[str, object]]) -> dict[str, object]:
    index_path = root_path / "wiki" / "index.md"
    if not index_path.exists():
        return {"schema": 1, "root": "wiki/index.md", "nodes": [], "edges": []}

    nav_nodes: dict[str, dict[str, object]] = {}
    edges: list[dict[str, object]] = []
    root_id = "nav:wiki/index.md"
    nav_nodes[root_id] = {
        "id": root_id,
        "kind": "index",
        "displayName": nodes.get("wiki/index.md", {}).get("displayName", "Index"),
        "qualifiedName": "Navigation / Index",
        "page": "wiki/index.md",
    }

    current_section_id = root_id
    for line in index_path.read_text(encoding="utf-8").splitlines():
        heading = re.match(r"^(#{2,6})\s+(.+?)\s*$", line.strip())
        if heading:
            section_title = heading.group(2).strip()
            current_section_id = f"nav:wiki/index.md#{slug_text(section_title)}"
            nav_nodes[current_section_id] = {
                "id": current_section_id,
                "kind": "section",
                "displayName": section_title,
                "qualifiedName": f"Navigation / {section_title}",
                "page": "wiki/index.md",
                "level": len(heading.group(1)),
            }
            edges.append({"s": root_id, "t": current_section_id, "k": "contains", "w": 1, "d": 1})
            continue

        if not re.match(r"^\s*[-*]\s+", line):
            continue

        for label, href in extract_md_links_with_text(line):
            target = canonicalize_graph_href(root_path, href)
            if target is None or target not in nodes:
                continue
            nav_nodes[target] = {
                "id": target,
                "kind": nodes[target].get("kind", "page"),
                "title": nodes[target].get("title", label or target),
                "displayName": nodes[target].get("displayName", label or target),
                "qualifiedName": nodes[target].get("qualifiedName", target),
                "breadcrumb": nodes[target].get("breadcrumb", []),
            }
            edges.append({"s": current_section_id, "t": target, "k": "listed_in", "w": 1, "d": 1})

    return {
        "schema": 1,
        "root": "wiki/index.md",
        "nodes": sorted(nav_nodes.values(), key=lambda item: str(item["id"])),
        "edges": dedupe_edges(edges),
    }


def raw_node(root_path: Path, rel_id: str) -> dict[str, object]:
    path = root_path / rel_id
    display = path.stem
    try:
        text = path.read_text(encoding="utf-8")
        fm = parse_frontmatter(text) or {}
        title = fm.get("title")
        if isinstance(title, str) and title.strip():
            display = title.strip()
    except (OSError, UnicodeDecodeError):
        pass
    return {
        "id": rel_id,
        "kind": "raw_source",
        "displayName": display,
        "qualifiedName": rel_id,
    }


def build_lineage_graph(
    root_path: Path,
    nodes: dict[str, dict[str, object]],
    frontmatters: dict[str, dict],
    texts: dict[str, str],
) -> dict[str, object]:
    lineage_nodes: dict[str, dict[str, object]] = {}
    edges: list[dict[str, object]] = []

    def add_page_node(rel_id: str) -> None:
        if rel_id not in nodes:
            return
        lineage_nodes[rel_id] = {
            "id": rel_id,
            "kind": nodes[rel_id].get("kind", "page"),
            "title": nodes[rel_id].get("title", rel_id),
            "displayName": nodes[rel_id].get("displayName", rel_id),
            "qualifiedName": nodes[rel_id].get("qualifiedName", rel_id),
            "breadcrumb": nodes[rel_id].get("breadcrumb", []),
        }

    for rel_id, fm in frontmatters.items():
        source_refs = ensure_string_list(fm.get("sources"))
        for source_ref in source_refs:
            raw_rel = canonicalize_raw_literal(root_path, source_ref)
            if raw_rel is not None:
                lineage_nodes[raw_rel] = raw_node(root_path, raw_rel)
                add_page_node(rel_id)
                edges.append({"s": raw_rel, "t": rel_id, "k": "ingested_as", "w": 1, "d": 1})
                continue

            source_page = canonicalize_graph_href(root_path, source_ref)
            if source_page is not None and source_page in nodes:
                add_page_node(source_page)
                add_page_node(rel_id)
                edges.append({"s": source_page, "t": rel_id, "k": "derived_from", "w": 1, "d": 1})

    for rel_id, text in texts.items():
        if nodes.get(rel_id, {}).get("kind") != "summary":
            continue
        add_page_node(rel_id)
        for href in extract_md_link_hrefs(text):
            target = canonicalize_graph_href(root_path, href)
            if target is None or target == rel_id or target not in nodes:
                continue
            add_page_node(target)
            edges.append({"s": rel_id, "t": target, "k": "mentions", "w": 1, "d": 1})

    for raw_file in sorted((root_path / "raw").rglob("*")) if (root_path / "raw").exists() else []:
        if not raw_file.is_file():
            continue
        rel_path = raw_file.relative_to(root_path)
        if any(part.startswith(".") for part in rel_path.parts):
            continue
        rel = rel_path.as_posix()
        if rel not in lineage_nodes:
            lineage_nodes[rel] = raw_node(root_path, rel)

    return {
        "schema": 1,
        "nodes": sorted(lineage_nodes.values(), key=lambda item: str(item["id"])),
        "edges": dedupe_edges(edges),
    }


def extract_log_touched_pages(root_path: Path, text: str) -> list[str]:
    touched: set[str] = set()
    for href in extract_md_link_hrefs(text):
        target = canonicalize_graph_href(root_path, href)
        if target is not None:
            touched.add(target)
    for m in WIKI_MD_LITERAL_RE.finditer(text):
        target = canonicalize_graph_literal(root_path, m.group("path"))
        if target is not None:
            touched.add(target)
    for m in BACKTICK_WIKI_MD_LITERAL_RE.finditer(text):
        target = canonicalize_graph_literal(root_path, m.group("path"))
        if target is not None:
            touched.add(target)
    return sorted(touched)


RECENT_BUCKET_DAYS = 3
RECENT_BUCKET_COUNT = 7
RECENT_BUCKETED_AGE = RECENT_BUCKET_DAYS * RECENT_BUCKET_COUNT  # 21
RECENT_FLOATING_MAX_AGE = RECENT_BUCKETED_AGE + RECENT_BUCKET_DAYS  # 24
RECENT_BUCKET_IDS = [f"r{i}" for i in range(RECENT_BUCKET_COUNT)]


def recent_bucket_for_age(age_days: int) -> str | None:
    """Map an entry's age-in-days onto a bucket label.
    r0..r6 are 3-day windows covering ages [0, 21); floating covers [21, 24);
    older entries return None (excluded)."""
    if age_days < 0:
        return None
    if age_days < RECENT_BUCKETED_AGE:
        return f"r{age_days // RECENT_BUCKET_DAYS}"
    if age_days < RECENT_FLOATING_MAX_AGE:
        return "floating"
    return None


def build_recent_log_graph(
    root_path: Path,
    nodes: dict[str, dict[str, object]],
    history_days: int = RECENT_FLOATING_MAX_AGE,
) -> dict[str, object]:
    log_dir = root_path / "log"
    entries: list[dict[str, object]] = []
    if not log_dir.exists() or not log_dir.is_dir():
        return {"schema": 1, "entries": [], "nodes": [], "edges": []}

    log_files = []
    for log_file in sorted(log_dir.glob("*.md"), reverse=True):
        m = LOG_FILENAME_RE.match(log_file.name)
        if not m:
            continue
        date = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
        log_date = datetime.strptime(date, "%Y-%m-%d").date()
        log_files.append((date, log_date, log_file))

    today = datetime.now().astimezone().date()
    bucket_by_date: dict[str, str] = {}
    age_by_date: dict[str, int] = {}
    included_log_files = []
    for date, log_date, log_file in log_files:
        age_days = (today - log_date).days
        if age_days < 0 or age_days >= history_days:
            continue
        bucket = recent_bucket_for_age(age_days)
        if bucket is None:
            continue
        bucket_by_date[date] = bucket
        age_by_date[date] = age_days
        included_log_files.append((date, log_file))

    for date, log_file in included_log_files:
        rel_log = log_file.relative_to(root_path).as_posix()
        current: dict[str, object] | None = None
        body_lines: list[str] = []

        def flush() -> None:
            nonlocal current, body_lines
            if current is None:
                return
            body = "\n".join(body_lines).strip()
            current["body"] = body
            current["touched"] = extract_log_touched_pages(root_path, body)
            entries.append(current)
            current = None
            body_lines = []

        for line in log_file.read_text(encoding="utf-8").splitlines():
            entry_match = LOG_ENTRY_RE.match(line)
            if entry_match:
                flush()
                time = entry_match.group("time")
                op = entry_match.group("op")
                title = entry_match.group("title")
                slug = re.sub(r"[^a-zA-Z0-9]+", "-", f"{time}-{op}-{title}".lower()).strip("-")
                entry_id = f"log-entry:{date.replace('-', '')}-{slug}"
                current = {
                    "id": entry_id,
                    "date": date,
                    "ageDays": age_by_date.get(date),
                    "bucket": bucket_by_date.get(date),
                    "time": time,
                    "op": op,
                    "title": title,
                    "logPath": rel_log,
                    "anchor": slug,
                    "touched": [],
                }
                body_lines = []
            elif current is not None:
                body_lines.append(line)
        flush()

    entries.sort(key=lambda item: (str(item["date"]), str(item["time"]), str(item["id"])), reverse=True)
    recent_entries = entries

    def depth_for_age(age_days: int | float | None) -> float:
        """Linear gradient from 1.0 (today) to ~0.1 (24 days ago)."""
        if age_days is None:
            return 1.0
        if age_days < 0:
            return 1.0
        return max(0.1, round(1.0 - age_days * 0.92 / RECENT_FLOATING_MAX_AGE, 2))

    def bucket_sort_key(bucket_id: str) -> int:
        if bucket_id.startswith("r") and bucket_id[1:].isdigit():
            return int(bucket_id[1:])
        return RECENT_BUCKET_COUNT + 1  # floating + unknown go last

    bucket_entries_by_id: dict[str, list[dict[str, object]]] = {
        bid: [] for bid in RECENT_BUCKET_IDS
    }
    edge_map: dict[tuple[str, str], dict[str, object]] = {}
    touched_ids: set[str] = set()
    touched_meta: dict[str, list[dict[str, object]]] = defaultdict(list)
    for entry in recent_entries:
        entry_id = str(entry["id"])
        bucket = str(entry.get("bucket") or "floating")
        age_days = entry.get("ageDays")
        depth = depth_for_age(age_days if isinstance(age_days, int) else None)
        entry_meta = {
            "id": entry_id,
            "date": entry["date"],
            "ageDays": entry.get("ageDays"),
            "bucket": bucket,
            "time": entry["time"],
            "op": entry["op"],
            "title": entry["title"],
            "logPath": entry["logPath"],
            "anchor": entry["anchor"],
            "d": depth,
        }
        if bucket in bucket_entries_by_id:
            bucket_entries_by_id[bucket].append(entry_meta)
        valid_touched: list[str] = []
        for target in entry.get("touched", []):
            target_id = str(target)
            if target_id not in nodes:
                continue
            valid_touched.append(target_id)
            touched_ids.add(target_id)
            touched_meta[target_id].append(entry_meta)
            if bucket not in bucket_entries_by_id:
                continue  # floating: no bucket node, so no edge
            # Suppress edges to wiki/index.md from any bucket other than the
            # most-recent (r0). The index is touched constantly, so non-r0
            # buckets would render as a dense star around index.md.
            if bucket != "r0" and target_id == "wiki/index.md":
                continue
            edge_key = (bucket, target_id)
            edge = edge_map.get(edge_key)
            if edge is None:
                edge = {
                    "s": bucket,
                    "t": target_id,
                    "k": "touched",
                    "w": 0,
                    "d": depth,
                    "bucket": bucket,
                    "entries": [],
                }
                edge_map[edge_key] = edge
            edge["w"] = int(edge.get("w", 0)) + 1
            edge["d"] = max(float(edge.get("d", 0)), depth)
            edge_entries = edge.get("entries")
            if isinstance(edge_entries, list):
                edge_entries.append(entry_meta)
        entry["touched"] = valid_touched
        entry["nodes"] = [
            {
                "id": target,
                "displayName": nodes[target].get("displayName", nodes[target].get("title", target)),
                "qualifiedName": nodes[target].get("qualifiedName", target),
                "kind": nodes[target].get("kind", "page"),
            }
            for target in valid_touched
        ]

    page_nodes = []
    for rel_id, data in sorted(nodes.items()):
        if rel_id not in touched_ids:
            continue
        entry_meta = sorted(
            touched_meta.get(rel_id, []),
            key=lambda item: (str(item["date"]), str(item["time"]), str(item["id"])),
            reverse=True,
        )
        buckets = sorted(
            set(str(item.get("bucket", "floating")) for item in entry_meta),
            key=bucket_sort_key,
        )
        primary_bucket = buckets[0] if buckets else "floating"
        page_nodes.append({
            "id": rel_id,
            "kind": data.get("kind", "page"),
            "title": data.get("title", rel_id),
            "displayName": data.get("displayName", data.get("title", rel_id)),
            "qualifiedName": data.get("qualifiedName", rel_id),
            "breadcrumb": data.get("breadcrumb", []),
            "bucket": primary_bucket,
            "buckets": buckets,
            "entries": entry_meta,
            "d": max((float(item.get("d", 1)) for item in entry_meta), default=1),
        })

    bucket_control_nodes: list[dict[str, object]] = []
    for i, bid in enumerate(RECENT_BUCKET_IDS):
        start = i * RECENT_BUCKET_DAYS
        end = start + RECENT_BUCKET_DAYS
        center_age = start + RECENT_BUCKET_DAYS / 2.0
        bucket_control_nodes.append({
            "id": bid,
            "kind": "recent",
            "displayName": f"Days {start}-{end - 1}",
            "qualifiedName": f"Recent activity / {start}-{end - 1} days ago",
            "entries": bucket_entries_by_id[bid],
            "d": depth_for_age(center_age),
            "bucketIndex": i,
            "ageRange": [start, end],
        })

    edges = sorted(edge_map.values(), key=lambda item: (str(item["s"]), str(item["t"])))
    all_bucket_ids = RECENT_BUCKET_IDS + ["floating"]
    dates_by_bucket = {
        bucket: [
            date for date, _, _ in log_files
            if bucket_by_date.get(date) == bucket
        ]
        for bucket in all_bucket_ids
    }
    ranges: dict[str, dict[str, object]] = {}
    for i, bid in enumerate(RECENT_BUCKET_IDS):
        start = i * RECENT_BUCKET_DAYS
        ranges[bid] = {
            "fromDaysAgo": start,
            "toDaysAgo": start + RECENT_BUCKET_DAYS,
            "node": bid,
        }
    ranges["floating"] = {
        "fromDaysAgo": RECENT_BUCKETED_AGE,
        "toDaysAgo": RECENT_FLOATING_MAX_AGE,
        "node": None,
    }

    return {
        "schema": 1,
        "window": {
            "kind": "relative_day_buckets",
            "generatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
            "anchorDate": today.isoformat(),
            "ranges": ranges,
            "historyDays": history_days,
            "datesByBucket": dates_by_bucket,
            "availableLogDays": len(log_files),
            "includedLogDays": len(included_log_files),
            "entryLimit": None,
            "touchedNodeLimit": None,
        },
        "entries": recent_entries,
        "nodes": bucket_control_nodes + page_nodes,
        "edges": edges,
    }


def sidecar_needs_refresh(root_path: Path, rel_id: str) -> bool:
    sidecar_path = Path(f"{root_path / rel_id}.graph")
    data = load_json_file(sidecar_path, None)
    if not isinstance(data, dict):
        return True
    required = {
        "displayName",
        "shortName",
        "qualifiedName",
        "breadcrumb",
        "fileName",
        "parentName",
    }
    return any(key not in data for key in required)


def open_audit_files(root_path: Path) -> list[Path]:
    audit_dir = root_path / "audit"
    if not audit_dir.exists() or not audit_dir.is_dir():
        return []
    return [
        p for p in sorted(audit_dir.glob("*.md"))
        if p.is_file() and p.name != ".gitkeep"
    ]


# ── audit sidecar ingest ────────────────────────────────────────────────────
#
# Sidecars are the wire format external writers (web viewer, mino_server
# gateway auditWrite, manual CLI, etc.) drop under `wiki/**/*.md.audit`.
# `lint_wiki.py` drains them into the canonical `audit/<id>-<slug>.md`
# storage form before the open-audit preflight gate fires, so downstream
# passes (audit_review.py, audit_cr.py, the agent itself) only ever see
# the canonical Markdown layout.
#
# Sidecar file is JSON. Top level is either a single audit object or an
# array of audit objects (one file can carry multiple entries):
#
#     [
#       {
#         "id": "20260525-180000-abcd",
#         "target": "wiki/concepts/Foo.md",
#         "type": "warn",
#         "start": [42, 3],
#         "end": [42, 28],
#         "author": "lewis",
#         "source": "web-viewer",
#         "created": "2026-05-25T18:00:00+08:00",
#         "status": "open",
#         "comment": "free-form markdown; --- and ```yaml --- ``` are fine"
#       }
#     ]
#
# Why JSON: the wire format is consumed by web frontends, backend gateways,
# and lint. All three already have standard JSON parsers. Markdown body
# content is just a string field — no escape rules, no delimiter collisions
# with markdown horizontal rules. The LLM-readable canonical form
# (`audit/<id>-<slug>.md`) is still YAML frontmatter + markdown body; lint
# does the translation.
#
# Discovery convention: filename matches `*.md.audit` somewhere under
# `wiki/`. The location is informational (typically next to the target page
# as `wiki/<...>.md.audit`), but lint glob doesn't require a specific
# placement — only the extension.

_AUDIT_ID_RE = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{4}$")
_AUDIT_REQUIRED_FIELDS = (
    "id", "target", "type", "start", "end",
    "author", "source", "created", "status",
)
_AUDIT_OPTIONAL_FIELDS = ("comment",)
_CANONICAL_AUDIT_FIELD_ORDER = _AUDIT_REQUIRED_FIELDS  # for canonical YAML render order


def _audit_slug(comment: str, max_len: int = 30) -> str:
    """Derive a filesystem-safe slug from the audit comment (first words)."""
    for line in comment.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or s.startswith("<!--"):
            continue
        words = s.lower().split()[:5]
        slug = re.sub(r"[^a-z0-9]+", "-", " ".join(words)).strip("-")
        return slug[:max_len]
    return ""


def _validate_audit_entry(entry: dict) -> str | None:
    """Return None if entry passes minimal shape checks, else an error string."""
    if not isinstance(entry, dict):
        return f"entry is not an object (got {type(entry).__name__})"
    missing = [f for f in _AUDIT_REQUIRED_FIELDS if f not in entry]
    if missing:
        return f"missing required fields: {', '.join(missing)}"
    aid = entry.get("id")
    if not isinstance(aid, str) or not _AUDIT_ID_RE.match(aid):
        return f"invalid id {aid!r} (expected YYYYMMDD-HHMMSS-<4hex>)"
    for coord_key in ("start", "end"):
        coord = entry.get(coord_key)
        if not (
            isinstance(coord, list)
            and len(coord) == 2
            and all(isinstance(v, int) and v >= 1 for v in coord)
        ):
            return f"invalid {coord_key} {coord!r} (expected [line, col] with 1-indexed ints)"
    return None


def _render_canonical_audit_value(v: object) -> str:
    """Render a value for canonical YAML frontmatter."""
    if isinstance(v, list):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, bool) or v is None:
        return json.dumps(v)
    if isinstance(v, (int, float)):
        return json.dumps(v)
    s = str(v)
    needs_quote = (
        not s
        or any(c in s for c in '":\n\r#&*!|>%@`')
        or s != s.strip()
        or s in ("true", "false", "null", "yes", "no", "~")
    )
    if needs_quote:
        return json.dumps(s, ensure_ascii=False)
    return s


def _render_canonical_audit(entry: dict, comment: str) -> str:
    """Serialize a JSON audit entry into the canonical audit/<id>-<slug>.md form.

    Frontmatter holds all metadata fields except `comment`; the markdown body
    is a `# Comment` section containing the free-form comment text. The
    `# Resolution` section is left as an HTML stub for the agent to fill in
    when the audit is processed.
    """
    yaml_lines = ["---"]
    seen: set[str] = set()
    for key in _CANONICAL_AUDIT_FIELD_ORDER:
        if key in entry:
            yaml_lines.append(f"{key}: {_render_canonical_audit_value(entry[key])}")
            seen.add(key)
    for key, value in entry.items():
        if key in seen or key == "comment":
            continue
        yaml_lines.append(f"{key}: {_render_canonical_audit_value(value)}")
    yaml_lines.append("---")
    yaml_lines.append("")
    out = "\n".join(yaml_lines) + "\n"
    body = comment.rstrip()
    out += "# Comment\n\n"
    if body:
        out += body + "\n"
    out += "\n# Resolution\n\n<!-- Filled in when the audit is processed and moved to resolved/ -->\n"
    return out


def _git_audit_sidecar_files(root_path: Path) -> list[Path] | None:
    """List wiki/**/*.md.audit sidecars via `git status`, or None if git unusable.

    Returns:
        list of resolved sidecar Paths when git status works and is authoritative
        (an empty list means "git is usable and saw no sidecars" — caller trusts
        it and skips the filesystem scan);
        None when git is unavailable, the repo can't be queried, or wiki/ is
        git-ignored — caller must fall back to a full rglob to avoid missing
        externally-dropped sidecars.

    Sidecars are typically untracked files (external writers don't `git add`).
    Wikis that .gitignore `*.md.audit` will see git report 0 here even when
    sidecars exist on disk — that is a user choice and out of scope for this
    fast path. Don't gitignore sidecars if you want this path to find them.
    """
    try:
        ignored = subprocess.run(
            ["git", "-C", str(root_path), "check-ignore", "--quiet", "wiki"],
            check=False, capture_output=True,
        )
        if ignored.returncode == 0:
            return None
        result = subprocess.run(
            [
                "git", "-C", str(root_path),
                "status", "--porcelain=v1", "-z", "--untracked-files=all",
                "--", "wiki",
            ],
            check=False, capture_output=True, text=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None

    out: list[Path] = []
    entries = [entry for entry in result.stdout.split("\0") if entry]
    idx = 0
    while idx < len(entries):
        raw_entry = entries[idx]
        idx += 1
        if len(raw_entry) < 4:
            continue
        status = raw_entry[:2]
        rel = raw_entry[3:].strip()
        if status[0] in {"R", "C"} or status[1] in {"R", "C"}:
            idx += 1
        if not rel.startswith("wiki/") or not rel.endswith(".md.audit"):
            continue
        path = root_path / rel
        if "D" in status or not path.exists():
            continue
        out.append(path.resolve())
    return sorted(set(out))


def ingest_audit_sidecars(root_path: Path) -> tuple[int, list[str]]:
    """Drain wiki/**/*.md.audit sidecars into audit/<id>-<slug>.md.

    Returns (emitted_count, warnings).

    Sidecar discovery prefers `git status` (a typed pathspec lookup; cheap on
    small/medium wikis and consistent with --changed mode's detection path)
    and falls back to a full rglob only when git is unavailable, the repo
    can't be queried, or wiki/ is git-ignored. This avoids paying for a full
    wiki/ tree walk on every lint run for GB-scale corpuses.

    Each sidecar is a JSON file whose top level is either a single audit
    object or an array of objects. Lint validates each entry, renders it as
    canonical YAML frontmatter + markdown body, and writes it to
    audit/<id>-<slug>.md. The sidecar is only removed when every entry
    inside was either ingested or skipped as a known duplicate; any
    parse/validate failure preserves the sidecar on disk for inspection.
    """
    wiki_dir = root_path / "wiki"
    if not wiki_dir.exists():
        return 0, []
    audit_dir = root_path / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)

    sidecars = _git_audit_sidecar_files(root_path)
    if sidecars is None:
        sidecars = sorted(wiki_dir.rglob("*.md.audit"))

    emitted = 0
    warnings: list[str] = []
    for sidecar in sidecars:
        try:
            content = sidecar.read_text(encoding="utf-8")
        except OSError as exc:
            warnings.append(f"failed to read {sidecar.relative_to(root_path)}: {exc}")
            continue
        try:
            parsed = json.loads(content) if content.strip() else None
        except json.JSONDecodeError as exc:
            warnings.append(f"invalid JSON in {sidecar.relative_to(root_path)}: {exc.msg} at line {exc.lineno}")
            continue
        if parsed is None:
            # Empty file. Keep on disk so write-in-progress / encoding bugs
            # surface instead of being silently swallowed.
            warnings.append(f"empty sidecar kept for inspection: {sidecar.relative_to(root_path)}")
            continue
        entries = parsed if isinstance(parsed, list) else [parsed]
        if not entries:
            warnings.append(f"empty audit array kept for inspection: {sidecar.relative_to(root_path)}")
            continue
        all_preserved = True
        for entry in entries:
            err = _validate_audit_entry(entry)
            if err is not None:
                warnings.append(f"invalid audit entry in {sidecar.relative_to(root_path)}: {err}")
                all_preserved = False
                continue
            audit_id = entry["id"]
            comment = entry.get("comment", "")
            if not isinstance(comment, str):
                comment = str(comment)
            slug = _audit_slug(comment)
            filename = f"{audit_id}-{slug}.md" if slug else f"{audit_id}.md"
            out_path = audit_dir / filename
            if out_path.exists():
                warnings.append(f"audit already exists, skipping: audit/{filename}")
                continue
            out_path.write_text(_render_canonical_audit(entry, comment), encoding="utf-8")
            emitted += 1
        if not all_preserved:
            # At least one entry could not be persisted to audit/. Keep the
            # sidecar so the failed entry's content is not lost.
            warnings.append(f"sidecar kept (some entries failed to ingest): {sidecar.relative_to(root_path)}")
            continue
        try:
            sidecar.unlink()
        except OSError as exc:
            warnings.append(f"could not remove sidecar {sidecar.relative_to(root_path)}: {exc}")
    return emitted, warnings


def print_open_audit_preflight(root_path: Path, files: list[Path]) -> None:
    print("\n🔴 Open audit inbox is not empty — process feedback before lint.")
    print()
    print("Full lint is intentionally blocked while user audit feedback is open.")
    print("Resolve, reject, or defer every audit item first, then move it to audit/resolved/.")
    print("The audit/ root must contain no open .md files before full lint can continue.")
    print()
    print("Open audit files:")
    for p in files[:20]:
        print(f"   {p.relative_to(root_path)}")
    if len(files) > 20:
        print(f"   … and {len(files) - 20} more")
    print()
    print("Suggested workflow:")
    print(f"   python3 scripts/audit_cr.py {root_path} --open --write")
    print(f"   python3 scripts/audit_review.py {root_path} --open")
    print("   # apply/reject/defer each audit")
    print("   # move every processed audit file from audit/*.md to audit/resolved/")
    print("   # audit/ must be empty of open .md files before the next step")
    print("   # do not delete audit files; clear them by moving them to audit/resolved/")
    print(f"   python3 scripts/audit_cr.py {root_path} --all --write")
    print(f"   python3 scripts/lint_wiki.py {root_path}")


def cleanup_legacy_agraph_artifacts(root_path: Path) -> int:
    removed = 0
    for base in (root_path / "graph", root_path / "wiki"):
        if not base.exists():
            continue
        for path in sorted(base.rglob("*.agraph")):
            if not path.is_file():
                continue
            path.unlink()
            removed += 1
    return removed


def build_sidecar_payload(
    rel_id: str,
    node: dict[str, object],
    adjacency: dict[str, list[dict[str, str]]],
) -> dict[str, object]:
    inbound = len(adjacency["in"])
    outbound = len(adjacency["out"])
    return {
        "schema": 1,
        "id": rel_id,
        "title": node["title"],
        "displayName": node["displayName"],
        "shortName": node["shortName"],
        "qualifiedName": node["qualifiedName"],
        "breadcrumb": node["breadcrumb"],
        "fileName": node["fileName"],
        "parentName": node["parentName"],
        "kind": node["kind"],
        "hash": node["hash"],
        "tags": node["tags"],
        "summary": node["summary"],
        "ego": adjacency,
        "stats": {
            "inbound": inbound,
            "outbound": outbound,
            "degree": inbound + outbound,
        },
    }


def write_scoped_graph_sidecars(
    root_path: Path,
    scoped_wiki_files: list[Path],
    all_wiki_file_set: set[Path],
    preloaded_texts: dict[str, str],
) -> dict[str, int]:
    cache_dir = root_path / ".graph-cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    old_reverse = load_json_file(cache_dir / "reverse-links.graph", {})
    if not isinstance(old_reverse, dict):
        old_reverse = {}
    if not old_reverse:
        print(
            "🟡 .graph-cache/reverse-links.graph missing — run a full lint at least once "
            "for complete inbound edges in page-local sidecars"
        )

    all_current_ids = {
        path.relative_to(root_path).as_posix()
        for path in all_wiki_file_set
    }
    scoped_ids = [path.relative_to(root_path).as_posix() for path in sorted(scoped_wiki_files)]
    scoped_id_set = set(scoped_ids)
    adjacency_by_id: dict[str, dict[str, list[dict[str, str]]]] = {}
    nodes: dict[str, dict[str, object]] = {}
    scoped_outgoing: dict[str, list[tuple[str, str]]] = defaultdict(list)

    for md_file in sorted(scoped_wiki_files):
        rel_id = md_file.relative_to(root_path).as_posix()
        text = preloaded_texts.get(rel_id)
        if text is None:
            text = md_file.read_text(encoding="utf-8")
        fm = parse_frontmatter(text) or {}
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        title = extract_page_title(text, md_file.stem)
        display = build_display_metadata(rel_id, title)
        nodes[rel_id] = {
            "id": rel_id,
            "title": title,
            "kind": infer_page_kind(rel_id, fm),
            "tags": ensure_string_list(fm.get("tags")),
            "summary": extract_page_summary(text),
            "hash": f"sha256:{digest}",
            **display,
        }
        adjacency_by_id[rel_id] = {"out": [], "in": []}

        seen_edges: set[tuple[str, str]] = set()
        for href in extract_md_link_hrefs(text):
            target = canonicalize_graph_href(root_path, href)
            if target is None or target == rel_id or target not in all_current_ids:
                continue
            edge = (target, "links_to")
            if edge in seen_edges:
                continue
            seen_edges.add(edge)
            scoped_outgoing[rel_id].append(edge)

        for source_ref in ensure_string_list(fm.get("sources")):
            target = canonicalize_graph_href(root_path, source_ref)
            if target is None or target == rel_id or target not in all_current_ids:
                continue
            edge = (target, "derived_from")
            if edge in seen_edges:
                continue
            seen_edges.add(edge)
            scoped_outgoing[rel_id].append(edge)

    for rel_id, edges in scoped_outgoing.items():
        adjacency_by_id[rel_id]["out"] = [
            {"to": target, "kind": kind}
            for target, kind in sorted(edges, key=lambda item: (item[0], item[1]))
        ]
        for target, kind in edges:
            if target in scoped_id_set:
                adjacency_by_id[target]["in"].append({"from": rel_id, "kind": kind})

    for rel_id in scoped_ids:
        cached_inbound = old_reverse.get(rel_id, [])
        if isinstance(cached_inbound, list):
            existing = {
                (item["from"], item["kind"])
                for item in adjacency_by_id[rel_id]["in"]
            }
            for source in sorted(str(v) for v in cached_inbound):
                if source == rel_id or source not in all_current_ids:
                    continue
                edge = (source, "links_to")
                if edge in existing:
                    continue
                adjacency_by_id[rel_id]["in"].append({"from": source, "kind": "links_to"})
                existing.add(edge)
        adjacency_by_id[rel_id]["in"].sort(key=lambda item: (item["from"], item["kind"]))

    written = 0
    for rel_id in scoped_ids:
        sidecar_path = Path(f"{root_path / rel_id}.graph")
        atomic_write_json(sidecar_path, build_sidecar_payload(rel_id, nodes[rel_id], adjacency_by_id[rel_id]))
        written += 1
    return {"sidecars": written}


def write_graph_artifacts(
    root_path: Path,
    all_wiki_files: list[Path],
    orphans: list[Path],
    dead_links: list[tuple[Path, str]],
    not_in_index: list[Path],
    scale_issues: dict[str, object] | None = None,
    contradiction_markers: list[dict[str, object]] | None = None,
    contradictory_claims: list[dict[str, object]] | None = None,
    preloaded_texts: dict[str, str] | None = None,
) -> dict[str, int]:
    graph_dir = root_path / "graph"
    cache_dir = root_path / ".graph-cache"
    graph_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)

    old_hashes = load_json_file(cache_dir / "page-hashes.graph", {})
    if not isinstance(old_hashes, dict):
        old_hashes = {}
    old_reverse = load_json_file(cache_dir / "reverse-links.graph", {})
    if not isinstance(old_reverse, dict):
        old_reverse = {}

    texts: dict[str, str] = {}
    frontmatters: dict[str, dict] = {}
    nodes: dict[str, dict[str, object]] = {}
    adjacency: dict[str, dict[str, list[dict[str, str]]]] = {}
    current_hashes: dict[str, str] = {}

    for md_file in sorted(all_wiki_files):
        rel_id = md_file.relative_to(root_path).as_posix()
        text = preloaded_texts[rel_id] if preloaded_texts and rel_id in preloaded_texts else md_file.read_text(encoding="utf-8")
        texts[rel_id] = text
        fm = parse_frontmatter(text) or {}
        frontmatters[rel_id] = fm
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        current_hashes[rel_id] = f"sha256:{digest}"
        title = extract_page_title(text, md_file.stem)
        display = build_display_metadata(rel_id, title)
        nodes[rel_id] = {
            "id": rel_id,
            "title": title,
            "kind": infer_page_kind(rel_id, fm),
            "tags": ensure_string_list(fm.get("tags")),
            "summary": extract_page_summary(text),
            "hash": current_hashes[rel_id],
            **display,
        }
        adjacency[rel_id] = {"out": [], "in": []}

    edge_map: dict[tuple[str, str, str], dict[str, object]] = {}
    current_ids = set(nodes.keys())
    graph_href_cache: dict[str, str | None] = {}
    for rel_id, text in texts.items():
        for href in extract_md_link_hrefs(text):
            if href not in graph_href_cache:
                graph_href_cache[href] = canonicalize_graph_href(root_path, href)
            target = graph_href_cache[href]
            if target is None or target == rel_id or target not in current_ids:
                continue
            add_graph_edge(adjacency, edge_map, rel_id, target, "links_to")

        for src_ref in ensure_string_list(frontmatters[rel_id].get("sources")):
            if src_ref not in graph_href_cache:
                graph_href_cache[src_ref] = canonicalize_graph_href(root_path, src_ref)
            target = graph_href_cache[src_ref]
            if target is None or target == rel_id or target not in current_ids:
                continue
            add_graph_edge(adjacency, edge_map, rel_id, target, "derived_from")

    reverse_links: dict[str, list[str]] = {}
    for rel_id, buckets in adjacency.items():
        buckets["out"].sort(key=lambda item: (item["to"], item["kind"]))
        buckets["in"].sort(key=lambda item: (item["from"], item["kind"]))
        reverse_links[rel_id] = [item["from"] for item in buckets["in"]]
        nodes[rel_id]["outbound"] = len(buckets["out"])
        nodes[rel_id]["inbound"] = len(buckets["in"])
        nodes[rel_id]["degree"] = len(buckets["out"]) + len(buckets["in"])

    removed_ids = set(str(k) for k in old_hashes.keys()) - current_ids
    changed_ids = {
        rel_id for rel_id, digest in current_hashes.items()
        if old_hashes.get(rel_id) != digest
    }
    dirty_ids = set(changed_ids)
    dirty_ids.update(rel_id for rel_id in current_ids if sidecar_needs_refresh(root_path, rel_id))
    for rel_id in changed_ids | removed_ids:
        old_inbound = old_reverse.get(rel_id, [])
        if isinstance(old_inbound, list):
            dirty_ids.update(str(v) for v in old_inbound)
        dirty_ids.update(reverse_links.get(rel_id, []))
        if rel_id in adjacency:
            dirty_ids.update(item["to"] for item in adjacency[rel_id]["out"])
            dirty_ids.update(item["from"] for item in adjacency[rel_id]["in"])
    dirty_current = sorted(rel_id for rel_id in dirty_ids if rel_id in current_ids)

    for rel_id in dirty_current:
        page_path = root_path / rel_id
        sidecar_path = Path(f"{page_path}.graph")
        atomic_write_json(sidecar_path, build_sidecar_payload(rel_id, nodes[rel_id], adjacency[rel_id]))

    for rel_id in sorted(removed_ids):
        stale_sidecar = Path(f"{root_path / rel_id}.graph")
        if stale_sidecar.exists():
            stale_sidecar.unlink()

    node_list = sorted(
        (
            {
                "id": rel_id,
                "title": data["title"],
                "displayName": data["displayName"],
                "shortName": data["shortName"],
                "qualifiedName": data["qualifiedName"],
                "breadcrumb": data["breadcrumb"],
                "fileName": data["fileName"],
                "parentName": data["parentName"],
                "kind": data["kind"],
                "tags": data["tags"],
                "summary": data["summary"],
                "degree": data["degree"],
                "inbound": data["inbound"],
                "outbound": data["outbound"],
            }
            for rel_id, data in nodes.items()
        ),
        key=lambda item: item["id"],
    )
    edge_list = sorted(
        edge_map.values(),
        key=lambda item: (str(item["s"]), str(item["t"]), str(item["k"])),
    )
    adjacency_payload = {rel_id: adjacency[rel_id] for rel_id in sorted(adjacency)}
    issues_payload = {
        "orphans": sorted(p.relative_to(root_path).as_posix() for p in orphans),
        "deadLinks": [
            {"source": source.relative_to(root_path).as_posix(), "target": href}
            for source, href in dead_links
        ],
        "missingIndexEntries": sorted(p.relative_to(root_path).as_posix() for p in not_in_index),
        "scale": scale_issues or {},
        "contradictionMarkers": contradiction_markers or [],
        "contradictoryClaims": contradictory_claims or [],
    }
    stats_payload = {
        "pages": len(node_list),
        "edges": len(edge_list),
        "orphans": len(orphans),
        "deadLinks": len(dead_links),
        "mdBytes": int((scale_issues or {}).get("totalBytes") or 0),
        "largePages": len((scale_issues or {}).get("largePages") or []),
        "contradictionMarkers": len(contradiction_markers or []),
        "contradictoryClaims": len(contradictory_claims or []),
        "byKind": dict(sorted(Counter(str(node["kind"]) for node in node_list).items())),
    }

    atomic_write_json(graph_dir / "nodes.graph", node_list)
    atomic_write_json(graph_dir / "adjacency.graph", adjacency_payload)
    atomic_write_json(graph_dir / "stats.graph", stats_payload)
    atomic_write_json(graph_dir / "issues.graph", issues_payload)
    recent_payload = build_recent_log_graph(root_path, nodes)
    atomic_write_json_semantic(graph_dir / "recent.graph", recent_payload, {"generatedAt"})
    navigation_payload = build_navigation_graph(root_path, nodes)
    atomic_write_json(graph_dir / "navigation.graph", navigation_payload)
    lineage_payload = build_lineage_graph(root_path, nodes, frontmatters, texts)
    atomic_write_json(graph_dir / "lineage.graph", lineage_payload)
    atomic_write_json(graph_dir / "settings.graph", build_graph_settings())

    shard_names: list[str] = []
    shard_size = 1000
    for index, start in enumerate(range(0, len(edge_list), shard_size)):
        shard_name = f"edges-{index:03d}.graph"
        shard_names.append(shard_name)
        atomic_write_json(graph_dir / shard_name, edge_list[start:start + shard_size])
    if not shard_names:
        shard_names = ["edges-000.graph"]
        atomic_write_json(graph_dir / shard_names[0], [])

    for stale in graph_dir.glob("edges-*.graph"):
        if stale.name not in shard_names:
            stale.unlink()

    manifest_payload = {
        "schema": 1,
        "generatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "root": root_path.name,
        "nodesFile": "nodes.graph",
        "adjacencyFile": "adjacency.graph",
        "edgeShards": shard_names,
        "statsFile": "stats.graph",
        "issuesFile": "issues.graph",
        "recentFile": "recent.graph",
        "navigationFile": "navigation.graph",
        "lineageFile": "lineage.graph",
        "settingsFile": "settings.graph",
        "viewFile": "view.graph",
    }
    atomic_write_json_semantic(graph_dir / "manifest.graph", manifest_payload, {"generatedAt"})

    view_path = graph_dir / "view.graph"
    if not view_path.exists():
        atomic_write_json(view_path, {})

    atomic_write_json(cache_dir / "page-hashes.graph", current_hashes)
    atomic_write_json(
        cache_dir / "reverse-links.graph",
        {key: sorted(set(value)) for key, value in sorted(reverse_links.items())},
    )
    atomic_write_json(cache_dir / "dirty.graph", dirty_current)

    return {
        "dirty": len(dirty_current),
        "removed": len(removed_ids),
        "nodes": len(node_list),
        "edges": len(edge_list),
    }


# ── Git-first incremental change detection (O(changes), not O(corpus)) ───────
# The stat-cache path (cached_changed_wiki_files) must stat every page and hold
# the whole corpus's stats in memory to spot `git pull` / checkout deltas — an
# O(total-pages) memory floor that defeats scale. When the wiki lives in git we
# get the same coverage in O(changes): `git status` for the working tree, plus
# `git diff <last-linted-HEAD> HEAD` for anything committed since the last lint
# (which is exactly what pull/checkout produce). The last-linted commit is kept
# in a tiny sidecar so no per-page state is needed.

def last_lint_head_path(root_path: Path) -> Path:
    return root_path / ".graph-cache" / "last-lint-head.graph"


def read_last_lint_head(root_path: Path) -> str | None:
    data = load_json_file(last_lint_head_path(root_path), {})
    if isinstance(data, dict):
        head = data.get("head")
        if isinstance(head, str) and head:
            return head
    return None


def write_last_lint_head(root_path: Path, head: str | None) -> None:
    if head:
        atomic_write_json(last_lint_head_path(root_path), {"head": head})


def git_head_sha(root_path: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root_path), "rev-parse", "HEAD"],
            check=False, capture_output=True, text=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def git_committed_wiki_changes(
    root_path: Path, last_sha: str | None, head: str | None
) -> tuple[list[Path], list[str]]:
    """`git diff --name-only <last_sha> <head> -- wiki` → committed-since-last-lint.
    Empty when there is no baseline, no HEAD, or the two match. Bad/rebased-away
    SHAs simply yield nothing (git status still covers the working tree)."""
    if not last_sha or not head or last_sha == head:
        return [], []
    try:
        result = subprocess.run(
            ["git", "-C", str(root_path), "diff", "--name-only", "-z",
             last_sha, head, "--", "wiki"],
            check=False, capture_output=True, text=True,
        )
    except (OSError, subprocess.SubprocessError):
        return [], []
    if result.returncode != 0:
        return [], []
    changed: list[Path] = []
    deleted: list[str] = []
    for rel in (e for e in result.stdout.split("\0") if e):
        if not rel.startswith("wiki/") or not rel.endswith(".md"):
            continue
        path = (root_path / rel).resolve()
        if path.exists():
            changed.append(path)
        else:
            deleted.append(rel)
    return changed, deleted


def git_toplevel(root_path: Path) -> Path | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root_path), "rev-parse", "--show-toplevel"],
            check=False, capture_output=True, text=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    top = result.stdout.strip()
    if not top:
        return None
    try:
        return Path(top).resolve()
    except OSError:
        return None


def changed_wiki_files_gitfirst(
    root_path: Path,
) -> tuple[list[Path], list[str], str, str | None] | None:
    """O(changes) change set for a git-backed wiki, else None (caller falls back
    to the stat-cache sweep). Union of working-tree status and commits since the
    last lint. Returns (changed_paths, deleted_rels, source, current_head).

    Engages ONLY when the wiki root IS the git repo root: git reports paths
    relative to the repo top-level, and the `wiki/…` pathspec/prefix logic
    assumes those coincide. A wiki nested inside a larger repo (e.g. a vendored
    demo) would yield `subdir/wiki/…` paths that silently miss the `wiki/`
    prefix — so those defer to the stat-cache path, which is correct there."""
    top = git_toplevel(root_path)
    if top is None or top != root_path.resolve():
        return None
    status = git_changed_wiki_files(root_path)  # None if not git / wiki ignored
    if status is None:
        return None
    status_changed, status_deleted, _ = status
    head = git_head_sha(root_path)
    last = read_last_lint_head(root_path)
    committed_changed, committed_deleted = git_committed_wiki_changes(root_path, last, head)
    changed = sorted(set(status_changed) | set(committed_changed))
    deleted = sorted(set(status_deleted) | set(committed_deleted))
    source = "git status+diff" if last else "git status (cold baseline recorded)"
    return changed, deleted, source, head


def collect_scale_for_pages(pages: set[Path]) -> dict[str, object]:
    """Scale payload over ONLY the given pages (O(changes) memory) — the
    incremental-mode analogue of collect_wiki_md_scale, which sweeps the whole
    corpus. `stats` is intentionally empty: git-first mode does not maintain the
    per-page stat cache."""
    files: list[Path] = []
    large_pages: list[tuple[int, Path]] = []
    huge_pages: list[tuple[int, Path]] = []
    stat_errors: list[tuple[Path, str]] = []
    total_bytes = 0
    for path in sorted(pages):
        try:
            stat = path.stat()
        except OSError as exc:
            stat_errors.append((path, str(exc)))
            continue
        size = stat.st_size
        files.append(path)
        total_bytes += size
        if size >= MD_PAGE_WARN_BYTES:
            large_pages.append((size, path))
        if size >= MD_PAGE_HARD_BYTES:
            huge_pages.append((size, path))
    large_pages.sort(key=lambda item: item[0], reverse=True)
    return {
        "files": files,
        "totalBytes": total_bytes,
        "largest": large_pages[:20],
        "stats": {},
        "largePages": large_pages,
        "hugePages": huge_pages,
        "statErrors": stat_errors,
    }


def lint(root: str, changed_only: bool = True) -> int:
    root_path = Path(root).resolve()
    wiki_path = root_path / "wiki"
    log_path = root_path / "log"
    audit_path = root_path / "audit"

    if not wiki_path.exists():
        print(f"ERROR: wiki/ directory not found at {wiki_path}", file=sys.stderr)
        return 1

    ingested, ingest_warnings = ingest_audit_sidecars(root_path)
    if ingested:
        print(f"✅ Ingested {ingested} audit entr{'y' if ingested == 1 else 'ies'} "
              f"from wiki/**/*.md.audit sidecars into audit/")
    for w in ingest_warnings:
        print(f"⚠️  audit sidecar: {w}", file=sys.stderr)

    open_audits = open_audit_files(root_path)
    if open_audits:
        print_open_audit_preflight(root_path, open_audits)
        return 1

    index_path = (wiki_path / "index.md").resolve()
    git_head_to_record: str | None = None
    use_stat_cache = True  # write the O(corpus) md stat cache? only in non-git modes

    deleted_changed_pages: list[str] = []
    if changed_only:
        gitfirst = changed_wiki_files_gitfirst(root_path)
        if gitfirst is not None:
            # ── Git-backed wiki → O(changes) detection, no whole-corpus sweep ──
            changed_files, deleted_changed_pages, source, git_head_to_record = gitfirst
            use_stat_cache = False
            changed_file_set = {
                p for p in changed_files if p.name.endswith(".md") and p.is_file()
            }
            all_wiki_files = []          # unused in incremental (orphan/graph skipped)
            wiki_file_set = set()        # inbound tracking unused in incremental
            md_scale = collect_scale_for_pages(changed_file_set)
            scale_issues = scale_issue_payload(root_path, md_scale)
            if not changed_file_set and not deleted_changed_pages:
                print(f"✅ Changed-only lint: no changed wiki Markdown files ({source})")
                write_last_lint_head(root_path, git_head_to_record)
                return 0
        else:
            # ── Non-git wiki → stat-cache sweep (O(corpus) memory; the fallback) ──
            md_scale = collect_wiki_md_scale(wiki_path)
            scale_issues = scale_issue_payload(root_path, md_scale)
            files_from_scale = md_scale.get("files", [])
            all_wiki_files = files_from_scale if isinstance(files_from_scale, list) else []
            wiki_file_set = set(all_wiki_files)
            changed_files, deleted_changed_pages, source = changed_wiki_files(root_path, md_scale)
            changed_file_set = {p for p in changed_files if p in wiki_file_set}
            if not changed_file_set and not deleted_changed_pages:
                print(f"✅ Changed-only lint: no changed wiki Markdown files ({source})")
                write_md_stat_cache(root_path, md_scale)
                return 0
        if not print_scale_preflight(
            root_path,
            md_scale,
            total_hard_stop=False,
            page_scope=changed_file_set,
        ):
            return 1
        all_lint_files = sorted(changed_file_set)
        print(
            f"✅ Changed-only lint scope: {len(all_lint_files)} changed file(s) "
            f"({source}); global graph/index/orphan checks skipped"
        )
        if deleted_changed_pages:
            print(
                f"\n🟡 Deleted wiki pages ({len(deleted_changed_pages)}) need full lint "
                "to repair inbound links/index:"
            )
            for rel in deleted_changed_pages[:20]:
                print(f"   {rel}")
            if len(deleted_changed_pages) > 20:
                print(f"   … and {len(deleted_changed_pages) - 20} more")
    else:
        md_scale = collect_wiki_md_scale(wiki_path)
        scale_issues = scale_issue_payload(root_path, md_scale)
        files_from_scale = md_scale.get("files", [])
        all_wiki_files = files_from_scale if isinstance(files_from_scale, list) else []
        wiki_file_set = set(all_wiki_files)
        git_head_to_record = git_head_sha(root_path)
        if not print_scale_preflight(root_path, md_scale):
            return 1
        all_lint_files = all_wiki_files

    issues = 0
    if deleted_changed_pages:
        issues += len(deleted_changed_pages)
    inbound: set[Path] = set()
    resolve_cache: dict[str, Path | None] = {}

    # ── Pass 1: banned ../ escape paths ─────────────────────────────────
    escape_links: list[tuple[Path, int, str, str | None]] = []
    absolute_links: list[tuple[Path, int, str, str | None]] = []
    missing_wiki_prefix_links: list[tuple[Path, int, str, str | None]] = []
    # ── Pass 3: dead MD links ──────────────────────────────────────────────
    dead_links: list[tuple[Path, str]] = []
    missing_href_counts: dict[str, int] = defaultdict(int)
    # ── Pass 4: malformed link URLs (unquoted whitespace) ──────────────────
    malformed: list[tuple[Path, int, str]] = []
    # ── Pass 8: residual wikilinks ─────────────────────────────────────────
    residual_hits: list[tuple[Path, int]] = []
    # ── Structural: expected-link sections with plain-text bullets ─────────
    non_link_bullets: list[tuple[Path, int, str, str]] = []
    contradiction_markers: list[dict[str, object]] = []
    numeric_claims: list[dict[str, object]] = []
    preloaded_texts: dict[str, str] = {}
    for md_file in all_lint_files:
        text = md_file.read_text(encoding="utf-8")
        rel_id = md_file.relative_to(root_path).as_posix()
        preloaded_texts[rel_id] = text
        line_starts = build_line_starts(text)
        rel_md = md_file.relative_to(root_path)

        for line_no, path in extract_banned_parent_paths(text, line_starts):
            suggested = None
            if path.startswith("../wiki/"):
                suggested = path[3:]
            escape_links.append((md_file, line_no, path, suggested))
        for line_no, path in extract_absolute_md_paths(text, line_starts):
            # A `/foo/bar.md` that resolves under wiki/ is a legal OKF
            # content-root link, not a stray filesystem-absolute path.
            if resolve_href(root_path, path) is not None:
                continue
            suggested = canonicalize_absolute_path(root_path, path)
            absolute_links.append((md_file, line_no, path, suggested))
        for line_no, url in extract_malformed_link_urls(text, line_starts):
            malformed.append((md_file, line_no, url))

        for line_no, section, bullet_text in extract_non_link_bullets(text, rel_md):
            non_link_bullets.append((md_file, line_no, section, bullet_text))

        hits = len(RESIDUAL_WIKILINK_RE.findall(text))
        if hits:
            residual_hits.append((md_file, hits))

        for line_no, term, excerpt in extract_contradiction_markers(text):
            contradiction_markers.append({
                "source": rel_id,
                "line": line_no,
                "term": term,
                "excerpt": excerpt,
            })
        for line_no, subject, predicate, kind, value, excerpt in extract_numeric_claims(text):
            numeric_claims.append({
                "source": rel_id,
                "line": line_no,
                "subject": subject,
                "predicate": predicate,
                "kind": kind,
                "value": value,
                "excerpt": excerpt,
            })

        for line_no, href in extract_md_link_hrefs_with_lines(text, line_starts):
            target: Path | None = None
            if "../" in href or href == "..":
                suggested = None
                if href.startswith("../wiki/"):
                    suggested = href[3:]
                if suggested is not None:
                    if suggested not in resolve_cache:
                        resolve_cache[suggested] = resolve_href(root_path, suggested)
                    target = resolve_cache[suggested]
                    if target is None:
                        dead_links.append((md_file, href))
                        missing_href_counts[suggested] += 1
                    elif target in wiki_file_set:
                        inbound.add(target)
                else:
                    dead_links.append((md_file, href))
                    missing_href_counts[posixpath.normpath(href)] += 1
                continue

            if href.startswith("/"):
                # OKF content-root anchor — legal, resolves under wiki/.
                resolved_href = posixpath.normpath("wiki" + href)
            else:
                normalized = posixpath.normpath(href)
                resolved_href = normalized
                if not any(normalized.startswith(p) for p in VALID_ROOT_PREFIXES):
                    suggested = canonicalize_href_with_wiki_prefix(normalized)
                    missing_wiki_prefix_links.append((md_file, line_no, href, suggested))
                    if suggested is not None:
                        resolved_href = suggested

            if resolved_href not in resolve_cache:
                resolve_cache[resolved_href] = resolve_href(root_path, resolved_href)
            target = resolve_cache[resolved_href]
            if target is None:
                dead_links.append((md_file, href))
                resolved_key = posixpath.normpath(resolved_href)
                missing_href_counts[resolved_key] += 1
            elif target in wiki_file_set:
                inbound.add(target)

    valid_prefix_list = ", ".join(p.rstrip("/") for p in VALID_ROOT_PREFIXES)
    if escape_links:
        print(f"\n🔴 Banned '../' paths ({len(escape_links)}) — links must be wiki-root-relative:")
        for source, line_no, href, suggested in escape_links:
            if suggested is not None:
                print(f"   {source.relative_to(root_path)}:{line_no} → {href}  (use: {suggested})")
            else:
                print(f"   {source.relative_to(root_path)}:{line_no} → {href}  (use: <cannot auto-suggest>)")
        print(f"   rule: paths are rooted at wiki-root ({valid_prefix_list}/) — never use ../")
        issues += len(escape_links)
    else:
        print("✅ No banned '../' paths")

    if missing_wiki_prefix_links:
        print(f"\n🔴 Missing root prefix ({len(missing_wiki_prefix_links)}) — links must start with a valid wiki-root segment:")
        for source, line_no, href, suggested in missing_wiki_prefix_links:
            if suggested is not None:
                print(f"   {source.relative_to(root_path)}:{line_no} → {href}  (use: {suggested})")
            else:
                print(f"   {source.relative_to(root_path)}:{line_no} → {href}  (use: <cannot auto-suggest>)")
        print(f"   rule: allowed root segments: {valid_prefix_list}/")
        issues += len(missing_wiki_prefix_links)
    else:
        print("✅ No missing root prefix")

    if absolute_links:
        print(f"\n🔴 Absolute paths ({len(absolute_links)}) — links must be wiki-root-relative:")
        for source, line_no, href, suggested in absolute_links:
            if suggested is not None:
                print(f"   {source.relative_to(root_path)}:{line_no} → {href}  (use: {suggested})")
            else:
                print(f"   {source.relative_to(root_path)}:{line_no} → {href}  (use: <cannot auto-suggest>)")
        print("   rule: inside wiki/, never use filesystem-absolute paths")
        issues += len(absolute_links)
    else:
        print("✅ No absolute paths")

    if dead_links:
        print(f"\n🔴 Dead links ({len(dead_links)}):")
        for source, href in dead_links:
            print(f"   {source.relative_to(root_path)} → {href}")
        issues += len(dead_links)
    else:
        print("✅ No dead links")

    if malformed:
        print(
            f"\n🟡 Malformed link URLs ({len(malformed)}) — whitespace in URL "
            f"but not wrapped in <...>:"
        )
        for source, line_no, url in malformed:
            print(f"   {source.relative_to(root_path)}:{line_no} → ({url})")
        print(
            "   fix: wrap the URL in angle brackets, e.g. "
            "[Page Name](<path with space.md>)"
        )
        issues += len(malformed)
    else:
        print("✅ No malformed link URLs")

    if non_link_bullets:
        print(
            f"\n🟡 Plain-text bullets in link-required sections ({len(non_link_bullets)}):"
        )
        for source, line_no, section, bullet_text in non_link_bullets:
            print(
                f"   {source.relative_to(root_path)}:{line_no} "
                f"[section: {section}] → {bullet_text}"
            )
        print("   fix: wrap each listed page in a standard MD link")
        issues += len(non_link_bullets)
    else:
        print("✅ No plain-text bullets in link-required sections")

    # ── Pass 5: orphan pages ────────────────────────────────────────────────
    orphans: list[Path] = []
    if changed_only:
        print("↪️  Skipped orphan-page check in changed-only mode")
    else:
        orphans = [
            p for p in all_wiki_files
            if p != index_path and p not in inbound
        ]
        if orphans:
            print(f"\n🟡 Orphan pages ({len(orphans)}) — no inbound links from other wiki pages:")
            for p in orphans:
                print(f"   {p.relative_to(root_path)}")
            issues += len(orphans)
        else:
            print("✅ No orphan pages")

    # ── Pass 6: missing index entries ───────────────────────────────────────
    not_in_index: list[Path] = []
    if changed_only:
        print("↪️  Skipped full index coverage check in changed-only mode")
    elif index_path.exists():
        index_text = index_path.read_text(encoding="utf-8")
        linked_from_index: set[Path] = set()
        for href in extract_md_link_hrefs(index_text):
            resolved_href = posixpath.normpath(href)
            if not resolved_href.startswith("wiki/"):
                suggested = canonicalize_href_with_wiki_prefix(resolved_href)
                if suggested is not None:
                    resolved_href = suggested
            if resolved_href not in resolve_cache:
                resolve_cache[resolved_href] = resolve_href(root_path, resolved_href)
            resolved = resolve_cache[resolved_href]
            if resolved:
                linked_from_index.add(resolved)
        not_in_index = [
            p for p in all_wiki_files
            if p != index_path and p not in linked_from_index
        ]
        if not_in_index:
            print(f"\n🟡 Pages missing from index.md ({len(not_in_index)}):")
            for p in not_in_index:
                print(f"   {p.relative_to(root_path)}")
            issues += len(not_in_index)
        else:
            print("✅ All pages in index.md")
    else:
        print("⚠️  wiki/index.md not found — skipping index check")

    # ── Pass 7: frequently-missing targets ─────────────────────────────────
    frequent_missing = [
        (key, count) for key, count in missing_href_counts.items() if count >= 3
    ]
    if frequent_missing:
        print(f"\n🟡 Missing link targets referenced 3+ times ({len(frequent_missing)}):")
        for key, count in sorted(frequent_missing, key=lambda x: -x[1]):
            # Print as relative to root when possible for readability.
            try:
                rel = str(Path(key).resolve().relative_to(root_path))
            except ValueError:
                rel = key
            print(f"   {rel} — referenced {count}x")
        issues += len(frequent_missing)
    else:
        print("✅ No frequently-missing targets")

    if residual_hits:
        total = sum(h for _, h in residual_hits)
        print(f"\n🟡 Residual [[wikilinks]] ({total} in {len(residual_hits)} files) — run:")
        print(f"   python3 scripts/migrate_wikilinks.py {root}")
        for p, hits in residual_hits[:10]:
            print(f"   {p.relative_to(root_path)} — {hits} hit(s)")
        if len(residual_hits) > 10:
            print(f"   … and {len(residual_hits) - 10} more file(s)")
        issues += total
    else:
        print("✅ No residual wikilinks")

    # ── Pass 12: dead `sources:` refs ──────────────────────────────────────
    dead_sources: list[tuple[Path, str]] = []
    for md_file in all_lint_files:
        rel_id = md_file.relative_to(root_path).as_posix()
        fm = parse_frontmatter(preloaded_texts.get(rel_id, "")) or {}
        for source_ref in ensure_string_list(fm.get("sources")):
            if EXTERNAL_URL_RE.match(source_ref):
                continue
            if canonicalize_raw_literal(root_path, source_ref) is not None:
                continue
            if canonicalize_graph_href(root_path, source_ref) is not None:
                continue
            dead_sources.append((md_file, source_ref))
    if dead_sources:
        print(f"\n🔴 Dead `sources:` refs ({len(dead_sources)}):")
        for source, ref in dead_sources:
            print(f"   {source.relative_to(root_path)} → {ref}")
        issues += len(dead_sources)
    else:
        print("✅ No dead `sources:` refs")

    if contradiction_markers:
        print(
            f"\n🟡 Contradiction/staleness markers ({len(contradiction_markers)}) — "
            "review before they become durable claims:"
        )
        for marker in contradiction_markers[:20]:
            print(
                f"   {marker['source']}:{marker['line']} "
                f"[{marker['term']}] → {marker['excerpt']}"
            )
        if len(contradiction_markers) > 20:
            print(f"   … and {len(contradiction_markers) - 20} more marker(s)")
        print(
            "   follow-up: reconcile the page, file an audit, or move the uncertainty "
            "to SCHEMA.md Open research questions."
        )
    else:
        print("✅ No contradiction/staleness markers")

    contradictory_claims = find_contradictory_claims(numeric_claims)
    if contradictory_claims:
        # Heuristic only: subject normalization is loose, so model variants
        # (e.g. "Llama has 7B" vs "Llama has 70B") will produce false positives.
        # Reported as a soft signal; does not affect lint exit code.
        print(
            f"\n🟡 Possible cross-page numeric claim conflicts ({len(contradictory_claims)}) — "
            "heuristic match, verify before treating as a real contradiction:"
        )
        for conflict in contradictory_claims[:10]:
            print(
                f"   {conflict['subject']} / {conflict['predicate']} / "
                f"{conflict['kind']} → {', '.join(conflict['values'])}"
            )
            for evidence in conflict["evidence"][:4]:
                print(
                    f"      {evidence['source']}:{evidence['line']} "
                    f"[{evidence['value']}] → {evidence['excerpt']}"
                )
        if len(contradictory_claims) > 10:
            print(f"   … and {len(contradictory_claims) - 10} more conflict(s)")
        print("   follow-up: if it is a real conflict, reconcile the pages or file an audit item.")
    else:
        print("✅ No cross-page numeric claim conflicts")

    # ── Pass 9: log/ shape ───────────────────────────────────────────────────
    if changed_only:
        print("↪️  Skipped log/ shape check in changed-only mode")
    elif log_path.exists() and log_path.is_dir():
        log_issues: list[str] = []
        for p in sorted(log_path.iterdir()):
            if p.is_dir():
                continue
            if p.name == ".gitkeep":
                continue
            m = LOG_FILENAME_RE.match(p.name)
            if not m:
                log_issues.append(f"   {p.relative_to(root_path)} — filename doesn't match YYYYMMDD.md")
                continue
            y, mo, d = m.groups()
            iso = f"{y}-{mo}-{d}"
            first_line = p.read_text(encoding="utf-8").splitlines()[:1]
            if not first_line or first_line[0].strip() != f"# {iso}":
                log_issues.append(
                    f"   {p.relative_to(root_path)} — expected first line '# {iso}' "
                    "(log files do not use YAML frontmatter)"
                )
        if log_issues:
            print(f"\n🟡 log/ shape issues ({len(log_issues)}):")
            for s in log_issues:
                print(s)
            issues += len(log_issues)
        else:
            print("✅ log/ shape OK")
    else:
        print("⚠️  log/ directory not found — skipping log shape check")

    # ── Pass 10: audit/ shape ─────────────────────────────────────────────────
    audit_targets_to_check: list[tuple[str, str]] = []
    if changed_only:
        print("↪️  Skipped audit/ shape check in changed-only mode")
    elif audit_path.exists() and audit_path.is_dir():
        audit_files = [
            p for p in audit_path.rglob("*.md") if p.name != ".gitkeep"
        ]
        audit_issues: list[str] = []
        for p in audit_files:
            text = p.read_text(encoding="utf-8")
            fm = parse_frontmatter(text)
            rel = p.relative_to(root_path)
            if fm is None:
                audit_issues.append(f"   {rel} — missing YAML frontmatter")
                continue
            missing = AUDIT_REQUIRED_FIELDS - set(fm.keys())
            if missing:
                audit_issues.append(
                    f"   {rel} — missing fields: {', '.join(sorted(missing))}"
                )
                continue
            if fm["type"] not in VALID_AUDIT_TYPES:
                audit_issues.append(
                    f"   {rel} — invalid type '{fm['type']}' (expected one of {sorted(VALID_AUDIT_TYPES)})"
                )
            if fm["source"] not in VALID_SOURCES:
                audit_issues.append(
                    f"   {rel} — invalid source '{fm['source']}'"
                )
            for coord_key in ("start", "end"):
                coord = fm.get(coord_key)
                if not (
                    isinstance(coord, list)
                    and len(coord) == 2
                    and all(isinstance(v, int) and v >= 1 for v in coord)
                ):
                    audit_issues.append(
                        f"   {rel} — invalid {coord_key} {coord!r} (expected [line, col] with 1-indexed ints)"
                    )
            expected_status = "resolved" if "resolved" in p.parts else "open"
            if fm["status"] != expected_status:
                audit_issues.append(
                    f"   {rel} — status '{fm['status']}' doesn't match directory (expected '{expected_status}')"
                )
            if fm["status"] == "open":
                audit_targets_to_check.append((fm["id"], fm["target"]))

        if audit_issues:
            print(f"\n🔴 audit/ shape issues ({len(audit_issues)}):")
            for s in audit_issues:
                print(s)
            issues += len(audit_issues)
        else:
            print(f"✅ audit/ shape OK ({len(audit_files)} files)")
    else:
        print("⚠️  audit/ directory not found — skipping audit shape check")

    # ── Pass 11: audit targets exist ─────────────────────────────────────────
    missing_targets: list[tuple[str, str]] = []
    for audit_id, target in audit_targets_to_check:
        target_path = root_path / target
        if not target_path.exists():
            alt = wiki_path / target
            if not alt.exists():
                missing_targets.append((audit_id, target))
    if missing_targets:
        print(f"\n🔴 Open audits with missing target files ({len(missing_targets)}):")
        for audit_id, target in missing_targets:
            print(f"   {audit_id} → {target}")
        issues += len(missing_targets)
    elif audit_targets_to_check:
        print("✅ All open-audit targets exist")

    if changed_only:
        print("↪️  Skipped legacy .agraph cleanup in changed-only mode")
    else:
        removed_agraph = cleanup_legacy_agraph_artifacts(root_path)
        if removed_agraph:
            print(f"✅ Removed legacy .agraph artifacts ({removed_agraph})")

    # ── Graph compile ───────────────────────────────────────────────────────
    if changed_only:
        scoped_graph_stats = write_scoped_graph_sidecars(
            root_path=root_path,
            scoped_wiki_files=all_lint_files,
            all_wiki_file_set=wiki_file_set,
            preloaded_texts=preloaded_texts,
        )
        print(
            "✅ Scoped graph sidecars refreshed "
            f"({scoped_graph_stats['sidecars']} page-local sidecars; global graph skipped)"
        )
        if issues == 0 and use_stat_cache:
            write_md_stat_cache(root_path, md_scale)
    else:
        graph_stats = write_graph_artifacts(
            root_path=root_path,
            all_wiki_files=all_wiki_files,
            orphans=orphans,
            dead_links=dead_links,
            not_in_index=not_in_index,
            scale_issues=scale_issues,
            contradiction_markers=contradiction_markers,
            contradictory_claims=contradictory_claims,
            preloaded_texts=preloaded_texts,
        )
        write_md_stat_cache(root_path, md_scale)
        print(
            "✅ Graph artifacts refreshed "
            f"({graph_stats['nodes']} nodes, {graph_stats['edges']} edges, "
            f"{graph_stats['dirty']} dirty sidecars, {graph_stats['removed']} removed)"
        )

    # ── Summary ─────────────────────────────────────────────────────────────
    print(f"\n{'─'*40}")
    if issues == 0:
        if changed_only:
            print("✅ Changed wiki files are healthy — no changed-scope issues found")
        else:
            print("✅ Wiki is healthy — no issues found")
    else:
        print(f"⚠️  {issues} issue(s) found — review above and fix before next ingest")

    # Advance the git-first baseline only on a clean pass, so an unresolved run
    # is re-examined next time rather than being marked "already linted".
    if issues == 0 and git_head_to_record:
        write_last_lint_head(root_path, git_head_to_record)

    return 0 if issues == 0 else 1


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Health check for an LLM Wiki.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("wiki_root", help="Path to the wiki root")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--full",
        action="store_true",
        dest="full",
        help="Run the full lint suite: rebuild graph/, plus orphan / missing-index / cross-page contradiction passes. Periodic only — by default lint is incremental.",
    )
    mode.add_argument(
        "--changed",
        "--changed-only",
        action="store_true",
        dest="changed_alias",
        help="(deprecated alias — incremental is now the default; passing this still works but is a no-op)",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = parse_args(sys.argv[1:])
    sys.exit(lint(args.wiki_root, changed_only=not args.full))
