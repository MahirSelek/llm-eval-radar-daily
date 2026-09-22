from __future__ import annotations

import json
import logging
import subprocess
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from eval_radar.collect.freshness import local_day
from eval_radar.collect.pipeline import collect_all, save_digest
from eval_radar.config import ROOT, get_settings
from eval_radar.publish.site import publish_daily_post
from eval_radar.report.render import render_report
from eval_radar.telegram.send import send_message

log = logging.getLogger("eval-radar-dispatch")


def collect_and_accumulate() -> dict:
    """Fresh collect, then merge into today's day-pool digest."""
    fresh = collect_all()
    return merge_into_day_pool(fresh)


def load_day_pool(day: str | None = None) -> dict | None:
    settings = get_settings()
    day = day or local_day(settings.report_tz)
    path = settings.digests_dir / f"{day}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def merge_into_day_pool(fresh: dict) -> dict:
    """Accumulate all same-day collects into one pool used by Telegram + GitHub."""
    settings = get_settings()
    day = fresh.get("report_day") or local_day(settings.report_tz)
    existing = load_day_pool(day) or {}

    by_key: dict[str, dict] = {}
    for it in (existing.get("items") or []) + (fresh.get("items") or []):
        key = ((it.get("url") or it.get("title") or "")).strip().lower()
        if not key:
            continue
        prev = by_key.get(key)
        if prev is None or float(it.get("score") or 0) >= float(prev.get("score") or 0):
            by_key[key] = it

    merged_items = sorted(
        by_key.values(),
        key=lambda x: float(x.get("score") or 0),
        reverse=True,
    )
    # Soft cap on day pool size — keep richest signals
    cap = max(settings.max_total_items * 3, 40)
    merged_items = merged_items[:cap]

    passes = list(existing.get("collect_passes") or [])
    passes.append(
        {
            "at": fresh.get("collected_at"),
            "counts": fresh.get("counts") or {},
            "errors": fresh.get("errors") or {},
        }
    )
    # keep last 48 passes max
    passes = passes[-48:]

    payload = {
        "collected_at": fresh.get("collected_at"),
        "report_day": day,
        "freshness": fresh.get("freshness") or existing.get("freshness") or {},
        "counts": {
            **(fresh.get("counts") or {}),
            "day_pool_total": len(merged_items),
            "collect_passes": len(passes),
        },
        "items": merged_items,
        "seeds_people": fresh.get("seeds_people")
        or existing.get("seeds_people")
        or [],
        "errors": fresh.get("errors") or {},
        "collect_passes": passes,
        "pool_mode": "accumulate_same_day",
    }
    save_digest(payload)
    return payload


def dispatch_marker_path(day: str | None = None) -> Path:
    settings = get_settings()
    day = day or local_day(settings.report_tz)
    return settings.memory_dir / f"dispatch_done_{day}.txt"


def already_dispatched_today() -> bool:
    return dispatch_marker_path().exists()


def mark_dispatched(extra: str = "") -> None:
    settings = get_settings()
    settings.memory_dir.mkdir(parents=True, exist_ok=True)
    path = dispatch_marker_path()
    path.write_text(
        f"{datetime.now().isoformat()}\n{extra}\n",
        encoding="utf-8",
    )


def daily_dispatch(*, force: bool = False, dry_run: bool = False) -> dict:
    """
    One coordinated daily run:
    1) collect + merge into day pool
    2) Telegram long report from pool
    3) GitHub Pages article from same pool
    4) optional git push of docs/content
    """
    settings = get_settings()
    day = local_day(settings.report_tz)
    if already_dispatched_today() and not force:
        return {
            "ok": True,
            "skipped": True,
            "reason": f"already dispatched for {day}",
            "day": day,
        }

    payload = collect_and_accumulate()
    result: dict = {
        "ok": True,
        "day": day,
        "counts": payload.get("counts"),
        "pool_items": len(payload.get("items") or []),
    }

    # 1) Telegram
    try:
        report = render_report(payload)
        if not dry_run:
            send_message(report, as_html=True)
        result["telegram"] = {"ok": True, "chars": len(report)}
    except Exception as e:
        log.exception("telegram dispatch failed")
        result["telegram"] = {"ok": False, "error": str(e)}

    # 2) GitHub Pages article from SAME pool
    try:
        pub = publish_daily_post(dry_run=dry_run, payload=payload)
        result["publish"] = {"ok": True, "post": pub.get("post"), "dry_run": dry_run}
    except Exception as e:
        log.exception("publish dispatch failed")
        result["publish"] = {"ok": False, "error": str(e)}

    # 3) Optional push so github.io updates while Mac is on
    if settings.auto_git_push and not dry_run:
        try:
            result["git_push"] = push_site_to_github(day=day)
        except Exception as e:
            log.exception("git push failed")
            result["git_push"] = {"ok": False, "error": str(e)}
    else:
        result["git_push"] = {"ok": False, "skipped": True}

    if not dry_run and result.get("publish", {}).get("ok"):
        mark_dispatched(extra=json.dumps(result.get("counts") or {}))

    return result


def push_site_to_github(*, day: str) -> dict:
    """Commit generated site artifacts and push to origin (needs local git auth)."""
    subprocess.run(
        ["git", "add", "content", "docs", "data/digests"],
        cwd=ROOT,
        check=False,
        capture_output=True,
    )
    check = subprocess.run(
        ["git", "diff", "--cached", "--quiet"],
        cwd=ROOT,
        capture_output=True,
    )
    if check.returncode == 0:
        return {"ok": True, "skipped": True, "reason": "no site changes"}

    msg = f"chore: daily eval post {day}"
    commit = subprocess.run(
        ["git", "commit", "-m", msg],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    combined = (commit.stdout or "") + (commit.stderr or "")
    if commit.returncode != 0 and "nothing to commit" in combined:
        return {"ok": True, "skipped": True, "reason": "nothing to commit"}
    if commit.returncode != 0:
        return {"ok": False, "error": combined[-500:] or "commit failed"}

    push = subprocess.run(
        ["git", "push", "origin", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if push.returncode != 0:
        return {
            "ok": False,
            "error": ((push.stderr or "") + (push.stdout or ""))[-500:] or "push failed",
        }
    return {"ok": True, "committed": True, "pushed": True, "message": msg}


def local_now():
    settings = get_settings()
    try:
        tz = ZoneInfo(settings.report_tz)
    except Exception:
        tz = ZoneInfo("UTC")
    return datetime.now(tz)
