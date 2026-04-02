"""Resolve 'see notes' placeholders using recipe notes / description text (page context)."""

from __future__ import annotations

import re
from typing import Any

from app.text_dedupe import dedupe_note_paragraphs

SEE_NOTE_REF = re.compile(
    r"(?i)\bsee\s+(notes?|below|above|details)\b|\(\s*see\s+notes?\s*\)|\*\s*see\s+notes?\s*\*"
)


def _context_blob(notes: str | None, description: str | None) -> str:
    parts: list[str] = []
    if notes and notes.strip():
        parts.append(notes.strip())
    if description and description.strip():
        parts.append(description.strip())
    return "\n\n".join(parts)


def _needs_quantity_from_notes(line: str) -> bool:
    """Ingredient lines that reference optional gravy/thickener but have no leading amount."""
    s = line.strip()
    if "(from notes)" in s.lower() or "per notes" in s.lower():
        return False
    if re.match(r"^\s*[\d./]", s):
        return False
    if re.match(r"^\s*\d+\s+to\s+\d+\b", s, re.I):
        return False
    low = s.lower()
    if not re.search(r"\b(flour|cornstarch|butter|starch)\b", low):
        return False
    if re.search(r"\b(optional|gravy|roux|thick|slurry|dust|dusting|below)\b", low):
        return True
    return False


def _pick_gravy_notes_block(blob: str) -> str:
    """
    Prefer the first gravy method (often slurry) when multiple alternatives appear
    (slurry vs roux) to avoid double-counting mutually exclusive options.
    """
    low = blob.lower()
    m_gravy = re.search(r"(?i)to\s+make\s+gravy|slurry\s+method", low)
    m_roux = list(
        re.finditer(
            r"(?i)(?:^|\n)\s*(?:to\s+)?make\s+[^\n]*\broux\b|with\s+a\s+roux|roux-based",
            low,
        )
    )
    if m_gravy:
        start = m_gravy.start()
        end = len(blob)
        for mr in m_roux:
            if mr.start() > start:
                end = mr.start()
                break
        return blob[start:end]
    if m_roux:
        return blob[m_roux[0].start() :]
    return blob


def extract_tbsp_lines_from_text(block: str) -> list[str]:
    """Pull quantified lines like '2 tbsp cornstarch' from a notes subsection."""
    out: list[str] = []
    for m in re.finditer(
        r"(?i)(\d+)\s*(?:tbsp|tablespoons?)\s+(?:of\s+)?([^.\n;]+?)(?=\.|;|\n\s*\n|$)",
        block,
    ):
        q, tail = m.group(1), m.group(2).strip()
        main = re.split(r"\s+or\s+", tail, maxsplit=1)[0].strip()
        main = re.sub(r"^(the|a|an)\s+", "", main, flags=re.I)
        main = main.strip(" ,()")
        if 2 <= len(main) <= 90:
            out.append(f"{q} tbsp {main}")
    return out


def supplemental_quantified_lines_from_notes(blob: str) -> list[str]:
    """Nutrition-only lines derived from notes (gravy, thickeners, etc.)."""
    if not blob.strip():
        return []
    block = _pick_gravy_notes_block(blob)
    return extract_tbsp_lines_from_text(block)


def _is_gravy_placeholder_line(line: str, blob: str) -> bool:
    """Flour/cornstarch/butter lines that defer amounts to notes."""
    if "(from notes)" in line.lower() or "per notes" in line.lower():
        return False
    if not supplemental_quantified_lines_from_notes(blob):
        return False
    low = line.lower()
    if not re.search(r"\b(flour|cornstarch|butter|starch)\b", low):
        return False
    if SEE_NOTE_REF.search(line):
        return True
    if _needs_quantity_from_notes(line):
        return True
    return False


def _try_wine_or_broth_substitution(line: str, blob: str) -> str | None:
    """When notes say wine can be replaced with broth, model broth for nutrition."""
    if "wine" not in line.lower() or not SEE_NOTE_REF.search(line):
        return None
    if not re.search(
        r"(?i)substitut\w+.*\b(broth|stock)\b|replace.*\b(broth|stock)\b|instead.*\b(broth|stock)\b",
        blob,
    ):
        return None
    m = re.match(
        r"(?i)^\s*([\d./\s]+)\s*(cups?|cup|tablespoons?|tbsp|teaspoons?|tsp)\b",
        line.strip(),
    )
    if m:
        return f"{m.group(1).strip()} {m.group(2)} beef broth (per notes, replaces wine)"
    return None


