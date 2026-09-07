# SPDX-License-Identifier: MIT
"""
Small, dependency-free Markdown → HTML converter for the chat panel.

Handles the subset Grok actually emits: headings, bold/italic, inline code,
fenced code blocks, lists, blockquotes, links, and paragraphs. Code is
escaped so FreeCAD Python in replies cannot break out of the document.
"""

from __future__ import annotations

import html
import re
from typing import List


_FENCE = re.compile(r"```([a-zA-Z0-9_+-]*)\n(.*?)```", re.DOTALL)
_INLINE_CODE = re.compile(r"`([^`]+)`")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_ITALIC = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)")
_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)]+)\)")
_HEADING = re.compile(r"^(#{1,4})\s+(.*)$")
_UL = re.compile(r"^[-*+]\s+(.*)$")
_OL = re.compile(r"^(\d+)\.\s+(.*)$")
_BQ = re.compile(r"^>\s?(.*)$")


def markdown_to_html(text: str) -> str:
    if not text:
        return ""
    text = text.replace("\r\n", "\n")
    fences: List[str] = []

    def _store_fence(m: re.Match) -> str:
        lang = m.group(1) or ""
        code = html.escape(m.group(2).rstrip("\n"))
        idx = len(fences)
        fences.append(
            f'<pre class="code-block" data-lang="{html.escape(lang)}">'
            f"<code>{code}</code></pre>"
        )
        return f"@@FENCE{idx}@@"

    text = _FENCE.sub(_store_fence, text)
    parts: List[str] = []
    buf: List[str] = []
    list_type = None  # "ul" | "ol" | None

    def flush_p() -> None:
        nonlocal buf
        if buf:
            para = " ".join(x.strip() for x in buf if x.strip())
            if para:
                parts.append(f"<p>{_inline(para)}</p>")
            buf = []

    def close_list() -> None:
        nonlocal list_type
        if list_type:
            parts.append(f"</{list_type}>")
            list_type = None

    for raw in text.split("\n"):
        line = raw.rstrip()
        if not line.strip():
            flush_p()
            close_list()
            continue
        hm = _HEADING.match(line)
        if hm:
            flush_p()
            close_list()
            level = len(hm.group(1))
            parts.append(f"<h{level}>{_inline(hm.group(2))}</h{level}>")
            continue
        if line.strip().startswith("@@FENCE"):
            flush_p()
            close_list()
            parts.append(line.strip())
            continue
        um = _UL.match(line)
        if um:
            flush_p()
            if list_type != "ul":
                close_list()
                parts.append("<ul>")
                list_type = "ul"
            parts.append(f"<li>{_inline(um.group(1))}</li>")
            continue
        om = _OL.match(line)
        if om:
            flush_p()
            if list_type != "ol":
                close_list()
                parts.append("<ol>")
                list_type = "ol"
            parts.append(f"<li>{_inline(om.group(2))}</li>")
            continue
        bm = _BQ.match(line)
        if bm:
            flush_p()
            close_list()
            parts.append(f"<blockquote>{_inline(bm.group(1))}</blockquote>")
            continue
        if line.strip() in {"---", "***", "___"}:
            flush_p()
            close_list()
            parts.append("<hr/>")
            continue
        buf.append(line)
    flush_p()
    close_list()
    html_out = "\n".join(parts)
    for i, fence in enumerate(fences):
        html_out = html_out.replace(f"@@FENCE{i}@@", fence)
    return html_out


def _inline(text: str) -> str:
    # Escape first, then re-introduce the few tags we synthesize.
    tokens: List[str] = []

    def stash(html_frag: str) -> str:
        tokens.append(html_frag)
        return f"@@T{len(tokens) - 1}@@"

    work = text
    work = _LINK.sub(
        lambda m: stash(
            f'<a href="{html.escape(m.group(2))}">{html.escape(m.group(1))}</a>'
        ),
        work,
    )
    work = _INLINE_CODE.sub(
        lambda m: stash(f"<code>{html.escape(m.group(1))}</code>"),
        work,
    )
    work = html.escape(work)
    work = re.sub(r"@@T(\d+)@@", lambda m: tokens[int(m.group(1))], work)
    # Bold / italic on the remaining (already escaped) text. Markers are
    # literal asterisks so this is safe.
    work = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", work)
    work = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<em>\1</em>", work)
    return work


def escape(text: str) -> str:
    return html.escape(text or "")
