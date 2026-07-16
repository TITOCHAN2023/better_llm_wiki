#!/usr/bin/env python3
"""
audit_cr.py — Build an Audit CR (Correction / Contradiction Register).

Read-only register over `audit/` files filed by humans: cluster recurring
corrections, contradictions, stale claims, and deferred audit decisions into
a prioritized cleanup queue. Does not edit wiki pages or move audit files.

Modes:
  --open       Scan open audits only (default).
  --resolved   Scan resolved audits only.
  --all        Scan both.

Usage:
    python3 audit_cr.py <wiki-root> [--open|--resolved|--all] [--write]

Examples:
    python3 audit_cr.py ./my-wiki --open                # default register
    python3 audit_cr.py ./my-wiki --all --write         # full register, write to outputs/

When --write is set, the report lands at
<wiki-root>/outputs/audit-cr/contradiction-register.md.

For the ingest-time neighborhood audit (1-hop scope around freshly-ingested
pages), see scripts/ingest_scan.py — that's a separate concern from the
human-feedback register this script produces.

Exit codes:
  0 — report generated
  2 — invalid wiki root
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
AUDIT_TYPE_ORDER = {"error": 0, "warn": 1, "suggest": 2, "info": 3}
DECISION_RE = re.compile(r"\b(accepted|partial|rejected|deferred)\b", re.IGNORECASE)
CONTRADICTION_PATTERNS = [
    "contradict", "conflict", "inconsistent", "wrong", "incorrect", "stale",
    "outdated", "ambiguous", "hallucinat", "false", "mismatch",
    "矛盾", "冲突", "不一致", "错误", "不对", "过时", "陈旧", "相反",
    "歧义", "幻觉", "事实错误", "口径",
]


@dataclass
class AuditEntry:
    path: str
    mode: str
    id: str
    target: str
    type: str
    status: str
    created: str
    author: str
    start: tuple[int, int] | None
    end: tuple[int, int] | None
    comment: str
    resolution: str
    decision: str

    @property
    def created_dt(self) -> datetime | None:
        try:
            dt = datetime.fromisoformat(self.created.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            return None


def parse_frontmatter(text: str) -> dict[str, Any]:
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}
    result: dict[str, Any] = {}
    for raw in m.group(1).splitlines():
        if not raw.strip() or raw.lstrip().startswith("#") or ":" not in raw:
            continue
        key, _, rest = raw.partition(":")
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


def section_text(text: str, heading: str) -> str:
    lines = text.splitlines()
    wanted = heading.strip().lower()
    out: list[str] = []
    in_section = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            current = stripped.lstrip("#").strip().lower()
            if in_section and current != wanted:
                break
            if current == wanted:
                in_section = True
                continue
        elif in_section:
            out.append(line)
    return "\n".join(out).strip()


def first_line(text: str, limit: int = 140) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:limit]
    return ""


def extract_decision(resolution: str) -> str:
    m = DECISION_RE.search(resolution)
    return m.group(1).lower() if m else ""


def load_entries(root: Path, mode: str) -> list[AuditEntry]:
    audit_dir = root / "audit"
    files: list[tuple[Path, str]] = []
    if mode in ("open", "all"):
        files.extend((p, "open") for p in sorted(audit_dir.glob("*.md")) if p.name != ".gitkeep")
    if mode in ("resolved", "all"):
        resolved_dir = audit_dir / "resolved"
        if resolved_dir.exists():
            files.extend((p, "resolved") for p in sorted(resolved_dir.glob("*.md")) if p.name != ".gitkeep")

    entries: list[AuditEntry] = []
    for path, entry_mode in files:
        text = path.read_text(encoding="utf-8")
        fm = parse_frontmatter(text)
        comment = section_text(text, "Comment")
        resolution = section_text(text, "Resolution")
        def _coord(value: Any) -> tuple[int, int] | None:
            if isinstance(value, (list, tuple)) and len(value) == 2:
                try:
                    return (int(value[0]), int(value[1]))
                except (TypeError, ValueError):
                    return None
            return None
        entries.append(AuditEntry(
            path=path.relative_to(root).as_posix(),
            mode=entry_mode,
            id=str(fm.get("id") or path.stem),
            target=str(fm.get("target") or "(no-target)"),
            type=str(fm.get("type") or "info"),
            status=str(fm.get("status") or entry_mode),
            created=str(fm.get("created") or ""),
            author=str(fm.get("author") or ""),
            start=_coord(fm.get("start")),
            end=_coord(fm.get("end")),
            comment=comment,
            resolution=resolution,
            decision=extract_decision(resolution),
        ))
    return entries


def has_contradiction_signal(entry: AuditEntry) -> bool:
    haystack = f"{entry.type}\n{entry.comment}\n{entry.resolution}".lower()
    if entry.type in {"error", "warn"}:
        return True
    if entry.decision in {"partial", "rejected", "deferred"}:
        return True
    return any(pattern.lower() in haystack for pattern in CONTRADICTION_PATTERNS)


def anchor_key(entry: AuditEntry) -> str:
    """Cluster key: same target + same coordinate range → same cluster."""
    if entry.start is not None and entry.end is not None:
        return f"{entry.target}#L{entry.start[0]}C{entry.start[1]}-L{entry.end[0]}C{entry.end[1]}"
    return entry.target


def age_days(entry: AuditEntry, now: datetime) -> int | None:
    created = entry.created_dt
    if created is None:
        return None
    return max(0, int((now - created).total_seconds() // 86400))


def entry_priority(entry: AuditEntry, now: datetime, stale_days: int) -> int:
    score = 0
    score += {"error": 50, "warn": 35, "suggest": 15, "info": 5}.get(entry.type, 5)
    if has_contradiction_signal(entry):
        score += 25
    if entry.decision == "deferred":
        score += 20
    if entry.mode == "open":
        score += 15
    days = age_days(entry, now)
    if days is not None and days >= stale_days:
        score += 15
    return score


def cluster_entries(entries: list[AuditEntry], now: datetime, stale_days: int) -> list[dict[str, Any]]:
    grouped: dict[str, list[AuditEntry]] = defaultdict(list)
    for entry in entries:
        if has_contradiction_signal(entry) or entry.mode == "open":
            grouped[anchor_key(entry)].append(entry)

    clusters: list[dict[str, Any]] = []
    for key, items in grouped.items():
        target_counts = Counter(item.target for item in items)
        target = target_counts.most_common(1)[0][0]
        decisions = Counter(item.decision or item.status for item in items)
        types = Counter(item.type for item in items)
        open_count = sum(1 for item in items if item.mode == "open")
        stale_count = sum(1 for item in items if (age_days(item, now) or 0) >= stale_days and item.mode == "open")
        priority = sum(entry_priority(item, now, stale_days) for item in items)
        if len(items) > 1:
            priority += 20
        clusters.append({
            "key": key,
            "target": target,
            "count": len(items),
            "openCount": open_count,
            "staleOpenCount": stale_count,
            "priority": priority,
            "topType": min((item.type for item in items), key=lambda s: AUDIT_TYPE_ORDER.get(s, 99)),
            "decisions": dict(decisions),
            "severities": dict(types),
            "entries": sorted(items, key=lambda item: (
                AUDIT_TYPE_ORDER.get(item.type, 99),
                item.created,
                item.id,
            )),
        })
    clusters.sort(key=lambda c: (c["priority"], c["openCount"], c["count"]), reverse=True)
    return clusters


def render_entry(entry: AuditEntry, now: datetime) -> str:
    days = age_days(entry, now)
    age = f"{days}d old" if days is not None else "unknown age"
    decision = f", {entry.decision}" if entry.decision else ""
    note = first_line(entry.comment or entry.resolution)
    return f"- `{entry.id}` · `{entry.type}` · `{entry.status}`{decision} · {age} · [{entry.path}]({entry.path})\n  - {note or '(no note)'}"


def render_markdown(root: Path, mode: str, clusters: list[dict[str, Any]], entries: list[AuditEntry], stale_days: int) -> str:
    now = datetime.now(timezone.utc)
    open_entries = [entry for entry in entries if entry.mode == "open"]
    resolved_entries = [entry for entry in entries if entry.mode == "resolved"]
    lines: list[str] = []
    lines.append("# Audit CR Register")
    lines.append("")
    lines.append(f"Generated: {now.isoformat(timespec='seconds')}")
    lines.append(f"Mode: `{mode}`")
    lines.append(f"Total audits scanned: {len(entries)}")
    lines.append(f"Open: {len(open_entries)} · Resolved: {len(resolved_entries)} · CR clusters: {len(clusters)}")
    lines.append("")
    lines.append("## Agent Cleanup Protocol")
    lines.append("- Process clusters top-down by priority.")
    lines.append("- For each cluster, read the audit file(s), target page, linked source summaries, and relevant query-index results.")
    lines.append("- Resolve true contradictions by updating the target page and moving accepted/rejected/deferred audits to `audit/resolved/` with a `# Resolution` note.")
    lines.append("- Defer only when source evidence is missing; add the question to `SCHEMA.md` Open research questions, write a deferred resolution, and clear the open audit inbox.")
    lines.append("")
    if not clusters:
        lines.append("## Queue")
        lines.append("")
        lines.append("No contradiction/correction clusters found.")
        lines.append("")
        return "\n".join(lines) + "\n"

    lines.append("## Queue")
    for idx, cluster in enumerate(clusters, start=1):
        target = str(cluster["target"])
        link_target = target if " " not in target else f"<{target}>"
        lines.append("")
        lines.append(f"### {idx}. [{target}]({link_target})")
        lines.append("")
        lines.append(
            f"- Priority: `{cluster['priority']}` · entries: `{cluster['count']}` · open: `{cluster['openCount']}` · stale open: `{cluster['staleOpenCount']}`"
        )
        lines.append(f"- Severities: `{json.dumps(cluster['severities'], ensure_ascii=False)}`")
        lines.append(f"- Decisions/statuses: `{json.dumps(cluster['decisions'], ensure_ascii=False)}`")
        lines.append("- Suggested cleanup:")
        lines.append(f"  - Run `python3 scripts/query_wiki.py <wiki-root> --related {shell_quote_path(target)} --depth 1 --limit 8`.")
        lines.append("  - Read the audit entries below and reconcile the target page against source summaries/raw evidence.")
        lines.append("")
        lines.append("Entries:")
        for entry in cluster["entries"]:
            lines.append(render_entry(entry, now))
    lines.append("")
    lines.append("## Stale Open Audits")
    stale = [entry for entry in open_entries if (age_days(entry, now) or 0) >= stale_days]
    if stale:
        for entry in sorted(stale, key=lambda e: (e.target, e.created)):
            lines.append(render_entry(entry, now))
    else:
        lines.append("")
        lines.append(f"No open audits older than {stale_days} days.")
    lines.append("")
    return "\n".join(lines) + "\n"


def shell_quote_path(path: str) -> str:
    if re.search(r"\s", path):
        return "'" + path.replace("'", "'\"'\"'") + "'"
    return path


def emit_json(clusters: list[dict[str, Any]], entries: list[AuditEntry]) -> None:
    def entry_payload(entry: AuditEntry) -> dict[str, Any]:
        return {
            "id": entry.id,
            "path": entry.path,
            "mode": entry.mode,
            "target": entry.target,
            "type": entry.type,
            "status": entry.status,
            "created": entry.created,
            "author": entry.author,
            "decision": entry.decision,
            "comment": first_line(entry.comment, 300),
            "resolution": first_line(entry.resolution, 300),
            "contradictionSignal": has_contradiction_signal(entry),
        }

    payload = {
        "total": len(entries),
        "clusters": [
            {
                "key": cluster["key"],
                "target": cluster["target"],
                "count": cluster["count"],
                "openCount": cluster["openCount"],
                "staleOpenCount": cluster["staleOpenCount"],
                "priority": cluster["priority"],
                "severities": cluster["severities"],
                "decisions": cluster["decisions"],
                "entries": [entry_payload(entry) for entry in cluster["entries"]],
            }
            for cluster in clusters
        ],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build an Audit CR contradiction/correction register.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("wiki_root", help="Path to the wiki root containing audit/")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--open", action="store_const", const="open", dest="mode", help="Scan open audits only")
    mode.add_argument("--resolved", action="store_const", const="resolved", dest="mode", help="Scan resolved audits only")
    mode.add_argument("--all", action="store_const", const="all", dest="mode", help="Scan open and resolved audits")
    parser.set_defaults(mode="open")
    parser.add_argument("--stale-days", type=int, default=14, help="Age threshold for stale open audits")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--write", action="store_true", help="Write outputs/audit-cr/contradiction-register.md")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    # The --ingest-scan mode previously lived here. It now ships as
    # scripts/ingest_scan.py — it's a different concern (post-ingest 1-hop
    # neighborhood) from this script's human-feedback register. Print a clear
    # pointer for anyone still typing the old form.
    if "--ingest-scan" in argv:
        print(
            "error: --ingest-scan was removed from audit_cr.py. Use:\n"
            "    python3 scripts/ingest_scan.py <wiki-root> [--seed ...] [--depth N] [--write]",
            file=sys.stderr,
        )
        return 2

    args = parse_args(argv)
    root = Path(args.wiki_root).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        print(f"error: wiki root does not exist or is not a directory: {root}", file=sys.stderr)
        return 2

    if not (root / "audit").exists():
        print(f"error: missing audit/ directory under {root}", file=sys.stderr)
        return 2
    if args.stale_days < 0:
        print("error: --stale-days must be >= 0", file=sys.stderr)
        return 2

    entries = load_entries(root, args.mode)
    clusters = cluster_entries(entries, datetime.now(timezone.utc), args.stale_days)
    if args.format == "json":
        emit_json(clusters, entries)
    else:
        report = render_markdown(root, args.mode, clusters, entries, args.stale_days)
        print(report, end="")
        if args.write:
            out = root / "outputs" / "audit-cr" / "contradiction-register.md"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(report, encoding="utf-8")
            print(f"\nWrote {out.relative_to(root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
