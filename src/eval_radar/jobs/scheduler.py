from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from eval_radar.config import get_settings
from eval_radar.jobs.dispatch import (
    already_dispatched_today,
    collect_and_accumulate,
    daily_dispatch,
    local_now,
)

log = logging.getLogger("eval-radar-scheduler")


async def scheduler_loop(stop: asyncio.Event) -> None:
    """
    While the agent process is running:
    - periodic collect → merge into same-day pool
    - at DAILY_DISPATCH_HOUR:MINUTE → Telegram + GitHub from that pool (once/day)
    """
    settings = get_settings()
    interval_sec = max(5, settings.collect_interval_minutes) * 60
    last_collect_at: datetime | None = None

    log.info(
        "scheduler on tz=%s collect_every=%sm dispatch=%02d:%02d auto_git_push=%s",
        settings.report_tz,
        settings.collect_interval_minutes,
        settings.daily_dispatch_hour,
        settings.daily_dispatch_minute,
        settings.auto_git_push,
    )

    # Warm the day pool shortly after boot
    try:
        payload = await asyncio.to_thread(collect_and_accumulate)
        last_collect_at = local_now()
        log.info(
            "boot collect ok pool=%s passes=%s",
            (payload.get("counts") or {}).get("day_pool_total"),
            (payload.get("counts") or {}).get("collect_passes"),
        )
    except Exception:
        log.exception("boot collect failed")

    while not stop.is_set():
        try:
            now = local_now()
            due_collect = (
                last_collect_at is None
                or (now - last_collect_at).total_seconds() >= interval_sec
            )
            if due_collect:
                payload = await asyncio.to_thread(collect_and_accumulate)
                last_collect_at = now
                log.info(
                    "interval collect ok pool=%s",
                    (payload.get("counts") or {}).get("day_pool_total"),
                )

            if (
                now.hour == settings.daily_dispatch_hour
                and now.minute == settings.daily_dispatch_minute
                and not already_dispatched_today()
            ):
                log.info("daily dispatch window hit — running Telegram + GitHub")
                result = await asyncio.to_thread(daily_dispatch, force=False, dry_run=False)
                log.info("daily dispatch result=%s", result)
        except Exception:
            log.exception("scheduler tick failed")

        try:
            await asyncio.wait_for(stop.wait(), timeout=30)
        except asyncio.TimeoutError:
            pass
