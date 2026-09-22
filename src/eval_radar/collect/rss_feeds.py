from __future__ import annotations

import logging
from datetime import datetime, timezone

import feedparser
import httpx

from eval_radar.collect.freshness import parse_datetime
from eval_radar.models import Item, keyword_score

UA = "eval-radar/0.1 (LLM evaluation research bot)"
log = logging.getLogger("eval-radar-rss")


def fetch_rss_feeds(
    feeds: list[dict | str],
    keywords: list[str],
    *,
    max_items: int = 12,
    timeout: float = 25.0,
) -> list[Item]:
    """Fetch Medium / Substack / generic RSS/Atom feeds listed in seeds."""
    items: list[Item] = []
    headers = {"User-Agent": UA}
    with httpx.Client(
        timeout=timeout, headers=headers, follow_redirects=True, trust_env=False
    ) as client:
        for raw in feeds:
            name, url = _normalize_feed(raw)
            if not url:
                continue
            try:
                r = client.get(url)
                r.raise_for_status()
                feed = feedparser.parse(r.text)
            except Exception as e:
                log.warning("rss fetch failed for %s (%s): %s", name, url, e)
                continue

            for entry in feed.entries[:25]:
                title = getattr(entry, "title", "") or ""
                summary = getattr(entry, "summary", "") or getattr(entry, "description", "") or ""
                summary = _strip_tags(summary)[:500]
                link = getattr(entry, "link", "") or ""
                published = _entry_published(entry)
                blob = f"{title} {summary}"
                score = keyword_score(blob, keywords)
                # Editorial feeds: allow weaker keyword hit but still require some relevance
                if score < 1 and not _soft_eval_match(blob):
                    continue
                items.append(
                    Item(
                        source=f"rss/{name}",
                        title=title.strip(),
                        url=link,
                        summary=summary,
                        score=float(score) + 1.0,
                        meta={
                            "feed": name,
                            "published_at": published.isoformat() if published else None,
                            "kind": "rss",
                        },
                    )
                )

    items.sort(key=lambda x: x.score, reverse=True)
    return _dedupe(items)[:max_items]


def _normalize_feed(raw: dict | str) -> tuple[str, str]:
    if isinstance(raw, str):
        return (raw.split("/")[-1] or "feed", raw.strip())
    name = str(raw.get("name") or raw.get("label") or "feed").strip()
    url = str(raw.get("url") or "").strip()
    return name, url


def _entry_published(entry) -> datetime | None:
    for key in ("published", "updated", "created"):
        val = getattr(entry, key, None)
        dt = parse_datetime(val)
        if dt:
            return dt
    for key in ("published_parsed", "updated_parsed"):
        val = getattr(entry, key, None)
        dt = parse_datetime(val)
        if dt:
            return dt
    return None


def _soft_eval_match(text: str) -> bool:
    t = text.lower()
    needles = (
        "llm",
        "benchmark",
        "evaluation",
        "leaderboard",
        "arena",
        "mmlu",
        "judge",
        "gpt",
        "claude",
        "gemini",
        "openai",
        "anthropic",
        "harness",
        "contamination",
    )
    return any(n in t for n in needles)


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
