from __future__ import annotations

import asyncio
import contextlib
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


HELP = """🤖 <b>LLM Eval Radar — 2026 Intelligence Harness</b>

⚡ <b>Komutlar:</b>
• /report — Günlük detaylı LLM evaluation & benchmark brifingi
• /memory — Öğrenilmiş tercihler ve agent hafıza profili
• /help — Komut ve yetenek listesi

💬 <b>Doğrudan Sohbet:</b>
Herhangi bir soru, model karşılaştırması veya benchmark metodolojisi sorusu yaz — senin için analiz edip yanıtlayayım.

🧠 <b>Hafıza Öğretme:</b>
• <code>boost: contamination</code>
• <code>mute: crypto</code>
• <code>takip et: John Schulman</code>
"""


async def _typing_loop(chat) -> None:
    """Keep Telegram typing status active while processing LLM tasks."""
    try:
        while True:
            await chat.send_action("typing")
            await asyncio.sleep(3.5)
    except asyncio.CancelledError:
        pass
    except Exception as e:
        log.debug("typing loop exception: %s", e)


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
        await update.message.reply_text(strip_markdown(cleaned))


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or not update.message:
        return
    chat_id = update.effective_chat.id
    save_chat_id(chat_id)
    append_chat("user", "/start", {"chat_id": chat_id})
    llm_note = (
        "🟢 <b>Cursor Premium LLM Aktif</b> (Uzun analiz, derin değerlendirme ve sohbet hazır)."
        if llm_configured()
        else "⚠️ <b>LLM Key Eksik</b>: <code>.env</code> dosyasına <code>CURSOR_API_KEY</code> ekleyin."
    )
    await _reply(
        update,
        f"👋 <b>Selam Mahir!</b> Eval Radar sistemine bağlandın.\nChat ID kaydedildi: <code>{chat_id}</code>\n\n{llm_note}\n\n{HELP}",
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
        "🧠 <b>Agent Hafızası & Tercihler</b>\n\n"
        f"🎯 <b>Boosted:</b> <code>{prefs.get('boost_topics', [])}</code>\n"
        f"🔇 <b>Muted:</b> <code>{prefs.get('mute_topics', [])}</code>\n"
        f"➕ <b>Ek Seed'ler:</b> <code>{prefs.get('approved_extra_seeds', [])}</code>\n\n"
        f"📋 <b>Profil Özeti:</b>\n<i>{profile.strip()}</i>"
    )
    await _reply(update, text[:3500])


async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_chat:
        return
    save_chat_id(update.effective_chat.id)
    
    status_msg = await update.message.reply_text(
        "📡 <b>Radar taranıyor ve analiz hazırlanıyor...</b>\n<i>arXiv, GitHub Releases ve topluluk sinyalleri taranıyor.</i>",
        parse_mode="HTML"
    )

    typing_task = asyncio.create_task(_typing_loop(update.message.chat))
    try:
        payload = await asyncio.to_thread(collect_all)
        await asyncio.to_thread(save_digest, payload)
        report = await asyncio.to_thread(render_report, payload)
        
        # Delete temporary status
        with contextlib.suppress(Exception):
            await status_msg.delete()

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
        await update.message.reply_text(f"❌ Rapor oluşturulamadı: {e}")
    finally:
        typing_task.cancel()


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
            f"📌 <b>Hafıza Notu:</b> {note}\n\n"
            "⚠️ Gerçek sohbet için Cursor Premium LLM gereklidir.\n"
            "1) <a href='https://cursor.com/dashboard/integrations'>cursor.com/dashboard/integrations</a> → API key al\n"
            "2) <code>.env</code> → <code>CURSOR_API_KEY=...</code> kaydet\n"
            "3) Botu yeniden başlat."
        )
        append_chat("assistant", reply)
        await _reply(update, reply)
        return

    typing_task = asyncio.create_task(_typing_loop(update.message.chat))
    try:
        reply = await asyncio.to_thread(_chat_reply, text, teach_note=note)
        reply = polish_llm_text(reply)
    except Exception as e:
        log.exception("chat failed")
        reply = f"⚠️ Sohbet tarafında bir aksaklık oldu: {e}\n(Öğrenme notu kaydedildi: {note})"
    finally:
        typing_task.cancel()

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
    user = f"""Kullanıcı (Mahir) mesajı:
\"\"\"{user_text}\"\"\"

Hafıza güncelleme notu: {teach_note}

Son sohbet geçmişi:
{chr(10).join(hist_lines)}

GÖREVİN VE YAZIM KURALLARI:
1. Sen dünyanın en yetkin LLM Değerlendirme & Benchmark Araştırmacısısın.
2. Çorba ve karmaşık metinlerden KESİNLİKLE kaçın. Paragraflar arasında 2 satır boşluk bırak.
3. Yanıtı ferah ve yapılandırılmış tut:
   - Ana düşünce veya doğrudan yanıt (kısa, net giriş)
   - Varsa teknik gerekçeler / karşılaştırmalar (maddeler halinde, aralarında boşluk olan temiz format)
   - Varsa tavsiye / aksiyon
4. Türkçe ağırlıklı yaz ancak uluslararası teknik terimleri (LLM-as-a-judge, contamination, benchmark leakage, few-shot prompt, MMLU vb.) orijinal bırak.
5. Markdown sembolleri yerine okunabilir paragraflar ve başlıklar kullan.
6. Asla boş dolgu yapma; net, keskin, ufuk açıcı ol.
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
