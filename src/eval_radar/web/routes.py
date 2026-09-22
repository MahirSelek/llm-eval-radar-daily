from __future__ import annotations

import hmac
import ipaddress
import traceback
from urllib.parse import urlparse

from flask import Blueprint, Response, flash, jsonify, redirect, render_template, request, session, url_for

from eval_radar.collect.pipeline import collect_all, save_digest
from eval_radar.config import get_settings
from eval_radar.llm import active_model_info, build_agent_system_prompt, complete, llm_configured
from eval_radar.memory.store import append_chat, learn_from_user_message, load_preferences, memory_paths, recent_chat
from eval_radar.report.render import render_report
from eval_radar.web.services import (
    dashboard_context,
    list_digests,
    load_digest,
    save_preferences_json,
    save_profile,
    save_seeds_yaml,
    settings_context,
    write_env_updates,
)

bp = Blueprint("web", __name__)


def _is_local_request() -> bool:
    # Don't trust forwarded headers in local single-host mode.
    if request.headers.get("X-Forwarded-For"):
        return False
    remote = (request.remote_addr or "").strip()
    if not remote:
        return False
    try:
        return ipaddress.ip_address(remote).is_loopback
    except ValueError:
        return remote in {"localhost"}


def _guard_local_only() -> Response | None:
    settings = get_settings()
    if settings.dashboard_local_only and not _is_local_request():
        return Response("Forbidden: local-only dashboard", 403)
    return None


def _is_logged_in() -> bool:
    return bool(session.get("auth_ok"))


def _safe_next_url(value: str | None) -> str:
    if not value:
        return url_for("web.home")
    p = urlparse(value)
    if p.scheme or p.netloc:
        return url_for("web.home")
    if not value.startswith("/") or value.startswith("//"):
        return url_for("web.home")
    return value


@bp.before_app_request
def auth_guard():
    local_block = _guard_local_only()
    if local_block is not None:
        return local_block
    settings = get_settings()
    endpoint = request.endpoint or ""
    public = {"web.login_page", "web.login_submit", "static"}
    if endpoint in public:
        return None
    if not settings.dashboard_password:
        return Response("Set DASHBOARD_PASSWORD in .env to enable dashboard login", 503)
    if _is_logged_in():
        return None
    if request.path.startswith("/api/"):
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    nxt = request.full_path if request.query_string else request.path
    return redirect(url_for("web.login_page", next=nxt))


@bp.get("/login")
def login_page():
    return render_template("login.html", next=_safe_next_url(request.args.get("next")))


@bp.post("/login")
def login_submit():
    settings = get_settings()
    entered = (request.form.get("password") or "").strip()
    if settings.dashboard_password and hmac.compare_digest(
        entered, settings.dashboard_password
    ):
        session.clear()
        session["auth_ok"] = True
        return redirect(_safe_next_url(request.form.get("next")))
    flash("Şifre hatalı.", "error")
    return render_template("login.html", next=_safe_next_url(request.form.get("next"))), 401


@bp.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("web.login_page"))


@bp.get("/")
def home():
    return render_template("dashboard.html", **dashboard_context())


@bp.get("/digests")
def digests():
    return render_template("digests.html", digests=list_digests())


@bp.get("/digests/<day>")
def digest_detail(day: str):
    data = load_digest(day)
    if not data:
        flash("Digest bulunamadı", "error")
        return redirect(url_for("web.digests"))
    return render_template("digest_detail.html", day=day, digest=data)


@bp.get("/memory")
def memory_page():
    from eval_radar.format_text import to_web_html

    prefs = load_preferences()
    profile = memory_paths()["profile"].read_text(encoding="utf-8")
    chat = recent_chat(80)[::-1]
    for row in chat:
        row["html"] = to_web_html(row.get("text") or "")
    return render_template("memory.html", prefs=prefs, profile=profile, chat=chat)


@bp.get("/settings")
def settings_page():
    return render_template("settings.html", **settings_context())


