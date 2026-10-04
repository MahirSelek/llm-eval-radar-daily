from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import feedparser
import httpx

from eval_radar.config import get_settings
from eval_radar.models import Item, keyword_score

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": UA,
    "Accept": "application/atom+xml, application/rss+xml, application/xml, text/xml;q=0.9, */*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
log = logging.getLogger("eval-radar-reddit")
CACHE_TTL_SEC = 45 * 60


def fetch_reddit(
    subs: list[str],
    keywords: list[str],
    *,
    max_items: int = 8,
    timeout: float = 20.0,
) -> list[Item]:
    """
    One multi-sub RSS request (/r/A+B+C/.rss) instead of N calls (avoids 429).
    Never raises — soft-fails to cache or [] so arXiv/GitHub collect still runs.
    """
    clean = [s.strip() for s in subs if s and str(s).strip()]
    if not clean:
        return []

    cached = _load_cache()
    items: list[Item] = []

    with httpx.Client(
        timeout=timeout, headers=HEADERS, follow_redirects=True, trust_env=False
    ) as client:
        for chunk in _combo_chunks(clean, max_per=6):
            if len(items) >= max_items:
                break
            multi = "+".join(chunk)
            text, err = _fetch_rss_once(client, multi)
            if not text:
                # Secondary chunks can rate-limit; continue with available chunks/cache.
                if "429" in (err or ""):
                    log.info("reddit rss rate-limited (%s): %s", multi, err)
                else:
                    log.warning("reddit rss skipped (%s): %s", multi, err)
                continue
            items.extend(_parse_rss(text, keywords))

    if items:
        _save_cache(items)
    elif cached:
        log.info("reddit live fetch empty/rate-limited — using cache (%d items)", len(cached))
        items = cached
    else:
        log.error("reddit unavailable (rate-limit) and no cache — continuing without it")
        return []

    seen: set[str] = set()
    uniq: list[Item] = []
    for it in sorted(items, key=lambda x: x.score, reverse=True):
        key = (it.url or it.title).lower()
        if not key or key in seen:
            continue
        seen.add(key)
        uniq.append(it)
    return uniq[:max_items]


def _combo_chunks(subs: list[str], *, max_per: int) -> list[list[str]]:
    return [subs[i : i + max_per] for i in range(0, len(subs), max_per)]


def _fetch_rss_once(client: httpx.Client, multi: str) -> tuple[str | None, str]:
    url = f"https://www.reddit.com/r/{multi}/.rss"
    try:
        r = client.get(url)
        if r.status_code == 429:
            # brief single retry only — don't hang the dashboard
            time.sleep(3.0)
            r = client.get(url)
        if r.status_code == 429:
            return None, "HTTP 429 rate-limited"
        if r.status_code >= 400:
            return None, f"HTTP {r.status_code}"
        body = r.text or ""
        if not _looks_like_feed(body):
            return None, "non-feed response"
        return body, ""
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def _parse_rss(xml: str, keywords: list[str]) -> list[Item]:
    feed = feedparser.parse(xml)
    out: list[Item] = []
    for entry in feed.entries[:80]:
        title = getattr(entry, "title", "") or ""
        summary = getattr(entry, "summary", "") or getattr(entry, "description", "") or ""
        summary = _strip_tags(summary)[:300]
        link = getattr(entry, "link", "") or ""
        published = getattr(entry, "published", None) or getattr(entry, "updated", None)
        sub = "reddit"
        if "/r/" in link:
            try:
                sub = link.split("/r/")[1].split("/")[0]
            except Exception:
                pass
        score = keyword_score(f"{title} {summary}", keywords)
        if score < 1:
            continue
        out.append(
            Item(
                source=f"reddit/r/{sub}",
                title=title,
                url=link,
                summary=summary,
                score=float(score),
                meta={"sub": sub, "published_at": published},
            )
        )
    return out


def _cache_path() -> Path:
    return get_settings().memory_dir / "reddit_cache.json"


def _load_cache() -> list[Item]:
    path = _cache_path()
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if time.time() - float(raw.get("saved_at") or 0) > CACHE_TTL_SEC:
            return []
        out: list[Item] = []
        for row in raw.get("items") or []:
            out.append(
                Item(
                    source=row.get("source") or "reddit",
                    title=row.get("title") or "",
                    url=row.get("url") or "",
                    summary=row.get("summary") or "",
                    score=float(row.get("score") or 0),
                    meta=row.get("meta") or {},
                )
            )
        return out
    except Exception:
        return []


def _save_cache(items: list[Item]) -> None:
    path = _cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "saved_at": time.time(),
        "items": [
            {
                "source": it.source,
                "title": it.title,
                "url": it.url,
                "summary": it.summary,
                "score": it.score,
                "meta": it.meta,
            }
            for it in items[:40]
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _looks_like_feed(body: str) -> bool:
    head = body.lstrip()[:220].lower()
    return (
        head.startswith("<?xml")
        or "<feed" in head
        or "<rss" in head
        or 'xmlns="http://www.w3.org/2005/atom"' in head
    )


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
