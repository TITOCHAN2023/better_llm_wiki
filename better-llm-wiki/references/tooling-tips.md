# Tooling Tips

Practical setup and usage notes for the current LLM Wiki stack.

## Local editing setup

The wiki is plain Markdown on disk, so any editor works. A good setup has:

1. Fast full-text search across `wiki/`, `raw/`, `audit/`, and `log/`
2. Split-pane markdown preview
3. Easy access to the integrated terminal for `lint`, `audit_review`, and local preview commands

Recommended habits:

- Keep `wiki/index.md` and `SCHEMA.md` pinned while you work.
- Write all internal links in content-root form — a leading `/`, e.g. `/concepts/Foo.md`.
- When saving images for use in articles, move them into `wiki/assets/`.

If you inherit a wiki that still uses legacy `[[wikilinks]]`, run:

```bash
python3 scripts/migrate_wikilinks.py <wiki-root> --apply
```

## Local web viewer — `web/`

The local web viewer is the primary interactive preview workflow.

```bash
cd web
npm install
npm run build
npm start -- --wiki "/path/to/wiki-root" --port 4175
```

Then open `http://127.0.0.1:4175`.

Features:

- left sidebar navigation from `wiki/index.md`
- rendered markdown with mermaid and KaTeX
- current-page audit list
- selection-based feedback writing into `<wiki-root>/audit/`

The server binds to `127.0.0.1` only. It is intended for local personal use.

## `.graph` demo viewer

For protocol debugging, the repo also includes a static viewer:

```bash
cd <repo-root>
python3 -m http.server 4173
```

Open `http://127.0.0.1:4173/demo-graph-viewer.html`.

This viewer loads:

- page-local sidecars: `wiki/**/*.md.graph`
- global graph files: `graph/*.graph`

Use it when you want to verify that `lint_wiki.py` generated the expected local graph structure.

## Capturing web articles

Any browser clipper or "save as markdown" workflow is fine as long as the final file lands in `raw/articles/`.

Good workflow:

1. Save the article as markdown into `raw/articles/<slug>.md`
2. If the page contains important images, download them and move them under `wiki/assets/` if they need to be cited from wiki pages
3. Run `ingest` on the saved markdown file

For difficult pages (paywalled, dynamic, cluttered), manual copy-paste into `raw/articles/<slug>.md` is usually better than fighting automation.

## qmd (optional, for large wikis)

[qmd](https://github.com/tobi/qmd) is a local semantic search engine for Markdown files with BM25 + vector hybrid search. Useful when the wiki grows beyond ~100 pages and `wiki/index.md` scanning becomes slow.

```bash
pip install qmd
qmd collection add wiki/ --name my-wiki
qmd embed
qmd query "what are the tradeoffs of RAG vs wiki" --collection my-wiki
```

qmd also has an MCP server so LLMs can use it as a native tool.

## Marp — generating slide decks from wiki content

If you want slide decks, keep the source as markdown and render with Marp CLI or any compatible renderer.

```markdown
---
marp: true
theme: default
---

# Slide title

Content here

---

# Next slide
```

## Generating charts

For quantitative analyses, ask the LLM to generate a matplotlib script and save it to `outputs/charts/`:

```python
# outputs/charts/my-analysis.py
import matplotlib.pyplot as plt
# ... chart code ...
plt.savefig("outputs/charts/my-analysis.png")
```

To reference the chart from a wiki page, move the final image into `wiki/assets/` and embed it with a content-root path (leading `/`):

```markdown
![my analysis](/assets/my-analysis.png)
```

Do not use `../` paths inside `wiki/` files.

## Git workflow

The wiki is a git repo. Benefits:

- version history for every article
- branchable research directions
- durable audit history
- trackable graph protocol changes

```bash
git add .
git commit -m "ingest: 3 papers on attention mechanisms"
git push
```

Keep large binaries out of git. Use pointer files under `raw/refs/`.

## Interactive HTML outputs

For complex analyses, the LLM can generate interactive HTML with JavaScript and save it to `outputs/`. These can be opened directly in a browser.
