from __future__ import annotations

import logging

import feedparser
import httpx

from eval_radar.models import Item, keyword_score

UA = "eval-radar/0.1 (LLM evaluation research bot)"
log = logging.getLogger("eval-radar-reddit")


def fetch_reddit(
    subs: list[str],
    keywords: list[str],
    *,
    max_items: int = 8,
    timeout: float = 30.0,
) -> list[Item]:
    """Reddit public JSON is often blocked; Atom RSS still works without API keys."""
    items: list[Item] = []
    headers = {"User-Agent": UA}

    success_feeds = 0
    last_error = ""
    with httpx.Client(
        timeout=timeout, headers=headers, follow_redirects=True, trust_env=False
    ) as client:
        for sub in subs:
            url = f"https://www.reddit.com/r/{sub}/.rss"
            try:
                r = client.get(url)
                r.raise_for_status()
                feed = feedparser.parse(r.text)
                success_feeds += 1
            except Exception:
                last_error = f"{sub} rss fetch failed"
                log.warning("reddit rss fetch failed for r/%s", sub)
                continue

            for entry in feed.entries[:40]:
                title = getattr(entry, "title", "") or ""
                summary = getattr(entry, "summary", "") or getattr(entry, "description", "") or ""
                # strip crude HTML
                summary = _strip_tags(summary)[:300]
                link = getattr(entry, "link", "") or ""
                blob = f"{title} {summary}"
                score = keyword_score(blob, keywords)
                if score < 1:
                    continue
                items.append(
                    Item(
                        source=f"reddit/r/{sub}",
                        title=title,
                        url=link,
                        summary=summary,
                        score=float(score),
                        meta={"sub": sub},
                    )
                )

    items.sort(key=lambda x: x.score, reverse=True)
    if subs and success_feeds == 0:
        raise RuntimeError(
            f"Reddit RSS fetch failed for all subs. Last error: {last_error or 'unknown'}"
        )
    return items[:max_items]


def _strip_tags(html: str) -> str:
    out: list[str] = []
    in_tag = False
    for ch in html:
        if ch == "<":
            in_tag = True
            continue
        if ch == ">":
            in_tag = False
            continue
        if not in_tag:
            out.append(ch)
    return " ".join("".join(out).split())
