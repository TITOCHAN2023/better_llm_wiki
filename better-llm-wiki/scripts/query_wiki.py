#!/usr/bin/env python3
"""
query_wiki.py — Agent-friendly discovery for an LLM Wiki.

This script does not answer the user's question. It gives the agent a ranked
reading list before synthesis: lexical matches, graph-near pages, recently
changed pages, and central pages.

Usage:
    python3 query_wiki.py <wiki-root> [question or keywords]

Examples:
    python3 query_wiki.py ./my-wiki "RAG vs llm-wiki tradeoffs"
    python3 query_wiki.py ./my-wiki --since 14d --sort updated
    python3 query_wiki.py ./my-wiki --related wiki/concepts/HyLo.md --depth 2
    python3 query_wiki.py ./my-wiki "long context" --related HyLo --format json
    python3 query_wiki.py ./my-wiki --kind summary --recent 30 --paths-only
    python3 query_wiki.py ./my-wiki "long context" --no-interest

Exit codes:
  0 — query ran
  2 — wiki root or options are invalid
"""

from __future__ import annotations

import argparse
import json
import math
import posixpath
import re
import sqlite3
import subprocess
import sys
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any


FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
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
EXTERNAL_URL_RE = re.compile(r"^[a-z][a-z0-9+.\-]*:", re.IGNORECASE)
ASCII_WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.+-]*")
CJK_RUN_RE = re.compile(r"[\u3400-\u9fff]+")
STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "can", "does", "for",
    "from", "how", "i", "in", "is", "it", "of", "on", "or", "say", "should",
    "the", "to", "vs", "what", "when", "where", "which", "who", "why", "wiki",
    "with",
}
INDEX_DB_REL = ".query-index/wiki.db"
INDEX_SCHEMA_VERSION = "1"


@dataclass
class Page:
    id: str
    path: Path
    title: str
    kind: str
    tags: list[str]
    summary: str
    text: str
    updated: datetime
    created: datetime | None
    inbound: int = 0
    outbound: int = 0
    outgoing: set[str] = field(default_factory=set)
    incoming: set[str] = field(default_factory=set)


@dataclass
class Hit:
    page: Page
    score: float
    lexical: float
    related: float
    centrality: float
    recency: float
    interest: float
    reasons: list[str]
    snippet: str


@dataclass
class InterestProfile:
    path: Path | None
    positive: Counter[str] = field(default_factory=Counter)
    negative: Counter[str] = field(default_factory=Counter)

    @property
    def active(self) -> bool:
        return bool(self.positive or self.negative)


def parse_frontmatter(text: str) -> dict[str, Any]:
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}
    result: dict[str, Any] = {}
    lines = m.group(1).splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#") or ":" not in line:
            i += 1
            continue
        key, _, rest = line.partition(":")
        key = key.strip()
        val = rest.strip()
        if val.startswith("[") and val.endswith("]"):
            inner = val[1:-1].strip()
            result[key] = [] if not inner else [p.strip().strip('"').strip("'") for p in inner.split(",")]
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
    return text[m.end():] if m else text


