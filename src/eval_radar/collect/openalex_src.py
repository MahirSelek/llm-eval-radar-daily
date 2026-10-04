from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import httpx

from eval_radar.models import Item, keyword_score

log = logging.getLogger("eval-radar-openalex")

API = "https://api.openalex.org/works"
MAILTO = "eval-radar@local"


def fetch_openalex(
    queries: list[str],
    keywords: list[str],
    *,
    max_items: int = 16,
    max_age_hours: float = 36.0,
    timeout: float = 30.0,
) -> list[Item]:
    """
    Pull fresh papers from OpenAlex.
    We bias for recent works and eval-related signals.
    """
    if not queries:
        return []

    since = (datetime.now(timezone.utc) - timedelta(hours=max_age_hours)).strftime("%Y-%m-%d")
    items: list[Item] = []
    seen: set[str] = set()

    with httpx.Client(timeout=timeout, follow_redirects=True, trust_env=False) as client:
        rate_limited = False
        for q in queries:
            if rate_limited:
                break
            if len(items) >= max_items:
                break
            rows, limited = _query_works(client, q, since=since)
            if limited:
                rate_limited = True
            for row in rows:
                if len(items) >= max_items:
                    break
                title = (row.get("title") or "").strip()
                if not title:
                    continue
                abstract = _abstract_text(row)[:1200]
                doi = row.get("doi") or ""
                link = doi if doi.startswith("http") else ""
                if not link:
                    primary = row.get("primary_location") or {}
                    src = primary.get("source") or {}
                    lineage = src.get("host_organization_lineage_names") or []
                    if lineage:
                        link = str(lineage[0] or "")
                openalex_id = row.get("id") or ""
                if not link:
                    link = openalex_id
                key = (link or title).strip().lower()
                if not key or key in seen:
                    continue
                seen.add(key)

                blob = f"{title} {abstract}"
                score = keyword_score(blob, keywords)
                if score < 1:
                    continue

                venue = ((row.get("primary_location") or {}).get("source") or {}).get("display_name")
                authors = [
                    (a.get("author") or {}).get("display_name")
                    for a in (row.get("authorships") or [])[:8]
                    if (a.get("author") or {}).get("display_name")
                ]
                pub = (
                    row.get("publication_date")
                    or row.get("from_publication_date")
                    or row.get("created_date")
                )
                items.append(
                    Item(
                        source="openalex",
                        title=title,
                        url=link,
                        summary=abstract[:350] or "OpenAlex work",
                        score=float(score) + 0.8,
                        meta={
                            "published_at": pub,
                            "venue": venue,
                            "authors": authors,
                            "openalex_id": openalex_id,
                            "cited_by_count": row.get("cited_by_count"),
                        },
                    )
                )

    items.sort(key=lambda x: x.score, reverse=True)
    return items[:max_items]


def _query_works(client: httpx.Client, query: str, *, since: str) -> tuple[list[dict], bool]:
    params = {
        "search": query,
        "filter": f"from_publication_date:{since},has_abstract:true",
        "sort": "publication_date:desc",
        "per-page": 15,
        "mailto": MAILTO,
    }
    try:
        r = client.get(API, params=params)
        if r.status_code == 429:
            log.warning("openalex rate-limited; skipping remaining openalex queries")
            return [], True
        r.raise_for_status()
        data = r.json()
        return list(data.get("results") or []), False
    except Exception as e:
        log.warning("openalex query failed (%s): %s", query, e)
        return [], False


def _abstract_text(row: dict) -> str:
    inv = row.get("abstract_inverted_index") or {}
    if not inv:
        return ""
    toks: list[tuple[int, str]] = []
    for word, pos_list in inv.items():
        for p in pos_list or []:
            try:
                toks.append((int(p), word))
            except Exception:
                continue
    toks.sort(key=lambda x: x[0])
    return " ".join(w for _, w in toks)
