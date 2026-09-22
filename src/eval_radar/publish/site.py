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
        "subtitle": article.get("subtitle", ""),
        "date": article["date"],
        "summary": article["summary"],
        "key_takeaways": article.get("key_takeaways", []),
        "source_count": len(article["sources"]),
        "model": article["model"],
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
            "summary": (it.get("summary") or "")[:800],
        }
        for it in items[:14]
    ]

    model_for_post = settings.publisher_model or settings.cursor_model
    system = (
        "You are a senior LLM evaluation research editor. "
        "Write in English only. "
        "Return strict JSON only with keys: title, subtitle, summary, key_takeaways, body_en, methodology_risks, tags. "
        "No markdown bold syntax (**), no code fences, no Turkish text."
    )
    user = json.dumps(
        {
            "date": day,
            "timezone": settings.report_tz,
            "counts": payload.get("counts", {}),
            "focus": [
                "benchmark shifts and leaderboard validity",
                "llm-as-judge changes and calibration drift",
                "evaluation methodology risks",
                "practical implications for research and infra teams",
            ],
            "items": condensed_items,
            "required_shape": {
                "title": "clear technical English title",
                "subtitle": "one-line thesis statement",
                "summary": "4-6 sentence executive summary",
                "key_takeaways": ["3-5 concrete implications"],
                "body_en": (
                    "900-1600 words, deep but readable, with section headings and examples from sources. "
                    "Explain why each development matters and what actions teams should take."
                ),
                "methodology_risks": "2-3 focused paragraphs on comparability, leakage, judge drift, and interpretation risk",
                "tags": ["evaluation", "benchmark", "llm-as-judge"],
            },
            "quality_bar": [
                "Do not just summarize links; synthesize and interpret.",
                "Compare signals and draw reasoned conclusions.",
                "Write like a senior research lead briefing technical stakeholders.",
            ],
        },
        ensure_ascii=False,
        indent=2,
    )
    raw = complete(
        system,
        user,
        max_tokens=3200,
        purpose="daily_publish_article",
        model_override=model_for_post,
    )
    usage_row = latest_usage_for_purpose("daily_publish_article")
    actual_model = str((usage_row or {}).get("model") or model_for_post)
    data = _extract_json(raw)
    title = str(data.get("title") or f"LLM Evaluation Daily - {day}").strip()
    slug = _slugify(f"{day}-{title}")[:96]
    return {
        "slug": slug,
        "date": day,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "title": title,
        "subtitle": str(data.get("subtitle") or "").strip(),
        "summary": str(data.get("summary") or "").strip(),
        "key_takeaways": _coerce_list(data.get("key_takeaways"))[:8],
        "body_en": str(data.get("body_en") or "").strip(),
        "methodology_risks": str(data.get("methodology_risks") or "").strip(),
        "tags": _coerce_tags(data.get("tags")),
        "sources": sources,
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
    if isinstance(data, list):
        return data
    return []


def write_posts_index(posts: list[dict]) -> None:
    INDEX_FILE.parent.mkdir(parents=True, exist_ok=True)
    INDEX_FILE.write_text(json.dumps(posts, ensure_ascii=False, indent=2), encoding="utf-8")


def build_docs_site(posts: list[dict]) -> None:
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    DOCS_POSTS.mkdir(parents=True, exist_ok=True)
    _write_docs_assets()

    cards = []
    for row in posts[:120]:
        subtitle = row.get("subtitle") or ""
        subtitle_html = f'<p class="sub">{html.escape(subtitle)}</p>' if subtitle else ""
        cards.append(
            f"""
            <article class="card">
              <div class="meta">{html.escape(row.get('date', ''))} · {html.escape(row.get('model', ''))}</div>
              <h2><a href="./posts/{html.escape(row.get('slug', ''))}.html">{html.escape(row.get('title', ''))}</a></h2>
              {subtitle_html}
              <p>{html.escape(row.get('summary', ''))}</p>
            </article>
            """
        )
    index_html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>LLM Evaluation Daily</title>
  <link rel="stylesheet" href="./assets/site.css" />
</head>
<body>
  <header class="wrap">
    <h1>LLM Evaluation Daily</h1>
    <p>Daily technical briefings on evaluation, benchmarks, and leaderboard reliability.</p>
  </header>
  <main class="wrap grid">
    {''.join(cards) if cards else '<p>No posts yet.</p>'}
  </main>
</body>
</html>
"""
    (DOCS_DIR / "index.html").write_text(index_html, encoding="utf-8")

    for row in posts[:120]:
        post_file = ROOT / row.get("post_path", "")
        if not post_file.exists():
            continue
        article = json.loads(post_file.read_text(encoding="utf-8"))
        post_html = _render_post_html(article)
        (DOCS_POSTS / f"{article['slug']}.html").write_text(post_html, encoding="utf-8")


def _render_post_html(article: dict) -> str:
    tags = " ".join(
        f'<span class="tag">{html.escape(str(t))}</span>' for t in article.get("tags", [])
    )
    sources = "".join(
        f'<li><a href="{html.escape(s["url"])}" target="_blank" rel="noopener">{html.escape(s["title"])}</a> <span class="muted">({html.escape(s["source"])})</span></li>'
        for s in article.get("sources", [])
    )
    body_en = _paragraphize(article.get("body_en", ""))
    takeaways = _listify_takeaways(article.get("key_takeaways") or [])
    methodology = _paragraphize(article.get("methodology_risks", ""))
    subtitle = article.get("subtitle") or ""
    subtitle_html = f'<p class="lead">{html.escape(subtitle)}</p>' if subtitle else ""
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>{html.escape(article.get('title', ''))}</title>
  <link rel="stylesheet" href="../assets/site.css" />
</head>
<body>
  <main class="wrap post">
    <a class="back" href="../index.html">← All posts</a>
    <h1>{html.escape(article.get('title', ''))}</h1>
    <p class="meta">{html.escape(article.get('date', ''))} · model: {html.escape(article.get('model', ''))}</p>
    {subtitle_html}
    <p class="lead">{html.escape(article.get('summary', ''))}</p>
    <div class="tags">{tags}</div>
    <section>
      <h2>Key Takeaways</h2>
      {takeaways}
    </section>
    <section>
      <h2>Deep Analysis</h2>
      {body_en}
    </section>
    <section>
      <h2>Methodology Risks</h2>
      {methodology}
    </section>
    <section>
      <h2>Sources</h2>
      <ul>{sources}</ul>
    </section>
  </main>
</body>
</html>
"""


def _write_docs_assets() -> None:
    assets = DOCS_DIR / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    css = """body {
  margin: 0;
  font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  background: #0b0f14;
  color: #e8eef3;
}
.wrap { max-width: 900px; margin: 0 auto; padding: 24px 18px; }
h1, h2, h3 { letter-spacing: -0.02em; }
.grid { display: grid; gap: 14px; }
.card {
  background: #131a21;
  border: 1px solid #293443;
  border-radius: 14px;
  padding: 14px 16px;
}
.card h2 { margin: 0 0 8px; font-size: 1.2rem; }
.card p { margin: 0; color: #b5c3d0; line-height: 1.5; }
.card .sub { margin-bottom: 8px; color: #9fc2dc; }
.meta { color: #93a5b7; font-size: 0.88rem; }
a { color: #66d9ef; text-decoration: none; }
a:hover { text-decoration: underline; }
.post .lead { color: #cddceb; font-size: 1.03rem; line-height: 1.6; }
.tag {
  display: inline-block;
  padding: 0.2rem 0.55rem;
  border-radius: 999px;
  border: 1px solid #2b3948;
  margin-right: 0.35rem;
  margin-bottom: 0.35rem;
  color: #adc0d0;
  font-size: 0.82rem;
}
.back { display: inline-block; margin-bottom: 8px; color: #9ec3df; }
ul, ol { line-height: 1.65; }
ul li, ol li { margin-bottom: 0.35rem; }
.muted { color: #93a5b7; }
section { margin-top: 18px; }
section p { line-height: 1.7; color: #d8e3ec; }
section h2 { margin-bottom: 10px; }
section h3 { margin: 14px 0 8px; }
"""
    (assets / "site.css").write_text(css, encoding="utf-8")


def _extract_json(raw: str) -> dict:
    text = (raw or "").strip()
    if text.startswith("{") and text.endswith("}"):
        return json.loads(text)

    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    return {
        "title": "LLM Evaluation Daily",
        "subtitle": "Daily research briefing",
        "summary": text[:500] if text else "Daily update",
        "key_takeaways": [],
        "body_en": text,
        "methodology_risks": "",
        "tags": ["evaluation", "benchmark"],
    }


def _coerce_tags(tags: object) -> list[str]:
    if not isinstance(tags, list):
        return ["evaluation", "benchmark"]
    out: list[str] = []
    for tag in tags:
        t = str(tag).strip().lower()
        if not t:
            continue
        out.append(t[:32])
    return out[:8] or ["evaluation", "benchmark"]


def _coerce_list(items: object) -> list[str]:
    if isinstance(items, list):
        return [str(i).strip() for i in items if str(i).strip()]
    if isinstance(items, str):
        return [ln.strip() for ln in items.splitlines() if ln.strip()]
    return []


def _sources_from_payload(payload: dict) -> list[dict]:
    out = []
    seen: set[str] = set()
    for it in payload.get("items") or []:
        url = (it.get("url") or "").strip()
        title = (it.get("title") or "").strip()
        source = (it.get("source") or "").strip()
        if not url or not title:
            continue
        if url in seen:
            continue
        seen.add(url)
        out.append({"title": title, "url": url, "source": source})
    return out[:16]


def _slugify(text: str) -> str:
    s = text.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s or "post"


def _clean_inline_markdown(text: str) -> str:
    t = str(text).strip()
    t = re.sub(r"\*\*(.+?)\*\*", r"\1", t)
    t = re.sub(r"__(.+?)__", r"\1", t)
    t = re.sub(r"`([^`]+)`", r"\1", t)
    return t


def _paragraphize(text: str) -> str:
    lines = [line.strip() for line in str(text).splitlines()]
    blocks = []
    bullet_open = False
    for line in lines:
        if not line:
            if bullet_open:
                blocks.append("</ul>")
                bullet_open = False
            continue

        if line.startswith("- "):
            if not bullet_open:
                blocks.append("<ul>")
                bullet_open = True
            blocks.append(f"<li>{html.escape(_clean_inline_markdown(line[2:].strip()))}</li>")
            continue

        if bullet_open:
            blocks.append("</ul>")
            bullet_open = False

        if line.startswith("### "):
            blocks.append(f"<h3>{html.escape(_clean_inline_markdown(line[4:].strip()))}</h3>")
        elif line.startswith("## "):
            blocks.append(f"<h3>{html.escape(_clean_inline_markdown(line[3:].strip()))}</h3>")
        else:
            blocks.append(f"<p>{html.escape(_clean_inline_markdown(line))}</p>")

    if bullet_open:
        blocks.append("</ul>")
    return "\n".join(blocks)


def _listify_takeaways(items: list[str]) -> str:
    cleaned = [i for i in (_clean_inline_markdown(x) for x in items) if i]
    if not cleaned:
        return "<p class=\"muted\">No explicit takeaways provided.</p>"
    lis = "".join(f"<li>{html.escape(x)}</li>" for x in cleaned)
    return f"<ul>{lis}</ul>"
