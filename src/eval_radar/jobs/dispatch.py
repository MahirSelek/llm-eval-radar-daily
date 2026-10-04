from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from eval_radar.collect.freshness import assess_publish_gate, local_day, select_publishable_items
from eval_radar.collect.pipeline import collect_all, save_digest
from eval_radar.config import ROOT, get_settings
from eval_radar.publish.site import publish_daily_post, today_post_exists
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
    # Never merge into a non-today file from a stale caller
    today = local_day(settings.report_tz)
    if day != today:
        day = today
        fresh = {**fresh, "report_day": today}

    existing = load_day_pool(day) or {}
    # Drop stale leftovers that somehow sat in an older digest path
    existing_items = [
        it
        for it in (existing.get("items") or [])
        if isinstance(it, dict)
    ]

    by_key: dict[str, dict] = {}
    for it in existing_items + (fresh.get("items") or []):
        if not isinstance(it, dict):
            continue
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
    cap = max(settings.max_total_items * 3, 40)
    merged_items = merged_items[:cap]

    # Keep a publish-ready slice marked on the digest
    publishable = select_publishable_items(
        {"items": merged_items, "report_day": day}, report_day=day
    )

    passes = list(existing.get("collect_passes") or [])
    passes.append(
        {
            "at": fresh.get("collected_at"),
            "counts": fresh.get("counts") or {},
            "errors": fresh.get("errors") or {},
        }
    )
    passes = passes[-48:]

    payload = {
        "collected_at": fresh.get("collected_at"),
        "report_day": day,
        "freshness": fresh.get("freshness") or existing.get("freshness") or {},
        "counts": {
            **(fresh.get("counts") or {}),
            "day_pool_total": len(merged_items),
            "publishable_fresh": len(publishable),
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


def daily_dispatch(
    *,
    force: bool = False,
    force_stale: bool = False,
    dry_run: bool = False,
) -> dict:
    """
    One coordinated daily run — only when TODAY has enough fresh signals.
    Never regenerates an older day's report just because the Mac opened again.
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
    gate = assess_publish_gate(
        payload,
        force=force,
        force_stale=force_stale,
        today_post_exists=today_post_exists(day),
    )
    result: dict = {
        "ok": True,
        "day": day,
        "counts": payload.get("counts"),
        "pool_items": len(payload.get("items") or []),
        "fresh_count": gate.get("fresh_count"),
        "gate": {k: v for k, v in gate.items() if k != "fresh_items"},
    }

    if not gate.get("ok"):
        result["skipped"] = True
        result["reason"] = gate.get("reason")
        log.info("dispatch skipped: %s", gate.get("reason"))
        return result

    # Restrict payload to hard-fresh items so LLM cannot recycle Sep 22 into Sep 24
    publish_payload = {
        **payload,
        "items": gate.get("fresh_items") or select_publishable_items(payload),
        "counts": {
            **(payload.get("counts") or {}),
            "publishable_fresh": gate.get("fresh_count"),
        },
    }

    # 1) Telegram
    try:
        report = render_report(publish_payload)
        if not dry_run:
            send_message(report, as_html=True)
        result["telegram"] = {"ok": True, "chars": len(report)}
        result["report_text"] = report
    except Exception as e:
        log.exception("telegram dispatch failed")
        result["telegram"] = {"ok": False, "error": str(e)}

    # 2) GitHub Pages from SAME fresh pool
    try:
        pub = publish_daily_post(
            dry_run=dry_run,
            payload=publish_payload,
            force=force,
            force_stale=True,  # already gated above
        )
        if pub.get("skipped"):
            result["publish"] = {"ok": False, "skipped": True, "reason": pub.get("reason")}
        else:
            result["publish"] = {"ok": True, "post": pub.get("post"), "dry_run": dry_run}
    except Exception as e:
        log.exception("publish dispatch failed")
        result["publish"] = {"ok": False, "error": str(e)}

    # 3) Optional push — required for github.io; failure must be visible
    if (
        settings.auto_git_push
        and not dry_run
        and result.get("publish", {}).get("ok")
    ):
        try:
            result["git_push"] = push_site_to_github(day=day)
        except Exception as e:
            log.exception("git push failed")
            result["git_push"] = {"ok": False, "error": str(e)}
    else:
        result["git_push"] = {"ok": False, "skipped": True}

    # Only mark day done when publish landed AND push succeeded (if AUTO_GIT_PUSH)
    publish_ok = bool(result.get("publish", {}).get("ok"))
    push = result.get("git_push") or {}
    if settings.auto_git_push:
        push_ok = bool(push.get("ok"))
    else:
        push_ok = True
    if not dry_run and publish_ok and push_ok:
        mark_dispatched(extra=json.dumps(result.get("counts") or {}))
    elif not dry_run and publish_ok and settings.auto_git_push and not push.get("ok"):
        result["ok"] = False
        result["reason"] = (
            "Post generated locally but GitHub push failed — "
            f"{push.get('error') or push.get('reason') or 'unknown'}. "
            "Live site will not update until push succeeds."
        )
        log.error("dispatch incomplete: %s", result["reason"])

    return result


def push_site_to_github(*, day: str) -> dict:
    """Commit site artifacts and push. Falls back to a /tmp worktree if .git is locked."""
    _clear_stale_git_lock()
    direct = _git_commit_push_in_repo(ROOT, day=day)
    if direct.get("ok"):
        return direct
    err = direct.get("error") or direct.get("reason") or ""
    if (
        "Operation not permitted" in err
        or "index.lock" in err
        or "unable to write" in err
        or "fetch first" in err
        or "non-fast-forward" in err
        or "failed to push some refs" in err
    ):
        log.warning("direct git push blocked (%s) — trying /tmp clone fallback", err[:120])
        return _git_push_via_tmp_clone(day=day, prior_error=err)
    return direct


def _git_commit_push_in_repo(repo: Path, *, day: str) -> dict:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    add = subprocess.run(
        ["git", "add", "--", "content", "docs", "data/digests"],
        cwd=repo,
        capture_output=True,
        text=True,
        env=env,
    )
    if add.returncode != 0:
        return {
            "ok": False,
            "error": f"git add failed: {(add.stderr or add.stdout or '')[-400:]}",
        }

    check = subprocess.run(
        ["git", "diff", "--cached", "--quiet"],
        cwd=repo,
        capture_output=True,
        env=env,
    )
    if check.returncode == 0:
        ahead = subprocess.run(
            ["git", "status", "-sb"],
            cwd=repo,
            capture_output=True,
            text=True,
            env=env,
        )
        if "ahead" in (ahead.stdout or ""):
            push = subprocess.run(
                ["git", "push", "origin", "HEAD"],
                cwd=repo,
                capture_output=True,
                text=True,
                env=env,
            )
            if push.returncode == 0:
                return {"ok": True, "pushed": True, "reason": "pushed existing commits"}
            return {
                "ok": False,
                "error": ((push.stderr or "") + (push.stdout or ""))[-500:] or "push failed",
            }
        return {"ok": True, "skipped": True, "reason": "no site changes"}

    msg = f"chore: daily eval post {day}"
    commit = subprocess.run(
        ["git", "commit", "-m", msg],
        cwd=repo,
        capture_output=True,
        text=True,
        env=env,
    )
    combined = (commit.stdout or "") + (commit.stderr or "")
    if commit.returncode != 0 and "nothing to commit" in combined:
        return {"ok": True, "skipped": True, "reason": "nothing to commit"}
    if commit.returncode != 0:
        return {"ok": False, "error": combined[-500:] or "commit failed"}

    push = subprocess.run(
        ["git", "push", "origin", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        env=env,
    )
    if push.returncode != 0:
        return {
            "ok": False,
            "error": ((push.stderr or "") + (push.stdout or ""))[-500:] or "push failed",
        }
    return {"ok": True, "committed": True, "pushed": True, "message": msg}


def _git_push_via_tmp_clone(*, day: str, prior_error: str = "") -> dict:
    """
    When the project .git cannot write objects (macOS/Cursor lock),
    copy published site files into a fresh clone under /tmp and push from there.
    Then fast-forward the local repo if possible.
    """
    import shutil
    import tempfile

    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    remote = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=env,
    )
    if remote.returncode != 0:
        return {
            "ok": False,
            "error": f"no origin remote; prior: {prior_error[-200:]}",
        }
    url = (remote.stdout or "").strip()
    tmp = Path(tempfile.mkdtemp(prefix="eval-radar-push-"))
    try:
        clone = subprocess.run(
            ["git", "clone", "--depth", "1", url, str(tmp / "repo")],
            capture_output=True,
            text=True,
            env=env,
        )
        if clone.returncode != 0:
            return {
                "ok": False,
                "error": f"tmp clone failed: {(clone.stderr or '')[-300:]} | prior: {prior_error[-150:]}",
            }
        repo = tmp / "repo"
        for rel in ("content", "docs", "data/digests"):
            src = ROOT / rel
            dst = repo / rel
            if not src.exists():
                continue
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst)
        result = _git_commit_push_in_repo(repo, day=day)
        if result.get("ok") and (result.get("pushed") or result.get("committed")):
            # Sync local tip pointer without discarding uncommitted code edits
            subprocess.run(
                ["git", "fetch", "origin"],
                cwd=ROOT,
                capture_output=True,
                env=env,
            )
            result["via"] = "tmp_clone"
            result["note"] = "pushed via /tmp clone; run git pull when convenient"
        return result
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _clear_stale_git_lock() -> None:
    """Remove leftover .git/index.lock from a crashed auto-push (common on Mac)."""
    lock = ROOT / ".git" / "index.lock"
    if not lock.exists():
        return
    try:
        # If lock is older than 60s, treat as stale
        age = time.time() - lock.stat().st_mtime
        if age >= 60:
            lock.unlink(missing_ok=True)
            log.warning("removed stale .git/index.lock (age=%.0fs)", age)
    except OSError as e:
        log.warning("could not clear git lock: %s", e)


def local_now():
    settings = get_settings()
    try:
        tz = ZoneInfo(settings.report_tz)
    except Exception:
        tz = ZoneInfo("UTC")
    return datetime.now(tz)