@bp.post("/settings/env")
def settings_env_save():
    keys = [
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "FLASK_SECRET_KEY",
        "DASHBOARD_PASSWORD",
        "DASHBOARD_LOCAL_ONLY",
        "CURSOR_API_KEY",
        "CURSOR_MODEL",
        "PUBLISHER_MODEL",
        "GEMINI_API_KEY",
        "OPENAI_API_KEY",
        "OPENAI_MODEL",
        "GITHUB_TOKEN",
        "MAX_ARXIV",
        "MAX_GITHUB",
        "MAX_REDDIT",
        "MAX_RSS",
        "MAX_FORUMS",
        "MAX_HN",
        "MAX_X",
        "MAX_TOTAL_ITEMS",
        "MAX_AGE_HOURS",
        "REPORT_TZ",
    ]
    updates = {k: (request.form.get(k) or "").strip() for k in keys}
    int_keys = (
        "MAX_ARXIV",
        "MAX_GITHUB",
        "MAX_REDDIT",
        "MAX_RSS",
        "MAX_FORUMS",
        "MAX_HN",
        "MAX_X",
        "MAX_TOTAL_ITEMS",
    )
    for k in int_keys:
        raw = updates.get(k, "")
        if raw:
            try:
                int(raw)
            except ValueError:
                flash(f"{k} sayı olmalı (integer).", "error")
                return redirect(url_for("web.settings_page"))
    age = updates.get("MAX_AGE_HOURS", "")
    if age:
        try:
            float(age)
        except ValueError:
            flash("MAX_AGE_HOURS sayı olmalı (ör. 36).", "error")
            return redirect(url_for("web.settings_page"))
    local_only = updates.get("DASHBOARD_LOCAL_ONLY", "")
    if local_only and local_only.lower() not in {"0", "1", "true", "false", "yes", "no", "on", "off"}:
        flash("DASHBOARD_LOCAL_ONLY için 1/0 (veya true/false) kullan.", "error")
        return redirect(url_for("web.settings_page"))
    write_env_updates(updates)
    flash("Settings kaydedildi (.env). LLM/Telegram için botu restart etmeyi unutma.", "ok")
    return redirect(url_for("web.settings_page"))


@bp.post("/settings/seeds")
def settings_seeds_save():
    try:
        save_seeds_yaml(request.form.get("seeds_raw") or "")
        flash("Seed / kanal listesi kaydedildi.", "ok")
    except Exception as e:
        flash(f"Seeds YAML hatalı: {e}", "error")
    return redirect(url_for("web.settings_page"))


@bp.post("/settings/prefs")
def settings_prefs_save():
    try:
        save_preferences_json(request.form.get("prefs_json") or "{}")
        flash("Preferences kaydedildi.", "ok")
    except Exception as e:
        flash(f"Preferences JSON hatalı: {e}", "error")
    return redirect(url_for("web.settings_page"))


@bp.post("/settings/profile")
def settings_profile_save():
    save_profile(request.form.get("profile") or "")
    flash("Profil / agent bellek metni kaydedildi.", "ok")
    return redirect(url_for("web.settings_page"))


@bp.get("/chat")
def chat_page():
    from eval_radar.format_text import to_web_html

    chat = recent_chat(40)
    for row in chat:
        row["html"] = to_web_html(row.get("text") or "")
    return render_template(
        "chat.html",
        chat=chat,
        model=active_model_info(),
        llm_ok=llm_configured(),
    )


@bp.post("/api/chat")
def api_chat():
    from eval_radar.format_text import polish_llm_text, strip_markdown, to_web_html

    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"ok": False, "error": "empty"}), 400
    append_chat("user", text, {"via": "dashboard"})
    note = learn_from_user_message(text)
    if not llm_configured():
        reply = (
            f"Not: {note}\n\n"
            "LLM yok — Settings’ten CURSOR_API_KEY ekle."
        )
        append_chat("assistant", reply, {"via": "dashboard"})
        return jsonify(
            {
                "ok": True,
                "reply": reply,
                "reply_html": to_web_html(reply),
                "llm": False,
            }
        )
    try:
        prefs = load_preferences()
        profile = memory_paths()["profile"].read_text(encoding="utf-8")
        history = recent_chat(16)
        hist = "\n".join(f"{r.get('role')}: {(r.get('text') or '')[:600]}" for r in history)
        system = build_agent_system_prompt(profile, prefs)
        user = f"""Mahir dashboard'dan yazdı:
\"\"\"{text}\"\"\"

Bellek notu: {note}
Bağlam:
{hist}

Gerçek sohbet cevabı ver; TR+EN; LLM eval odaklı; doyurucu uzunluk.
ASLA markdown kullanma (** ## __ yok). Düz, okunaklı metin yaz.
"""
        reply = complete(system, user, max_tokens=2000, purpose="dashboard_chat")
        reply = polish_llm_text(reply)
    except Exception as e:
        reply = f"Hata: {e}"
    append_chat("assistant", strip_markdown(reply), {"via": "dashboard"})
    return jsonify(
        {
            "ok": True,
            "reply": strip_markdown(reply),
            "reply_html": to_web_html(reply),
            "llm": True,
        }
    )

@bp.post("/api/collect")
def api_collect():
    try:
        payload = collect_all()
        path = save_digest(payload)
        return jsonify({"ok": True, "path": str(path), "counts": payload.get("counts")})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "trace": traceback.format_exc()}), 500


@bp.post("/api/report-preview")
@bp.post("/api/report/preview")
def api_report_preview():
    try:
        from eval_radar.format_text import to_web_html
        payload = collect_all()
        save_digest(payload)
        text = render_report(payload)
        return jsonify({
            "ok": True,
            "report": text,
            "report_html": to_web_html(text),
            "counts": payload.get("counts")
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
