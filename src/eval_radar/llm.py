from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from eval_radar.config import ROOT, get_settings
from eval_radar.memory.usage import log_usage

log = logging.getLogger("eval-radar-llm")


class LLMError(RuntimeError):
    pass


def active_model_info() -> dict[str, str]:
    s = get_settings()
    if s.cursor_api_key:
        return {
            "provider": "cursor",
            "model": s.cursor_model,
            "configured": "yes",
        }
    if s.gemini_api_key:
        return {"provider": "gemini", "model": "gemini-2.0-flash", "configured": "yes"}
    if s.openai_api_key:
        return {"provider": "openai", "model": s.openai_model, "configured": "yes"}
    return {"provider": "none", "model": "—", "configured": "no"}


def llm_configured() -> bool:
    return active_model_info()["configured"] == "yes"


def complete(
    system: str,
    user: str,
    *,
    max_tokens: int = 2500,
    purpose: str = "complete",
    model_override: str | None = None,
) -> str:
    """Priority: Cursor (Premium account) → Gemini → OpenAI."""
    settings = get_settings()
    if settings.cursor_api_key:
        selected_model = model_override or settings.cursor_model
        return _cursor(
            settings.cursor_api_key,
            selected_model,
            system,
            user,
            purpose=purpose,
        )
    if settings.gemini_api_key:
        return _gemini(
            settings.gemini_api_key, system, user, max_tokens=max_tokens, purpose=purpose
        )
    if settings.openai_api_key:
        return _openai(
            settings.openai_api_key,
            model_override or settings.openai_model,
            system,
            user,
            max_tokens=max_tokens,
            purpose=purpose,
        )
    raise LLMError(
        "LLM yok. Cursor Premium için .env → CURSOR_API_KEY ekle "
        "(https://cursor.com/dashboard/integrations). "
        "Alternatif: GEMINI_API_KEY veya OPENAI_API_KEY."
    )


def _cursor(api_key: str, model: str, system: str, user: str, *, purpose: str) -> str:
    """Uses Cursor SDK agent with tools disabled (text-only) — bills Cursor account usage."""
    try:
        from cursor_sdk import Agent, AgentOptions, LocalAgentOptions
    except ImportError as e:
        raise LLMError("cursor-sdk yüklü değil: pip install cursor-sdk") from e

    prompt = (
        f"{system}\n\n---\n\nUSER MESSAGE:\n{user}\n\n"
        "Respond with the final answer text only. Do not edit files. Do not run tools."
    )
    try:
        result = Agent.prompt(
            prompt,
            AgentOptions(
                api_key=api_key,
                model=model,
                # Text-only: no shell/edit — safe for Telegram/dashboard chat
                tools=[],
                local=LocalAgentOptions(cwd=str(ROOT)),
            ),
        )
    except Exception as e:
        raise LLMError(f"Cursor SDK hatası: {e}") from e

    status = getattr(result, "status", None)
    text = (getattr(result, "result", None) or "").strip()
    if status and str(status) not in ("finished", "RunResultStatus.finished", "finished"):
        # status may be enum
        st = getattr(status, "value", str(status))
        if st not in ("finished", "success"):
            if not text:
                raise LLMError(f"Cursor run failed: status={status}")

    if not text:
        raise LLMError("Cursor boş yanıt verdi")

    usage = getattr(result, "usage", None)
    prompt_tokens = int(getattr(usage, "input_tokens", 0) or 0) if usage else 0
    completion_tokens = int(getattr(usage, "output_tokens", 0) or 0) if usage else 0
    total_tokens = int(getattr(usage, "total_tokens", 0) or 0) if usage else 0
    used_model = model
    sel = getattr(result, "model", None)
    if sel is not None:
        used_model = getattr(sel, "id", None) or getattr(sel, "model_id", None) or str(sel) or model

    log_usage(
        provider="cursor",
        model=str(used_model),
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        purpose=purpose,
        meta={"run_id": getattr(result, "id", None), "duration_ms": getattr(result, "duration_ms", None)},
    )
    return text


def _gemini(
    api_key: str, system: str, user: str, *, max_tokens: int, purpose: str
) -> str:
    model = "gemini-2.0-flash"
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        f"?key={api_key}"
    )
    body: dict[str, Any] = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {
            "temperature": 0.7,
            "maxOutputTokens": max_tokens,
        },
    }
    with httpx.Client(timeout=90.0) as client:
        r = client.post(url, json=body)
        if r.status_code >= 400:
            log.error("Gemini error %s: %s", r.status_code, r.text[:400])
            r.raise_for_status()
        data = r.json()
    try:
        parts = data["candidates"][0]["content"]["parts"]
        text = "".join(p.get("text", "") for p in parts).strip()
    except (KeyError, IndexError, TypeError) as e:
        raise LLMError(f"Gemini yanıtı parse edilemedi: {data}") from e
    if not text:
        raise LLMError("Gemini boş yanıt verdi")

    usage = data.get("usageMetadata") or {}
    log_usage(
        provider="gemini",
        model=model,
        prompt_tokens=int(usage.get("promptTokenCount") or 0),
        completion_tokens=int(usage.get("candidatesTokenCount") or 0),
        total_tokens=int(usage.get("totalTokenCount") or 0),
        purpose=purpose,
    )
    return text


def _openai(
    api_key: str, model: str, system: str, user: str, *, max_tokens: int, purpose: str
) -> str:
    url = "https://api.openai.com/v1/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body = {
        "model": model,
        "temperature": 0.7,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    with httpx.Client(timeout=90.0) as client:
        r = client.post(url, headers=headers, json=body)
        if r.status_code >= 400:
            log.error("OpenAI error %s: %s", r.status_code, r.text[:400])
            r.raise_for_status()
        data = r.json()
    text = data["choices"][0]["message"]["content"].strip()
    usage = data.get("usage") or {}
    log_usage(
        provider="openai",
        model=model,
        prompt_tokens=int(usage.get("prompt_tokens") or 0),
        completion_tokens=int(usage.get("completion_tokens") or 0),
        total_tokens=int(usage.get("total_tokens") or 0),
        purpose=purpose,
    )
    return text


def build_agent_system_prompt(profile: str, prefs: dict) -> str:
    return f"""Sen Mahir'in kişisel LLM Evaluation Radar asistanısın — ikinci beyni gibi çalışırsın.

KİMLİK / ÜSLUP
- Samimi, zeki, doğrudan; abartılı formalite yok. Mahir'le gerçek sohbet et.
- Hem Türkçe hem İngilizce kullan: önemli noktaları iki dilde ver veya doğalçe karıştır.
- Sadece large language model EVALUATION alanına odaklan (benchmark, leaderboard, LLM-as-judge, contamination, eval methodology, harness). Alakasız hype'ı kes.
- Kaynak linklerini koru; uydurma.

YAZIM KURALLARI (ÇOK ÖNEMLİ — okunabilirlik)
- ASLA markdown kullanma: **, __, ##, ``` , *bold* yok.
- Düz, temiz metin yaz. Vurgu gerekirse kısa cümle veya "→" / "•" kullan.
- Telegram ve web'de ham yıldız (**) Mahir'i rahatsız ediyor; üretme.

PROFİL
{profile}

TERCİHLER (JSON)
{json.dumps(prefs, ensure_ascii=False, indent=2)}
"""
