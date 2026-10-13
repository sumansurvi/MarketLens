"""Statements are shortened at a sentence or word boundary, never mid-word (D-86)."""

from __future__ import annotations

from app.agents.supervisor import clip_text


def test_clip_prefers_the_last_full_sentence() -> None:
    text = "First sentence is here. " * 20 + "A final sentence that runs past the limit and gets cut in comp"
    clipped = clip_text(text, 300)
    assert len(clipped) <= 300 and clipped.endswith("is here.")


def test_clip_falls_back_to_a_whole_word() -> None:
    clipped = clip_text("word " * 100, 52)
    assert clipped.endswith("word…") and len(clipped) <= 52


def test_short_text_is_unchanged() -> None:
    assert clip_text("  Short.  ", 600) == "Short."
