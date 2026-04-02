"""Parse free-form pasted recipe text into the same shape as JSON-LD extraction."""

from __future__ import annotations

import re
from typing import Any


def _parse_yield_from_blob(blob: str) -> float | None:
    m = re.search(
        r"(?i)(\d+(?:\.\d+)?)\s*(?:servings?|serves|people|portions?|makes|yield)\b",
        blob,
    )
    if m:
        try:
            v = float(m.group(1))
            return v if 0 < v <= 100 else None
        except ValueError:
            return None
    return None


def _detect_section_header(line: str) -> str | None:
    s = line.strip().strip("*#").strip()
    s = re.sub(r"^\*\*|\*\*$", "", s).strip()
    if re.match(r"(?i)^notes?\s*[&]\s*tips?", s):
        return "notes"
    m = re.match(
        r"(?i)^(ingredients?|instructions?|directions?|steps?|method|notes?|tips?|substitutions?|recipe\s+notes?)\s*[:.\-]?\s*$",
        s,
    )
    if not m:
        return None
    key = m.group(1).lower()
    if key in ("ingredient", "ingredients"):
        return "ingredients"
    if key in ("instructions", "instruction", "directions", "direction", "steps", "step", "method"):
        return "steps"
    if key in ("notes", "note", "tips", "tip", "substitutions", "substitution", "recipe notes"):
        return "notes"
    return None


def _merge_numbered_step_lines(lines: list[str]) -> list[str]:
    """Join wrapped lines under the same numbered step (1. … 2. …)."""
    if not lines:
        return []
    if not any(re.match(r"^\s*\d+[\).\s]", x) for x in lines):
        return [x.strip() for x in lines if x.strip()]

    out: list[str] = []
    buf: list[str] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if re.match(r"^\s*\d+[\).\s]", line):
            if buf:
                out.append(" ".join(buf))
            buf = [line]
        else:
            if buf:
                buf.append(line)
            else:
                out.append(line)
    if buf:
        out.append(" ".join(buf))
    return out


def parse_pasted_recipe_text(raw: str) -> tuple[dict[str, Any], list[str]]:
    """
    Split pasted text on section headers (Ingredients, Instructions, Notes, …).
    """
    text = raw.replace("\r\n", "\n").strip()
    if len(text) < 10:
        raise ValueError("Paste at least a few lines of recipe text.")

    lines = text.split("\n")
    section = "preamble"
    buckets: dict[str, list[str]] = {
        "preamble": [],
        "ingredients": [],
        "steps": [],
        "notes": [],
    }

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        hdr = _detect_section_header(stripped)
        if hdr:
            section = hdr
            continue
        buckets[section].append(stripped)

    ing_lines = buckets["ingredients"]
    step_lines = buckets["steps"]
    note_lines = buckets["notes"]
    preamble = list(buckets["preamble"])

    # No section headers: split preamble heuristically
    if not ing_lines and not step_lines and not note_lines and len(preamble) >= 3:
        all_lines = list(preamble)
        num_step = re.compile(r"^\s*\d+[\).\s]\s*\S")
        maybe_steps = [ln for ln in all_lines if num_step.match(ln)]
        if len(maybe_steps) >= 2:
            step_set = set(maybe_steps)
            ing_lines = [ln for ln in all_lines if ln not in step_set]
            step_lines = [ln for ln in all_lines if ln in step_set]
            preamble.clear()
        else:
            ing_lines = all_lines
            preamble.clear()

    # Title + description from preamble
    title = "Pasted recipe"
    description: str | None = None
    if preamble:
        first = preamble[0]
        if len(first) <= 160 and "\n" not in first:
            title = first[:500]
            preamble = preamble[1:]
        if preamble:
            description = "\n".join(preamble).strip() or None

    ingredients = [{"order": i, "raw": x} for i, x in enumerate(ing_lines) if x]
    steps_raw = _merge_numbered_step_lines(step_lines) if step_lines else []
    steps = [{"order": i, "text": t[:8000]} for i, t in enumerate(steps_raw) if t]
    notes_text = "\n".join(note_lines).strip() or None

    servings = _parse_yield_from_blob(text)

    warnings: list[str] = []
    if not ingredients:
        warnings.append(
            "No ingredient lines were detected. Use an Ingredients heading or paste lines with amounts."
        )
    if not steps:
        warnings.append(
            "No instruction steps were detected. Use Instructions / Steps or numbered steps (1. … 2. …)."
        )

    recipe: dict[str, Any] = {
        "schemaVersion": 1,
        "title": title,
        "description": description,
        "ingredients": ingredients,
        "steps": steps,
        "notes": notes_text,
        "servings": servings,
        "prepTime": None,
        "cookTime": None,
        "totalTime": None,
        "imageUrl": None,
        "source": {
            "kind": "manual",
            "canonicalUrl": "",
            "displayName": "Pasted text",
        },
    }
    return recipe, warnings
