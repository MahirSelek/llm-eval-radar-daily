from __future__ import annotations

import html
import re


_BOLD = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")
_ITALIC = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)|(?<!_)_(?!_)(.+?)(?<!_)_(?!_)")
_CODE = re.compile(r"`([^`]+)`")
_CODEBLOCK = re.compile(r"```([a-zA-Z0-9_-]*)\n([\s\S]*?)```")
_HEADING = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
_BULLET = re.compile(r"^[\*\-]\s+(.+)$", re.MULTILINE)
_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)]+)\)")


def strip_markdown(text: str) -> str:
    """Remove markdown markers for clean plain reading without raw symbols."""
    if not text:
        return ""
    t = text.replace("\r\n", "\n")
    t = _CODEBLOCK.sub(r"\2", t)
    t = _LINK.sub(r"\1 (\2)", t)
    t = _BOLD.sub(lambda m: m.group(1) or m.group(2) or "", t)
    t = _ITALIC.sub(lambda m: m.group(1) or m.group(2) or "", t)
    t = _CODE.sub(r"\1", t)
    t = _HEADING.sub(r"\2", t)
    t = _BULLET.sub(r"• \1", t)
    t = t.replace("**", "").replace("__", "")
    t = re.sub(r"[ \t]+\n", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def to_web_html(text: str) -> str:
    """Safe, elegant HTML with real <strong>, <em>, <code>, <a>, headers and lists."""
    if not text:
        return ""
    t = text.replace("\r\n", "\n").strip()

    # Code blocks
    codeblocks: list[str] = []
    def _save_codeblock(m: re.Match) -> str:
        lang = html.escape(m.group(1) or "")
        code = html.escape(m.group(2))
        codeblocks.append(f'<pre class="code-block"><code class="language-{lang}">{code}</code></pre>')
        return f"\x00CODEBLOCK{len(codeblocks) - 1}\x00"

    t = _CODEBLOCK.sub(_save_codeblock, t)

    # Links
    links: list[tuple[str, str]] = []
    def _save_link(m: re.Match) -> str:
        links.append((m.group(1), m.group(2)))
        return f"\x00LINK{len(links) - 1}\x00"

    t = _LINK.sub(_save_link, t)
    t = html.escape(t)

    # Bolds & Italics
    t = re.sub(r"\*\*(.+?)\*\*|__(.+?)__", lambda m: f"<strong>{m.group(1) or m.group(2)}</strong>", t)
    t = re.sub(
        r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)|(?<!_)_(?!_)(.+?)(?<!_)_(?!_)",
        lambda m: f"<em>{m.group(1) or m.group(2)}</em>",
        t,
    )
    t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)

    # Headings to clean title badges
    t = re.sub(r"^#{1,3}\s+(.+)$", r'<h4 class="chat-section-title">\1</h4>', t, flags=re.MULTILINE)
    t = re.sub(r"^#{4,6}\s+(.+)$", r'<h5 class="chat-section-subtitle">\1</h5>', t, flags=re.MULTILINE)

    # Bullet lists
    t = re.sub(r"^[\*\-]\s+(.+)$", r'<li class="chat-bullet">\1</li>', t, flags=re.MULTILINE)

    # Restore links
    for i, (label, url) in enumerate(links):
        safe_url = html.escape(url, quote=True)
        safe_label = html.escape(label)
        t = t.replace(
            f"\x00LINK{i}\x00",
            f'<a href="{safe_url}" target="_blank" rel="noopener">{safe_label}</a>',
        )

    # Autolink bare URLs
    t = re.sub(
        r"(?<![\"'>])(https?://[^\s<]+)",
        r'<a href="\1" target="_blank" rel="noopener">\1</a>',
        t,
    )

    # Restore code blocks
    for i, cb in enumerate(codeblocks):
        t = t.replace(f"\x00CODEBLOCK{i}\x00", cb)

    # Wrap paragraphs cleanly
    paragraphs = t.split("\n\n")
    out_paras = []
    for p in paragraphs:
        p_clean = p.strip()
        if not p_clean:
            continue
        if p_clean.startswith("<pre") or p_clean.startswith("<h4") or p_clean.startswith("<h5"):
            out_paras.append(p_clean)
        elif '<li class="chat-bullet">' in p_clean:
            out_paras.append(f'<ul class="chat-list">{p_clean.replace(chr(10), "")}</ul>')
        else:
            out_paras.append(f"<p>{p_clean.replace(chr(10), '<br>')}</p>")

    return "\n".join(out_paras)


def to_telegram_html(text: str) -> str:
    """Telegram HTML parse_mode: <b>, <i>, <a>, <code>, <pre>, <blockquote> with clean line breaks."""
    if not text:
        return ""
    t = text.replace("\r\n", "\n").strip()

    # Pre/code blocks
    blocks: list[str] = []
    def _save_block(m: re.Match) -> str:
        code = html.escape(m.group(2))
        blocks.append(f"<pre><code>{code}</code></pre>")
        return f"\x00PRE{len(blocks) - 1}\x00"

    t = _CODEBLOCK.sub(_save_block, t)

    # Links
    links: list[tuple[str, str]] = []
    def _save_link(m: re.Match) -> str:
        links.append((m.group(1), m.group(2)))
        return f"\x00LINK{len(links) - 1}\x00"

    t = _LINK.sub(_save_link, t)
    t = html.escape(t)

    # Markdown elements to Telegram HTML
    t = re.sub(r"\*\*(.+?)\*\*|__(.+?)__", lambda m: f"<b>{m.group(1) or m.group(2)}</b>", t)
    t = re.sub(
        r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)|(?<!_)_(?!_)(.+?)(?<!_)_(?!_)",
        lambda m: f"<i>{m.group(1) or m.group(2)}</i>",
        t,
    )
    t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)

    # Headings to bold headers with spacing
    t = re.sub(r"^#{1,3}\s+(.+)$", r"\n<b>\1</b>\n", t, flags=re.MULTILINE)
    t = re.sub(r"^#{4,6}\s+(.+)$", r"\n<b>\1</b>\n", t, flags=re.MULTILINE)

    # Clean bullet points with generous spacing
    t = re.sub(r"^[\*\-]\s+(.+)$", r"• \1", t, flags=re.MULTILINE)

    # Clean leftover raw markers
    t = t.replace("**", "").replace("__", "")

    # Restore links
    for i, (label, url) in enumerate(links):
        safe_url = html.escape(url, quote=True)
        safe_label = html.escape(label)
        t = t.replace(f"\x00LINK{i}\x00", f'<a href="{safe_url}">{safe_label}</a>')

    # Restore pre blocks
    for i, block in enumerate(blocks):
        t = t.replace(f"\x00PRE{i}\x00", block)

    # Normalize multiple line breaks to maximum 2
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def polish_llm_text(text: str) -> str:
    """Normalize model output before channel-specific formatting."""
    if not text:
        return ""
    t = text.strip()
    # Collapse leftover orphan markers
    if t.count("**") % 2 == 1:
        t = t.replace("**", "")
    return t
