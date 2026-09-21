from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from eval_radar.collect.arxiv_src import fetch_arxiv
from eval_radar.collect.github_src import fetch_github_repos
from eval_radar.collect.reddit_src import fetch_reddit
from eval_radar.config import get_settings, load_seeds
from eval_radar.models import Item

log = logging.getLogger("eval-radar-collect")


def collect_all() -> dict:
    settings = get_settings()
    seeds = load_seeds()
    keywords = list(seeds.get("keywords") or [])

    errors: dict[str, str] = {}
    arxiv_items = _collect_source(
        "arxiv",
        errors,
        fetch_arxiv,
        list(seeds.get("arxiv_queries") or []),
        keywords,
        max_items=settings.max_arxiv,
    )
    github_items = _collect_source(
        "github",
        errors,
        fetch_github_repos,
        list(seeds.get("github_repos") or []),
        keywords,
        token=settings.github_token,
        max_items=settings.max_github,
    )
    reddit_items = _collect_source(
        "reddit",
        errors,
        fetch_reddit,
        list(seeds.get("reddit_subs") or []),
        keywords,
        max_items=settings.max_reddit,
    )

    merged = _merge_and_cap(arxiv_items + github_items + reddit_items, settings.max_total_items)
    payload = {
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "counts": {
            "arxiv": len(arxiv_items),
            "github": len(github_items),
            "reddit": len(reddit_items),
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
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
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
