from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

from eval_radar.config import get_settings
from eval_radar.format_text import polish_llm_text
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
                f"(LLM raporu oluşturulamadı: {e})\n\n"
                "Aşağıda kaynak bazlı taslak brifing yer almaktadır:\n\n"
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
    user = f"""Bugünün LLM evaluation ve benchmark sinyallerini Mahir için profesyonel, ferah ve derin bir araştırma brifingine dönüştür.

Zaman: {now}
Ham Sinyal Dağılımı: {json.dumps(payload.get('counts') or {}, ensure_ascii=False)}
Sinyal Maddeleri (JSON):
{json.dumps(items, ensure_ascii=False, indent=2)}

FORMAT VE YAZIM DÜZENİ KURALLARI:
1. Kesinlikle çorba veya sıkışık metin olmasın. Her ana bölüm ve paragraf arasında 2 satır boşluk bırak.
2. Aşağıdaki temiz yapılandırılmış şablonu takip et:

🎯 EXECUTIVE BRIEF / GÜNÜN TEMASI
(Kısa, çarpıcı Türkçe paragraf + ardından kısa İngilizce özet paragraf)

🔬 KRİTİK METODOLOJİ & BENCHMARK GELİŞMELERİ
(En önemli 3–5 madde için:
• [KAYNAK / BAŞLIK]
  - TR Derinlikli Açıklama (3-5 cümle: ne değişti, neden önemli?)
  - EN Key Takeaway (2-3 sentences)
  - URL)

🚨 HAKEM & SIZINTI ALARMLARI (LLM-as-a-Judge / Leakage / Leaderboard)
(Hakem modellerindeki kaymalar, contamination veya harness düzeltmeleri hakkında 2-3 madde)

💡 EYLEM PLANI & GÖZLEM LİSTESİ
(Mahir'in bu hafta takip etmesi veya dikkat etmesi gereken 3-4 net madde)

YASAK: Markdown bold (**), başlık etiketi (##) gibi ham işaretler KULLANMA. Düz, temiz, okunabilir metin formatında yaz.
"""
    raw = complete(system, user, max_tokens=3500, purpose="daily_report")
    return polish_llm_text(raw)


def _render_template(payload: dict) -> str:
    settings = get_settings()
    tz = ZoneInfo(settings.report_tz)
    now = datetime.now(tz).strftime("%Y-%m-%d %H:%M %Z")
    filtered = filter_items(payload)
    errors = payload.get("errors") or {}

    lines = [
        f"🎯 LLM EVALUATION RADAR — {now}",
        f"📊 Sinyaller: arXiv {payload.get('counts', {}).get('arxiv', 0)} · "
        f"GitHub {payload.get('counts', {}).get('github', 0)} · "
        f"Reddit {payload.get('counts', {}).get('reddit', 0)}",
        "",
        "────────────────────────────────────────",
        "",
    ]

    if errors:
        lines.append("⚠️ Kaynak Uyarıları:")
        for src, err in errors.items():
            lines.append(f"• {src}: {err}")
        lines.append("")

    if not filtered:
        lines.append("Bugün kaydedilen kritik sinyal bulunamadı.")
    else:
        for i, it in enumerate(filtered, 1):
            src = it.get("source", "?").upper()
            title = it.get("title") or "untitled"
            url = it.get("url") or ""
            summary = (it.get("summary") or "").strip()
            lines.append(f"{i}. [{src}] {title}")
            lines.append(f"   {summary}")
            if url:
                lines.append(f"   🔗 {url}")
            lines.append("")

    return "\n".join(lines)
