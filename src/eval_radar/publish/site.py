from __future__ import annotations

import html
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from eval_radar.collect.pipeline import collect_all, save_digest
from eval_radar.config import ROOT, get_settings
from eval_radar.llm import complete
from eval_radar.memory.usage import latest_usage_for_purpose
from eval_radar.report.render import filter_items

POSTS_DIR = ROOT / "content" / "posts"
INDEX_FILE = ROOT / "content" / "posts.json"
DOCS_DIR = ROOT / "docs"
DOCS_POSTS = DOCS_DIR / "posts"
DOCS_ASSETS = DOCS_DIR / "assets"


def publish_daily_post(*, dry_run: bool = False) -> dict:
    payload = collect_all()
    save_digest(payload)
    article = generate_article(payload)
    if dry_run:
        return {"article": article, "counts": payload.get("counts", {})}

    post_path = save_article(article)
    posts = load_posts_index()
    post_row = {
        "slug": article["slug"],
        "title": article["title"],
        "date": article["date"],
        "summary": article["summary"],
        "key_takeaways": article.get("key_takeaways", []),
        "source_count": len(article.get("sources", [])),
        "model": article.get("model", "grok-4.5"),
        "reading_time": article.get("reading_time", "4 min read"),
        "tags": article.get("tags", []),
        "post_path": str(post_path.relative_to(ROOT)),
    }
    posts = [p for p in posts if p.get("slug") != article["slug"]]
    posts.insert(0, post_row)
    write_posts_index(posts)
    build_docs_site(posts)
    return {"post": post_row, "counts": payload.get("counts", {})}


def generate_article(payload: dict) -> dict:
    settings = get_settings()
    tz = ZoneInfo(settings.report_tz)
    day = datetime.now(tz).strftime("%Y-%m-%d")

    sources = _sources_from_payload(payload)
    items = filter_items(payload)
    condensed_items = [
        {
            "source": it.get("source"),
            "title": it.get("title"),
            "url": it.get("url"),
            "summary": (it.get("summary") or "")[:750],
        }
        for it in items[:14]
    ]

    model_for_post = settings.publisher_model or settings.cursor_model
    system = (
        "You are a senior editor focused only on LLM evaluation and benchmark methodology. "
        "Write in clear English. Return strict JSON only."
    )
    user = json.dumps(
        {
            "date": day,
            "focus": [
                "benchmark leakage and contamination",
                "LLM-as-a-judge changes",
                "evaluation harness updates",
                "methodology comparability risks",
            ],
            "items": condensed_items,
            "required_json_schema": {
                "title": "string",
                "subtitle": "string",
                "summary": "short paragraph",
                "key_takeaways": ["string", "string", "string"],
                "body_en": "long plain text analysis",
                "methodology_risks": "plain text",
                "tags": ["string", "string"],
            },
        },
        ensure_ascii=False,
        indent=2,
    )

    raw = complete(
        system,
        user,
        max_tokens=2600,
        purpose="daily_publish_article",
        model_override=model_for_post,
    )
    usage_row = latest_usage_for_purpose("daily_publish_article")
    actual_model = str((usage_row or {}).get("model") or model_for_post)
    data = _extract_json(raw)

    title = str(data.get("title") or f"LLM Evaluation Report - {day}").strip()
    slug = _slugify(f"{day}-{title}")[:96]
    body_en = str(data.get("body_en") or "").strip()

    return {
        "slug": slug,
        "date": day,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "title": title,
        "subtitle": str(data.get("subtitle") or "").strip(),
        "summary": str(data.get("summary") or "").strip(),
        "key_takeaways": _coerce_list(data.get("key_takeaways")),
        "body_en": body_en,
        "methodology_risks": str(data.get("methodology_risks") or "").strip(),
        "tags": _coerce_tags(data.get("tags")),
        "sources": sources,
        "reading_time": _calc_reading_time(body_en),
        "model": actual_model,
        "requested_model": model_for_post,
    }


