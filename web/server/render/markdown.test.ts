import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { createRenderer } from "./markdown.js";

function makeWiki(): string {
  return fs.mkdtempSync(path.join(os.tmpdir(), "llm-wiki-markdown-"));
}

function writeFile(root: string, relativePath: string, body = "# Target\n"): void {
  const full = path.join(root, relativePath);
  fs.mkdirSync(path.dirname(full), { recursive: true });
  fs.writeFileSync(full, body, "utf-8");
}

function firstLink(html: string): { href: string; target: string } {
  const href = /href="([^"]+)"/.exec(html)?.[1];
  const target = /data-wikilink-target="([^"]+)"/.exec(html)?.[1];
  assert.ok(href, `missing href in ${html}`);
  assert.ok(target, `missing data-wikilink-target in ${html}`);
  return { href, target };
}

test("content-root links resolve under wiki/ and keep Unicode paths", (t) => {
  const root = makeWiki();
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  writeFile(root, "wiki/summaries/中文 文档.md");

  const rendered = createRenderer({ wikiRoot: root }).render(
    "[中文](</summaries/中文 文档.md>)\n",
    "wiki/summaries/source.md",
  );
  const link = firstLink(rendered.html);
  const url = new URL(link.href, "http://127.0.0.1");

  assert.match(rendered.html, /class="wikilink wikilink-alive"/);
  assert.equal(link.target, "wiki/summaries/中文 文档.md");
  assert.equal(url.searchParams.get("page"), "wiki/summaries/中文 文档.md");
});

test("missing content-root links stay inside wiki/ and are marked dead", (t) => {
  const root = makeWiki();
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));

  const rendered = createRenderer({ wikiRoot: root }).render(
    "[missing](/concepts/Missing.md)\n",
    "wiki/summaries/source.md",
  );
  const link = firstLink(rendered.html);
  const url = new URL(link.href, "http://127.0.0.1");

  assert.match(rendered.html, /class="wikilink wikilink-dead"/);
  assert.equal(link.target, "wiki/concepts/Missing.md");
  assert.equal(url.searchParams.get("page"), "wiki/concepts/Missing.md");
});

test("content-root traversal is never resolved to a repository file", (t) => {
  const root = makeWiki();
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  writeFile(root, "SCHEMA.md", "# Secret-adjacent repository file\n");

  const rendered = createRenderer({ wikiRoot: root }).render(
    "[escape](/../SCHEMA.md)\n",
    "wiki/summaries/source.md",
  );

  assert.match(rendered.html, /class="wikilink wikilink-dead"/);
  assert.doesNotMatch(rendered.html, /data-wikilink-target="SCHEMA\.md"/);
});
