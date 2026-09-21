from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

from eval_radar.config import get_settings
from eval_radar.llm import LLMError, build_agent_system_prompt, complete, llm_configured
from eval_radar.memory.store import load_preferences, memory_paths


def filter_items(payload: dict) -> list[dict]:
    settings = get_settings()
    prefs = load_preferences()
    max_items = int(prefs.get("max_items_per_report") or settings.max_total_items)
    mute = [m.lower() for m in (prefs.get("mute_topics") or [])]
    boost = [b.lower() for b in (prefs.get("boost_topics") or [])]

    filtered: list[dict] = []
    for it in payload.get("items") or []:
        blob = f"{it.get('title', '')} {it.get('summary', '')}".lower()
        if any(m in blob for m in mute if m):
            continue
        row = dict(it)
        row["_rank"] = float(it.get("score") or 0) + sum(
            1 for b in boost if b and b in blob
        )
        filtered.append(row)
    filtered.sort(key=lambda x: x.get("_rank", 0), reverse=True)
    return filtered[:max_items]


def render_report(payload: dict) -> str:
    """Long bilingual brief via LLM; falls back to expanded template."""
    if llm_configured():
        try:
            return _render_llm(payload)
        except Exception as e:
            fallback = _render_template(payload)
            return (
                f"(LLM özet başarısız: {e})\n\n"
                "Aşağıda genişletilmiş kaynaklı taslak var:\n\n"
                f"{fallback}"
            )
    return _render_template(payload)


def _render_llm(payload: dict) -> str:
    settings = get_settings()
    prefs = load_preferences()
    profile = memory_paths()["profile"].read_text(encoding="utf-8")
    items = filter_items(payload)
    tz = ZoneInfo(settings.report_tz)
    now = datetime.now(tz).strftime("%Y-%m-%d %H:%M %Z")

    system = build_agent_system_prompt(profile, prefs)
    user = f"""Bugünün LLM evaluation radar verisini Mahir için UZUN bir brifinge çevir.

Zaman: {now}
Ham sayılar: {json.dumps(payload.get('counts') or {}, ensure_ascii=False)}
Kaynak hataları (varsa): {json.dumps(payload.get('errors') or {}, ensure_ascii=False)}

Öğeler (JSON):
{json.dumps(items, ensure_ascii=False, indent=2)}

FORMAT ZORUNLU:
1) Giriş: 1 kısa Türkçe paragraf + 1 short English paragraph (bugünün teması).
2) Her önemli sinyal için:
   - Başlık (düz metin, yıldız yok)
   - Türkçe: 4–7 cümlelik derin özet
   - English: 3–5 sentence deep summary
   - Kaynak URL (aynen)
3) Ne izlemeli / What to watch — 3–5 madde (• ile)
4) Kapanış: Mahir'e samimi 1–2 cümle.

YASAK: **, __, ##, markdown. Sadece düz okunaklı metin.
Uzun yaz ama boş dolgu yapma (~1200–2200 kelime hedef).
"""
    raw = complete(system, user, max_tokens=3500, purpose="daily_report")
    from eval_radar.format_text import polish_llm_text

    return polish_llm_text(raw)


def _render_template(payload: dict) -> str:
    settings = get_settings()
    prefs = load_preferences()
    tz = ZoneInfo(settings.report_tz)
    now = datetime.now(tz).strftime("%Y-%m-%d %H:%M %Z")
    filtered = filter_items(payload)
    errors = payload.get("errors") or {}

    lines = [
        f"LLM Eval Radar — {now}",
        f"Sinyaller / Signals: arXiv {payload.get('counts', {}).get('arxiv', 0)} · "
        f"GitHub {payload.get('counts', {}).get('github', 0)} · "
        f"Reddit {payload.get('counts', {}).get('reddit', 0)}",
        "",
        "Not: Daha zengin sohbet + uzun TR/EN özet için .env içine GEMINI_API_KEY ekle "
        "(ücretsiz: https://aistudio.google.com/apikey), botu yeniden başlat.",
        "",
        "=== Bugünün maddeleri / Today's items ===",
        "",
    ]

    if errors:
        lines.append("Kaynak uyarıları / Source warnings:")
        for src, err in errors.items():
            lines.append(f"- {src}: {err}")
        lines.append("")

    if not filtered:
        lines.append("Bugün güçlü sinyal yok / No strong signals today.")
    else:
        for i, it in enumerate(filtered, 1):
            src = it.get("source", "?")
            title = it.get("title") or "untitled"
            url = it.get("url") or ""
            summary = (it.get("summary") or "").strip()
            lines.append(f"{i}. [{src}] {title}")
            lines.append("")
            lines.append("TR:")
            lines.append(
                summary
                if summary
                else "Özet metni kaynakta sınırlı; linkten detaya bak."
            )
            lines.append("")
            lines.append("EN:")
            lines.append(summary if summary else "Limited source blurb; open the link for depth.")
            if url:
                lines.append(f"Source: {url}")
            lines.append("")
            lines.append("-" * 40)
            lines.append("")

    extra = prefs.get("approved_extra_seeds") or []
    if extra:
        lines.append("Onaylı ekstra seed / Approved extra seeds: " + ", ".join(extra[:10]))

    lines.extend(
        [
            "",
            "Komutlar: /report /memory /help",
            "Öğret: boost: … | mute: … | takip et: …",
        ]
    )
    return "\n".join(lines).strip()