def string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def parse_dateish(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    if "T" in raw or raw.endswith("Z"):
        try:
            normalized = raw.replace("Z", "+00:00")
            dt = datetime.fromisoformat(normalized)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            pass
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.strptime(raw[:len(datetime.now().strftime(fmt))], fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


def path_mtime(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)


def extract_title(text: str, fallback: str) -> str:
    fm = parse_frontmatter(text)
    title = fm.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()
    for line in strip_frontmatter(text).splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return fallback


def extract_summary(text: str) -> str:
    body = strip_frontmatter(text)
    lines: list[str] = []
    in_fence = False
    for raw in body.splitlines():
        stripped = raw.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence or not stripped:
            if lines and not stripped:
                break
            continue
        if stripped.startswith("#") or stripped.startswith("!["):
            continue
        lines.append(stripped)
        if len(" ".join(lines)) > 220:
            break
    return re.sub(r"\s+", " ", " ".join(lines)).strip()[:260].rstrip()


def infer_kind(rel_id: str, fm: dict[str, Any]) -> str:
    page_type = fm.get("type")
    if isinstance(page_type, str) and page_type.strip():
        return page_type.strip()
    parts = rel_id.split("/")
    if len(parts) >= 3:
        if parts[1] == "concepts":
            return "concept"
        if parts[1] == "entities":
            return "entity"
        if parts[1] == "summaries":
            return "summary"
    return "index" if rel_id == "wiki/index.md" else "page"


def tokenize(text: str) -> Counter[str]:
    tokens: Counter[str] = Counter()
    for m in ASCII_WORD_RE.finditer(text.lower()):
        token = m.group(0)
        if len(token) >= 2 and token not in STOPWORDS:
            tokens[token] += 1
    for m in CJK_RUN_RE.finditer(text):
        run = m.group(0)
        if len(run) == 1:
            tokens[run] += 1
        else:
            tokens[run] += 2
            for i in range(len(run) - 1):
                tokens[run[i:i + 2]] += 1
    return tokens


def strip_md_link_markup(text: str) -> str:
    text = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\((?:<[^>]+>|[^)]+)\)", r"\1", text)
    return text


def parse_weight_hint(line: str, default: float) -> float:
    m = re.search(r"\bweight\s*:\s*([0-9]+(?:\.[0-9]+)?)", line, re.IGNORECASE)
    if not m:
        return default
    try:
        return max(0.1, min(5.0, float(m.group(1))))
    except ValueError:
        return default


def interest_signal_text(item: str, section: str) -> str:
    item = re.sub(r"^\[[ xX]\]\s*", "", item.strip())
    if not item or item.startswith("<") or "<" in item:
        return ""
    signal = item.split("|", 1)[0].strip()
    if "discussion signals" in section or "讨论信号" in section:
        parts = [part.strip() for part in signal.split("—") if part.strip()]
        if len(parts) >= 2:
            signal = parts[-1]
    signal = re.sub(r"\b\d{4}-\d{2}-\d{2}\b", "", signal).strip(" -—")
    if not signal or "initial empty interest profile" in signal.lower():
        return ""
    return signal


def load_interest_profile(root: Path) -> InterestProfile:
    path = root / "INTEREST.md"
    profile = InterestProfile(path=path if path.exists() else None)
    if not path.exists():
        return profile
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return profile

    section = ""
    in_fence = False
    positive_sections = (
        "current focus",
        "当前关注",
        "recurring interests",
        "长期兴趣",
        "positive signals",
        "正向信号",
        "source preferences",
        "来源偏好",
        "exploration queue",
        "探索队列",
        "discussion signals",
        "讨论信号",
    )
    negative_sections = (
        "downrank",
        "less relevant",
        "not interested",
        "暂不关注",
        "降权",
        "低优先级",
        "排除",
    )

    for raw in text.splitlines():
        stripped = raw.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if stripped.startswith("#"):
            section = stripped.lstrip("#").strip().lower()
            continue
        if not stripped.startswith(("-", "*")):
            continue
        item = strip_md_link_markup(stripped.lstrip("-* ").strip())
        if not item or item.startswith("<"):
            continue
        section_l = section.lower()
        is_negative = any(key in section_l for key in negative_sections)
        is_positive = any(key in section_l for key in positive_sections)
        if not is_negative and not is_positive:
            continue
        weight = parse_weight_hint(item, 1.0)
        signal = interest_signal_text(item, section_l)
        if not signal:
            continue
        tokens = tokenize(re.sub(r"\bweight\s*:\s*[0-9]+(?:\.[0-9]+)?", "", signal, flags=re.IGNORECASE))
        target = profile.negative if is_negative else profile.positive
        for token, count in tokens.items():
            target[token] += count * weight
    return profile


def interest_query_text(profile: InterestProfile, max_terms: int = 12) -> str:
    if not profile.positive:
        return ""
    terms = [
        term for term, _ in profile.positive.most_common(max_terms)
        if len(term) >= 2 and term not in STOPWORDS
    ]
    return " ".join(terms)


def extract_md_hrefs(text: str) -> list[str]:
    hrefs: list[str] = []
    for m in MD_LINK_RE.finditer(text):
        href = (m.group("bracketed") or m.group("bare") or "").strip()
        if "#" in href:
            href = href.split("#", 1)[0]
        if not href or EXTERNAL_URL_RE.match(href):
            continue
        hrefs.append(href)
    return hrefs


def canonicalize_href(root: Path, href: str) -> str | None:
    joined = posixpath.normpath(href)
    if joined.startswith("../") or joined == "..":
        return None
    if not joined.startswith("wiki/"):
        joined = posixpath.normpath(posixpath.join("wiki", joined))
    target = root / Path(joined)
    if not target.exists() or not target.is_file():
        return None
    try:
        return target.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return None


def load_pages(root: Path) -> dict[str, Page]:
    wiki_dir = root / "wiki"
    pages: dict[str, Page] = {}
    if not wiki_dir.exists():
        raise ValueError(f"missing wiki/ directory: {wiki_dir}")
    for path in sorted(wiki_dir.rglob("*.md")):
        if path.name.endswith(".md.graph") or ".graph-cache" in path.parts:
            continue
        rel_id = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8")
        fm = parse_frontmatter(text)
        updated = parse_dateish(fm.get("updated")) or parse_dateish(fm.get("created")) or path_mtime(path)
        created = parse_dateish(fm.get("created"))
        pages[rel_id] = Page(
            id=rel_id,
            path=path,
            title=extract_title(text, path.stem),
            kind=infer_kind(rel_id, fm),
            tags=string_list(fm.get("tags")),
            summary=extract_summary(text),
            text=text,
            updated=updated,
            created=created,
        )

    for page in pages.values():
        for href in extract_md_hrefs(page.text):
            target = canonicalize_href(root, href)
            if target and target in pages and target != page.id:
                page.outgoing.add(target)
                pages[target].incoming.add(page.id)

    for page in pages.values():
        page.outbound = len(page.outgoing)
        page.inbound = len(page.incoming)
    return pages


def index_db_path(root: Path) -> Path:
    return root / INDEX_DB_REL


def collect_indexable_wiki_files(root: Path) -> list[Path]:
    wiki_dir = root / "wiki"
    if not wiki_dir.exists():
        return []
    return [
        path for path in sorted(wiki_dir.rglob("*.md"))
        if path.is_file() and not path.name.endswith(".md.graph")
    ]


def query_index_is_fresh(root: Path, conn: sqlite3.Connection) -> bool:
    try:
        current_files = collect_indexable_wiki_files(root)
        current_ids = {path.relative_to(root).as_posix() for path in current_files}
        rows = conn.execute("SELECT id, mtime_ns, size_bytes FROM documents").fetchall()
    except (OSError, sqlite3.Error):
        return False

    indexed: dict[str, tuple[int, int]] = {}
    for row in rows:
        try:
            indexed[str(row[0])] = (int(row[1]), int(row[2]))
        except (TypeError, ValueError):
            return False

    if set(indexed.keys()) != current_ids:
        return False

    for path in current_files:
        rel_id = path.relative_to(root).as_posix()
        try:
            stat = path.stat()
        except OSError:
            return False
        if indexed.get(rel_id) != (stat.st_mtime_ns, stat.st_size):
            return False
    return True


INDEX_HEAD_REL = ".query-index/last-index-head.graph"


def _git_out(root: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args], check=False, capture_output=True, text=True
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout if result.returncode == 0 else None


def _wiki_is_git_root(root: Path) -> bool:
    top = _git_out(root, "rev-parse", "--show-toplevel")
    if not top:
        return False
    try:
        return Path(top.strip()).resolve() == root.resolve()
    except OSError:
        return False


def _git_head(root: Path) -> str | None:
    out = _git_out(root, "rev-parse", "HEAD")
    return out.strip() if out and out.strip() else None


def _record_index_head(root: Path) -> None:
    """Remember the commit the index reflects, for the O(1) freshness fast-path.
    No-op when the wiki is not its own git repo."""
    if not _wiki_is_git_root(root):
        return
    head = _git_head(root)
    if not head:
        return
    try:
        (root / INDEX_HEAD_REL).write_text(json.dumps({"head": head}), encoding="utf-8")
    except OSError:
        pass


def git_index_fresh_fast(root: Path) -> bool:
    """O(1) freshness check: the index is fresh iff the wiki is its own git repo,
    its working tree is clean under wiki/, and HEAD matches the commit the index
    was built at. Returns False when it cannot confirm cheaply — the caller then
    runs the exact stat-based check, so this never yields a stale index."""
    if not _wiki_is_git_root(root):
        return False
    head = _git_head(root)
    if not head:
        return False
    try:
        stored = json.loads((root / INDEX_HEAD_REL).read_text(encoding="utf-8")).get("head")
    except (OSError, json.JSONDecodeError, AttributeError):
        return False
    if stored != head:
        return False
    status = _git_out(root, "status", "--porcelain", "--", "wiki")
    return status is not None and status.strip() == ""


def ensure_query_index(root: Path, rebuild: bool = False) -> bool:
    db_path = index_db_path(root)
    if db_path.exists() and not rebuild:
        # O(1) git fast-path: skip the whole-corpus stat sweep when git confirms
        # nothing under wiki/ changed since the index was built.
        if git_index_fresh_fast(root):
            return True
        try:
            conn = sqlite3.connect(db_path)
            try:
                row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
                if row and str(row[0]) == INDEX_SCHEMA_VERSION and query_index_is_fresh(root, conn):
                    _record_index_head(root)
                    return True
            finally:
                conn.close()
        except sqlite3.Error:
            pass

    index_script = Path(__file__).with_name("index_wiki.py")
    cmd = [sys.executable, str(index_script), str(root)]
    if rebuild:
        cmd.append("--rebuild")
    result = subprocess.run(cmd, text=True, capture_output=True)
    if result.returncode != 0:
        if result.stderr.strip():
            print(result.stderr.strip(), file=sys.stderr)
        elif result.stdout.strip():
            print(result.stdout.strip(), file=sys.stderr)
        return False
    _record_index_head(root)
    return True


def quote_fts_term(term: str) -> str:
    return '"' + term.replace('"', '""') + '"'


def fts_query_for(text: str) -> str:
    terms = list(tokenize(text).keys())
    if not terms:
        return ""
    # FTS5 handles ASCII terms well; quoted CJK runs are harmless when tokenized
    # by sqlite, and LIKE fallback below covers tokenizer misses.
    return " OR ".join(quote_fts_term(term) for term in terms[:24])


def index_search_page_ids(conn: sqlite3.Connection, query: str, limit: int) -> list[str]:
    if not query.strip():
        return []
    page_ids: list[str] = []
    seen: set[str] = set()
    fts_query = fts_query_for(query)
    if fts_query:
        try:
            for row in conn.execute(
                """
                SELECT page_id, COUNT(*) AS hits
                FROM chunks_fts
                WHERE chunks_fts MATCH ?
                GROUP BY page_id
                ORDER BY hits DESC
                LIMIT ?
                """,
                (fts_query, limit),
            ):
                page_id = str(row[0])
                if page_id not in seen:
                    seen.add(page_id)
                    page_ids.append(page_id)
        except sqlite3.Error as exc:
            print(f"warning: FTS query failed, using LIKE fallback: {exc}", file=sys.stderr)

    terms = [term for term in tokenize(query).keys() if len(term) >= 2][:16]
    if terms and len(page_ids) < limit:
        clauses: list[str] = []
        params: list[str | int] = []
        for term in terms:
            like = f"%{term.lower()}%"
            clauses.append(
                "(LOWER(d.title) LIKE ? OR LOWER(d.summary) LIKE ? "
                "OR LOWER(d.tags_json) LIKE ? OR LOWER(c.body) LIKE ?)"
            )
            params.extend([like, like, like, like])
        params.append(limit)
        sql = f"""
            SELECT d.id, COUNT(*) AS hits
            FROM documents d
            LEFT JOIN chunks c ON c.page_id = d.id
            WHERE {' OR '.join(clauses)}
            GROUP BY d.id
            ORDER BY hits DESC, d.updated_at DESC
            LIMIT ?
        """
        for row in conn.execute(sql, params):
            page_id = str(row[0])
            if page_id not in seen:
                seen.add(page_id)
                page_ids.append(page_id)
                if len(page_ids) >= limit:
                    break
    return page_ids


def resolve_seed_index(conn: sqlite3.Connection, value: str) -> str | None:
    raw = value.strip()
    if not raw:
        return None
    normalized = posixpath.normpath(raw)
    candidates = [normalized]
    if not normalized.startswith("wiki/"):
        candidates.append(posixpath.normpath(posixpath.join("wiki", normalized)))
    for candidate in candidates:
        row = conn.execute("SELECT id FROM documents WHERE id = ?", (candidate,)).fetchone()
        if row:
            return str(row[0])

    needle = raw.lower()
    exact = conn.execute(
        """
        SELECT id FROM documents
        WHERE LOWER(title) = ? OR LOWER(id) = ?
        ORDER BY LENGTH(id) ASC
        LIMIT 1
        """,
        (needle, needle),
    ).fetchone()
    if exact:
        return str(exact[0])
    fuzzy = conn.execute(
        """
        SELECT id FROM documents
        WHERE LOWER(title) LIKE ? OR LOWER(id) LIKE ?
        ORDER BY LENGTH(id) ASC
        LIMIT 1
        """,
        (f"%{needle}%", f"%{needle}%"),
    ).fetchone()
    return str(fuzzy[0]) if fuzzy else None


def related_page_ids_index(conn: sqlite3.Connection, seeds: list[str], depth: int) -> set[str]:
    if not seeds:
        return set()
    out: set[str] = set(seeds)
    queue: deque[tuple[str, int]] = deque((seed, 0) for seed in seeds)
    seen: set[str] = set(seeds)
    while queue:
        current, dist = queue.popleft()
        if dist >= depth:
            continue
        rows = conn.execute(
            """
            SELECT target_page_id FROM links WHERE source_page_id = ?
            UNION
            SELECT source_page_id FROM links WHERE target_page_id = ?
            """,
            (current, current),
        )
        for row in rows:
            neighbor = str(row[0])
            out.add(neighbor)
            if neighbor not in seen:
                seen.add(neighbor)
                queue.append((neighbor, dist + 1))
    return out


def parse_iso_datetime(value: str | None) -> datetime:
    if not value:
        return datetime.fromtimestamp(0, tz=timezone.utc)
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return datetime.fromtimestamp(0, tz=timezone.utc)


def load_pages_from_index(
    root: Path,
    query: str,
    interest: InterestProfile,
    related_values: list[str],
    depth: int,
    result_limit: int,
    include_index: bool,
) -> tuple[dict[str, Page], list[str], list[str]]:
    conn = sqlite3.connect(index_db_path(root))
    conn.row_factory = sqlite3.Row
    try:
        seeds: list[str] = []
        unresolved: list[str] = []
        for raw in related_values:
            seed = resolve_seed_index(conn, raw)
            if seed:
                seeds.append(seed)
            else:
                unresolved.append(raw)
        seeds = sorted(set(seeds))

        candidate_ids: set[str] | None
        if query.strip():
            search_limit = max(result_limit * 20, 100)
            candidate_ids = set(index_search_page_ids(conn, query, search_limit))
        else:
            candidate_ids = None
        if interest.active:
            interest_text = interest_query_text(interest)
            if interest_text:
                interest_limit = max(result_limit * 10, 50)
                interest_ids = set(index_search_page_ids(conn, interest_text, interest_limit))
                candidate_ids = interest_ids if candidate_ids is None else candidate_ids | interest_ids
        if seeds:
            related = related_page_ids_index(conn, seeds, depth)
            candidate_ids = related if candidate_ids is None else candidate_ids | related
        if candidate_ids is not None and not include_index:
            candidate_ids.discard("wiki/index.md")

        if candidate_ids is None:
            # No keyword / interest / seed anchor — a browse/recency query.
            # Loading every document here OOMs on a large corpus (tens of GB /
            # millions of pages). A no-anchor query is inherently recency-first,
            # so bound the working set to a generous recency-ordered pool instead
            # of the whole table; ranking then applies as usual within it.
            browse_cap = max(result_limit * 50, 1000)
            doc_rows = conn.execute(
                "SELECT * FROM documents ORDER BY updated_at DESC LIMIT ?",
                (browse_cap,),
            ).fetchall()
        elif not candidate_ids:
            return {}, seeds, unresolved
        else:
            placeholders = ",".join("?" for _ in candidate_ids)
            doc_rows = conn.execute(f"SELECT * FROM documents WHERE id IN ({placeholders})", sorted(candidate_ids)).fetchall()

        pages: dict[str, Page] = {}
        for row in doc_rows:
            page_id = str(row["id"])
            chunk_rows = conn.execute(
                "SELECT body FROM chunks WHERE page_id = ? ORDER BY ordinal ASC",
                (page_id,),
            ).fetchall()
            text = "\n\n".join(str(chunk["body"]) for chunk in chunk_rows)
            tags = json.loads(row["tags_json"] or "[]")
            page = Page(
                id=page_id,
                path=root / page_id,
                title=str(row["title"]),
                kind=str(row["kind"]),
                tags=[str(tag) for tag in tags],
                summary=str(row["summary"]),
                text=text,
                updated=parse_iso_datetime(row["updated_at"]),
                created=parse_iso_datetime(row["created_at"]) if row["created_at"] else None,
            )
            pages[page_id] = page

        for page in pages.values():
            outgoing = [
                str(row[0])
                for row in conn.execute("SELECT target_page_id FROM links WHERE source_page_id = ?", (page.id,))
            ]
            incoming = [
                str(row[0])
                for row in conn.execute("SELECT source_page_id FROM links WHERE target_page_id = ?", (page.id,))
            ]
            page.outgoing = set(outgoing)
            page.incoming = set(incoming)
            page.outbound = len(page.outgoing)
            page.inbound = len(page.incoming)
        return pages, seeds, unresolved
    finally:
        conn.close()


def parse_window(value: str | None, now: datetime) -> datetime | None:
    if not value:
        return None
    raw = value.strip().lower()
    m = re.fullmatch(r"(\d+)\s*([dwmy])", raw)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        days = {"d": 1, "w": 7, "m": 30, "y": 365}[unit] * n
        return now - timedelta(days=days)
    parsed = parse_dateish(raw)
    return parsed


def parse_until(value: str | None) -> datetime | None:
    parsed = parse_dateish(value)
    if parsed and parsed.time() == time(0, 0):
        return parsed + timedelta(days=1) - timedelta(microseconds=1)
    return parsed


def resolve_seed(pages: dict[str, Page], value: str) -> str | None:
    raw = value.strip()
    normalized = posixpath.normpath(raw)
    candidates = [normalized]
    if not normalized.startswith("wiki/"):
        candidates.append(posixpath.normpath(posixpath.join("wiki", normalized)))
    for candidate in candidates:
        if candidate in pages:
            return candidate

    needle = raw.lower()
    exact: list[str] = []
    fuzzy: list[str] = []
    for page in pages.values():
        haystacks = [page.title.lower(), page.id.lower(), Path(page.id).stem.lower()]
        if needle in haystacks:
            exact.append(page.id)
        elif any(needle in h for h in haystacks):
            fuzzy.append(page.id)
    if exact:
        return sorted(exact, key=len)[0]
    if fuzzy:
        return sorted(fuzzy, key=len)[0]
    return None


def related_scores(pages: dict[str, Page], seeds: list[str], depth: int) -> dict[str, float]:
    if not seeds:
        return {}
    scores: dict[str, float] = {}
    queue: deque[tuple[str, int]] = deque()
    seen: set[str] = set()
    for seed in seeds:
        queue.append((seed, 0))
        seen.add(seed)
        scores[seed] = max(scores.get(seed, 0.0), 1.25)

    while queue:
        current, dist = queue.popleft()
        if dist >= depth:
            continue
        page = pages.get(current)
        if page is None:
            # Node referenced in the link graph but not loaded into `pages`
            # (e.g. wiki/index.md is discarded by load_pages_from_index when
            # --include-index is off, but it still appears as a neighbor of
            # most content pages). It can carry a score as a neighbor, but
            # we have no link graph for it, so don't try to expand further.
            continue
        neighbors = sorted(page.outgoing | page.incoming)
        for neighbor in neighbors:
            next_dist = dist + 1
            contribution = 1.0 / (next_dist + 0.35)
            scores[neighbor] = max(scores.get(neighbor, 0.0), contribution)
            if neighbor not in seen:
                seen.add(neighbor)
                queue.append((neighbor, next_dist))
    return scores


def lexical_score(query_tokens: Counter[str], page_tokens: Counter[str], page: Page) -> tuple[float, list[str]]:
    if not query_tokens:
        return 0.0, []
    score = 0.0
    matched: list[str] = []
    title_tokens = tokenize(page.title)
    tag_tokens = tokenize(" ".join(page.tags))
    summary_tokens = tokenize(page.summary)
    for token, q_weight in query_tokens.items():
        if token in title_tokens:
            score += 3.0 * q_weight
            matched.append(token)
        if token in tag_tokens:
            score += 2.2 * q_weight
            matched.append(token)
        if token in summary_tokens:
            score += 1.5 * q_weight
            matched.append(token)
        if token in page_tokens:
            score += min(2.5, 0.25 * page_tokens[token]) * q_weight
            matched.append(token)
    # A gentle dampener keeps long pages from dominating.
    norm = math.sqrt(max(1, sum(page_tokens.values())))
    return score / norm * 8.0, sorted(set(matched))


def interest_score(profile: InterestProfile, page_tokens: Counter[str], page: Page) -> tuple[float, list[str], list[str]]:
    if not profile.active:
        return 0.0, [], []
    score = 0.0
    positive_hits: list[str] = []
    negative_hits: list[str] = []
    title_tokens = tokenize(page.title)
    tag_tokens = tokenize(" ".join(page.tags))
    summary_tokens = tokenize(page.summary)
    for token, weight in profile.positive.items():
        if token in title_tokens:
            score += 2.4 * weight
            positive_hits.append(token)
        if token in tag_tokens:
            score += 2.0 * weight
            positive_hits.append(token)
        if token in summary_tokens:
            score += 1.2 * weight
            positive_hits.append(token)
        if token in page_tokens:
            score += min(1.8, 0.18 * page_tokens[token]) * weight
            positive_hits.append(token)
    for token, weight in profile.negative.items():
        if token in title_tokens or token in tag_tokens or token in summary_tokens or token in page_tokens:
            score -= 1.4 * weight
            negative_hits.append(token)
    norm = math.sqrt(max(1, sum(page_tokens.values())))
    return score / norm * 4.0, sorted(set(positive_hits)), sorted(set(negative_hits))


def recency_score(updated: datetime, now: datetime) -> float:
    age_days = max(0.0, (now - updated).total_seconds() / 86400)
    return 1.0 / (1.0 + age_days / 30.0)


def make_snippet(page: Page, query_tokens: Counter[str]) -> str:
    body = strip_frontmatter(page.text)
    lines = [re.sub(r"\s+", " ", line).strip() for line in body.splitlines()]
    lines = [line for line in lines if line and not line.startswith("#") and not line.startswith("```")]
    if not lines:
        return page.summary
    if query_tokens:
        for line in lines:
            line_tokens = tokenize(line)
            if any(token in line_tokens for token in query_tokens):
                return line[:260].rstrip()
    return (page.summary or lines[0])[:260].rstrip()


def rank_pages(
    pages: dict[str, Page],
    query: str,
    interest: InterestProfile,
    seeds: list[str],
    depth: int,
    since: datetime | None,
    until: datetime | None,
    kinds: set[str],
    tags: set[str],
    limit: int,
    sort: str,
    include_index: bool,
) -> list[Hit]:
    now = datetime.now(timezone.utc)
    query_tokens = tokenize(query)
    all_page_tokens = {page.id: tokenize(f"{page.title}\n{' '.join(page.tags)}\n{page.summary}\n{strip_frontmatter(page.text)}") for page in pages.values()}
    related = related_scores(pages, seeds, depth)
    max_degree = max((p.inbound + p.outbound for p in pages.values()), default=1)
    hits: list[Hit] = []

    for page in pages.values():
        if (query_tokens or seeds) and page.kind == "index" and not include_index:
            continue
        if since and page.updated < since:
            continue
        if until and page.updated > until:
            continue
        if kinds and page.kind not in kinds:
            continue
        if tags and not tags.intersection(set(page.tags)):
            continue

        lex, matched = lexical_score(query_tokens, all_page_tokens[page.id], page)
        interest_component, interest_hits, downrank_hits = interest_score(interest, all_page_tokens[page.id], page)
        rel = related.get(page.id, 0.0)
        if query_tokens and lex <= 0 and rel <= 0 and interest_component <= 0:
            continue
        central = (page.inbound + page.outbound) / max(1, max_degree)
        recent = recency_score(page.updated, now)

        if query_tokens or seeds:
            score = (2.0 * lex) + (1.5 * rel) + (0.4 * central) + (0.3 * recent) + (0.7 * interest_component)
        elif since or until:
            score = (1.2 * recent) + (0.4 * central) + (0.7 * interest_component)
        else:
            score = (0.8 * central) + (0.4 * recent) + (0.9 * interest_component)

        reasons: list[str] = []
        if matched:
            reasons.append("matched " + ", ".join(matched[:6]))
        if page.id in seeds:
            reasons.append("seed page")
        elif rel:
            reasons.append(f"graph distance <= {depth}")
        if page.inbound or page.outbound:
            reasons.append(f"degree {page.inbound + page.outbound}")
        if interest_hits:
            reasons.append("interest " + ", ".join(interest_hits[:4]))
        if downrank_hits:
            reasons.append("downrank " + ", ".join(downrank_hits[:4]))
        reasons.append(f"updated {page.updated.date().isoformat()}")

        hits.append(Hit(
            page=page,
            score=score,
            lexical=lex,
            related=rel,
            centrality=central,
            recency=recent,
            interest=interest_component,
            reasons=reasons,
            snippet=make_snippet(page, query_tokens),
        ))

    if sort == "updated":
        hits.sort(key=lambda h: (h.page.updated, h.score), reverse=True)
    elif sort == "degree":
        hits.sort(key=lambda h: (h.page.inbound + h.page.outbound, h.score), reverse=True)
    elif sort == "title":
        hits.sort(key=lambda h: h.page.title.lower())
    else:
        hits.sort(key=lambda h: (h.score, h.page.inbound + h.page.outbound, h.page.updated), reverse=True)
    return hits[:limit]


def format_md_link(page: Page) -> str:
    href = page.id
    if " " in href:
        href = f"<{href}>"
    return f"[{page.title}]({href})"


def emit_text(hits: list[Hit], seeds: list[str], unresolved: list[str], args: argparse.Namespace) -> None:
    print("# Wiki Query Discovery")
    print()
    if args.query:
        print(f"Query: {args.query}")
    if seeds:
        print("Related seed pages: " + ", ".join(seeds))
    if unresolved:
        print("Unresolved related seeds: " + ", ".join(unresolved))
    filters: list[str] = []
    if args.since:
        filters.append(f"since={args.since}")
    if args.until:
        filters.append(f"until={args.until}")
    if args.kind:
        filters.append("kind=" + ",".join(args.kind))
    if args.tag:
        filters.append("tag=" + ",".join(args.tag))
    if filters:
        print("Filters: " + "; ".join(filters))
    print(f"Results: {len(hits)}")
    print()
    if not hits:
        print("No matching pages. Read wiki/index.md and consider ingesting more source material.")
        return

    print("## Read First")
    for i, hit in enumerate(hits, start=1):
        page = hit.page
        reason = "; ".join(hit.reasons)
        print(
            f"{i}. {format_md_link(page)} "
            f"({page.kind}, score {hit.score:.2f}, in {page.inbound}/out {page.outbound})"
        )
        print(f"   - why: {reason}")
        if hit.snippet:
            print(f"   - note: {hit.snippet}")
    print()
    print("## Agent Next Step")
    print("Read SCHEMA.md, INTEREST.md, and wiki/index.md first if you have not already, then read the pages above in order.")
    print("Follow only the most relevant one-hop links before answering.")
    print("If the evidence is thin or all hits are low-score, say what is missing instead of filling gaps from general knowledge.")


def emit_json(hits: list[Hit], seeds: list[str], unresolved: list[str], args: argparse.Namespace) -> None:
    payload = {
        "query": args.query,
        "seeds": seeds,
        "unresolvedSeeds": unresolved,
        "results": [
            {
                "path": hit.page.id,
                "title": hit.page.title,
                "kind": hit.page.kind,
                "tags": hit.page.tags,
                "updated": hit.page.updated.isoformat(),
                "inbound": hit.page.inbound,
                "outbound": hit.page.outbound,
                "score": round(hit.score, 4),
                "components": {
                    "lexical": round(hit.lexical, 4),
                    "related": round(hit.related, 4),
                    "centrality": round(hit.centrality, 4),
                    "recency": round(hit.recency, 4),
                    "interest": round(hit.interest, 4),
                },
                "reasons": hit.reasons,
                "snippet": hit.snippet,
                "neighbors": {
                    "out": sorted(hit.page.outgoing),
                    "in": sorted(hit.page.incoming),
                },
            }
            for hit in hits
        ],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Rank wiki pages for an agent before it answers a query.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("wiki_root", help="Path to the wiki root containing wiki/")
    parser.add_argument("terms", nargs="*", help="Question or keywords to search")
    parser.add_argument("--query", "-q", help="Question or keywords. Overrides positional terms.")
    parser.add_argument("--related", "-r", action="append", default=[], help="Seed page path/title to expand by graph proximity. Repeatable.")
    parser.add_argument("--depth", type=int, default=1, help="Graph expansion depth for --related (default: 1)")
    parser.add_argument("--since", help="Only pages updated after DATE or relative window like 14d, 3w, 2m")
    parser.add_argument("--until", help="Only pages updated before DATE")
    parser.add_argument("--recent", type=int, help="Shortcut for --since Nd")
    parser.add_argument("--kind", action="append", default=[], help="Filter by page kind: concept, entity, summary, index, page. Repeatable.")
    parser.add_argument("--tag", action="append", default=[], help="Filter by frontmatter tag. Repeatable.")
    parser.add_argument("--limit", "-n", type=int, default=12, help="Maximum results (default: 12)")
    parser.add_argument("--sort", choices=("relevance", "updated", "degree", "title"), default="relevance")
    parser.add_argument("--include-index", action="store_true", help="Allow wiki/index.md in query/related results")
    parser.add_argument("--rebuild-index", action="store_true", help="Recompile .query-index/wiki.db before querying")
    parser.add_argument("--no-index", action="store_true", help="Fallback to scanning Markdown files directly")
    parser.add_argument("--no-interest", action="store_true", help="Do not read INTEREST.md or apply interest-based ranking hints")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--paths-only", action="store_true", help="Print only result paths, one per line")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    root = Path(args.wiki_root).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        print(f"error: wiki root does not exist or is not a directory: {root}", file=sys.stderr)
        return 2
    if args.depth < 0:
        print("error: --depth must be >= 0", file=sys.stderr)
        return 2
    if args.limit <= 0:
        print("error: --limit must be > 0", file=sys.stderr)
        return 2

    query = args.query if args.query is not None else " ".join(args.terms).strip()
    args.query = query
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=args.recent) if args.recent is not None else parse_window(args.since, now)
    until = parse_until(args.until)
    if args.since and since is None:
        print(f"error: could not parse --since value: {args.since}", file=sys.stderr)
        return 2
    if args.until and until is None:
        print(f"error: could not parse --until value: {args.until}", file=sys.stderr)
        return 2
    interest = InterestProfile(path=None) if args.no_interest else load_interest_profile(root)

    if args.no_index:
        try:
            pages = load_pages(root)
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        seeds: list[str] = []
        unresolved: list[str] = []
        for raw in args.related:
            seed = resolve_seed(pages, raw)
            if seed:
                seeds.append(seed)
            else:
                unresolved.append(raw)
        seeds = sorted(set(seeds))
    else:
        if not ensure_query_index(root, rebuild=args.rebuild_index):
            print(
                f"error: query index is unavailable. Run: python3 {Path(__file__).with_name('index_wiki.py')} {root}",
                file=sys.stderr,
            )
            return 2
        try:
            pages, seeds, unresolved = load_pages_from_index(
                root=root,
                query=query,
                interest=interest,
                related_values=args.related,
                depth=args.depth,
                result_limit=args.limit,
                include_index=args.include_index,
            )
        except (sqlite3.Error, OSError, json.JSONDecodeError) as exc:
            print(f"error: failed to read query index: {exc}", file=sys.stderr)
            return 2

    hits = rank_pages(
        pages=pages,
        query=query,
        interest=interest,
        seeds=seeds,
        depth=args.depth,
        since=since,
        until=until,
        kinds=set(args.kind),
        tags=set(args.tag),
        limit=args.limit,
        sort=args.sort,
        include_index=args.include_index,
    )

    if args.paths_only:
        for hit in hits:
            print(hit.page.id)
        return 0
    if args.format == "json":
        emit_json(hits, seeds, unresolved, args)
    else:
        emit_text(hits, seeds, unresolved, args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
