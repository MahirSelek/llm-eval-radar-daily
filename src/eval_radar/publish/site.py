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
    now = datetime.now(tz)
    day = now.strftime("%Y-%m-%d")
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
        "You are a world-class principal AI research scientist and technical editor specializing exclusively in "
        "Large Language Model (LLM) Evaluation, Benchmark Integrity, and Leaderboard Shifts. "
        "Write in flawless, authoritative English. "
        "Return STRICT JSON only, matching the exact schema provided. No markdown codeblock wrappers."
    )
    user = json.dumps(
        {
            "date": day,
            "focus_areas": [
                "Benchmark leakage and few-shot prompt contamination",
                "LLM-as-a-judge model retirements and scoring drift",
                "Evaluation harness upgrades (EleutherAI lm-eval, Stanford HELM, Inspect)",
                "Novel cognitive & domain-specific benchmarks",
                "Internal representation probing vs output-only metrics",
            ],
            "raw_signals": condensed_items,
            "required_json_schema": {
                "title": "Compelling, rigorous research headline in English",
                "subtitle": "One sentence analytical summary",
                "summary": "High-impact executive summary (3-4 sentences)",
                "key_takeaways": [
                    "Bullet 1: Direct implication on benchmarks",
                    "Bullet 2: Judge model or harness change",
                    "Bullet 3: Practical recommendation for researchers",
                ],
                "body_en": "Full deep-dive analytical article in English (structured with clean sections, paragraphs, and bullet points. No raw markdown stars).",
                "methodology_risks": "2-3 paragraphs discussing methodological caveats, comparability breaks, and evaluation pitfalls.",
                "tags": ["Benchmark Leakage", "LLM-as-a-Judge", "lm-eval-harness", "HELM", "Methodology"],
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
    
    title = str(data.get("title") or f"LLM Evaluation & Benchmark Report — {day}").strip()
    slug = _slugify(f"{day}-{title}")[:96]
    body_en = str(data.get("body_en") or "").strip()
    summary = str(data.get("summary") or "").strip()
    key_takeaways = _coerce_list(data.get("key_takeaways"))
    methodology_risks = str(data.get("methodology_risks") or "").strip()
    tags = _coerce_tags(data.get("tags"))
    reading_time = _calc_reading_time(body_en)

    return {
        "slug": slug,
        "date": day,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "title": title,
        "subtitle": str(data.get("subtitle") or "").strip(),
        "summary": summary,
        "key_takeaways": key_takeaways,
        "body_en": body_en,
        "methodology_risks": methodology_risks,
        "tags": tags,
        "sources": sources,
        "reading_time": reading_time,
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


def build_docs_site(posts: list[dict] | None = None) -> None:
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    DOCS_POSTS.mkdir(parents=True, exist_ok=True)
    DOCS_ASSETS.mkdir(parents=True, exist_ok=True)
    _write_docs_assets()

    if posts is None:
        posts = load_posts_index()

    # Read all post details
    articles: list[dict] = []
    for row in posts:
        post_path = ROOT / row.get("post_path", "")
        if post_path.exists():
            try:
                art = json.loads(post_path.read_text(encoding="utf-8"))
                articles.append(art)
            except Exception:
                continue

    if not articles:
        return

    # 1. Render index.html
    featured = articles[0]
    previous = articles[1:]

    featured_tags_html = "".join(
        f'<span class="badge badge-accent">{html.escape(t)}</span>' for t in featured.get("tags", [])[:4]
    )
    featured_takeaways_html = "".join(
        f'<li><span class="bullet-dot"></span><span>{html.escape(t)}</span></li>'
        for t in featured.get("key_takeaways", [])[:4]
    )

    previous_cards_html = []
    for art in previous:
        p_tags = "".join(
            f'<span class="badge">{html.escape(t)}</span>' for t in art.get("tags", [])[:3]
        )
        previous_cards_html.append(
            f"""
            <article class="report-card" data-tags="{html.escape(' '.join(art.get('tags', [])))}">
              <div class="card-meta">
                <span class="date">{html.escape(art.get('date', ''))}</span>
                <span class="dot">·</span>
                <span class="model-tag">{html.escape(art.get('model', 'LLM'))}</span>
                <span class="dot">·</span>
                <span class="reading-time">{html.escape(art.get('reading_time', '4 min read'))}</span>
              </div>
              <h3 class="card-title">
                <a href="./posts/{html.escape(art['slug'])}.html">{html.escape(art.get('title', ''))}</a>
              </h3>
              <p class="card-summary">{html.escape(art.get('summary', ''))[:220]}...</p>
              <div class="card-footer">
                <div class="card-tags">{p_tags}</div>
                <a class="read-link" href="./posts/{html.escape(art['slug'])}.html">Read Analysis →</a>
              </div>
            </article>
            """
        )

    all_tags = sorted(list({t for a in articles for t in a.get("tags", [])}))
    tag_pills_html = "".join(
        f'<button class="tag-filter-btn" data-tag="{html.escape(t)}">{html.escape(t)}</button>'
        for t in all_tags[:8]
    )

    index_html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>LLM Evaluation Radar — Daily Intelligence on AI Benchmarks & Methodology</title>
  <meta name="description" content="Autonomous daily research intelligence tracking LLM evaluation methodology, benchmark contamination, judge model drift, and leaderboard shifts." />
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
  <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&family=Newsreader:ital,wght@0,400;0,600;1,400&display=swap" rel="stylesheet" />
  <link rel="stylesheet" href="./assets/site.css" />
</head>
<body>
  <!-- Top Global Header -->
  <header class="site-header">
    <div class="header-inner">
      <div class="brand">
        <a href="./index.html" class="logo">
          <span class="logo-pulse"></span>
          <span class="logo-text">LLM Eval <strong>Radar</strong></span>
        </a>
        <span class="status-badge">
          <span class="status-dot"></span> 2026 Live Radar
        </span>
      </div>
      <nav class="nav-links">
        <a href="#featured">Featured</a>
        <a href="#archive">Archive</a>
        <a href="https://github.com/MahirSelek/llm-eval-radar-daily" target="_blank" rel="noopener" class="github-link">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><path d="M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57 0-.285-.015-1.23-.015-2.235-3.015.555-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695-.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99.105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225-.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405s2.04.135 3 .405c2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.905 1.23 3.225 0 4.605-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3 0 .315.225.69.825.57A12.02 12.02 0 0024 12c0-6.63-5.37-12-12-12z"/></svg>
          GitHub
        </a>
      </nav>
    </div>
  </header>

  <main class="site-main">
    <!-- Hero Banner -->
    <section class="hero-section">
      <div class="hero-glow"></div>
      <div class="hero-content">
        <div class="hero-eyebrow">
          <span class="pulse-indicator"></span> Autonomous Research Intelligence
        </div>
        <h1 class="hero-title">Tracking LLM Benchmarks, Judge Drift & Evaluation Rigor</h1>
        <p class="hero-lead">
          Daily analytical syntheses on evaluation methodology shifts, harness bug fixes, benchmark contamination, and leaderboard validity.
        </p>
        <div class="hero-metrics">
          <div class="metric-item">
            <span class="metric-val">{len(articles)}</span>
            <span class="metric-lbl">Daily Editions</span>
          </div>
          <div class="metric-divider"></div>
          <div class="metric-item">
            <span class="metric-val">3</span>
            <span class="metric-lbl">Ingest Streams (arXiv / GitHub / Community)</span>
          </div>
          <div class="metric-divider"></div>
          <div class="metric-item">
            <span class="metric-val">05:00 UTC</span>
            <span class="metric-lbl">Autonomous Daily Dispatch</span>
          </div>
        </div>
      </div>
    </section>

    <!-- Featured Spotlight Article -->
    <section id="featured" class="featured-section">
      <div class="section-header">
        <div class="section-title-wrap">
          <span class="badge badge-primary">Latest Intelligence</span>
          <h2>Featured Daily Synthesis</h2>
        </div>
        <span class="featured-date-badge">{html.escape(featured.get('date', ''))}</span>
      </div>

      <div class="featured-card">
        <div class="featured-card-header">
          <div class="featured-meta">
            <span class="badge badge-model">{html.escape(featured.get('model', 'Grok-4.5'))}</span>
            <span class="reading-time">⏱️ {html.escape(featured.get('reading_time', '4 min read'))}</span>
            <span class="source-count">📊 {len(featured.get('sources', []))} Verified Sources</span>
          </div>
          <div class="featured-tags">{featured_tags_html}</div>
        </div>

        <h2 class="featured-headline">
          <a href="./posts/{html.escape(featured['slug'])}.html">{html.escape(featured.get('title', ''))}</a>
        </h2>

        {f'<p class="featured-subtitle">{html.escape(featured.get("subtitle", ""))}</p>' if featured.get("subtitle") else ''}

        <p class="featured-summary">{html.escape(featured.get('summary', ''))}</p>

        {f'''
        <div class="featured-takeaways">
          <h4 class="takeaways-title">⚡ Key Strategic Signals</h4>
          <ul class="takeaways-list">
            {featured_takeaways_html}
          </ul>
        </div>
        ''' if featured_takeaways_html else ''}

        <div class="featured-cta">
          <a href="./posts/{html.escape(featured['slug'])}.html" class="btn-primary">
            Read Full Technical Analysis
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M5 12h14M12 5l7 7-7 7"/></svg>
          </a>
        </div>
      </div>
    </section>

    <!-- Archive & Previous Reports -->
    <section id="archive" class="archive-section">
      <div class="section-header">
        <div class="section-title-wrap">
          <span class="badge">Archive</span>
          <h2>All Intelligence Editions</h2>
        </div>
        <div class="filter-controls">
          <div class="search-wrap">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg>
            <input type="text" id="report-search" placeholder="Search benchmark, harness, judge..." autocomplete="off" />
          </div>
        </div>
      </div>

      <div class="tag-filters">
        <button class="tag-filter-btn active" data-tag="all">All Topics</button>
        {tag_pills_html}
      </div>

      <div class="reports-grid" id="reports-grid">
        {"".join(previous_cards_html) if previous_cards_html else '<div class="empty-card">More daily reports will appear here as autonomous dispatches run.</div>'}
      </div>
    </section>
  </main>

  <footer class="site-footer">
    <div class="footer-inner">
      <div class="footer-left">
        <p class="footer-brand">LLM Evaluation Radar</p>
        <p class="footer-sub">Autonomous research intelligence curated by Mahir Selek.</p>
      </div>
      <div class="footer-right">
        <p class="footer-note">Open-source & published automatically via GitHub Actions.</p>
        <p class="footer-copy">© 2026 LLM Eval Radar · Verified with arXiv & GitHub Releases</p>
      </div>
    </div>
  </footer>

  <script src="./assets/site.js"></script>
</body>
</html>
"""
    (DOCS_DIR / "index.html").write_text(index_html, encoding="utf-8")

    # 2. Render each post in docs/posts/*.html
    for art in articles:
        post_html = _render_post_html(art)
        (DOCS_POSTS / f"{art['slug']}.html").write_text(post_html, encoding="utf-8")


def _render_post_html(article: dict) -> str:
    tags_html = "".join(
        f'<span class="badge">{html.escape(str(t))}</span>' for t in article.get("tags", [])
    )
    
    # Render sources as clean cards
    sources_cards = []
    for s in article.get("sources", []):
        src_kind = html.escape(str(s.get("source", "paper"))).upper()
        sources_cards.append(
            f"""
            <a href="{html.escape(s.get('url', '#'))}" target="_blank" rel="noopener" class="source-card">
              <span class="source-tag source-{src_kind.lower()}">{src_kind}</span>
              <span class="source-title">{html.escape(s.get('title', 'Source reference'))}</span>
              <span class="source-arrow">↗</span>
            </a>
            """
        )

    takeaways = article.get("key_takeaways", [])
    takeaways_html = ""
    if takeaways:
        items_html = "".join(f"<li><span class='bullet-dot'></span><span>{html.escape(t)}</span></li>" for t in takeaways)
        takeaways_html = f"""
        <div class="post-takeaways-box">
          <h3 class="takeaways-header">⚡ Core Strategic Signals & Implications</h3>
          <ul class="takeaways-list">{items_html}</ul>
        </div>
        """

    methodology = article.get("methodology_risks", "")
    methodology_html = ""
    if methodology:
        methodology_html = f"""
        <div class="post-callout-box">
          <div class="callout-header">
            <span class="callout-icon">🚨</span>
            <h3>Methodological Validity & Leaderboard Pitfalls</h3>
          </div>
          <div class="callout-content">
            {_paragraphize(methodology)}
          </div>
        </div>
        """

    body_en_rendered = _paragraphize(article.get("body_en", ""))

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{html.escape(article.get('title', 'Daily Report'))} — LLM Eval Radar</title>
  <meta name="description" content="{html.escape(article.get('summary', ''))[:160]}" />
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
  <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&family=Newsreader:ital,wght@0,400;0,600;1,400&display=swap" rel="stylesheet" />
  <link rel="stylesheet" href="../assets/site.css" />
</head>
<body class="article-page">
  <div class="reading-progress-bar" id="progress-bar"></div>

  <!-- Top Global Header -->
  <header class="site-header">
    <div class="header-inner">
      <div class="brand">
        <a href="../index.html" class="logo">
          <span class="logo-pulse"></span>
          <span class="logo-text">LLM Eval <strong>Radar</strong></span>
        </a>
      </div>
      <nav class="nav-links">
        <a href="../index.html" class="back-link">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M19 12H5M12 19l-7-7 7-7"/></svg>
          All Editions
        </a>
      </nav>
    </div>
  </header>

  <main class="article-main">
    <article class="article-container">
      <div class="article-header">
        <div class="article-meta-ribbon">
          <span class="date">{html.escape(article.get('date', ''))}</span>
          <span class="dot">·</span>
          <span class="badge badge-model">{html.escape(article.get('model', 'Grok-4.5'))}</span>
          <span class="dot">·</span>
          <span class="reading-time">⏱️ {html.escape(article.get('reading_time', '4 min read'))}</span>
        </div>

        <h1 class="article-title">{html.escape(article.get('title', ''))}</h1>

        {f'<p class="article-subtitle">{html.escape(article.get("subtitle", ""))}</p>' if article.get("subtitle") else ''}

        <div class="article-tags-row">{tags_html}</div>
      </div>

      <!-- Executive Summary Lead -->
      <div class="article-lead-box">
        <span class="lead-label">EXECUTIVE SUMMARY</span>
        <p class="lead-text">{html.escape(article.get('summary', ''))}</p>
      </div>

      {takeaways_html}

      <!-- Main Deep Dive Content -->
      <div class="article-body">
        {body_en_rendered}
      </div>

      {methodology_html}

      <!-- Primary Sources Section -->
      <div class="article-sources-section">
        <h3 class="sources-header">📑 Primary Sources & Verification Papers</h3>
        <p class="sources-sub">Verified citations from arXiv submissions, GitHub release notes, and benchmark repositories:</p>
        <div class="sources-grid">
          {"".join(sources_cards) if sources_cards else '<p class="muted">No direct references attached.</p>'}
        </div>
      </div>

      <!-- Bottom Navigation -->
      <div class="article-footer-nav">
        <a href="../index.html" class="btn-secondary">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M19 12H5M12 19l-7-7 7-7"/></svg>
          Back to Radar Feed
        </a>
      </div>
    </article>
  </main>

  <footer class="site-footer">
    <div class="footer-inner">
      <p class="footer-copy">© 2026 LLM Eval Radar · Curated by Mahir Selek · Published via GitHub Actions</p>
    </div>
  </footer>

  <script src="../assets/site.js"></script>
</body>
</html>
"""


def _write_docs_assets() -> None:
    DOCS_ASSETS.mkdir(parents=True, exist_ok=True)
    
    # Modern 2026 Dark Cyber-Academic CSS
    css = """/* ==========================================================================
   LLM Evaluation Radar — 2026 Cutting-Edge Research Portal Theme
   ========================================================================== */

:root {
  --bg-deep: #080b0f;
  --bg-surface: #0e131a;
  --bg-card: #131a24;
  --bg-card-hover: #182230;
  --bg-glass: rgba(19, 26, 36, 0.75);
  
  --border-subtle: rgba(255, 255, 255, 0.08);
  --border-card: rgba(255, 255, 255, 0.12);
  --border-focus: #38bdf8;
  
  --text-main: #f1f5f9;
  --text-muted: #94a3b8;
  --text-dim: #64748b;
  
  --accent-cyan: #38bdf8;
  --accent-emerald: #34d399;
  --accent-violet: #818cf8;
  --accent-amber: #fbbf24;
  --accent-rose: #f43f5e;
  
  --font-sans: "Plus Jakarta Sans", system-ui, -apple-system, sans-serif;
  --font-mono: "JetBrains Mono", ui-monospace, monospace;
  --font-editorial: "Newsreader", Georgia, serif;
  
  --radius-sm: 8px;
  --radius-md: 14px;
  --radius-lg: 20px;
  --radius-full: 9999px;
  
  --shadow-card: 0 10px 30px -10px rgba(0, 0, 0, 0.5);
  --shadow-glow: 0 0 35px -5px rgba(56, 189, 248, 0.18);
}

*, *::before, *::after {
  box-sizing: border-box;
  margin: 0;
  padding: 0;
}

html {
  scroll-behavior: smooth;
  color-scheme: dark;
}

body {
  font-family: var(--font-sans);
  background-color: var(--bg-deep);
  color: var(--text-main);
  line-height: 1.6;
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  -webkit-font-smoothing: antialiased;
  background-image: 
    radial-gradient(circle at 50% 0%, rgba(56, 189, 248, 0.08), transparent 45%),
    radial-gradient(circle at 100% 20%, rgba(129, 140, 248, 0.05), transparent 40%),
    radial-gradient(circle at 0% 50%, rgba(52, 211, 153, 0.04), transparent 35%);
  background-attachment: fixed;
}

a {
  color: inherit;
  text-decoration: none;
}

/* Header */
.site-header {
  position: sticky;
  top: 0;
  z-index: 100;
  background: var(--bg-glass);
  backdrop-filter: blur(14px);
  -webkit-backdrop-filter: blur(14px);
  border-bottom: 1px solid var(--border-subtle);
}

.header-inner {
  max-width: 1140px;
  margin: 0 auto;
  padding: 0.9rem 1.5rem;
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.brand {
  display: flex;
  align-items: center;
  gap: 1rem;
}

.logo {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  font-size: 1.15rem;
  font-weight: 700;
  letter-spacing: -0.02em;
}

.logo-text strong {
  color: var(--accent-cyan);
}

.logo-pulse {
  width: 10px;
  height: 10px;
  background: var(--accent-cyan);
  border-radius: 50%;
  box-shadow: 0 0 12px var(--accent-cyan);
  animation: pulse-ring 2.5s infinite;
}

@keyframes pulse-ring {
  0% { transform: scale(0.95); opacity: 0.8; }
  50% { transform: scale(1.2); opacity: 1; box-shadow: 0 0 16px var(--accent-cyan); }
  100% { transform: scale(0.95); opacity: 0.8; }
}

.status-badge {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.2rem 0.65rem;
  background: rgba(56, 189, 248, 0.1);
  border: 1px solid rgba(56, 189, 248, 0.25);
  border-radius: var(--radius-full);
  font-size: 0.72rem;
  font-weight: 600;
  color: var(--accent-cyan);
  letter-spacing: 0.02em;
  text-transform: uppercase;
}

.status-dot {
  width: 6px;
  height: 6px;
  background: var(--accent-cyan);
  border-radius: 50%;
}

.nav-links {
  display: flex;
  align-items: center;
  gap: 1.4rem;
  font-size: 0.9rem;
  font-weight: 500;
  color: var(--text-muted);
}

.nav-links a:hover {
  color: var(--text-main);
}

.github-link, .back-link {
  display: inline-flex;
  align-items: center;
  gap: 0.45rem;
  padding: 0.35rem 0.85rem;
  border-radius: var(--radius-sm);
  background: rgba(255, 255, 255, 0.05);
  border: 1px solid var(--border-subtle);
  transition: all 0.2s ease;
}

.github-link:hover, .back-link:hover {
  background: rgba(255, 255, 255, 0.1);
  border-color: var(--border-card);
  color: var(--text-main);
}

/* Hero Section */
.hero-section {
  position: relative;
  max-width: 1140px;
  margin: 0 auto;
  padding: 4.5rem 1.5rem 3rem;
  text-align: center;
}

.hero-eyebrow {
  display: inline-flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.35rem 0.9rem;
  border-radius: var(--radius-full);
  background: rgba(56, 189, 248, 0.08);
  border: 1px solid rgba(56, 189, 248, 0.2);
  color: var(--accent-cyan);
  font-size: 0.8rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  margin-bottom: 1.5rem;
}

.pulse-indicator {
  width: 7px;
  height: 7px;
  background: var(--accent-cyan);
  border-radius: 50%;
}

.hero-title {
  font-size: clamp(2.2rem, 4.2vw, 3.5rem);
  font-weight: 800;
  line-height: 1.15;
  letter-spacing: -0.035em;
  max-width: 860px;
  margin: 0 auto 1.25rem;
  background: linear-gradient(180deg, #ffffff 0%, #cbd5e1 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
}

.hero-lead {
  font-size: 1.15rem;
  color: var(--text-muted);
  max-width: 700px;
  margin: 0 auto 2.5rem;
  line-height: 1.6;
}

.hero-metrics {
  display: inline-flex;
  align-items: center;
  gap: 1.8rem;
  padding: 0.9rem 1.8rem;
  border-radius: var(--radius-lg);
  background: var(--bg-surface);
  border: 1px solid var(--border-subtle);
}

.metric-item {
  display: flex;
  flex-direction: column;
  align-items: center;
}

.metric-val {
  font-family: var(--font-mono);
  font-size: 1.15rem;
  font-weight: 700;
  color: var(--text-main);
}

.metric-lbl {
  font-size: 0.72rem;
  color: var(--text-dim);
  text-transform: uppercase;
  letter-spacing: 0.03em;
  margin-top: 0.15rem;
}

.metric-divider {
  width: 1px;
  height: 28px;
  background: var(--border-subtle);
}

/* Layout Container */
.site-main {
  flex: 1;
}

.featured-section, .archive-section {
  max-width: 1140px;
  margin: 0 auto 4rem;
  padding: 0 1.5rem;
}

.section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 1.5rem;
  flex-wrap: wrap;
  gap: 1rem;
}

.section-title-wrap {
  display: flex;
  align-items: center;
  gap: 0.8rem;
}

.section-title-wrap h2 {
  font-size: 1.6rem;
  font-weight: 700;
  letter-spacing: -0.02em;
}

/* Badges */
.badge {
  display: inline-flex;
  align-items: center;
  padding: 0.25rem 0.65rem;
  border-radius: var(--radius-full);
  font-size: 0.75rem;
  font-weight: 600;
  background: rgba(255, 255, 255, 0.06);
  border: 1px solid var(--border-subtle);
  color: var(--text-muted);
}

.badge-primary {
  background: rgba(56, 189, 248, 0.12);
  border-color: rgba(56, 189, 248, 0.3);
  color: var(--accent-cyan);
}

.badge-accent {
  background: rgba(129, 140, 248, 0.12);
  border-color: rgba(129, 140, 248, 0.3);
  color: var(--accent-violet);
}

.badge-model {
  font-family: var(--font-mono);
  background: rgba(52, 211, 153, 0.1);
  border-color: rgba(52, 211, 153, 0.25);
  color: var(--accent-emerald);
}

/* Featured Card */
.featured-card {
  background: linear-gradient(180deg, rgba(19, 26, 36, 0.95), rgba(14, 19, 26, 0.95));
  border: 1px solid rgba(56, 189, 248, 0.25);
  border-radius: var(--radius-lg);
  padding: 2.2rem 2.4rem;
  box-shadow: var(--shadow-glow), var(--shadow-card);
  position: relative;
  overflow: hidden;
}

.featured-card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 1.25rem;
  flex-wrap: wrap;
  gap: 0.8rem;
}

.featured-meta {
  display: flex;
  align-items: center;
  gap: 0.9rem;
  font-size: 0.82rem;
  color: var(--text-muted);
}

.featured-headline {
  font-size: clamp(1.6rem, 2.8vw, 2.2rem);
  font-weight: 800;
  line-height: 1.25;
  letter-spacing: -0.03em;
  margin-bottom: 0.85rem;
}

.featured-headline a:hover {
  color: var(--accent-cyan);
}

.featured-subtitle {
  font-size: 1.1rem;
  color: var(--text-muted);
  margin-bottom: 1.2rem;
  font-weight: 500;
}

.featured-summary {
  font-size: 1.02rem;
  color: #cbd5e1;
  line-height: 1.7;
  margin-bottom: 1.6rem;
}

.featured-takeaways {
  background: rgba(255, 255, 255, 0.03);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-md);
  padding: 1.2rem 1.5rem;
  margin-bottom: 1.8rem;
}

.takeaways-title {
  font-size: 0.88rem;
  font-weight: 700;
  color: var(--accent-amber);
  text-transform: uppercase;
  letter-spacing: 0.03em;
  margin-bottom: 0.8rem;
}

.takeaways-list {
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
}

.takeaways-list li {
  display: flex;
  align-items: flex-start;
  gap: 0.65rem;
  font-size: 0.94rem;
  color: #e2e8f0;
  line-height: 1.5;
}

.bullet-dot {
  width: 6px;
  height: 6px;
  background: var(--accent-cyan);
  border-radius: 50%;
  margin-top: 0.5rem;
  flex-shrink: 0;
}

.btn-primary {
  display: inline-flex;
  align-items: center;
  gap: 0.6rem;
  padding: 0.8rem 1.6rem;
  background: var(--accent-cyan);
  color: #04111d;
  font-weight: 700;
  font-size: 0.95rem;
  border-radius: var(--radius-sm);
  transition: all 0.2s ease;
}

.btn-primary:hover {
  background: #7dd3fc;
  box-shadow: 0 0 20px rgba(56, 189, 248, 0.4);
  transform: translateY(-1px);
}

.btn-secondary {
  display: inline-flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.7rem 1.3rem;
  background: var(--bg-card);
  border: 1px solid var(--border-card);
  color: var(--text-main);
  font-weight: 600;
  font-size: 0.9rem;
  border-radius: var(--radius-sm);
  transition: all 0.2s ease;
}

.btn-secondary:hover {
  background: var(--bg-card-hover);
  border-color: var(--accent-cyan);
}

/* Tag Filters & Search */
.tag-filters {
  display: flex;
  gap: 0.5rem;
  flex-wrap: wrap;
  margin-bottom: 1.8rem;
}

.tag-filter-btn {
  background: var(--bg-surface);
  border: 1px solid var(--border-subtle);
  color: var(--text-muted);
  padding: 0.4rem 0.9rem;
  border-radius: var(--radius-full);
  font-size: 0.82rem;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.2s ease;
}

.tag-filter-btn:hover {
  color: var(--text-main);
  border-color: var(--border-card);
}

.tag-filter-btn.active {
  background: rgba(56, 189, 248, 0.12);
  border-color: var(--accent-cyan);
  color: var(--accent-cyan);
}

.search-wrap {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  background: var(--bg-surface);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-full);
  padding: 0.4rem 1rem;
}

.search-wrap input {
  background: transparent;
  border: none;
  outline: none;
  color: var(--text-main);
  font-size: 0.85rem;
  width: 220px;
}

.search-wrap input::placeholder {
  color: var(--text-dim);
}

/* Reports Grid */
.reports-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(330px, 1fr));
  gap: 1.5rem;
}

.report-card {
  background: var(--bg-surface);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-md);
  padding: 1.5rem;
  display: flex;
  flex-direction: column;
  transition: all 0.25s ease;
}

.report-card:hover {
  transform: translateY(-3px);
  border-color: var(--border-card);
  box-shadow: var(--shadow-card);
  background: var(--bg-card);
}

.card-meta {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.78rem;
  color: var(--text-dim);
  margin-bottom: 0.8rem;
}

.card-meta .model-tag {
  font-family: var(--font-mono);
  color: var(--accent-emerald);
}

.card-title {
  font-size: 1.15rem;
  font-weight: 700;
  line-height: 1.35;
  margin-bottom: 0.65rem;
}

.card-title a:hover {
  color: var(--accent-cyan);
}

.card-summary {
  font-size: 0.88rem;
  color: var(--text-muted);
  line-height: 1.6;
  flex: 1;
  margin-bottom: 1.25rem;
}

.card-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-top: 1px solid var(--border-subtle);
  padding-top: 0.9rem;
}

.read-link {
  font-size: 0.82rem;
  font-weight: 700;
  color: var(--accent-cyan);
}

/* Single Article Page */
.reading-progress-bar {
  position: fixed;
  top: 0;
  left: 0;
  height: 3px;
  background: var(--accent-cyan);
  width: 0%;
  z-index: 999;
  transition: width 0.1s ease;
}

.article-main {
  max-width: 820px;
  margin: 0 auto;
  padding: 2.5rem 1.5rem 5rem;
}

.article-header {
  margin-bottom: 2.2rem;
}

.article-meta-ribbon {
  display: flex;
  align-items: center;
  gap: 0.65rem;
  font-size: 0.85rem;
  color: var(--text-muted);
  margin-bottom: 1rem;
}

.article-title {
  font-size: clamp(2rem, 3.8vw, 2.9rem);
  font-weight: 800;
  line-height: 1.2;
  letter-spacing: -0.03em;
  margin-bottom: 0.85rem;
}

.article-subtitle {
  font-size: 1.2rem;
  color: var(--text-muted);
  line-height: 1.5;
  margin-bottom: 1.2rem;
}

.article-tags-row {
  display: flex;
  gap: 0.5rem;
  flex-wrap: wrap;
}

.article-lead-box {
  background: linear-gradient(180deg, rgba(56, 189, 248, 0.08), rgba(56, 189, 248, 0.02));
  border-left: 4px solid var(--accent-cyan);
  border-radius: 0 var(--radius-md) var(--radius-md) 0;
  padding: 1.4rem 1.6rem;
  margin-bottom: 2.2rem;
}

.lead-label {
  display: block;
  font-size: 0.72rem;
  font-weight: 800;
  letter-spacing: 0.08em;
  color: var(--accent-cyan);
  margin-bottom: 0.5rem;
}

.lead-text {
  font-size: 1.12rem;
  line-height: 1.65;
  color: #f8fafc;
}

.post-takeaways-box {
  background: var(--bg-surface);
  border: 1px solid var(--border-card);
  border-radius: var(--radius-md);
  padding: 1.4rem 1.6rem;
  margin-bottom: 2.5rem;
}

.takeaways-header {
  font-size: 0.95rem;
  font-weight: 700;
  color: var(--accent-amber);
  margin-bottom: 0.9rem;
}

.article-body {
  font-size: 1.08rem;
  line-height: 1.8;
  color: #cbd5e1;
}

.article-body p {
  margin-bottom: 1.5rem;
}

.article-body h2, .article-body h3, .article-body h4 {
  color: var(--text-main);
  font-weight: 700;
  letter-spacing: -0.02em;
  margin: 2.2rem 0 1rem;
}

.article-body h2 { font-size: 1.5rem; }
.article-body h3 { font-size: 1.25rem; }

.article-body ul {
  margin-bottom: 1.5rem;
  padding-left: 1.4rem;
}

.article-body li {
  margin-bottom: 0.5rem;
}

.post-callout-box {
  background: rgba(244, 63, 94, 0.06);
  border: 1px solid rgba(244, 63, 94, 0.25);
  border-radius: var(--radius-md);
  padding: 1.5rem;
  margin: 2.5rem 0;
}

.callout-header {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  margin-bottom: 0.9rem;
}

.callout-header h3 {
  font-size: 1.05rem;
  font-weight: 700;
  color: #fda4af;
}

.callout-content p {
  font-size: 0.95rem;
  line-height: 1.65;
  color: #f1f5f9;
  margin-bottom: 0.75rem;
}

.article-sources-section {
  margin-top: 3.5rem;
  border-top: 1px solid var(--border-subtle);
  padding-top: 2rem;
}

.sources-header {
  font-size: 1.25rem;
  font-weight: 700;
  margin-bottom: 0.35rem;
}

.sources-sub {
  font-size: 0.88rem;
  color: var(--text-dim);
  margin-bottom: 1.4rem;
}

.sources-grid {
  display: grid;
  gap: 0.8rem;
}

.source-card {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: var(--bg-surface);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  padding: 0.85rem 1.1rem;
  gap: 1rem;
  transition: all 0.2s ease;
}

.source-card:hover {
  background: var(--bg-card);
  border-color: var(--accent-cyan);
}

.source-tag {
  font-family: var(--font-mono);
  font-size: 0.72rem;
  font-weight: 700;
  padding: 0.2rem 0.5rem;
  border-radius: 4px;
}

.source-arxiv { background: rgba(56, 189, 248, 0.15); color: var(--accent-cyan); }
.source-github { background: rgba(129, 140, 248, 0.15); color: var(--accent-violet); }
.source-reddit { background: rgba(251, 191, 36, 0.15); color: var(--accent-amber); }

.source-title {
  font-size: 0.9rem;
  font-weight: 600;
  flex: 1;
  color: var(--text-main);
}

.source-arrow {
  color: var(--text-dim);
  font-weight: 700;
}

.article-footer-nav {
  margin-top: 3rem;
  display: flex;
  justify-content: flex-start;
}

/* Footer */
.site-footer {
  border-top: 1px solid var(--border-subtle);
  background: var(--bg-surface);
  padding: 2.5rem 1.5rem;
  margin-top: auto;
}

.footer-inner {
  max-width: 1140px;
  margin: 0 auto;
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 1.5rem;
}

.footer-brand {
  font-weight: 700;
  font-size: 1rem;
  color: var(--text-main);
}

.footer-sub, .footer-copy, .footer-note {
  font-size: 0.82rem;
  color: var(--text-dim);
}

@media (max-width: 768px) {
  .hero-metrics { flex-direction: column; gap: 0.8rem; }
  .metric-divider { display: none; }
  .featured-card { padding: 1.5rem; }
  .header-inner { flex-direction: column; gap: 0.8rem; }
}
"""
    (DOCS_ASSETS / "site.css").write_text(css, encoding="utf-8")

    # Interactive client-side JS (tag filtering, live search, reading progress)
    js = """// LLM Evaluation Radar Client Scripts
document.addEventListener('DOMContentLoaded', () => {
  // Reading Progress Bar
  const progressBar = document.getElementById('progress-bar');
  if (progressBar) {
    window.addEventListener('scroll', () => {
      const total = document.documentElement.scrollHeight - window.innerHeight;
      const progress = (window.scrollY / total) * 100;
      progressBar.style.width = Math.min(100, Math.max(0, progress)) + '%';
    });
  }

  // Tag Filtering on Index
  const tagButtons = document.querySelectorAll('.tag-filter-btn');
  const reportCards = document.querySelectorAll('.report-card');
  const searchInput = document.getElementById('report-search');

  function applyFilters() {
    const activeBtn = document.querySelector('.tag-filter-btn.active');
    const selectedTag = activeBtn ? activeBtn.getAttribute('data-tag').toLowerCase() : 'all';
    const query = searchInput ? searchInput.value.toLowerCase().trim() : '';

    reportCards.forEach(card => {
      const cardTags = (card.getAttribute('data-tags') || '').toLowerCase();
      const cardText = card.innerText.toLowerCase();

      const matchesTag = (selectedTag === 'all') || cardTags.includes(selectedTag);
      const matchesQuery = !query || cardText.includes(query);

      if (matchesTag && matchesQuery) {
        card.style.display = 'flex';
      } else {
        card.style.display = 'none';
      }
    });
  }

  tagButtons.forEach(btn => {
    btn.addEventListener('click', () => {
      tagButtons.forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      applyFilters();
    });
  });

  if (searchInput) {
    searchInput.addEventListener('input', applyFilters);
  }
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


def _paragraphize(text: str) -> str:
    if not text:
        return ""
    paras = text.split("\n\n")
    out = []
    for p in paras:
        p_clean = p.strip()
        if not p_clean:
            continue
        lines = [line.strip() for line in p_clean.split("\n") if line.strip()]
        if all(line.startswith("- ") or line.startswith("• ") for line in lines):
            bullets = "".join(f"<li>{html.escape(re.sub(r'^[-•]\s*', '', line))}</li>" for line in lines)
            out.append(f"<ul>{bullets}</ul>")
        elif p_clean.startswith("### ") or p_clean.startswith("## "):
            header_text = re.sub(r"^#{2,3}\s*", "", p_clean)
            out.append(f"<h3>{html.escape(header_text)}</h3>")
        else:
            safe = html.escape(p_clean).replace("\n", "<br>")
            out.append(f"<p>{safe}</p>")
    return "\n".join(out)
