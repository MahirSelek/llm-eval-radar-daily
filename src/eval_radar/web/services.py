from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

from eval_radar.config import ROOT, get_settings, load_seeds
from eval_radar.llm import active_model_info, llm_configured
from eval_radar.memory.store import load_preferences, memory_paths, recent_chat
from eval_radar.memory.usage import usage_summary


SECRET_KEYS = {
    "TELEGRAM_BOT_TOKEN",
    "GITHUB_TOKEN",
    "FLASK_SECRET_KEY",
    "DASHBOARD_PASSWORD",
    "CURSOR_API_KEY",
    "GEMINI_API_KEY",
    "OPENAI_API_KEY",
}


def mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "••••••••"
    return value[:4] + "…" + value[-4:]


def read_env_map() -> dict[str, str]:
    path = ROOT / ".env"
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        k, v = raw.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def write_env_updates(updates: dict[str, str]) -> None:
    """Update keys in .env; blank secret values mean keep existing."""
    path = ROOT / ".env"
    existing = read_env_map()
    for k, v in updates.items():
        if k in SECRET_KEYS and (not v or v.startswith("••••") or "…" in v):
            continue
        existing[k] = v

    # Preserve comments / order where possible
    lines_out: list[str] = []
    seen: set[str] = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip() or line.strip().startswith("#") or "=" not in line:
                lines_out.append(line)
                continue
            k = line.split("=", 1)[0].strip()
            if k in existing:
                lines_out.append(f"{k}={existing[k]}")
                seen.add(k)
            else:
                lines_out.append(line)
    for k, v in existing.items():
        if k not in seen:
            lines_out.append(f"{k}={v}")
    path.write_text("\n".join(lines_out).rstrip() + "\n", encoding="utf-8")
    # Reload for current process
    for k, v in existing.items():
        os.environ[k] = v


def list_digests() -> list[dict[str, Any]]:
    d = get_settings().digests_dir
    if not d.exists():
        return []
    rows = []
    for p in sorted(d.glob("*.json"), reverse=True):
        if p.name == "latest.json":
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        rows.append(
            {
                "day": p.stem,
                "path": str(p),
                "counts": data.get("counts") or {},
                "item_count": len(data.get("items") or []),
                "collected_at": data.get("collected_at"),
            }
        )
    return rows


def load_digest(day: str | None = None) -> dict[str, Any] | None:
    d = get_settings().digests_dir
    path = d / "latest.json" if not day or day == "latest" else d / f"{day}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def extract_topics_and_authors(digest: dict | None) -> dict[str, Any]:
    if not digest:
        return {"topics": [], "authors": [], "channels": Counter()}
    topic_counter: Counter[str] = Counter()
    author_counter: Counter[str] = Counter()
    channels: Counter[str] = Counter()
    seeds = load_seeds()
    keywords = [k.lower() for k in (seeds.get("keywords") or [])]

    for it in digest.get("items") or []:
        src = it.get("source") or "unknown"
        channels[src.split("/")[0] if "/" in src else src] += 1
        blob = f"{it.get('title','')} {it.get('summary','')}".lower()
        for kw in keywords:
            if kw in blob:
                topic_counter[kw] += 1
        for a in (it.get("meta") or {}).get("authors") or []:
            author_counter[a] += 1
        # github repo as "author" proxy
        repo = (it.get("meta") or {}).get("repo")
        if repo:
            author_counter[repo] += 1

    return {
        "topics": topic_counter.most_common(20),
        "authors": author_counter.most_common(20),
        "channels": dict(channels),
    }


def dashboard_context() -> dict[str, Any]:
    digest = load_digest("latest")
    extracted = extract_topics_and_authors(digest)
    usage = usage_summary()
    model = active_model_info()
    seeds = load_seeds()
    prefs = load_preferences()
    return {
        "model": model,
        "llm_ok": llm_configured(),
        "usage": usage,
        "digest": digest,
        "digests": list_digests()[:14],
        "topics": extracted["topics"],
        "authors": extracted["authors"],
        "channels": extracted["channels"],
        "seeds": seeds,
        "prefs": prefs,
        "chat_preview": recent_chat(8)[::-1],
    }


def settings_context() -> dict[str, Any]:
    env = read_env_map()
    masked = {}
    for k, v in env.items():
        masked[k] = mask_secret(v) if k in SECRET_KEYS else v
    seeds_raw = (ROOT / "config" / "seeds.yaml").read_text(encoding="utf-8")
    prefs = load_preferences()
    profile = memory_paths()["profile"].read_text(encoding="utf-8")
    return {
        "env_masked": masked,
        "env_raw_keys": sorted(env.keys()),
        "seeds_raw": seeds_raw,
        "prefs_json": json.dumps(prefs, ensure_ascii=False, indent=2),
        "profile": profile,
        "model": active_model_info(),
        "secret_keys": sorted(SECRET_KEYS),
    }


def save_seeds_yaml(text: str) -> None:
    # validate
    yaml.safe_load(text)
    (ROOT / "config" / "seeds.yaml").write_text(text, encoding="utf-8")


def save_preferences_json(text: str) -> None:
    data = json.loads(text)
    memory_paths()["preferences"].write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def save_profile(text: str) -> None:
    memory_paths()["profile"].write_text(text, encoding="utf-8")
