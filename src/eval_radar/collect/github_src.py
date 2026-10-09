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
    max_items: int = 8,
    timeout: float = 30.0,
) -> list[Item]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "eval-radar/0.1",
    }

    items: list[Item] = []
    failed = 0
    last_error = ""
    failed_repos: list[str] = []
    # Anonymous GitHub API quota is limited. Sample a focused subset to avoid noisy 403 storms.
    active_repos = repos[:8] if len(repos) > 8 else repos

    rate_limited = False
    with httpx.Client(
        timeout=timeout,
        headers=headers,
        trust_env=False,
        follow_redirects=True,
    ) as client:
        for repo in active_repos:
            if rate_limited:
                break
            releases, err1, code1 = _safe_get(client, f"{API}/repos/{repo}/releases?per_page=3")
            if code1 == 403 and "rate limit" in (err1 or "").lower():
                rate_limited = True
            if err1:
                failed += 1
                last_error = err1
                failed_repos.append(f"{repo}:releases")
                log.debug("github releases fetch failed for %s: %s", repo, err1)
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

            commits, err2, code2 = _safe_get(client, f"{API}/repos/{repo}/commits?per_page=5")
            if code2 == 403 and "rate limit" in (err2 or "").lower():
                rate_limited = True
            if err2:
                failed += 1
                last_error = err2
                failed_repos.append(f"{repo}:commits")
                log.debug("github commits fetch failed for %s: %s", repo, err2)
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
    if failed_repos and not rate_limited:
        # Keep logs readable: one summary line instead of dozens of per-repo warnings.
        log.warning(
            "github source partial failures: %s requests failed across %s repos (checked=%s, last=%s)",
            len(failed_repos),
            len(repos),
            len(active_repos),
            last_error or "unknown",
        )
    if rate_limited:
        log.info("github source rate-limited; continuing without github signals in this pass")
    elif repos and not items and failed >= max(1, len(active_repos)):
        log.warning("GitHub source produced no items: %s", last_error or "unknown")
    return items[:max_items]


def _safe_get(client: httpx.Client, url: str) -> tuple[list, str, int]:
    try:
        r = client.get(url)
        if r.status_code == 404:
            return [], "", 404
        r.raise_for_status()
        data = r.json()
        return (data if isinstance(data, list) else []), "", r.status_code
    except Exception as e:
        code = 0
        if isinstance(e, httpx.HTTPStatusError):
            code = e.response.status_code
        return [], str(e), code