def save_article(article: dict) -> Path:
    POSTS_DIR.mkdir(parents=True, exist_ok=True)
    path = POSTS_DIR / f"{article['slug']}.json"
    path.write_text(json.dumps(article, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_posts_index() -> list[dict]:
    if not INDEX_FILE.exists():
        return []
    try:
        data = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def write_posts_index(posts: list[dict]) -> None:
    INDEX_FILE.parent.mkdir(parents=True, exist_ok=True)
    INDEX_FILE.write_text(json.dumps(posts, ensure_ascii=False, indent=2), encoding="utf-8")


def build_docs_site(posts: list[dict] | None = None) -> None:
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    DOCS_POSTS.mkdir(parents=True, exist_ok=True)
    DOCS_ASSETS.mkdir(parents=True, exist_ok=True)
    _write_docs_assets()

    if posts is None:
        posts = load_posts_index()

    articles: list[dict] = []
    for row in posts:
        post_path = ROOT / row.get("post_path", "")
        if post_path.exists():
            try:
                articles.append(json.loads(post_path.read_text(encoding="utf-8")))
            except Exception:
                continue

    if not articles:
        return

    featured = articles[0]
    archive_cards = []
    for art in articles[1:]:
        archive_cards.append(
            f"""
            <article class="archive-card" data-tags="{html.escape(' '.join(art.get('tags', [])))}">
              <p class="meta">{html.escape(art.get('date', ''))} · {html.escape(art.get('model', 'llm'))}</p>
              <h3><a href="./posts/{html.escape(art.get('slug', ''))}.html">{html.escape(art.get('title', 'Untitled'))}</a></h3>
              <p class="summary">{html.escape((art.get('summary') or '')[:260])}</p>
              <p class="tags">{html.escape(' '.join(art.get('tags', [])[:4]))}</p>
            </article>
            """
        )

    all_tags = sorted({t for a in articles for t in a.get("tags", [])})
    tag_buttons = "".join(
        f'<button class="tag-btn" data-tag="{html.escape(t)}">{html.escape(t)}</button>'
        for t in all_tags[:10]
    )

    index_html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>LLM Evaluation Radar</title>
  <meta name="description" content="Daily analysis of LLM evaluation methodology and benchmark shifts." />
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet" />
  <link rel="stylesheet" href="./assets/site.css" />
</head>
<body>
  <header class="topbar">
    <div class="container topbar-inner">
      <a class="brand" href="./index.html">LLM Evaluation Radar</a>
      <a class="repo-link" href="https://github.com/MahirSelek/llm-eval-radar-daily" target="_blank" rel="noopener">Repository</a>
    </div>
  </header>

  <main class="container">
    <section class="hero">
      <p class="kicker">Daily LLM evaluation intelligence</p>
      <h1>Clean, trackable analysis without noise</h1>
      <p class="hero-sub">Focused reporting on benchmark methodology, judge drift, and reliability risks.</p>
    </section>

    <section class="featured">
      <p class="section-title">Latest</p>
      <article class="featured-card">
        <p class="meta">{html.escape(featured.get('date', ''))} · {html.escape(featured.get('model', 'llm'))} · {html.escape(featured.get('reading_time', '4 min read'))}</p>
        <h2><a href="./posts/{html.escape(featured.get('slug', ''))}.html">{html.escape(featured.get('title', 'Untitled'))}</a></h2>
        {f'<p class="subtitle">{html.escape(featured.get("subtitle", ""))}</p>' if featured.get("subtitle") else ''}
        <p class="summary">{html.escape(featured.get('summary', ''))}</p>
        <p class="tags">{html.escape(' '.join(featured.get('tags', [])[:5]))}</p>
      </article>
    </section>

    <section class="archive">
      <div class="archive-head">
        <p class="section-title">Archive</p>
        <input id="search" placeholder="Search posts" />
      </div>
      <div class="tags-row">
        <button class="tag-btn active" data-tag="all">All</button>
        {tag_buttons}
      </div>
      <div class="archive-grid" id="archive-grid">
        {''.join(archive_cards) if archive_cards else '<p>No previous posts yet.</p>'}
      </div>
    </section>
  </main>

  <footer class="footer">
    <div class="container">
      <p>Published daily via GitHub Actions</p>
    </div>
  </footer>

  <script src="./assets/site.js"></script>
</body>
</html>
"""
    (DOCS_DIR / "index.html").write_text(index_html, encoding="utf-8")

    for art in articles:
        post_html = _render_post_html(art)
        (DOCS_POSTS / f"{art['slug']}.html").write_text(post_html, encoding="utf-8")


def _render_post_html(article: dict) -> str:
    takeaways = article.get("key_takeaways", [])
    takeaways_html = ""
    if takeaways:
        items = "".join(f"<li>{html.escape(_clean_inline_markdown(t))}</li>" for t in takeaways)
        takeaways_html = f"""
        <section class="block">
          <p class="section-title">Key Takeaways</p>
          <ul>{items}</ul>
        </section>
        """

    methodology = article.get("methodology_risks", "")
    methodology_html = ""
    if methodology:
        methodology_html = f"""
        <section class="block">
          <p class="section-title">Methodology Risks</p>
          {_paragraphize(methodology)}
        </section>
        """

    sources_html = "".join(
        f'<li><a href="{html.escape(s.get("url", "#"))}" target="_blank" rel="noopener">{html.escape(s.get("title", "Source"))}</a> <span class="source-type">({html.escape(s.get("source", "web"))})</span></li>'
        for s in article.get("sources", [])
    )

    body_html = _paragraphize(article.get("body_en", ""))

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{html.escape(article.get('title', 'Daily report'))}</title>
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet" />
  <link rel="stylesheet" href="../assets/site.css" />
</head>
<body>
  <header class="topbar">
    <div class="container topbar-inner">
      <a class="brand" href="../index.html">LLM Evaluation Radar</a>
      <a class="repo-link" href="../index.html">All posts</a>
    </div>
  </header>

  <main class="container article">
    <a class="back-link" href="../index.html">Back to all posts</a>
    <p class="meta">{html.escape(article.get('date', ''))} · {html.escape(article.get('model', 'llm'))} · {html.escape(article.get('reading_time', '4 min read'))}</p>
    <h1>{html.escape(article.get('title', 'Untitled'))}</h1>
    {f'<p class="subtitle">{html.escape(article.get("subtitle", ""))}</p>' if article.get("subtitle") else ''}
    <p class="tags">{html.escape(' '.join(article.get('tags', [])))}</p>

    <section class="block">
      <p class="section-title">Executive Summary</p>
      <p>{html.escape(_clean_inline_markdown(article.get('summary', '')))}</p>
    </section>

    {takeaways_html}

    <section class="block">
      <p class="section-title">Analysis</p>
      {body_html}
    </section>

    {methodology_html}

    <section class="block">
      <p class="section-title">Sources</p>
      <ul>{sources_html}</ul>
    </section>
  </main>
</body>
</html>
"""


def _write_docs_assets() -> None:
    DOCS_ASSETS.mkdir(parents=True, exist_ok=True)

    css = """
:root {
  --bg: #0c0f14;
  --surface: #11161f;
  --surface-2: #171d29;
  --border: #29303d;
  --text: #e6ebf2;
  --muted: #9ba6b8;
  --link: #7cc4ff;
}
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; }
body {
  font-family: Inter, system-ui, -apple-system, sans-serif;
  background: var(--bg);
  color: var(--text);
  line-height: 1.65;
}
a { color: var(--link); text-decoration: none; }
a:hover { text-decoration: underline; }
.container { width: min(980px, calc(100vw - 2rem)); margin: 0 auto; }
.topbar { border-bottom: 1px solid var(--border); background: rgba(12, 15, 20, 0.92); }
.topbar-inner {
  height: 56px;
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.brand { font-weight: 700; color: var(--text); }
.repo-link, .back-link { font-size: 0.9rem; color: var(--muted); }
.hero { padding: 2.2rem 0 1.2rem; }
.kicker {
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 0.08em;
  font-size: 0.75rem;
  margin-bottom: 0.5rem;
}
h1 { margin: 0 0 0.8rem; line-height: 1.2; letter-spacing: -0.02em; }
.hero-sub { color: var(--muted); margin: 0; }
.section-title {
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  font-size: 0.75rem;
  margin: 0 0 0.75rem;
}
.featured { margin: 1.2rem 0 2rem; }
.featured-card,
.archive-card,
.block {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 1rem 1.1rem;
}
.featured-card h2,
.archive-card h3 { margin: 0.45rem 0 0.55rem; line-height: 1.3; }
.subtitle, .summary, .meta, .tags { color: var(--muted); }
.meta { font-size: 0.86rem; font-family: JetBrains Mono, ui-monospace, monospace; }
.tags { font-size: 0.82rem; }
.archive-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 1rem;
  margin-bottom: 0.8rem;
}
#search {
  width: 280px;
  max-width: 100%;
  background: var(--surface);
  border: 1px solid var(--border);
  color: var(--text);
  border-radius: 8px;
  padding: 0.5rem 0.7rem;
}
.tags-row {
  display: flex;
  gap: 0.5rem;
  flex-wrap: wrap;
  margin-bottom: 0.9rem;
}
.tag-btn {
  background: var(--surface);
  border: 1px solid var(--border);
  color: var(--muted);
  border-radius: 999px;
  padding: 0.3rem 0.7rem;
  font-size: 0.8rem;
  cursor: pointer;
}
.tag-btn.active { color: var(--text); border-color: #4e6a8a; }
.archive-grid {
  display: grid;
  gap: 0.8rem;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  margin-bottom: 2rem;
}
.article { padding: 1.4rem 0 2.2rem; }
.article h1 { margin-top: 0.4rem; }
.block { margin-top: 1rem; }
.block p { margin: 0.5rem 0 0; }
.block ul, .block ol { margin: 0.45rem 0 0; padding-left: 1.15rem; }
.block li { margin-bottom: 0.4rem; }
.source-type { color: var(--muted); font-size: 0.86rem; }
.footer {
  border-top: 1px solid var(--border);
  padding: 1.1rem 0;
  color: var(--muted);
  font-size: 0.86rem;
}
"""
    (DOCS_ASSETS / "site.css").write_text(css, encoding="utf-8")

    js = """
document.addEventListener("DOMContentLoaded", () => {
  const search = document.getElementById("search");
  const cards = [...document.querySelectorAll(".archive-card")];
  const tagButtons = [...document.querySelectorAll(".tag-btn")];
  let activeTag = "all";

  function apply() {
    const q = (search?.value || "").toLowerCase().trim();
    cards.forEach((card) => {
      const tags = (card.getAttribute("data-tags") || "").toLowerCase();
      const text = card.innerText.toLowerCase();
      const tagOk = activeTag == "all" || tags.includes(activeTag);
      const qOk = !q || text.includes(q);
      card.style.display = tagOk && qOk ? "block" : "none";
    });
  }

  tagButtons.forEach((btn) => {
    btn.addEventListener("click", () => {
      tagButtons.forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      activeTag = (btn.getAttribute("data-tag") || "all").toLowerCase();
      apply();
    });
  });

  search?.addEventListener("input", apply);
});
"""
    (DOCS_ASSETS / "site.js").write_text(js, encoding="utf-8")


def _sources_from_payload(payload: dict) -> list[dict]:
    items = filter_items(payload)
    out: list[dict] = []
    seen: set[str] = set()
    for it in items:
        url = it.get("url") or ""
        if not url or url in seen:
            continue
        seen.add(url)
        out.append(
            {
                "title": it.get("title") or "Source",
                "url": url,
                "source": it.get("source") or "web",
            }
        )
    return out


def _calc_reading_time(text: str) -> str:
    words = len(text.split())
    minutes = max(1, round(words / 220))
    return f"{minutes} min read"


def _extract_json(raw: str) -> dict:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z0-9_-]*\n", "", raw)
        raw = re.sub(r"\n```$", "", raw)
    match = re.search(r"\{[\s\S]*\}", raw)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    return {}


def _slugify(text: str) -> str:
    t = text.lower()
    t = re.sub(r"[^a-z0-9\s-]", "", t)
    t = re.sub(r"\s+", "-", t)
    return re.sub(r"-+", "-", t).strip("-")


def _coerce_list(val: object) -> list[str]:
    if isinstance(val, list):
        return [str(x).strip() for x in val if str(x).strip()]
    if isinstance(val, str) and val.strip():
        return [x.strip() for x in val.split("\n") if x.strip()]
    return []


def _coerce_tags(val: object) -> list[str]:
    if isinstance(val, list):
        return [str(x).strip() for x in val if str(x).strip()]
    if isinstance(val, str) and val.strip():
        return [x.strip() for x in val.split(",") if x.strip()]
    return ["Evaluation", "Benchmarks", "LLM-as-a-Judge"]


def _clean_inline_markdown(text: str) -> str:
    if not text:
        return ""
    t = text.strip()
    t = re.sub(r"\*\*(.+?)\*\*", r"\1", t)
    t = re.sub(r"__(.+?)__", r"\1", t)
    t = re.sub(r"`([^`]+)`", r"\1", t)
    t = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"\1 (\2)", t)
    return t


