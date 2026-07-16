#!/usr/bin/env python3
"""
index_wiki.py — Compile a Markdown wiki into a local SQLite query index.

Markdown stays the source of truth. This script writes a derived index at:

    <wiki-root>/.query-index/wiki.db

The index is built for agent query planning at long-running scale:
page metadata, bounded chunks, FTS5 search rows, and page-level links are
stored in SQLite so query_wiki.py does not need to scan every Markdown file.

Usage:
    python3 index_wiki.py <wiki-root> [--rebuild]

Exit codes:
  0 — index updated
  2 — invalid wiki root or SQLite/FTS5 unavailable
"""

from __future__ import annotations

import argparse
import hashlib
import json
import posixpath
import re
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
INDEX_DIR = ".query-index"
INDEX_DB = "wiki.db"
DEFAULT_CHUNK_MAX_TOKENS = 3000
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
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


@dataclass(frozen=True)
class Chunk:
    id: str
    page_id: str
    ordinal: int
    heading: str
    heading_path: str
    body: str
    token_count: int
    content_sha256: str
    start_line: int
    end_line: int


def index_path(root: Path) -> Path:
    return root / INDEX_DIR / INDEX_DB


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def approx_token_count(text: str) -> int:
    # Conservative enough for chunk bounds without a tokenizer dependency.
    return max(1, (len(text) + 3) // 4) if text.strip() else 0


def parse_frontmatter(text: str) -> dict[str, Any]:
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}
    result: dict[str, Any] = {}
    for line in m.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("#") or ":" not in line:
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
    return result


def strip_frontmatter(text: str) -> str:
    m = FRONTMATTER_RE.match(text)
    return text[m.end():] if m else text


def frontmatter_line_offset(text: str) -> int:
    m = FRONTMATTER_RE.match(text)
    return text[:m.end()].count("\n") if m else 0


def string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def parse_dateish(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    if "T" in raw or raw.endswith("Z"):
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).isoformat()
        except ValueError:
            pass
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(raw[:len(datetime.now().strftime(fmt))], fmt).replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            pass
    return None


