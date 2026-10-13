"""Handling for untrusted retrieved text (news summaries, filing excerpts).

Defense in depth (VOICE_AND_AGENTS §9): strip markup, collapse whitespace, truncate,
and flag instruction-like content. Flagging never executes or obeys anything — it only
labels the evidence so the report can say the source contained suspicious text.
"""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser

INJECTION_FLAG = "suspected_prompt_injection"

_INJECTION_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bignore (?:all |any )?(?:previous|prior|above|earlier) (?:instructions|prompts?|rules)\b",
        r"\bdisregard (?:all |any )?(?:previous|prior|above) (?:instructions|rules)\b",
        r"\b(?:reveal|show|print|leak|disclose)\b.{0,40}\b(?:system prompt|instructions|api key|secret|password)s?\b",
        r"\b(?:send|share|post|email)\b.{0,40}\b(?:api key|secret|credentials|token)s?\b",
        r"\b(?:call|invoke|use|run)\b.{0,30}\btool\b",
        r"\b(?:execute|place|make|submit)\b.{0,20}\b(?:trade|order)s?\b",
        r"\bbuy \d[\d,]* shares\b",
        r"\byou are now\b",
        r"\bnew instructions?\s*:",
    )
]

_BLOCK_TAGS = {
    "p",
    "div",
    "br",
    "tr",
    "td",
    "th",
    "li",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "table",
    "section",
    "title",
}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self._skip_depth += 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._skip_depth:
            self._skip_depth -= 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self.parts.append(data)


def html_to_text(markup: str) -> str:
    """Visible text from HTML with block boundaries preserved as newlines."""
    parser = _TextExtractor()
    parser.feed(markup)
    parser.close()
    text = "".join(parser.parts)
    text = html.unescape(text).replace("\xa0", " ")
    lines = [re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in text.split("\n")]
    return "\n".join(line for line in lines if line)


def clean_snippet(text: str | None, max_chars: int = 600) -> str | None:
    """Single-line, markup-free, length-bounded excerpt."""
    if not text:
        return None
    plain = html_to_text(text) if "<" in text and ">" in text else text
    plain = re.sub(r"\s+", " ", plain).strip()
    if len(plain) <= max_chars:
        return plain
    cut = plain[:max_chars]
    boundary = cut.rfind(". ")
    if boundary > max_chars * 0.6:
        cut = cut[: boundary + 1]
    return cut.rstrip() + " …"


def detect_injection(text: str | None) -> bool:
    if not text:
        return False
    return any(p.search(text) for p in _INJECTION_PATTERNS)


def flags_for(text: str | None) -> list[str]:
    return [INJECTION_FLAG] if detect_injection(text) else []