def enrich_ingredient_lines_for_nutrition(
    ingredients: list[str],
    *,
    notes: str | None = None,
    description: str | None = None,
) -> list[str]:
    """
    Expand 'see notes' / unquantified optional gravy lines using notes + description.
    Replaces wine-with-notes using broth when notes specify; collapses gravy placeholders
    into quantified lines from notes.
    """
    blob = _context_blob(notes, description)
    extras = supplemental_quantified_lines_from_notes(blob)
    out: list[str] = []
    gravy_replaced = False

    for line in ingredients:
        raw = line.strip()
        if not raw:
            continue

        if blob and SEE_NOTE_REF.search(raw):
            sub = _try_wine_or_broth_substitution(raw, blob)
            if sub:
                out.append(sub)
                continue

        if blob and extras and _is_gravy_placeholder_line(raw, blob):
            if not gravy_replaced:
                out.append(" and ".join(extras) + " (optional gravy, from notes)")
                gravy_replaced = True
            continue

        out.append(raw)

    return out


def _gravy_instruction_excerpt(notes: str | None, description: str | None) -> str:
    """Text from notes to append to a step that references gravy / below / see notes."""
    blob = _context_blob(notes, description)
    if not blob.strip():
        return ""
    block = _pick_gravy_notes_block(blob)
    if len(block.strip()) < 12:
        return ""
    return block.strip()


_STEP_NOTES_TRIGGER = re.compile(
    r"(?i)\b(serve\s+with|make\s+gravy|gravy|below\)|see\s+notes?|if\s+desired|juices?\s+or)\b"
)


def merge_instruction_steps_with_notes(
    steps: list[dict[str, Any]],
    notes: str | None,
    description: str | None,
) -> list[dict[str, Any]]:
    """Append relevant note paragraphs to steps that reference gravy / notes / below."""
    excerpt = _gravy_instruction_excerpt(notes, description)
    if not excerpt:
        return [dict(s) for s in steps if isinstance(s, dict)]

    out: list[dict[str, Any]] = []
    merged_once = False
    for s in steps:
        if not isinstance(s, dict):
            continue
        text = str(s.get("text", ""))
        if not merged_once and _STEP_NOTES_TRIGGER.search(text):
            combined = text.rstrip() + "\n\nFrom notes —\n" + excerpt
            out.append({**s, "text": combined[:8000]})
            merged_once = True
        else:
            out.append(dict(s))

    if not merged_once:
        n = len(out)
        out.append(
            {
                "order": n,
                "text": ("Gravy / finishing (from notes)\n\n" + excerpt)[:8000],
            }
        )

    for i, s in enumerate(out):
        s["order"] = i
    return out


def enrich_recipe_display_from_notes(recipe: dict[str, Any]) -> None:
    """
    Mutate recipe in place: resolve ingredient placeholders from notes and merge note
    instructions into steps. recipe.notes is left unchanged for the Notes panel.
    """
    raw_notes = recipe.get("notes")
    if isinstance(raw_notes, str) and raw_notes.strip():
        recipe["notes"] = dedupe_note_paragraphs(raw_notes)

    notes = recipe.get("notes")
    desc = recipe.get("description")
    if not (notes and str(notes).strip()) and not (desc and str(desc).strip()):
        return

    ingredients = recipe.get("ingredients") or []
    lines = [str(x.get("raw", "")) for x in ingredients if isinstance(x, dict)]
    if not lines:
        return

    enriched_lines = enrich_ingredient_lines_for_nutrition(
        lines,
        notes=notes if isinstance(notes, str) else None,
        description=desc if isinstance(desc, str) else None,
    )
    recipe["ingredients"] = [
        {"order": i, "raw": r} for i, r in enumerate(enriched_lines)
    ]

    steps = recipe.get("steps") or []
    if steps:
        recipe["steps"] = merge_instruction_steps_with_notes(
            [s for s in steps if isinstance(s, dict)],
            notes if isinstance(notes, str) else None,
            desc if isinstance(desc, str) else None,
        )

