from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import commit_wiki  # noqa: E402
import lint_wiki  # noqa: E402


def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def write(root: Path, relative: str, body: str) -> None:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")


class RecentActivityTests(unittest.TestCase):
    def make_wiki(self) -> Path:
        root = Path(tempfile.mkdtemp(prefix="llm-wiki-recent-"))
        git(root, "init", "-q")
        git(root, "config", "user.name", "Wiki Test")
        git(root, "config", "user.email", "wiki-test@example.com")
        git(root, "config", "core.quotepath", "false")
        today = datetime.now().astimezone()
        compact = today.strftime("%Y%m%d")
        iso = today.strftime("%Y-%m-%d")
        write(root, ".gitignore", "graph/\n.graph-cache/\n")
        write(root, "wiki/index.md", "# Index\n")
        write(
            root,
            f"log/{compact}.md",
            f"# {iso}\n\n## [00:01] scaffold | base\n- Touched: [Index](/index.md)\n",
        )
        git(root, "add", ".")
        git(root, "commit", "-q", "-m", "scaffold: base")
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        return root

    def test_recent_recovers_unlogged_ingest_from_git(self) -> None:
        root = self.make_wiki()
        write(root, "raw/articles/source.md", "source\n")
        write(root, "wiki/summaries/中文总结.md", "# 中文总结\n")
        git(root, "add", "raw", "wiki")
        git(root, "commit", "-q", "-m", "wiki checkpoint: raw (1), wiki (1)")

        rc = lint_wiki.lint(str(root), changed_only=True)
        payload = json.loads((root / "graph/recent.graph").read_text(encoding="utf-8"))
        recovered = [entry for entry in payload["entries"] if entry.get("source") == "git"]

        self.assertEqual(rc, 0)
        self.assertEqual(len(payload["edges"]), 2)
        self.assertEqual(payload["window"]["gitFallbackCommits"], 1)
        self.assertEqual(len(recovered), 1)
        self.assertEqual(recovered[0]["op"], "ingest")
        self.assertEqual(recovered[0]["touched"], ["wiki/summaries/中文总结.md"])

    def test_commit_synthesizes_log_paths_and_refreshes_recent(self) -> None:
        root = self.make_wiki()
        write(root, "raw/articles/new-source.md", "source\n")
        write(root, "wiki/summaries/新总结.md", "# 新总结\n")

        rc = commit_wiki.main([str(root), "--no-tag"])
        self.assertEqual(rc, 0)

        today_log = root / "log" / f"{datetime.now().astimezone():%Y%m%d}.md"
        log_text = today_log.read_text(encoding="utf-8")
        self.assertRegex(log_text, r"## \[\d{2}:\d{2}\] ingest \|")
        self.assertIn("[新总结](</summaries/新总结.md>)", log_text)

        committed = set(git(root, "show", "--format=", "--name-only", "HEAD").splitlines())
        self.assertIn("raw/articles/new-source.md", committed)
        self.assertIn("wiki/summaries/新总结.md", committed)
        self.assertIn(today_log.relative_to(root).as_posix(), committed)

        payload = json.loads((root / "graph/recent.graph").read_text(encoding="utf-8"))
        latest_ingests = [entry for entry in payload["entries"] if entry["op"] == "ingest"]
        self.assertTrue(latest_ingests)
        self.assertIn("wiki/summaries/新总结.md", latest_ingests[0]["touched"])
        self.assertEqual(git(root, "status", "--short"), "")


if __name__ == "__main__":
    unittest.main()
