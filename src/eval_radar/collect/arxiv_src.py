from __future__ import annotations

import xml.etree.ElementTree as ET
from urllib.parse import quote_plus

import httpx

from eval_radar.models import Item, keyword_score

ARXIV_API = "https://export.arxiv.org/api/query"


def fetch_arxiv(
    queries: list[str],
    keywords: list[str],
    *,
    max_items: int = 8,
    timeout: float = 30.0,
) -> list[Item]:
    items: list[Item] = []
    per_query = max(3, max_items // max(len(queries), 1))

    with httpx.Client(
        timeout=timeout, headers={"User-Agent": "eval-radar/0.1"}, trust_env=False
    ) as client:
        for q in queries:
            url = (
                f"{ARXIV_API}?search_query={quote_plus(q)}"
                f"&start=0&max_results={per_query}&sortBy=submittedDate&sortOrder=descending"
            )
            resp = client.get(url)
            resp.raise_for_status()
            items.extend(_parse_atom(resp.text, keywords))

    items.sort(key=lambda x: x.score, reverse=True)
    return _dedupe(items)[:max_items]


def _parse_atom(xml_text: str, keywords: list[str]) -> list[Item]:
    root = ET.fromstring(xml_text)
    ns = {"a": "http://www.w3.org/2005/Atom"}
    out: list[Item] = []
    for entry in root.findall("a:entry", ns):
        title = (entry.findtext("a:title", default="", namespaces=ns) or "").strip()
        title = " ".join(title.split())
        summary = (entry.findtext("a:summary", default="", namespaces=ns) or "").strip()
        summary = " ".join(summary.split())[:1200]
        link = ""
        for ln in entry.findall("a:link", ns):
            if ln.attrib.get("type") == "text/html" or ln.attrib.get("rel") == "alternate":
                link = ln.attrib.get("href", "")
                break
        if not link:
            id_text = entry.findtext("a:id", default="", namespaces=ns) or ""
            link = id_text
        published = entry.findtext("a:published", default="", namespaces=ns) or ""
        authors = []
        for a in entry.findall("a:author", ns):
            name = a.findtext("a:name", default="", namespaces=ns) or ""
            if name.strip():
                authors.append(name.strip())
        blob = f"{title} {summary}"
        score = keyword_score(blob, keywords) + 1.0
        out.append(
            Item(
                source="arxiv",
                title=title,
                url=link,
                summary=summary,
                score=score,
                meta={"published": published, "authors": authors},
            )
        )
    return out


def _dedupe(items: list[Item]) -> list[Item]:
    seen: set[str] = set()
    out: list[Item] = []
    for it in items:
        key = it.url or it.title
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out
