from __future__ import annotations

import httpx

from eval_radar.config import get_settings
from eval_radar.format_text import polish_llm_text, to_telegram_html
from eval_radar.memory.store import load_chat_id


TG_API = "https://api.telegram.org"


def send_message(text: str, chat_id: str | None = None, *, as_html: bool = True) -> dict:
    settings = get_settings()
    token = settings.telegram_bot_token
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN missing — copy .env.example to .env")

    cid = chat_id or load_chat_id()
    if not cid:
        raise RuntimeError(
            "TELEGRAM_CHAT_ID missing — start the bot and send /start once, or set it in .env"
        )

    polished = polish_llm_text(text)
    payload_text = to_telegram_html(polished) if as_html else polished
    chunks = _chunk(payload_text, 3500)
    last: dict = {}
    with httpx.Client(timeout=60.0) as client:
        for chunk in chunks:
            body = {
                "chat_id": cid,
                "text": chunk,
                "disable_web_page_preview": True,
            }
            if as_html:
                body["parse_mode"] = "HTML"
            r = client.post(f"{TG_API}/bot{token}/sendMessage", json=body)
            if r.status_code >= 400 and as_html:
                # Fallback plain if HTML parse fails
                body.pop("parse_mode", None)
                body["text"] = chunk.replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", "")
                r = client.post(f"{TG_API}/bot{token}/sendMessage", json=body)
            r.raise_for_status()
            last = r.json()
    return last


def _chunk(text: str, size: int) -> list[str]:
    if len(text) <= size:
        return [text]
    parts: list[str] = []
    buf: list[str] = []
    n = 0
    for line in text.splitlines(keepends=True):
        if n + len(line) > size and buf:
            parts.append("".join(buf))
            buf, n = [], 0
        buf.append(line)
        n += len(line)
    if buf:
        parts.append("".join(buf))
    return parts
