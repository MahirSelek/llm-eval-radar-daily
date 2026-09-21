from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from eval_radar.config import get_settings


def usage_path() -> Path:
    d = get_settings().memory_dir
    d.mkdir(parents=True, exist_ok=True)
    return d / "usage.jsonl"


def log_usage(
    *,
    provider: str,
    model: str,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int = 0,
    purpose: str = "complete",
    meta: dict[str, Any] | None = None,
) -> None:
    if total_tokens <= 0:
        total_tokens = prompt_tokens + completion_tokens
    row = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "provider": provider,
        "model": model,
        "prompt_tokens": int(prompt_tokens or 0),
        "completion_tokens": int(completion_tokens or 0),
        "total_tokens": int(total_tokens or 0),
        "purpose": purpose,
        "meta": meta or {},
    }
    with usage_path().open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_usage(limit: int | None = None) -> list[dict]:
    path = usage_path()
    if not path.exists():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    if limit:
        return rows[-limit:]
    return rows


def usage_summary() -> dict[str, Any]:
    rows = read_usage()
    today = datetime.now(timezone.utc).date().isoformat()
    total = 0
    today_total = 0
    by_model: dict[str, int] = {}
    by_purpose: dict[str, int] = {}
    last_model = None
    last_provider = None
    for r in rows:
        tok = int(r.get("total_tokens") or 0)
        total += tok
        model = r.get("model") or "unknown"
        by_model[model] = by_model.get(model, 0) + tok
        purpose = r.get("purpose") or "other"
        by_purpose[purpose] = by_purpose.get(purpose, 0) + tok
        ts = (r.get("ts") or "")[:10]
        if ts == today:
            today_total += tok
        last_model = model
        last_provider = r.get("provider")
    return {
        "calls": len(rows),
        "total_tokens": total,
        "today_tokens": today_total,
        "by_model": by_model,
        "by_purpose": by_purpose,
        "last_model": last_model,
        "last_provider": last_provider,
        "recent": rows[-12:][::-1],
    }