def path_mtime_iso(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()


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


def canonicalize_href(root: Path, href: str) -> str | None:
    if href.startswith("/"):
        # OKF content-root anchor: leading `/` == `<root>/wiki/`.
        joined = posixpath.normpath("wiki" + href)
    else:
        joined = posixpath.normpath(href)
        if joined.startswith("../") or joined == "..":
            return None
        if not joined.startswith("wiki/"):
            joined = posixpath.normpath("wiki/" + joined)
    target = root / Path(joined)
    if not target.exists() or not target.is_file():
        return None
    try:
        return target.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return None


def extract_page_links(root: Path, text: str) -> list[str]:
    targets: list[str] = []
    for m in MD_LINK_RE.finditer(text):
        href = (m.group("bracketed") or m.group("bare") or "").strip()
        if "#" in href:
            href = href.split("#", 1)[0]
        if not href or EXTERNAL_URL_RE.match(href):
            continue
        target = canonicalize_href(root, href)
        if target:
            targets.append(target)
    return sorted(set(targets))


def split_oversize_body(
    page_id: str,
    ordinal_start: int,
    heading: str,
    heading_path: str,
    body: str,
    start_line: int,
    max_tokens: int,
) -> list[Chunk]:
    max_chars = max_tokens * 4
    paragraphs = re.split(r"(\n\s*\n)", body)
    pieces: list[str] = []
    acc = ""
    for part in paragraphs:
        if not part:
            continue
        if len(acc) + len(part) > max_chars and acc.strip():
            pieces.append(acc.strip())
            acc = part
        else:
            acc += part
    if acc.strip():
        pieces.append(acc.strip())
    if not pieces:
        pieces = [body.strip()]

    chunks: list[Chunk] = []
    ordinal = ordinal_start
    line_cursor = start_line
    for piece in pieces:
        line_count = max(1, piece.count("\n") + 1)
        digest = sha256_text(f"{page_id}\0{heading_path}\0{ordinal}\0{piece}")
        chunks.append(Chunk(
            id=f"chunk:{digest[:32]}",
            page_id=page_id,
            ordinal=ordinal,
            heading=heading,
            heading_path=heading_path,
            body=piece,
            token_count=approx_token_count(piece),
            content_sha256=sha256_text(piece),
            start_line=line_cursor,
            end_line=line_cursor + line_count - 1,
        ))
        ordinal += 1
        line_cursor += line_count
    return chunks


def chunk_page(page_id: str, text: str, max_tokens: int) -> list[Chunk]:
    body = strip_frontmatter(text)
    line_offset = frontmatter_line_offset(text)
    lines = body.splitlines()
    sections: list[tuple[str, str, list[str], int]] = []
    heading_stack: list[tuple[int, str]] = []
    current_heading = ""
    current_path = ""
    current_lines: list[str] = []
    current_start = line_offset + 1
    in_fence = False

    def flush() -> None:
        nonlocal current_lines, current_start, current_heading, current_path
        content = "\n".join(current_lines).strip()
        if content:
            sections.append((current_heading, current_path, current_lines[:], current_start))
        current_lines = []

    for idx, raw in enumerate(lines, start=line_offset + 1):
        stripped = raw.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
        heading = HEADING_RE.match(stripped) if not in_fence else None
        if heading:
            flush()
            level = len(heading.group(1))
            title = heading.group(2).strip()
            heading_stack = [(lvl, h) for lvl, h in heading_stack if lvl < level]
            heading_stack.append((level, title))
            current_heading = title
            current_path = " / ".join(h for _, h in heading_stack)
            current_start = idx
            current_lines = [raw]
        else:
            if not current_lines:
                current_start = idx
            current_lines.append(raw)
    flush()

    if not sections and body.strip():
        sections = [("", "", body.splitlines(), line_offset + 1)]

    chunks: list[Chunk] = []
    ordinal = 0
    for heading, heading_path, section_lines, start_line in sections:
        section_body = "\n".join(section_lines).strip()
        if not section_body:
            continue
        if approx_token_count(section_body) > max_tokens:
            split = split_oversize_body(page_id, ordinal, heading, heading_path, section_body, start_line, max_tokens)
            chunks.extend(split)
            ordinal += len(split)
            continue
        digest = sha256_text(f"{page_id}\0{heading_path}\0{ordinal}\0{section_body}")
        chunks.append(Chunk(
            id=f"chunk:{digest[:32]}",
            page_id=page_id,
            ordinal=ordinal,
            heading=heading,
            heading_path=heading_path,
            body=section_body,
            token_count=approx_token_count(section_body),
            content_sha256=sha256_text(section_body),
            start_line=start_line,
            end_line=start_line + max(1, section_body.count("\n") + 1) - 1,
        ))
        ordinal += 1

    if not chunks:
        digest = sha256_text(f"{page_id}\0empty")
        chunks.append(Chunk(
            id=f"chunk:{digest[:32]}",
            page_id=page_id,
            ordinal=0,
            heading="",
            heading_path="",
            body="",
            token_count=0,
            content_sha256=sha256_text(""),
            start_line=1,
            end_line=1,
        ))
    return chunks


SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    kind TEXT NOT NULL,
    tags_json TEXT NOT NULL,
    summary TEXT NOT NULL,
    created_at TEXT,
    updated_at TEXT NOT NULL,
    mtime_ns INTEGER NOT NULL,
    size_bytes INTEGER NOT NULL,
    sha256 TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chunks (
    id TEXT PRIMARY KEY,
    page_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL,
    heading TEXT NOT NULL,
    heading_path TEXT NOT NULL,
    body TEXT NOT NULL,
    token_count INTEGER NOT NULL,
    content_sha256 TEXT NOT NULL,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    chunk_id UNINDEXED,
    page_id UNINDEXED,
    title,
    heading,
    tags,
    body,
    tokenize = 'unicode61'
);

CREATE TABLE IF NOT EXISTS links (
    source_page_id TEXT NOT NULL,
    target_page_id TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'links_to',
    PRIMARY KEY (source_page_id, target_page_id, kind)
);

CREATE INDEX IF NOT EXISTS idx_documents_updated ON documents(updated_at);
CREATE INDEX IF NOT EXISTS idx_documents_kind ON documents(kind);
CREATE INDEX IF NOT EXISTS idx_chunks_page ON chunks(page_id, ordinal);
CREATE INDEX IF NOT EXISTS idx_links_source ON links(source_page_id);
CREATE INDEX IF NOT EXISTS idx_links_target ON links(target_page_id);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    conn.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
        ("schema_version", str(SCHEMA_VERSION)),
    )


def delete_document(conn: sqlite3.Connection, page_id: str, *, remove_inbound: bool) -> None:
    chunk_ids = [row[0] for row in conn.execute("SELECT id FROM chunks WHERE page_id = ?", (page_id,))]
    for chunk_id in chunk_ids:
        conn.execute("DELETE FROM chunks_fts WHERE chunk_id = ?", (chunk_id,))
    if remove_inbound:
        conn.execute("DELETE FROM links WHERE source_page_id = ? OR target_page_id = ?", (page_id, page_id))
    else:
        conn.execute("DELETE FROM links WHERE source_page_id = ?", (page_id,))
    conn.execute("DELETE FROM chunks WHERE page_id = ?", (page_id,))
    conn.execute("DELETE FROM documents WHERE id = ?", (page_id,))


