from __future__ import annotations

import asyncio
import logging

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from eval_radar.collect.pipeline import collect_all, save_digest
from eval_radar.config import get_settings
from eval_radar.format_text import polish_llm_text, strip_markdown
from eval_radar.llm import build_agent_system_prompt, complete, llm_configured
from eval_radar.memory.store import (
    append_chat,
    learn_from_user_message,
    load_preferences,
    memory_paths,
    recent_chat,
    save_chat_id,
)
from eval_radar.report.render import render_report
from eval_radar.telegram.send import send_message

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("eval-radar-bot")


HELP = """LLM Eval Radar — sohbet + günlük brifing

/start — chat id kaydet
/report — uzun TR+EN rapor
/memory — profil + tercihler
/help — bu mesaj

Serbest yaz: gerçek sohbet ederim (LLM gerekli).
Öğret:
• boost: contamination
• mute: crypto
• takip et: Someone Name
"""


async def _reply(update: Update, text: str) -> None:
    if not update.message or not update.effective_chat:
        return
    cleaned = polish_llm_text(text)
    try:
        await asyncio.to_thread(
            send_message, cleaned, chat_id=str(update.effective_chat.id), as_html=True
        )
    except Exception:
        log.exception("send_message failed; plain fallback")
        from eval_radar.format_text import strip_markdown

        await update.message.reply_text(strip_markdown(cleaned))


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or not update.message:
        return
    chat_id = update.effective_chat.id
    save_chat_id(chat_id)
    append_chat("user", "/start", {"chat_id": chat_id})
    llm_note = (
        "Cursor/LLM hazır — uzun TR/EN rapor + sohbet açık."
        if llm_configured()
        else "LLM yok: .env → CURSOR_API_KEY ekle (cursor.com/dashboard/integrations), botu restart et."
    )
    await _reply(
        update,
        f"Selam Mahir — chat id kaydedildi ({chat_id}).\n{llm_note}\n\n{HELP}",
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        await _reply(update, HELP)


async def cmd_memory(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    prefs = load_preferences()
    profile = memory_paths()["profile"].read_text(encoding="utf-8")[-2000:]
    text = (
        "Preferences\n"
        f"boost: {prefs.get('boost_topics')}\n"
        f"mute: {prefs.get('mute_topics')}\n"
        f"extra seeds: {prefs.get('approved_extra_seeds')}\n"
        f"rejected: {prefs.get('rejected_seeds')}\n\n"
        f"Profile tail\n{profile}"
    )
    await _reply(update, text[:3500])


async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_chat:
        return
    save_chat_id(update.effective_chat.id)
    await update.message.reply_text(
        "Topluyorum + uzun TR/EN brifing yazıyorum — 30–90 sn sürebilir..."
    )
    try:
        payload = await asyncio.to_thread(collect_all)
        await asyncio.to_thread(save_digest, payload)
        report = await asyncio.to_thread(render_report, payload)
        await asyncio.to_thread(
            send_message, report, chat_id=str(update.effective_chat.id), as_html=True
        )
        append_chat(
            "assistant",
            report[:2000],
            {"kind": "report", "counts": payload.get("counts")},
        )
    except Exception as e:
        log.exception("report failed")
        await update.message.reply_text(f"Rapor patladı: {e}")


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return
    if update.effective_chat:
        save_chat_id(update.effective_chat.id)
    text = update.message.text.strip()
    append_chat("user", text)

    note = learn_from_user_message(text)

    if not llm_configured():
        reply = (
            f"Not aldım: {note}\n\n"
            "Gerçek sohbet için Cursor Premium LLM lazım.\n"
            "1) cursor.com/dashboard/integrations → API key\n"
            "2) .env → CURSOR_API_KEY=...\n"
            "3) Botu Ctrl+C + yeniden başlat\n"
            "Sonra yaz — hesabındaki Cursor modeli ile konuşuruz."
        )
        append_chat("assistant", reply)
        await _reply(update, reply)
        return

    await update.message.chat.send_action("typing")
    try:
        reply = await asyncio.to_thread(_chat_reply, text, teach_note=note)
        reply = polish_llm_text(reply)
    except Exception as e:
        log.exception("chat failed")
        reply = f"Sohbet tarafı takıldı: {e}\n(Öğrenme notu yine kaydoldu: {note})"
    append_chat("assistant", strip_markdown(reply))
    await _reply(update, reply)


def _chat_reply(user_text: str, *, teach_note: str) -> str:
    prefs = load_preferences()
    profile = memory_paths()["profile"].read_text(encoding="utf-8")
    history = recent_chat(18)
    hist_lines = []
    for row in history:
        role = row.get("role", "?")
        t = (row.get("text") or "")[:800]
        hist_lines.append(f"{role}: {t}")
    system = build_agent_system_prompt(profile, prefs)
    user = f"""Mahir az önce yazdı:
\"\"\"{user_text}\"\"\"

Bellek güncelleme notu: {teach_note}

Son sohbet bağlamı:
{chr(10).join(hist_lines)}

Görevin: gerçek bir sohbet cevabı ver (robotik komut onayı değil).
- Türkçe ağırlıklı ama kritik terimleri İngilizce bırak / gerekirse EN paragraf ekle.
- LLM evaluation uzmanı gibi düşün; alakasız konularda nazikçe alana çek.
- Uzun ve doyurucu ol (genelde 150–400 kelime); soru sor, görüş belirt.
- Bilmediğin iddiayı uydurma; kaynak istersen söyle.
- ASLA markdown kullanma (** ## __ yok). Düz okunaklı metin.
"""
    return complete(system, user, max_tokens=2000, purpose="telegram_chat")


def main() -> None:
    settings = get_settings()
    if not settings.telegram_bot_token:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN in .env first")

    app = Application.builder().token(settings.telegram_bot_token).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("memory", cmd_memory))
    app.add_handler(CommandHandler("report", cmd_report))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    log.info("Eval Radar bot polling... llm=%s", llm_configured())
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
