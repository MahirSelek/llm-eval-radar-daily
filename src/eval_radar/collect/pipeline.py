from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from eval_radar.collect.arxiv_src import fetch_arxiv
from eval_radar.collect.forums_src import fetch_discourse_forums
from eval_radar.collect.freshness import apply_freshness, local_day
from eval_radar.collect.github_src import fetch_github_repos
from eval_radar.collect.hn_src import fetch_hackernews
from eval_radar.collect.reddit_src import fetch_reddit
from eval_radar.collect.rss_feeds import fetch_rss_feeds
from eval_radar.config import get_settings, load_seeds
from eval_radar.models import Item

log = logging.getLogger("eval-radar-collect")


def collect_all() -> dict:
    settings = get_settings()
    seeds = load_seeds()
    keywords = list(seeds.get("keywords") or [])
    max_age = float(settings.max_age_hours)

    errors: dict[str, str] = {}
    buckets: dict[str, list[Item]] = {}

    buckets["arxiv"] = _collect_source(
        "arxiv",
        errors,
        fetch_arxiv,
        list(seeds.get("arxiv_queries") or []),
        keywords,
        max_items=settings.max_arxiv,
    )
    buckets["github"] = _collect_source(
        "github",
        errors,
        fetch_github_repos,
        list(seeds.get("github_repos") or []),
        keywords,
        token=settings.github_token,
        max_items=settings.max_github,
    )
    buckets["reddit"] = _collect_source(
        "reddit",
        errors,
        fetch_reddit,
        list(seeds.get("reddit_subs") or []),
        keywords,
        max_items=settings.max_reddit,
    )
    buckets["rss"] = _collect_source(
        "rss",
        errors,
        fetch_rss_feeds,
        list(seeds.get("rss_feeds") or []),
        keywords,
        max_items=settings.max_rss,
    )
    buckets["forums"] = _collect_source(
        "forums",
        errors,
        fetch_discourse_forums,
        list(seeds.get("discourse_forums") or []),
        keywords,
        max_items=settings.max_forums,
    )
    buckets["hackernews"] = _collect_source(
        "hackernews",
        errors,
        fetch_hackernews,
        list(seeds.get("hn_queries") or []),
        keywords,
        max_items=settings.max_hn,
        max_age_hours=max_age,
    )

    # Optional open RSS mirrors for X/Twitter accounts (no password / official API).
    # Put RSSHub / Nitter / similar public feeds under seeds.x_rss_feeds.
    buckets["x"] = _collect_source(
        "x",
        errors,
        fetch_rss_feeds,
        list(seeds.get("x_rss_feeds") or []),
        keywords,
        max_items=settings.max_x,
    )

    raw_all = [it for group in buckets.values() for it in group]
    fresh = apply_freshness(
        raw_all,
        max_age_hours=max_age,
        soft_fallback_hours=max(max_age * 2, 72.0),
        min_keep=max(6, settings.max_total_items // 2),
    )
    merged = _merge_and_cap(fresh, settings.max_total_items)

    day = local_day(settings.report_tz)
    payload = {
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "report_day": day,
        "freshness": {
            "max_age_hours": max_age,
            "timezone": settings.report_tz,
            "policy": "prefer_today_then_last_36h_then_soft_72h",
        },
        "counts": {
            "arxiv": len(buckets["arxiv"]),
            "github": len(buckets["github"]),
            "reddit": len(buckets["reddit"]),
            "rss": len(buckets["rss"]),
            "forums": len(buckets["forums"]),
            "hackernews": len(buckets["hackernews"]),
            "x": len(buckets["x"]),
            "raw_total": len(raw_all),
            "fresh_total": len(fresh),
            "total": len(merged),
        },
        "items": [i.to_dict() for i in merged],
        "seeds_people": [p.get("name") for p in (seeds.get("people") or [])],
        "errors": errors,
    }
    return payload


def save_digest(payload: dict, digests_dir: Path | None = None) -> Path:
    settings = get_settings()
    out_dir = digests_dir or settings.digests_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    day = payload.get("report_day") or local_day(settings.report_tz)
    path = out_dir / f"{day}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    latest = out_dir / "latest.json"
    latest.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _merge_and_cap(items: list[Item], cap: int) -> list[Item]:
    items = sorted(items, key=lambda x: x.score, reverse=True)
    seen: set[str] = set()
    out: list[Item] = []
    for it in items:
        key = (it.url or it.title).lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
        if len(out) >= cap:
            break
    return out


def _collect_source(name: str, errors: dict[str, str], fn, *args, **kwargs) -> list[Item]:
    try:
        return fn(*args, **kwargs)
    except Exception as exc:
        errors[name] = str(exc)
        log.exception("collect source failed: %s", name)
        return []
