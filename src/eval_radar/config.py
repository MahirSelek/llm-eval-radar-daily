from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    telegram_chat_id: str
    github_token: str
    cursor_api_key: str
    cursor_model: str
    publisher_model: str
    gemini_api_key: str
    openai_api_key: str
    openai_model: str
    max_arxiv: int
    max_github: int
    max_reddit: int
    max_rss: int
    max_forums: int
    max_hn: int
    max_x: int
    max_total_items: int
    max_age_hours: float
    report_tz: str
    flask_secret_key: str
    dashboard_password: str
    dashboard_local_only: bool
    seeds_path: Path
    memory_dir: Path
    digests_dir: Path


def get_settings() -> Settings:
    return Settings(
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", "").strip(),
        github_token=os.getenv("GITHUB_TOKEN", "").strip(),
        cursor_api_key=os.getenv("CURSOR_API_KEY", "").strip(),
        cursor_model=os.getenv("CURSOR_MODEL", "composer-2.5").strip() or "composer-2.5",
        publisher_model=os.getenv("PUBLISHER_MODEL", "").strip(),
        gemini_api_key=os.getenv("GEMINI_API_KEY", "").strip(),
        openai_api_key=os.getenv("OPENAI_API_KEY", "").strip(),
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip(),
        max_arxiv=int(os.getenv("MAX_ARXIV", "12")),
        max_github=int(os.getenv("MAX_GITHUB", "12")),
        max_reddit=int(os.getenv("MAX_REDDIT", "12")),
        max_rss=int(os.getenv("MAX_RSS", "12")),
        max_forums=int(os.getenv("MAX_FORUMS", "12")),
        max_hn=int(os.getenv("MAX_HN", "10")),
        max_x=int(os.getenv("MAX_X", "8")),
        max_total_items=int(os.getenv("MAX_TOTAL_ITEMS", "18")),
        max_age_hours=float(os.getenv("MAX_AGE_HOURS", "36")),
        report_tz=os.getenv("REPORT_TZ", "Europe/Istanbul"),
        flask_secret_key=os.getenv("FLASK_SECRET_KEY", "").strip(),
        dashboard_password=os.getenv("DASHBOARD_PASSWORD", "").strip(),
        dashboard_local_only=_parse_bool(os.getenv("DASHBOARD_LOCAL_ONLY", "1")),
        seeds_path=ROOT / "config" / "seeds.yaml",
        memory_dir=ROOT / "data" / "memory",
        digests_dir=ROOT / "data" / "digests",
    )


def load_seeds(path: Path | None = None) -> dict:
    p = path or get_settings().seeds_path
    with p.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _parse_bool(raw: str) -> bool:
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}
