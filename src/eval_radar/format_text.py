from __future__ import annotations

import html
import re


_BOLD = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")
_ITALIC = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)|(?<!_)_(?!_)(.+?)(?<!_)_(?!_)")
_CODE = re.compile(r"`([^`]+)`")
_HEADING = re.compile(r"^#{1,6}\s+", re.MULTILINE)
_BULLET = re.compile(r"^[\*\-]\s+", re.MULTILINE)
_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)]+)\)")


def strip_markdown(text: str) -> str:
    """Remove markdown markers for clean plain reading."""
    if not text:
        return ""
    t = text.replace("\r\n", "\n")
    t = _LINK.sub(r"\1 (\2)", t)
    t = _BOLD.sub(lambda m: m.group(1) or m.group(2) or "", t)
    t = _ITALIC.sub(lambda m: m.group(1) or m.group(2) or "", t)
    t = _CODE.sub(r"\1", t)
    t = _HEADING.sub("", t)
    t = _BULLET.sub("• ", t)
    t = t.replace("**", "").replace("__", "")
    t = re.sub(r"[ \t]+\n", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def to_web_html(text: str) -> str:
    """Safe HTML with real <strong>/<em> instead of raw **."""
    if not text:
        return ""
    t = text.replace("\r\n", "\n").strip()
    # Extract links first into placeholders
    links: list[tuple[str, str]] = []

    def _save_link(m: re.Match) -> str:
        links.append((m.group(1), m.group(2)))
        return f"\x00LINK{len(links) - 1}\x00"

    t = _LINK.sub(_save_link, t)
    t = html.escape(t)
    t = re.sub(r"\*\*(.+?)\*\*|__(.+?)__", lambda m: f"<strong>{m.group(1) or m.group(2)}</strong>", t)
    t = re.sub(
        r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)|(?<!_)_(?!_)(.+?)(?<!_)_(?!_)",
        lambda m: f"<em>{m.group(1) or m.group(2)}</em>",
        t,
    )
    t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
    t = _HEADING.sub("", t)
    t = _BULLET.sub("• ", t)
    t = t.replace("**", "").replace("__", "")
    for i, (label, url) in enumerate(links):
        safe_url = html.escape(url, quote=True)
        safe_label = html.escape(label)
        t = t.replace(
            f"\x00LINK{i}\x00",
            f'<a href="{safe_url}" target="_blank" rel="noopener">{safe_label}</a>',
        )
    # Also autolink bare URLs
    t = re.sub(
        r"(?<![\"'>])(https?://[^\s<]+)",
        r'<a href="\1" target="_blank" rel="noopener">\1</a>',
        t,
    )
    return t.replace("\n", "<br>\n")


def to_telegram_html(text: str) -> str:
    """Telegram HTML parse_mode: <b>, <i>, <a>, <code>."""
    if not text:
        return ""
    t = text.replace("\r\n", "\n").strip()
    links: list[tuple[str, str]] = []

    def _save_link(m: re.Match) -> str:
        links.append((m.group(1), m.group(2)))
        return f"\x00LINK{len(links) - 1}\x00"

    t = _LINK.sub(_save_link, t)
    t = html.escape(t)
    t = re.sub(r"\*\*(.+?)\*\*|__(.+?)__", lambda m: f"<b>{m.group(1) or m.group(2)}</b>", t)
    t = re.sub(
        r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)|(?<!_)_(?!_)(.+?)(?<!_)_(?!_)",
        lambda m: f"<i>{m.group(1) or m.group(2)}</i>",
        t,
    )
    t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
    t = _HEADING.sub("", t)
    t = _BULLET.sub("• ", t)
    t = t.replace("**", "").replace("__", "")
    for i, (label, url) in enumerate(links):
        safe_url = html.escape(url, quote=True)
        safe_label = html.escape(label)
        t = t.replace(f"\x00LINK{i}\x00", f'<a href="{safe_url}">{safe_label}</a>')
    return t


def polish_llm_text(text: str) -> str:
    """Normalize model output before channel-specific formatting."""
    if not text:
        return ""
    t = text.strip()
    # Collapse leftover orphan markers
    if t.count("**") % 2 == 1:
        t = t.replace("**", "")
    return t