def upsert_document(conn: sqlite3.Connection, root: Path, path: Path, max_tokens: int) -> bool:
    page_id = path.relative_to(root).as_posix()
    stat = path.stat()
    old = conn.execute(
        "SELECT sha256, mtime_ns, size_bytes FROM documents WHERE id = ?",
        (page_id,),
    ).fetchone()
    if old and old[1] == stat.st_mtime_ns and old[2] == stat.st_size:
        return False

    text = path.read_text(encoding="utf-8")
    digest = sha256_text(text)
    if old and old[0] == digest:
        if old[1] != stat.st_mtime_ns or old[2] != stat.st_size:
            conn.execute(
                "UPDATE documents SET mtime_ns = ?, size_bytes = ? WHERE id = ?",
                (stat.st_mtime_ns, stat.st_size, page_id),
            )
        return False

    delete_document(conn, page_id, remove_inbound=False)
    fm = parse_frontmatter(text)
    title = extract_title(text, path.stem)
    tags = string_list(fm.get("tags"))
    created_at = parse_dateish(fm.get("created"))
    updated_at = parse_dateish(fm.get("updated")) or created_at or path_mtime_iso(path)
    summary = extract_summary(text)
    kind = infer_kind(page_id, fm)
    chunks = chunk_page(page_id, text, max_tokens)

    conn.execute(
        """
        INSERT INTO documents(id, title, kind, tags_json, summary, created_at, updated_at, mtime_ns, size_bytes, sha256)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (page_id, title, kind, json.dumps(tags, ensure_ascii=False), summary, created_at, updated_at, stat.st_mtime_ns, stat.st_size, digest),
    )
    for chunk in chunks:
        conn.execute(
            """
            INSERT INTO chunks(id, page_id, ordinal, heading, heading_path, body, token_count, content_sha256, start_line, end_line)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (chunk.id, chunk.page_id, chunk.ordinal, chunk.heading, chunk.heading_path, chunk.body, chunk.token_count, chunk.content_sha256, chunk.start_line, chunk.end_line),
        )
        conn.execute(
            """
            INSERT INTO chunks_fts(chunk_id, page_id, title, heading, tags, body)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (chunk.id, chunk.page_id, title, chunk.heading_path or chunk.heading, " ".join(tags), chunk.body),
        )

    for target in extract_page_links(root, text):
        if target != page_id:
            conn.execute(
                "INSERT OR IGNORE INTO links(source_page_id, target_page_id, kind) VALUES (?, ?, 'links_to')",
                (page_id, target),
            )
    return True


def collect_wiki_files(root: Path) -> list[Path]:
    wiki_dir = root / "wiki"
    return [
        path for path in sorted(wiki_dir.rglob("*.md"))
        if path.is_file() and not path.name.endswith(".md.graph")
    ]


def index_wiki(root: Path, rebuild: bool = False, max_tokens: int = DEFAULT_CHUNK_MAX_TOKENS) -> dict[str, int]:
    db_path = index_path(root)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if rebuild and db_path.exists():
        db_path.unlink()
        for suffix in ("-wal", "-shm"):
            sidecar = Path(f"{db_path}{suffix}")
            if sidecar.exists():
                sidecar.unlink()

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        ensure_schema(conn)
        current_files = collect_wiki_files(root)
        current_ids = {path.relative_to(root).as_posix() for path in current_files}
        existing_ids = {row[0] for row in conn.execute("SELECT id FROM documents")}
        removed = sorted(existing_ids - current_ids)
        changed = 0
        with conn:
            for page_id in removed:
                delete_document(conn, page_id, remove_inbound=True)
            for path in current_files:
                if upsert_document(conn, root, path, max_tokens):
                    changed += 1
            conn.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
                ("generated_at", datetime.now(timezone.utc).isoformat()),
            )
            conn.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
                ("root", str(root)),
            )
        docs = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        links = conn.execute("SELECT COUNT(*) FROM links").fetchone()[0]
        return {"documents": docs, "chunks": chunks, "links": links, "changed": changed, "removed": len(removed)}
    finally:
        conn.close()


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compile a Markdown wiki into .query-index/wiki.db.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("wiki_root", help="Path to the wiki root containing wiki/")
    parser.add_argument("--rebuild", action="store_true", help="Delete and rebuild the index from scratch")
    parser.add_argument("--max-tokens", type=int, default=DEFAULT_CHUNK_MAX_TOKENS, help="Approximate max tokens per chunk")
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
    if args.max_tokens <= 0:
        print("error: --max-tokens must be > 0", file=sys.stderr)
        return 2
    try:
        sqlite3.connect(":memory:").execute("CREATE VIRTUAL TABLE fts_probe USING fts5(x)")
    except sqlite3.Error as exc:
        print(f"error: SQLite FTS5 is unavailable: {exc}", file=sys.stderr)
        return 2
    try:
        stats = index_wiki(root, rebuild=args.rebuild, max_tokens=args.max_tokens)
    except (OSError, UnicodeDecodeError, sqlite3.Error) as exc:
        print(f"error: failed to index wiki: {exc}", file=sys.stderr)
        return 2
    print(
        "Indexed {documents} documents, {chunks} chunks, {links} links "
        "({changed} changed, {removed} removed) at {path}".format(
            **stats,
            path=index_path(root),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
