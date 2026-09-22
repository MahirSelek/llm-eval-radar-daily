from __future__ import annotations

import logging
from datetime import datetime, timezone
from urllib.parse import quote_plus

import httpx

from eval_radar.models import Item, keyword_score

log = logging.getLogger("eval-radar-hn")
UA = "eval-radar/0.1 (LLM evaluation research bot)"
API = "https://hn.algolia.com/api/v1/search_by_date"


def fetch_hackernews(
    queries: list[str],
    keywords: list[str],
    *,
    max_items: int = 10,
    max_age_hours: float = 36.0,
    timeout: float = 20.0,
) -> list[Item]:
    """Hacker News — excellent 'what dropped today' open source signal."""
    items: list[Item] = []
    now = datetime.now(timezone.utc).timestamp()
    min_ts = now - (max_age_hours * 3600)

    with httpx.Client(
        timeout=timeout, headers={"User-Agent": UA}, trust_env=False
    ) as client:
        for q in queries:
            url = (
                f"{API}?query={quote_plus(q)}"
                f"&tags=story&hitsPerPage=20&numericFilters=created_at_i>{int(min_ts)}"
            )
            try:
                r = client.get(url)
                r.raise_for_status()
                data = r.json()
            except Exception as e:
                log.warning("hn fetch failed for %s: %s", q, e)
                continue

            for hit in data.get("hits") or []:
                title = (hit.get("title") or "").strip()
                if not title:
                    continue
                link = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
                created = hit.get("created_at") or ""
                points = int(hit.get("points") or 0)
                comments = int(hit.get("num_comments") or 0)
                blob = f"{title} {hit.get('story_text') or ''}"
                score = keyword_score(blob, keywords) + min(points / 40.0, 3.0) + min(comments / 30.0, 1.5)
                if score < 1 and not any(
                    k in title.lower()
                    for k in ("llm", "benchmark", "eval", "gpt", "claude", "gemini", "arena", "mmlu")
                ):
                    continue
                items.append(
                    Item(
                        source="hackernews",
                        title=title,
                        url=link,
                        summary=f"HN points={points} comments={comments}",
                        score=float(score) + 1.5,
                        meta={
                            "published_at": created,
                            "points": points,
                            "comments": comments,
                            "hn_id": hit.get("objectID"),
                            "kind": "hn",
                        },
                    )
                )

    items.sort(key=lambda x: x.score, reverse=True)
    return _dedupe(items)[:max_items]


def _dedupe(items: list[Item]) -> list[Item]:
    seen: set[str] = set()
    out: list[Item] = []
    for it in items:
        key = (it.url or it.title).lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out
