import MarkdownIt from "markdown-it";
import anchor from "markdown-it-anchor";
// @ts-expect-error - no types shipped
import attrs from "markdown-it-attrs";
// @ts-expect-error - no types shipped
import texmath from "markdown-it-texmath";
import katex from "katex";
import path from "node:path";
import fs from "node:fs";

export interface RenderedPage {
  html: string;
  frontmatter: Record<string, unknown> | null;
  rawMarkdown: string;
  title: string | null;
}

export interface RendererOptions {
  wikiRoot: string;
}

interface RenderEnv {
  currentFilePath?: string;
}

const EXTERNAL_URL_RE = /^[a-z][a-z0-9+.\-]*:/i;

export function createRenderer(opts: RendererOptions) {
  const md = new MarkdownIt({
    html: false,
    linkify: true,
    typographer: false,
    breaks: false,
  });

  md.use(attrs, {});
  md.use(anchor, {
    permalink: anchor.permalink.linkInsideHeader({
      symbol: "§",
      placement: "before",
    }),
  });
  md.use(texmath, {
    engine: katex,
    delimiters: "dollars",
    katexOptions: { throwOnError: false, strict: false },
  });

  // Internal MD link rewriter — resolve [text](relative/path.md) from the
  // wiki root, rewrite href to /?page=..., and tag alive/dead.
  // Also stamps the class `wikilink` so the client-side SPA interceptor and
  // existing CSS keep working unchanged.
  const defaultLinkOpen =
    md.renderer.rules.link_open ??
    ((tokens, idx, options, _env, self) =>
      self.renderToken(tokens, idx, options));

  md.renderer.rules.link_open = (tokens, idx, options, env, self) => {
    const tok = tokens[idx]!;
    const hrefAttr = tok.attrGet("href");
    if (hrefAttr) {
      const rewritten = rewriteInternalLink(
        hrefAttr,
        opts.wikiRoot,
        (env as RenderEnv | undefined)?.currentFilePath,
      );
      if (rewritten) {
        tok.attrSet("href", rewritten.href);
        const existing = tok.attrGet("class") ?? "";
        const cls = `wikilink ${rewritten.alive ? "wikilink-alive" : "wikilink-dead"}`;
        tok.attrSet("class", existing ? `${existing} ${cls}` : cls);
        tok.attrSet("data-wikilink-target", rewritten.target);
      }
    }
    return defaultLinkOpen(tokens, idx, options, env, self);
  };

  const defaultImage =
    md.renderer.rules.image ??
    ((tokens, idx, options, _env, self) =>
      self.renderToken(tokens, idx, options));

  md.renderer.rules.image = (tokens, idx, options, env, self) => {
    const tok = tokens[idx]!;
    const srcAttr = tok.attrGet("src");
    if (srcAttr) {
      const rewritten = rewriteInternalAsset(
        srcAttr,
        opts.wikiRoot,
        (env as RenderEnv | undefined)?.currentFilePath,
      );
      if (rewritten) {
        tok.attrSet("src", rewritten.src);
        if (!rewritten.alive) {
          const existing = tok.attrGet("class") ?? "";
          tok.attrSet("class", existing ? `${existing} wikilink-dead` : "wikilink-dead");
        }
        tok.attrSet("data-wikilink-target", rewritten.target);
      }
    }
    return defaultImage(tokens, idx, options, env, self);
  };

  md.core.ruler.push("source-line", (state) => {
    for (const tok of state.tokens) {
      if (tok.map && tok.level === 0 && tok.type.endsWith("_open")) {
        tok.attrSet("data-source-line", `${tok.map[0]},${tok.map[1]}`);
      }
    }
  });

  const defaultFence = md.renderer.rules.fence!;
  md.renderer.rules.fence = (tokens, idx, options, env, self) => {
    const tok = tokens[idx]!;
    const info = (tok.info || "").trim();
    const lang = info.split(/\s+/)[0];
    if (lang === "mermaid") {
      const line =
        tok.map && tok.level === 0
          ? ` data-source-line="${tok.map[0]},${tok.map[1]}"`
          : "";
      return `<pre class="mermaid-block"${line}><code class="language-mermaid">${escapeHtml(tok.content)}</code></pre>\n`;
    }
    return defaultFence(tokens, idx, options, env, self);
  };

  return {
    render(rawMarkdown: string, filePath: string): RenderedPage {
      const { frontmatter, body, title } = stripFrontmatter(rawMarkdown);
      const html = md.render(body, { currentFilePath: filePath } satisfies RenderEnv);
      return { html, frontmatter, rawMarkdown, title };
    },
  };
}

interface RewriteResult {
  href: string;
  alive: boolean;
  target: string;
}

interface AssetRewriteResult {
  src: string;
  alive: boolean;
  target: string;
}

