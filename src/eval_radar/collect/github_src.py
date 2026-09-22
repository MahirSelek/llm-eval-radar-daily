from __future__ import annotations

import logging

import httpx

from eval_radar.models import Item, keyword_score

API = "https://api.github.com"
log = logging.getLogger("eval-radar-github")


def fetch_github_repos(
    repos: list[str],
    keywords: list[str],
    *,
    token: str = "",
    max_items: int = 8,
    timeout: float = 30.0,
) -> list[Item]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "eval-radar/0.1",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    items: list[Item] = []
    failed = 0
    last_error = ""
    with httpx.Client(
        timeout=timeout,
        headers=headers,
        trust_env=False,
        follow_redirects=True,
    ) as client:
        for repo in repos:
            releases, err1 = _safe_get(client, f"{API}/repos/{repo}/releases?per_page=3")
            if err1:
                failed += 1
                last_error = err1
                log.warning("github releases fetch failed for %s: %s", repo, err1)
            for rel in releases[:2]:
                title = f"[release] {repo}: {rel.get('name') or rel.get('tag_name')}"
                body = (rel.get("body") or "")[:1200]
                url = rel.get("html_url") or f"https://github.com/{repo}/releases"
                published = rel.get("published_at") or rel.get("created_at") or ""
                score = keyword_score(f"{title} {body}", keywords) + 2.0
                items.append(
                    Item(
                        source="github",
                        title=title,
                        url=url,
                        summary=body.replace("\r", " ").strip(),
                        score=score,
                        meta={
                            "repo": repo,
                            "kind": "release",
                            "published_at": published,
                        },
                    )
                )

            commits, err2 = _safe_get(client, f"{API}/repos/{repo}/commits?per_page=5")
            if err2:
                failed += 1
                last_error = err2
                log.warning("github commits fetch failed for %s: %s", repo, err2)
            if commits:
                c0 = commits[0]
                msg = (c0.get("commit", {}).get("message") or "").split("\n")[0][:160]
                url = c0.get("html_url") or f"https://github.com/{repo}"
                published = (
                    (c0.get("commit") or {}).get("author", {}).get("date")
                    or (c0.get("commit") or {}).get("committer", {}).get("date")
                    or ""
                )
                score = keyword_score(msg, keywords) + 0.5
                items.append(
                    Item(
                        source="github",
                        title=f"[commit] {repo}: {msg}",
                        url=url,
                        summary="",
                        score=score,
                        meta={
                            "repo": repo,
                            "kind": "commit",
                            "published_at": published,
                        },
                    )
                )

    items.sort(key=lambda x: x.score, reverse=True)
    if repos and not items and failed >= max(1, len(repos)):
        raise RuntimeError(
            f"GitHub fetch failed across repos. Last error: {last_error or 'unknown'}"
        )
    return items[:max_items]


def _safe_get(client: httpx.Client, url: str) -> tuple[list, str]:
    try:
        r = client.get(url)
        if r.status_code == 404:
            return [], ""
        r.raise_for_status()
        data = r.json()
        return (data if isinstance(data, list) else []), ""
    except Exception as e:
        return [], str(e)
