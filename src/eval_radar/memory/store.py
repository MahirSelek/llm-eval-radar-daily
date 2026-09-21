from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from eval_radar.config import get_settings


def memory_paths() -> dict[str, Path]:
    d = get_settings().memory_dir
    d.mkdir(parents=True, exist_ok=True)
    return {
        "dir": d,
        "profile": d / "profile.md",
        "preferences": d / "preferences.json",
        "chat_log": d / "chat_log.jsonl",
        "chat_id": d / "chat_id.txt",
    }


def load_preferences() -> dict:
    path = memory_paths()["preferences"]
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_preferences(prefs: dict) -> None:
    path = memory_paths()["preferences"]
    path.write_text(json.dumps(prefs, ensure_ascii=False, indent=2), encoding="utf-8")


def append_chat(role: str, text: str, meta: dict | None = None) -> None:
    path = memory_paths()["chat_log"]
    row = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "role": role,
        "text": text,
        "meta": meta or {},
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def recent_chat(limit: int = 16) -> list[dict]:
    path = memory_paths()["chat_log"]
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    rows: list[dict] = []
    for line in lines[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def save_chat_id(chat_id: int | str) -> None:
    memory_paths()["chat_id"].write_text(str(chat_id).strip(), encoding="utf-8")


def load_chat_id() -> str:
    settings = get_settings()
    if settings.telegram_chat_id:
        return settings.telegram_chat_id
    path = memory_paths()["chat_id"]
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    return ""


def learn_from_user_message(text: str) -> str:
    """Lightweight preference updates from natural language (no external LLM)."""
    prefs = load_preferences()
    lower = text.lower().strip()
    notes: list[str] = []

    if lower.startswith("boost:") or lower.startswith("önemse:"):
        topic = text.split(":", 1)[1].strip()
        boost = prefs.setdefault("boost_topics", [])
        if topic and topic not in boost:
            boost.append(topic)
            notes.append(f"boost eklendi: {topic}")
    elif lower.startswith("mute:") or lower.startswith("önemseme:"):
        topic = text.split(":", 1)[1].strip()
        mute = prefs.setdefault("mute_topics", [])
        if topic and topic not in mute:
            mute.append(topic)
            notes.append(f"mute eklendi: {topic}")
    elif lower.startswith("seed+") or lower.startswith("takip et:"):
        name = text.split(":", 1)[1].strip() if ":" in text else text[5:].strip()
        approved = prefs.setdefault("approved_extra_seeds", [])
        if name and name not in approved:
            approved.append(name)
            notes.append(f"seed adayı onaylandı: {name}")
    elif lower.startswith("seed-") or lower.startswith("takip etme:"):
        name = text.split(":", 1)[1].strip() if ":" in text else text[5:].strip()
        rejected = prefs.setdefault("rejected_seeds", [])
        if name and name not in rejected:
            rejected.append(name)
            notes.append(f"seed reddedildi: {name}")
    else:
        # Freeform: keep in chat log only; acknowledge
        notes.append("not alındı — profil/sohbet belleğine yazıldı")

    if any(lower.startswith(p) for p in ("boost:", "önemse:", "mute:", "önemseme:", "seed+", "takip et:", "seed-", "takip etme:")):
        save_preferences(prefs)

    profile = memory_paths()["profile"]
    with profile.open("a", encoding="utf-8") as f:
        f.write(f"\n- ({datetime.now(timezone.utc).date()}) user: {text.strip()[:500]}")

    return " · ".join(notes) if notes else "tamam"
