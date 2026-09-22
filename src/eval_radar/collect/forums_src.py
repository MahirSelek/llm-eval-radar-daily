from __future__ import annotations

import logging
from urllib.parse import urljoin

import httpx

from eval_radar.models import Item, keyword_score

UA = "eval-radar/0.1 (LLM evaluation research bot)"
log = logging.getLogger("eval-radar-forums")


def fetch_discourse_forums(
    forums: list[dict | str],
    keywords: list[str],
    *,
    max_items: int = 12,
    timeout: float = 25.0,
) -> list[Item]:
    """OpenAI / HF / Google AI / Anthropic-adjacent Discourse boards via /latest.json."""
    items: list[Item] = []
    headers = {"User-Agent": UA, "Accept": "application/json"}

    with httpx.Client(
        timeout=timeout, headers=headers, follow_redirects=True, trust_env=False
    ) as client:
        for raw in forums:
            name, base = _normalize(raw)
            if not base:
                continue
            url = urljoin(base.rstrip("/") + "/", "latest.json")
            try:
                r = client.get(url)
                r.raise_for_status()
                data = r.json()
            except Exception as e:
                log.warning("forum fetch failed for %s: %s", name, e)
                continue

            topic_list = (data.get("topic_list") or {}).get("topics") or []
            users = {
                u.get("id"): u.get("username")
                for u in (data.get("users") or [])
                if isinstance(u, dict)
            }
            for topic in topic_list[:40]:
                title = (topic.get("title") or "").strip()
                if not title:
                    continue
                slug = topic.get("slug") or ""
                tid = topic.get("id")
                link = urljoin(base.rstrip("/") + "/", f"t/{slug}/{tid}")
                created = topic.get("created_at") or topic.get("bumped_at") or ""
                excerpt = (topic.get("excerpt") or "").replace("\n", " ")[:400]
                blob = f"{title} {excerpt}"
                score = keyword_score(blob, keywords)
                if score < 1 and not _soft_match(blob):
                    continue
                poster = ""
                posters = topic.get("posters") or []
                if posters and isinstance(posters[0], dict):
                    poster = users.get(posters[0].get("user_id"), "") or ""
                items.append(
                    Item(
                        source=f"forum/{name}",
                        title=title,
                        url=link,
                        summary=excerpt,
                        score=float(score) + 1.2,
                        meta={
                            "published_at": created,
                            "forum": name,
                            "author": poster,
                            "kind": "discourse",
                            "posts_count": topic.get("posts_count"),
                        },
                    )
                )

    items.sort(key=lambda x: x.score, reverse=True)
    return _dedupe(items)[:max_items]


def _normalize(raw: dict | str) -> tuple[str, str]:
    if isinstance(raw, str):
        base = raw.strip()
        name = base.replace("https://", "").replace("http://", "").split("/")[0]
        return name, base
    name = str(raw.get("name") or "forum").strip()
    base = str(raw.get("url") or raw.get("base") or "").strip()
    return name, base


def _soft_match(text: str) -> bool:
    t = text.lower()
    must = (
        "eval",
        "benchmark",
        "leaderboard",
        "arena",
        "mmlu",
        "judge",
        "harness",
        "contamination",
        "reward model",
        "preference",
        "simple-evals",
        "lighteval",
        "livebench",
        "swe-bench",
    )
    return any(k in t for k in must)


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
