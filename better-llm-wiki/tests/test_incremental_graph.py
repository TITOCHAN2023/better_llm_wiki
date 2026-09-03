from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import lint_wiki  # noqa: E402


def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def page(title: str, links: str = "") -> str:
    return f"""---
title: "{title}"
type: concept
created: 2026-09-02
updated: 2026-09-02
sources: []
tags: []
---

# {title}

{links}
"""


def write(root: Path, relative: str, body: str) -> None:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")


def sidecar(root: Path, relative: str) -> dict:
    return json.loads((root / f"{relative}.graph").read_text(encoding="utf-8"))


class IncrementalGraphTests(unittest.TestCase):
    def make_wiki(self) -> Path:
        root = Path(tempfile.mkdtemp(prefix="llm-wiki-graph-"))
        git(root, "init", "-q")
        git(root, "config", "user.name", "Wiki Test")
        git(root, "config", "user.email", "wiki-test@example.com")
        write(root, ".gitignore", "graph/\n.graph-cache/\n*.md.graph\n")
        write(
            root,
            "wiki/index.md",
            page(
                "Index",
                "- [A](/A.md)\n- [B](/B.md)\n- [C](/C.md)\n",
            ),
        )
        write(root, "wiki/A.md", page("A", "[B](/B.md)\n"))
        write(root, "wiki/B.md", page("B"))
        write(root, "wiki/C.md", page("C"))
        git(root, "add", ".")
        git(root, "commit", "-q", "-m", "scaffold: graph test")
        self.assertEqual(lint_wiki.lint(str(root), changed_only=False), 0)
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        return root

    def assert_edge_moved(self, root: Path) -> None:
        a_graph = sidecar(root, "wiki/A.md")
        b_graph = sidecar(root, "wiki/B.md")
        c_graph = sidecar(root, "wiki/C.md")
        reverse = json.loads((root / ".graph-cache/reverse-links.graph").read_text(encoding="utf-8"))

        self.assertEqual(a_graph["ego"]["out"], [{"kind": "links_to", "to": "wiki/C.md"}])
        self.assertNotIn({"from": "wiki/A.md", "kind": "links_to"}, b_graph["ego"]["in"])
        self.assertIn({"from": "wiki/A.md", "kind": "links_to"}, c_graph["ego"]["in"])
        self.assertIn({"from": "wiki/index.md", "kind": "links_to"}, b_graph["ego"]["in"])
        self.assertIn({"from": "wiki/index.md", "kind": "links_to"}, c_graph["ego"]["in"])
        self.assertNotIn("wiki/A.md", reverse["wiki/B.md"])
        self.assertIn("wiki/A.md", reverse["wiki/C.md"])

    def test_git_incremental_lint_moves_outbound_and_inbound_edges(self) -> None:
        root = self.make_wiki()
        write(root, "wiki/A.md", page("A", "[C](/C.md)\n"))

        self.assertEqual(lint_wiki.lint(str(root), changed_only=True), 0)
        self.assert_edge_moved(root)

    def test_git_incremental_lint_preserves_backlinks_with_cold_reverse_cache(self) -> None:
        root = self.make_wiki()
        (root / ".graph-cache/reverse-links.graph").unlink()
        write(root, "wiki/A.md", page("A", "[C](/C.md)\n"))

        self.assertEqual(lint_wiki.lint(str(root), changed_only=True), 0)
        self.assert_edge_moved(root)

    def test_block_lists_compile_sources_tags_and_source_urls(self) -> None:
        root = self.make_wiki()
        write(root, "raw/articles/source.md", "original source\n")
        write(
            root,
            "wiki/A.md",
            """---
title: "A"
type: concept
created: 2026-09-02
updated: 2026-09-02
sources:
  - raw/articles/source.md
source_urls:
  - https://example.com/source
tags:
  - demo
  - provenance
---

# A
""",
        )

        self.assertEqual(lint_wiki.lint(str(root), changed_only=False), 0)
        a_graph = sidecar(root, "wiki/A.md")
        lineage = json.loads((root / "graph/lineage.graph").read_text(encoding="utf-8"))

        self.assertEqual(a_graph["tags"], ["demo", "provenance"])
        self.assertEqual(a_graph["sourceUrls"], ["https://example.com/source"])
        self.assertIn(
            {
                "s": "raw/articles/source.md",
                "t": "wiki/A.md",
                "k": "ingested_as",
                "w": 1,
                "d": 1,
            },
            lineage["edges"],
        )


if __name__ == "__main__":
    unittest.main()