def _strip_extra_bullet(text: str) -> str:
    t = text.strip()
    if t.startswith("- "):
        return t[2:].strip()
    return t


def _paragraphize(text: str) -> str:
    if not text:
        return ""

    paras = re.split(r"\n{2,}", text.replace("\r\n", "\n"))
    out = []
    for p in paras:
        p_clean = p.strip()
        if not p_clean:
            continue

        lines = [line.strip() for line in p_clean.split("\n") if line.strip()]

        if all(line.startswith("- ") or line.startswith("• ") for line in lines):
            bullets = "".join(
                f"<li>{html.escape(_strip_extra_bullet(_clean_inline_markdown(re.sub(r'^[-•]\\s*', '', line))))}</li>"
                for line in lines
            )
            out.append(f"<ul>{bullets}</ul>")
            continue

        if all(re.match(r"^\d+\.\s+", line) for line in lines):
            bullets = "".join(
                f"<li>{html.escape(_clean_inline_markdown(re.sub(r'^\\d+\\.\\s*', '', line)))}</li>"
                for line in lines
            )
            out.append(f"<ol>{bullets}</ol>")
            continue

        if re.match(r"^#{1,3}\s+", lines[0]):
            header = re.sub(r"^#{1,3}\s*", "", lines[0])
            out.append(f"<h3>{html.escape(_clean_inline_markdown(header))}</h3>")
            if len(lines) > 1:
                remainder = "\n".join(lines[1:])
                out.append(f"<p>{html.escape(_clean_inline_markdown(remainder)).replace(chr(10), '<br>')}</p>")
            continue

        out.append(f"<p>{html.escape(_clean_inline_markdown(p_clean)).replace(chr(10), '<br>')}</p>")

    return "\n".join(out)
