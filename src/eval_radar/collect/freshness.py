from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from zoneinfo import ZoneInfo

from eval_radar.models import Item


def parse_datetime(value: Any) -> datetime | None:
    """Best-effort parse into timezone-aware UTC datetime."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        # Heuristic: seconds vs milliseconds
        ts = float(value)
        if ts > 1e12:
            ts /= 1000.0
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, (tuple, list)) and len(value) >= 6:
        try:
            # feedparser time.struct_time-like
            return datetime(*[int(x) for x in value[:6]], tzinfo=timezone.utc)
        except Exception:
            return None
    text = str(value).strip()
    if not text:
        return None
    # ISO-ish
    try:
        iso = text.replace("Z", "+00:00")
        dt = datetime.fromisoformat(iso)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    # RFC 2822
    try:
        dt = parsedate_to_datetime(text)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def local_day(tz_name: str) -> str:
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = timezone.utc
    return datetime.now(tz).strftime("%Y-%m-%d")


def age_hours(published: datetime | None, *, now: datetime | None = None) -> float | None:
    if not published:
        return None
    n = now or now_utc()
    return max(0.0, (n - published.astimezone(timezone.utc)).total_seconds() / 3600.0)


def freshness_bonus(published: datetime | None, *, now: datetime | None = None) -> float:
    """Strong preference for today's / last-24h signals."""
    h = age_hours(published, now=now)
    if h is None:
        return 0.0
    if h <= 12:
        return 8.0
    if h <= 24:
        return 5.0
    if h <= 36:
        return 3.0
    if h <= 48:
        return 1.5
    if h <= 72:
        return 0.5
    return -3.0


def is_fresh(
    published: datetime | None,
    *,
    max_age_hours: float,
    now: datetime | None = None,
) -> bool:
    if published is None:
        # Keep undated items only if we are desperate later; default soft-reject.
        return False
    h = age_hours(published, now=now)
    return h is not None and h <= max_age_hours


def apply_freshness(
    items: list[Item],
    *,
    max_age_hours: float = 36.0,
    soft_fallback_hours: float = 72.0,
    min_keep: int = 6,
) -> list[Item]:
    """Filter to recent items; if too few remain, widen window once."""
    now = now_utc()
    scored: list[Item] = []
    for it in items:
        published = parse_datetime((it.meta or {}).get("published_at") or (it.meta or {}).get("published"))
        if published:
            it.meta["published_at"] = published.astimezone(timezone.utc).isoformat()
        bonus = freshness_bonus(published, now=now)
        it.score = float(it.score) + bonus
        scored.append(it)

    hard = [
        it
        for it in scored
        if is_fresh(
            parse_datetime((it.meta or {}).get("published_at")),
            max_age_hours=max_age_hours,
            now=now,
        )
    ]
    if len(hard) >= min_keep:
        return sorted(hard, key=lambda x: x.score, reverse=True)

    soft = [
        it
        for it in scored
        if is_fresh(
            parse_datetime((it.meta or {}).get("published_at")),
            max_age_hours=soft_fallback_hours,
            now=now,
        )
    ]
    if soft:
        return sorted(soft, key=lambda x: x.score, reverse=True)

    # Last resort: keep top scored, but mark as stale-aware.
    return sorted(scored, key=lambda x: x.score, reverse=True)


def cutoff_iso(*, max_age_hours: float) -> str:
    return (now_utc() - timedelta(hours=max_age_hours)).isoformat()