function rewriteInternalLink(
  href: string,
  wikiRoot: string,
  currentFilePath?: string,
): RewriteResult | null {
  let h = href.trim();
  // CommonMark already unwraps angle brackets before this point, but be safe.
  if (h.startsWith("<") && h.endsWith(">")) h = h.slice(1, -1);
  // markdown-it normalises hrefs via encodeURI, so spaces arrive as %20.
  // Decode so we can hit the filesystem and measure true existence.
  try {
    h = decodeURI(h);
  } catch {
    // leave as-is on malformed sequences
  }
  if (!h) return null;
  if (EXTERNAL_URL_RE.test(h)) return null;
  if (h.startsWith("#")) return null;

  const [pathPart, anchorPart] = splitAnchor(h);
  if (!pathPart.endsWith(".md")) return null;

  const targetKey = resolveInternalTarget(pathPart, wikiRoot, currentFilePath);
  if (!targetKey) {
    const joined = path.posix.normalize(pathPart);
    return {
      href: `/?page=${encodeURIComponent(joined)}${anchorPart ? `#${anchorPart}` : ""}`,
      alive: false,
      target: joined,
    };
  }

  const fullOnDisk = path.resolve(wikiRoot, targetKey);
  const relFromRoot = path.relative(wikiRoot, fullOnDisk).split(path.sep).join("/");
  const escapesRoot =
    relFromRoot.startsWith("..") || path.isAbsolute(relFromRoot);

  let alive = false;
  try {
    alive = fs.statSync(fullOnDisk).isFile();
  } catch {
    alive = false;
  }
  const anchorSuffix = anchorPart ? `#${anchorPart}` : "";
  return {
    href: `/?page=${encodeURIComponent(escapesRoot ? targetKey : relFromRoot)}${anchorSuffix}`,
    alive,
    target: escapesRoot ? targetKey : relFromRoot,
  };
}

function rewriteInternalAsset(
  src: string,
  wikiRoot: string,
  currentFilePath?: string,
): AssetRewriteResult | null {
  let s = src.trim();
  if (s.startsWith("<") && s.endsWith(">")) s = s.slice(1, -1);
  try {
    s = decodeURI(s);
  } catch {
    // leave as-is on malformed sequences
  }
  if (!s) return null;
  if (EXTERNAL_URL_RE.test(s)) return null;
  if (s.startsWith("#")) return null;

  const targetKey = resolveInternalTarget(s, wikiRoot, currentFilePath);
  if (!targetKey) {
    const joined = path.posix.normalize(s);
    return {
      src: `/api/raw?path=${encodeURIComponent(joined)}`,
      alive: false,
      target: joined,
    };
  }

  const fullOnDisk = path.resolve(wikiRoot, targetKey);
  const relFromRoot = path.relative(wikiRoot, fullOnDisk).split(path.sep).join("/");
  const escapesRoot =
    relFromRoot.startsWith("..") || path.isAbsolute(relFromRoot);

  let alive = false;
  try {
    alive = fs.statSync(fullOnDisk).isFile();
  } catch {
    alive = false;
  }
  return {
    src: `/api/raw?path=${encodeURIComponent(escapesRoot ? targetKey : relFromRoot)}`,
    alive,
    target: escapesRoot ? targetKey : relFromRoot,
  };
}

function resolveInternalTarget(
  rawPath: string,
  wikiRoot: string,
  currentFilePath?: string,
): string | null {
  const normalized = path.posix.normalize(rawPath);
  if (path.posix.isAbsolute(normalized)) return null;

  const candidates: string[] = [];
  if (normalized.startsWith("wiki/")) {
    candidates.push(normalized);
  } else {
    if (currentFilePath?.startsWith("wiki/")) {
      candidates.push(path.posix.normalize(`wiki/${normalized}`));
    }
    if (currentFilePath) {
      candidates.push(path.posix.normalize(path.posix.join(path.posix.dirname(currentFilePath), normalized)));
    } else {
      candidates.push(normalized);
    }
  }

  for (const candidate of candidates) {
    if (candidate === "." || candidate === ".." || candidate.startsWith("../")) continue;
    const fullOnDisk = path.resolve(wikiRoot, candidate);
    const relFromRoot = path.relative(wikiRoot, fullOnDisk).split(path.sep).join("/");
    if (relFromRoot.startsWith("..") || path.isAbsolute(relFromRoot)) continue;
    if (fs.existsSync(fullOnDisk)) {
      return relFromRoot;
    }
  }

  for (const candidate of candidates) {
    if (candidate === "." || candidate === ".." || candidate.startsWith("../")) continue;
    return candidate;
  }
  return null;
}

function splitAnchor(h: string): [string, string] {
  const i = h.indexOf("#");
  if (i < 0) return [h, ""];
  return [h.slice(0, i), h.slice(i + 1)];
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

const FRONTMATTER_RE = /^---\n([\s\S]*?)\n---\n?/;

function stripFrontmatter(text: string): {
  frontmatter: Record<string, unknown> | null;
  body: string;
  title: string | null;
} {
  const m = FRONTMATTER_RE.exec(text);
  let frontmatter: Record<string, unknown> | null = null;
  let body = text;
  if (m) {
    frontmatter = {};
    for (const line of m[1]!.split("\n")) {
      const idx = line.indexOf(":");
      if (idx < 0) continue;
      frontmatter[line.slice(0, idx).trim()] = line.slice(idx + 1).trim();
    }
    body = text.slice(m[0].length);
  }
  const h1 = /^#\s+(.+?)\s*$/m.exec(body);
  const title =
    (frontmatter && typeof frontmatter.title === "string" && (frontmatter.title as string)) ||
    (h1 && h1[1]) ||
    null;
  return { frontmatter, body, title };
}
