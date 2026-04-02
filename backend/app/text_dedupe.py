"""Deduplicate recipe notes text (HTML often merges heading + WPRM blocks with identical content)."""

from __future__ import annotations

import re


def _norm_para(p: str) -> str:
    return re.sub(r"\s+", " ", p.strip().lower())


def dedupe_note_paragraphs(text: str) -> str:
    """Remove repeated paragraphs (same text after normalizing whitespace)."""
    if not text or not text.strip():
        return text
    parts = re.split(r"\n\s*\n+", text.strip())
    seen: set[str] = set()
    out: list[str] = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        key = _norm_para(p)
        if not key:
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return "\n\n".join(out)


def merge_note_sources(heading_notes: str | None, wprm_notes: str | None) -> str | None:
    """
    Combine notes from section headings vs WPRM containers; drop one if it is
    a duplicate of the other (common when the H2 wraps the same .wprm-recipe-notes node).
    """
    if not heading_notes and not wprm_notes:
        return None
    if not wprm_notes:
        return heading_notes
    if not heading_notes:
        return wprm_notes
    h = heading_notes.strip()
    w = wprm_notes.strip()
    hn = _norm_para(h)
    wn = _norm_para(w)
    if wn in hn:
        return h
    if hn in wn:
        return w
    if len(wn) > 200 and wn[: min(600, len(wn))] in hn:
        return h
    if len(hn) > 200 and hn[: min(600, len(hn))] in wn:
        return w
    merged = f"{h}\n\n{w}"
    return dedupe_note_paragraphs(merged)
