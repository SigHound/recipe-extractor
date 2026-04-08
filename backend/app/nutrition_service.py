"""Nutrition estimates: prefer USDA FoodData Central (free API key), else Edamam (free tier)."""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from curl_cffi import requests as curl_requests
from curl_cffi.requests import RequestsError

from app.config import Settings
from app.note_enrichment import enrich_ingredient_lines_for_nutrition
from app.nutrition_cache import (
    normalize_cache_key,
    try_get_cached_per_100g,
    try_save_nutrition_cache,
)

FDC_SEARCH_URL = "https://api.nal.usda.gov/fdc/v1/foods/search"
FDC_FOOD_URL = "https://api.nal.usda.gov/fdc/v1/food"

EDAMAM_URL = "https://api.edamam.com/api/nutrition-details"

# Cooked rice: ~158g = 1 US cup (USDA); recipes often imply a fuller plate — use generous defaults.
_RICE_COOKED_G_PER_CUP: float = 200.0
_RICE_COOKED_G_NO_AMOUNT: float = 235.0  # ~1.5 cups — typical side / “with rice” line

# Output ids aligned with the frontend (same as prior Edamam keys where possible)
NUTRIENT_DISPLAY_ORDER: list[tuple[str, str, str | None]] = [
    ("PROCNT", "Protein", "g"),
    ("CHOCDF", "Carbohydrate", "g"),
    ("FAT", "Fat", "g"),
    ("FIBTG", "Fiber", "g"),
    ("SUGAR", "Sugars", "g"),
    ("NA", "Sodium", "mg"),
    ("CHOLE", "Cholesterol", "mg"),
    ("VITA_RAE", "Vitamin A", "µg"),
    ("VITC", "Vitamin C", "mg"),
    ("CA", "Calcium", "mg"),
    ("FE", "Iron", "mg"),
    ("K", "Potassium", "mg"),
]

# USDA FoodData nutrient number (legacy) -> our id
USDA_NUM_TO_ID: dict[str, str] = {
    "208": "ENERC_KCAL",  # Energy
    "203": "PROCNT",  # Protein
    "204": "FAT",  # Total lipid (fat)
    "205": "CHOCDF",  # Carbohydrate, by difference
    "291": "FIBTG",  # Fiber, total dietary
    "269": "SUGAR",  # Sugars, total including NLEA
    "307": "NA",  # Sodium, Na
    "601": "CHOLE",  # Cholesterol
    "318": "VITA_RAE",  # Vitamin A, RAE
    "401": "VITC",  # Vitamin C, total ascorbic acid
    "301": "CA",  # Calcium, Ca
    "303": "FE",  # Iron, Fe
    "306": "K",  # Potassium, K
}

# FDC `nutrient.id` when legacy `number` is missing (common in Branded / some Survey foods).
USDA_FDC_NUTRIENT_ID: dict[int, str] = {
    1008: "ENERC_KCAL",
    1003: "PROCNT",
    1004: "FAT",
    1005: "CHOCDF",
    1079: "FIBTG",
    2000: "SUGAR",
    1093: "NA",
    1253: "CHOLE",
    1106: "VITA_RAE",
    1162: "VITC",
    1087: "CA",
    1089: "FE",
    1092: "K",
}

def _friendly_usda_display_name(usda_search_query: str, fallback_line: str = "") -> str:
    """
    Human-readable label from the USDA search string: title case, drop common
    database boilerplate (e.g. raw, lean, meat).
    """
    base = (usda_search_query or "").strip() or (fallback_line or "").strip()
    if not base:
        return "Ingredient"
    s = re.sub(
        r"\b(?:raw|lean|meat|boneless|skinless|trimmed|unprepared|drained)\b",
        " ",
        base,
        flags=re.I,
    )
    s = re.sub(r"\bready\s+to\s+eat\b", " ", s, flags=re.I)
    s = re.sub(r"\s+", " ", s).strip()
    if not s:
        s = base[:80]
    return s.title()


def _friendly_edamam_ingredient_name(text: str) -> str:
    """Short display name from Edamam ingredient text (strip notes in parens / after comma)."""
    s = (text or "").strip()
    s = re.sub(r"\([^)]*\)", "", s).strip()
    parts = re.split(r"[,;]", s, maxsplit=1)
    s = (parts[0] if parts else s).strip()
    if len(s) > 56:
        s = s[:53].rsplit(" ", 1)[0] + "…"
    return s.title() if s else "Ingredient"


def _part_to_row_nutrients(part: dict[str, float]) -> tuple[float | None, list[dict[str, Any]]]:
    """Calories (full recipe line) + nutrient rows for API (same ids as top-level label)."""
    kcal = _line_kcal_from_part(part)
    out: list[dict[str, Any]] = []
    for nid, default_label, unit in NUTRIENT_DISPLAY_ORDER:
        if nid == "ENERC_KCAL":
            continue
        q = part.get(nid)
        if q is None:
            continue
        out.append(
            {
                "id": nid,
                "label": default_label,
                "quantity": float(q),
                "unit": unit or "",
            }
        )
    return (float(kcal) if kcal is not None else None, out)


EDAMAM_KEYS = [
    ("PROCNT", "Protein", "g"),
    ("CHOCDF", "Carbohydrate", "g"),
    ("FAT", "Fat", "g"),
    ("FIBTG", "Fiber", "g"),
    ("SUGAR", "Sugars", "g"),
    ("NA", "Sodium", "mg"),
    ("CHOLE", "Cholesterol", "mg"),
    ("VITA_RAE", "Vitamin A", "µg"),
    ("VITC", "Vitamin C", "mg"),
    ("CA", "Calcium", "mg"),
    ("FE", "Iron", "mg"),
    ("K", "Potassium", "mg"),
]


def _client_usda(client_key: str | None) -> str | None:
    if client_key is None:
        return None
    s = client_key.strip()
    return s if s else None


def _client_edamam_pair(
    app_id: str | None, app_key: str | None
) -> tuple[str, str] | None:
    if app_id is None or app_key is None:
        return None
    i, k = app_id.strip(), app_key.strip()
    return (i, k) if i and k else None


def analyze_recipe_nutrition(
    title: str,
    ingredient_lines: list[str],
    settings: Settings,
    *,
    notes: str | None = None,
    description: str | None = None,
    client_usda_api_key: str | None = None,
    client_edamam_app_id: str | None = None,
    client_edamam_app_key: str | None = None,
) -> dict[str, Any]:
    ingr = [ln.strip() for ln in ingredient_lines if ln and ln.strip()]
    ingr = enrich_ingredient_lines_for_nutrition(
        ingr, notes=notes, description=description
    )
    if not ingr:
        return {
            "ok": False,
            "source": None,
            "message": "No ingredients to analyze.",
            "nutrients": [],
            "calories": None,
            "calorie_breakdown": [],
            "ingredient_nutrient_breakdown": [],
        }

    cu = _client_usda(client_usda_api_key)
    ce = _client_edamam_pair(client_edamam_app_id, client_edamam_app_key)

    # Browser overrides win over .env; USDA preferred when both client keys exist.
    if cu:
        merged = settings.model_copy(update={"usda_api_key": cu})
        return _analyze_usda(ingr, merged)
    if ce:
        i, k = ce
        merged = settings.model_copy(update={"edamam_app_id": i, "edamam_app_key": k})
        return _analyze_edamam(title, ingr, merged)

    if settings.usda_api_key:
        return _analyze_usda(ingr, settings)

    if settings.edamam_app_id and settings.edamam_app_key:
        return _analyze_edamam(title, ingr, settings)

    return {
        "ok": False,
        "source": None,
        "message": (
            "Nutrition is disabled. Set USDA_API_KEY (free from FoodData Central: "
            "https://fdc.nal.usda.gov/api-key-signup) or EDAMAM_APP_ID + EDAMAM_APP_KEY "
            "(Edamam free developer tier), or use “Update API Key” in the app."
        ),
        "nutrients": [],
        "calories": None,
        "calorie_breakdown": [],
        "ingredient_nutrient_breakdown": [],
    }


def validate_nutrition_api_keys(
    usda_api_key: str | None,
    edamam_app_id: str | None,
    edamam_app_key: str | None,
) -> dict[str, Any]:
    """
    Call provider APIs with the given credentials. Validates only non-empty subsets.
    At least one of: USDA key, or both Edamam fields, must be provided.
    """
    cu = _client_usda(usda_api_key)
    ce = _client_edamam_pair(edamam_app_id, edamam_app_key)
    if not cu and not ce:
        return {
            "ok": False,
            "message": "Enter a USDA API key or both Edamam App ID and App Key.",
        }

    errors: list[str] = []

    if cu:
        ok, err = _try_usda_api_key(cu)
        if not ok:
            errors.append(err or "USDA API key was rejected.")

    if ce:
        i, k = ce
        ok, err = _try_edamam_keys(i, k)
        if not ok:
            errors.append(err or "Edamam credentials were rejected.")

    if errors:
        return {"ok": False, "message": " ".join(errors)}

    return {"ok": True, "message": None}


def _try_usda_api_key(api_key: str) -> tuple[bool, str]:
    search_payload = {
        "query": "rice",
        "pageSize": 1,
        "dataType": ["Foundation", "SR Legacy"],
    }
    try:
        r = curl_requests.post(
            FDC_SEARCH_URL,
            params={"api_key": api_key},
            json=search_payload,
            impersonate="chrome131",
            timeout=20.0,
        )
    except RequestsError as e:
        return False, f"USDA request failed: {e!s}"
    if r.status_code == 200:
        return True, ""
    try:
        data = r.json()
        if isinstance(data, dict):
            err = data.get("error") or {}
            msg = err.get("message") if isinstance(err, dict) else None
            if msg:
                return False, f"USDA: {msg}"
    except Exception:
        pass
    return False, f"USDA returned HTTP {r.status_code}."


def _try_edamam_keys(app_id: str, app_key: str) -> tuple[bool, str]:
    try:
        r = curl_requests.post(
            EDAMAM_URL,
            params={"app_id": app_id, "app_key": app_key},
            json={"title": "Validation", "ingr": ["100 g cooked rice"]},
            impersonate="chrome131",
            timeout=25.0,
        )
    except RequestsError as e:
        return False, f"Edamam request failed: {e!s}"
    if r.status_code == 200:
        return True, ""
    try:
        data = r.json()
        if isinstance(data, dict) and data.get("message"):
            return False, f"Edamam: {data['message']}"
    except Exception:
        pass
    return False, f"Edamam returned HTTP {r.status_code}."


def _leading_quantity(line: str) -> tuple[float, str]:
    """Parse leading numeric amount; return (quantity, rest of string). No number → (0.0, line)."""
    s = line.strip()
    m = re.match(
        r"^\s*(\d+)\s+(\d+)\s*/\s*(\d+)|^\s*(\d+)\s*/\s*(\d+)|^\s*(\d+(?:\.\d+)?)",
        s,
    )
    if not m:
        return 0.0, s
    if m.group(1) is not None:
        den = int(m.group(3))
        if den == 0:
            return 0.0, s
        q = int(m.group(1)) + int(m.group(2)) / den
    elif m.group(4) is not None:
        den = int(m.group(5))
        if den == 0:
            return 0.0, s
        q = int(m.group(4)) / den
    else:
        q = float(m.group(6))
    rest = s[m.end() :].strip()
    return q, rest


def _extract_parens_grams(line: str) -> float | None:
    """Explicit grams in parentheses, e.g. (180-190g) or (200 g)."""
    m = re.search(r"\(\s*(\d+)\s*-\s*(\d+)\s*g\s*\)", line, re.I)
    if m:
        return (int(m.group(1)) + int(m.group(2))) / 2.0
    m = re.search(r"\(\s*(\d+(?:\.\d+)?)\s*g\s*\)", line, re.I)
    if m:
        return float(m.group(1))
    return None


def _is_oil_or_fat_line(low: str) -> bool:
    # Word-boundary match avoids substring false positives (e.g. "boil" contains "oil").
    return bool(
        re.search(
            r"\b(oil|butter|ghee|shortening|lard|mayo|mayonnaise)\b",
            low,
            re.I,
        )
    )


def _is_salt_line(low: str) -> bool:
    return bool(re.search(r"\bsalt\b", low))


def _standard_serving_grams(low: str) -> float | None:
    """
    One typical US-style serving (grams) when the recipe line has no numeric amount.
    Returns None if we should fall back to the generic small default instead.
    """
    # More specific matches first (avoid "rice" in "rice vinegar", "bread" in "breadcrumbs" logic, etc.)
    if re.search(r"\b(rice vinegar|rice wine|rice paper|hoisin)\b", low):
        return None
    if re.search(r"\b(oat milk|almond milk|soy milk)\b", low):
        return None
    if re.search(r"\b(breadcrumb|breadcrumbs|panko)\b", low):
        return 20.0
    if re.search(r"\b(for dusting|to dust|for rolling)\b", low) and re.search(
        r"\b(flour)\b", low
    ):
        return 8.0
    if re.search(
        r"\b(flour|cornmeal|semolina)\b", low
    ) and not re.search(r"\b(cooked|batter|dough)\b", low):
        return None

    if re.search(r"\b(bagel|bagels)\b", low):
        return 95.0
    if re.search(r"\b(english muffin|english muffins)\b", low):
        return 60.0
    if re.search(r"\b(croissant|croissants)\b", low):
        return 65.0
    if re.search(r"\b(tortilla|tortillas|wrap|wraps|pita|pitas)\b", low):
        return 45.0
    if re.search(
        r"\b(flatbread|roti|naan|paratha|chapati|chapathi|puri|bhatura|kulcha)\b",
        low,
    ):
        return 45.0
    if re.search(r"\b(slice of toast|bread slice)\b", low) or (
        re.search(r"\bslices?\b", low)
        and re.search(r"\b(bread|toast|rye|sourdough|whole wheat|multigrain)\b", low)
    ):
        return 35.0
    if re.search(r"\b(bun|buns|sub roll|hoagie|dinner roll)\b", low):
        return 55.0
    if re.search(r"\b(bread|loaf|loaves)\b", low) and not re.search(
        r"\b(crumb|breadcrumbs|panko|flour)\b", low
    ):
        return 45.0

    if re.search(r"\b(cauli rice|cauliflower rice)\b", low):
        return 100.0
    if re.search(
        r"\b(basmati|jasmine|arborio|brown rice|white rice|wild rice|\brice\b|risotto)\b",
        low,
    ):
        return _RICE_COOKED_G_NO_AMOUNT
    if re.search(
        r"\b(pasta|spaghetti|linguine|fettuccine|penne|rigatoni|macaroni|noodles|ramen|udon|soba|orzo|lasagna)\b",
        low,
    ):
        if re.search(r"\b(dry|uncooked)\b", low):
            return 56.0
        return 140.0
    if re.search(r"\b(quinoa|couscous|bulgur|farro|barley)\b", low):
        return 150.0
    if re.search(r"\b(oat|oats|oatmeal)\b", low):
        return 45.0
    if re.search(r"\b(polenta|grits)\b", low):
        return 150.0

    if re.search(r"\b(sweet potato|sweet potatoes|yam|yams)\b", low):
        return 130.0
    if re.search(r"\b(potato|potatoes)\b", low):
        return 150.0

    if re.search(r"\b(crackers?)\b", low):
        return 15.0
    if re.search(r"\b(cereal)\b", low):
        return 40.0

    return None


def _estimate_grams(line: str) -> float:
    """
    Heuristic mass (grams) for one ingredient line. USDA nutrients are per 100g;
    we scale by grams/100, not by a bare leading integer (which wrongly implied 100g units).
    If there is no leading amount, common sides (rice, bread, pasta, etc.) use one standard serving.
    """
    pg = _extract_parens_grams(line)
    if pg is not None:
        return pg

    s0 = line.strip()
    # "3 to 4 lb chuck roast" / "3–4 lb beef" — average pounds (avoid only stripping "3 " and
    # leaving "to 4 lb..." which breaks search + underestimates grams).
    m_lb = re.match(
        r"^\s*(\d+)\s+to\s+(\d+)\s*(?:lb|lbs?|pounds?)\b",
        s0,
        re.I,
    )
    if not m_lb:
        m_lb = re.match(
            r"^\s*(\d+)\s*[-–]\s*(\d+)\s*(?:lb|lbs?|pounds?)\b",
            s0,
            re.I,
        )
    if m_lb:
        avg_lb = (int(m_lb.group(1)) + int(m_lb.group(2))) / 2.0
        return avg_lb * 453.592

    q, rest = _leading_quantity(line)
    low = line.lower()
    rlow = rest.lower().lstrip(" ,(-").strip()

    if q <= 0:
        if re.search(r"\b(to taste|pinch|dash)\b", low):
            return 0.35
        if _is_salt_line(low) and len(low) < 40:
            return 0.5
        serving = _standard_serving_grams(low)
        if serving is not None:
            return serving
        return 5.0

    # Longest / most specific unit patterns first
    unit_patterns: list[tuple[str, str]] = [
        (r"^(?:fluid\s+ounces?|fl\.?\s*oz)\b", "floz"),
        (r"^(?:tablespoons?|tbsps?|tbsp)\b", "tbsp"),
        (r"^(?:teaspoons?|tsps?|tsp)\b", "tsp"),
        (r"^(?:cups?)\b", "cup"),
        (r"^(?:milliliters?|millilitres?|ml)\b", "ml"),
        (r"^(?:liters?|litres?)\b(?!er\b)", "l"),
        (r"^(?:kilograms?|kg)\b", "kg"),
        (r"^(?:grams?|g)\b", "g"),
        (r"^(?:pounds?|lbs?)\b", "lb"),
        (r"^(?:ounces?|oz)\b", "oz"),
        (r"^cloves?\b", "clove"),
        (r"^inch(?:es)?\b", "inch"),
    ]

    kind: str | None = None
    for pat, k in unit_patterns:
        if re.match(pat, rlow, re.I):
            kind = k
            break

    if kind is None:
        # e.g. "4 garlic cloves" — quantity before word "cloves" sometimes appears as "4 ... cloves"
        if re.search(r"\bcloves?\b", rlow) or re.search(r"\bcloves?\b", low):
            if "garlic" in low:
                return q * 3.0
        if re.search(r"\binch\b", rlow) or re.search(r"\binches\b", rlow):
            return q * 15.0
        if re.match(r"^slices?\b", rlow, re.I):
            return q * 35.0
        if re.match(r"^pieces?\b", rlow, re.I):
            return q * 40.0
        std = _standard_serving_grams(low)
        if std is not None:
            return q * std
        return q * 25.0

    if kind == "g":
        return q
    if kind == "kg":
        return q * 1000.0
    if kind == "lb":
        return q * 453.592
    if kind == "oz":
        return q * 28.3495
    if kind == "ml":
        if _is_oil_or_fat_line(low):
            return q * 0.92
        return q * 1.0
    if kind == "l":
        ml = q * 1000.0
        if _is_oil_or_fat_line(low):
            return ml * 0.92
        return ml
    if kind == "floz":
        ml = q * 29.5735
        if _is_oil_or_fat_line(low):
            return ml * 0.92
        return ml

    if kind == "tsp":
        if _is_salt_line(low):
            return q * 6.0
        if _is_oil_or_fat_line(low):
            return q * 4.92892 * 0.92
        return q * 2.3

    if kind == "tbsp":
        if _is_oil_or_fat_line(low):
            return q * 14.7868 * 0.92
        if "fresh" in low and any(h in low for h in ("ginger", "turmeric", "garlic")):
            return q * 9.0
        return q * 12.0

    if kind == "cup":
        if _is_oil_or_fat_line(low):
            return q * 218.0
        if "lentil" in low:
            return q * 190.0
        if "flour" in low:
            return q * 120.0
        if "rice" in low:
            # Dry rice ~185g/cup vs cooked; recipes usually mean cooked when serving
            if re.search(r"\b(dry|uncooked)\b", low):
                return q * 185.0
            return q * _RICE_COOKED_G_PER_CUP
        if "oat" in low:
            return q * 185.0
        if any(x in low for x in ("bean", "lentil", "split")):
            return q * 195.0
        if any(x in low for x in ("sugar", "salt")):
            return q * 200.0
        return q * 236.588

    if kind == "clove":
        if "garlic" in low:
            return q * 3.0
        return q * 3.0

    if kind == "inch":
        return q * 15.0

    return q * 25.0


def _split_compound_line(line: str) -> list[str]:
    """
    Split long 'serve with A and B' lines into separate USDA lookups so totals
    aren't a single bad match (e.g. rice + flatbread counted once).
    """
    raw = line.strip()
    if len(raw) < 24:
        return [raw]
    s = re.sub(
        r"^(?:for\s+serving|optional|serve\s+with)\s*:\s*",
        "",
        raw,
        flags=re.I,
    )
    parts = re.split(r"\s+and\s+", s, flags=re.I)
    if len(parts) < 2:
        return [raw]
    trimmed: list[str] = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        p = re.sub(r"\s+such\s+as\s+.*$", "", p, flags=re.I)
        p = re.sub(r"\s+like\s+.*$", "", p, flags=re.I)
        p = p.strip(" ,;—-")
        if len(p) >= 8:
            trimmed.append(p)
    if len(trimmed) >= 2 and all(len(x) >= 8 for x in trimmed):
        return trimmed
    return [raw]


def _grams_scale_for_usda_line(line: str) -> float:
    """Multiply per-100g USDA values by this to get amounts for the line."""
    g = _estimate_grams(line)
    return max(g, 0.05) / 100.0


def _search_query(line: str) -> str:
    """Strip quantity for USDA search; must handle '3 to 4 lb beef' so we don't search 'to 4 lb beef'."""
    s = line.strip()
    m = re.match(
        r"^\s*\d+\s+to\s+\d+\s*(?:lb|lbs?|pounds?|oz|ounces?|g|kg|grams?)\s*",
        s,
        re.I,
    )
    if m:
        s = s[m.end() :].strip()
    else:
        m2 = re.match(
            r"^\s*\d+\s*[-–]\s*\d+\s*(?:lb|lbs?|pounds?|oz|ounces?|g|kg)\s*",
            s,
            re.I,
        )
        if m2:
            s = s[m2.end() :].strip()
        else:
            s = re.sub(
                r"^\s*[\d./\s\u00BC-\u00BE]+\s*",
                "",
                s,
            )
    s = re.sub(
        r"^\s*(?:fl\.?\s*oz|fluid\s*ounces?|cups?|tablespoons?|teaspoons?|tbsp|tsp|oz|g|kg|lb|ml|l|liters?|litres?)\b\s*",
        "",
        s,
        flags=re.I,
    )
    q = s.strip()
    return (q[:200] if q else line.strip()[:200]) or "food"


def _usda_fdc_search_query(line: str) -> str:
    """
    USDA search terms for the *ingredient line* (not raw ingredient name alone).
    Biases toward cooked rice / prepared pasta / baked bread so per-100g data matches
    our gram estimates (cooked rice / prepared pasta / baked bread, not raw grain).
    """
    base = _search_query(line).strip()
    low = line.lower()
    if not base:
        base = line.strip()[:160] or "food"

    if re.search(r"\b(rice vinegar|rice wine|rice paper|hoisin)\b", low):
        return base[:200]

    # Cooking oils: "light" / "extra light" on olive oil means mild flavor, not fewer calories.
    # USDA search for "light olive oil" often returns a branded or odd first hit with no mapped
    # energy/macros → grams estimate looks right but calories stay blank.
    if re.search(r"\bolive\b", low) and re.search(r"\boil\b", low):
        return "olive oil"[:200]

    # Rice: raw grain ~365 kcal/100g vs cooked ~130 — we assume cooked unless stated dry
    if re.search(
        r"\b(basmati|jasmine|arborio|brown rice|white rice|wild rice|\brice\b|risotto)\b",
        low,
    ) and not re.search(r"\b(cauliflower rice)\b", low):
        if re.search(r"\b(dry|uncooked)\b", low):
            combined = f"{base} raw uncooked"
            return re.sub(r"\s+", " ", combined).strip()[:200]
        combined = f"{base} cooked"
        return re.sub(r"\s+", " ", combined).strip()[:200]

    # Pasta: cooked nutrient profile unless explicitly dry
    if re.search(
        r"\b(pasta|spaghetti|linguine|fettuccine|penne|rigatoni|macaroni|noodles|ramen|udon|soba|orzo|lasagna)\b",
        low,
    ):
        if re.search(r"\b(dry|uncooked)\b", low):
            return base[:200]
        combined = f"{base} cooked"
        return re.sub(r"\s+", " ", combined).strip()[:200]

    # South Asian flatbreads — match prepared bread, not atta flour
    if m := re.search(r"\b(naan|roti|paratha|chapati|kulcha|bhatura)\b", low, re.I):
        return f"{m.group(1).lower()} bread"[:200]

    if re.search(r"\b(flatbread)\b", low) and not re.search(r"\b(flour)\b", low):
        return "flatbread ready to eat"[:200]

    if re.search(r"\b(tortilla|pita)\b", low) and not re.search(r"\b(flour)\b", low):
        combined = f"{base} wheat tortilla"
        return re.sub(r"\s+", " ", combined).strip()[:200]

    if re.search(r"\bslices?\b", low) and re.search(r"\bbread\b", low) and not re.search(
        r"\b(flour|crumb)\b", low
    ):
        if "sourdough" in low:
            return "sourdough bread sliced"[:200]
        if "rye" in low:
            return "rye bread sliced"[:200]
        return "bread white sliced"[:200]

    if re.search(r"\b(whole wheat bread|multigrain)\b", low) and not re.search(
        r"\b(flour|crumb)\b", low
    ):
        combined = f"{base} sliced bread"
        return re.sub(r"\s+", " ", combined).strip()[:200]

    if re.search(r"\b(slice of toast|toast)\b", low) and not re.search(r"\bbread\b", low):
        return "white bread toast"[:200]

    # Uncooked beef roasts / large cuts (avoid matching "beef broth", etc.)
    if re.search(r"\b(broth|stock|bouillon|consommé|consomme)\b", low):
        return base[:200]
    if re.search(r"\b(chuck|arm)\s+roast\b", low) or (
        re.search(r"\bchuck\b", low) and re.search(r"\broast\b", low)
    ):
        return "beef chuck roast raw"[:200]
    if re.search(r"\bbrisket\b", low):
        return "beef brisket flat half lean raw"[:200]
    if re.search(r"\b(sirloin|rump|round)\s+roast\b", low):
        return "beef round roast raw lean"[:200]

    if re.search(r"\b(bread|bun|roll|loaf|baguette)\b", low) and not re.search(
        r"\b(flour|crumb|panko|breadcrumbs)\b",
        low,
    ):
        combined = f"{base} bread ready to eat"
        return re.sub(r"\s+", " ", combined).strip()[:200]

    return base[:200]


def _fdc_get_json(url: str, params: dict[str, Any]) -> dict[str, Any] | None:
    try:
        r = curl_requests.get(
            url,
            params=params,
            impersonate="chrome131",
            timeout=25.0,
        )
    except RequestsError:
        return None
    if r.status_code >= 400:
        return None
    try:
        return r.json()
    except Exception:
        return None


def _nutrients_from_fdc_food(data: dict[str, Any]) -> dict[str, float]:
    out: dict[str, float] = {}
    for fn in data.get("foodNutrients") or []:
        if not isinstance(fn, dict):
            continue
        n = fn.get("nutrient")
        if not isinstance(n, dict):
            n = {}
        nid: str | None = None
        num = str(n.get("number", "") or "").strip()
        if num in USDA_NUM_TO_ID:
            nid = USDA_NUM_TO_ID[num]
        if nid is None and n.get("id") is not None:
            try:
                nid = USDA_FDC_NUTRIENT_ID.get(int(n["id"]))
            except (TypeError, ValueError):
                pass
        if nid is None and fn.get("nutrientId") is not None:
            try:
                nid = USDA_FDC_NUTRIENT_ID.get(int(fn["nutrientId"]))
            except (TypeError, ValueError):
                pass
        if not nid:
            continue
        amt = fn.get("amount")
        if amt is None:
            amt = fn.get("value")
        if amt is None:
            continue
        try:
            out[nid] = out.get(nid, 0.0) + float(amt)
        except (TypeError, ValueError):
            continue
    return out


def _atwater_kcal_from_scaled_macros(part: dict[str, float]) -> float | None:
    """When FDC omits energy, estimate kcal from protein/carbs/fat (grams for this line)."""
    p = part.get("PROCNT")
    f = part.get("FAT")
    c = part.get("CHOCDF")
    if p is None and f is None and c is None:
        return None
    p = float(p or 0)
    f = float(f or 0)
    c = float(c or 0)
    if p == 0 and f == 0 and c == 0:
        return None
    return 4.0 * c + 4.0 * p + 9.0 * f


def _line_kcal_from_part(part: dict[str, float]) -> float | None:
    k = part.get("ENERC_KCAL")
    if k is not None:
        return float(k)
    return _atwater_kcal_from_scaled_macros(part)


def _usda_fragment_result(line: str, api_key: str, settings: Settings) -> dict[str, Any]:
    """
    Per-fragment USDA match: scaled nutrients, search key used, and estimated grams
    for the full recipe (for per-serving amount on the client: grams ÷ yield).
    """
    search_q = _usda_fdc_search_query(line)
    cache_key = normalize_cache_key(search_q)
    scale = _grams_scale_for_usda_line(line)
    grams_full = max(_estimate_grams(line), 0.05)

    empty_meta = {
        "part": {},
        "usda_search_query": search_q,
        "grams_full_recipe": float(grams_full),
    }

    if settings.nutrition_cache_enabled:
        cached = try_get_cached_per_100g(cache_key)
        if cached:
            part = {k: v * scale for k, v in cached.items()}
            return {**empty_meta, "part": part}

    search_payload = {
        "query": search_q,
        "pageSize": 1,
        "dataType": ["Foundation", "SR Legacy", "Survey (FNDDS)", "Branded"],
    }
    try:
        r = curl_requests.post(
            FDC_SEARCH_URL,
            params={"api_key": api_key},
            json=search_payload,
            impersonate="chrome131",
            timeout=25.0,
        )
    except RequestsError:
        return empty_meta
    if r.status_code >= 400:
        return empty_meta
    try:
        data = r.json()
    except Exception:
        return empty_meta
    foods = data.get("foods") or []
    if not foods:
        return empty_meta
    fdc_id = foods[0].get("fdcId")
    if not fdc_id:
        return empty_meta

    detail = _fdc_get_json(f"{FDC_FOOD_URL}/{fdc_id}", {"api_key": api_key})
    if not detail:
        return empty_meta

    per_100g = _nutrients_from_fdc_food(detail)
    if settings.nutrition_cache_enabled and per_100g:
        try_save_nutrition_cache(cache_key, int(fdc_id), per_100g)

    part = {k: v * scale for k, v in per_100g.items()}
    return {**empty_meta, "part": part}


def _analyze_usda(ingr: list[str], settings: Settings) -> dict[str, Any]:
    api_key = settings.usda_api_key
    totals: dict[str, float] = {}
    max_original = 30
    lines = ingr[:max_original]

    fragments: list[str] = []
    for ln in lines:
        for f in _split_compound_line(ln):
            if len(fragments) >= 80:
                break
            fragments.append(f)
        if len(fragments) >= 80:
            break

    def worker(frag: str) -> dict[str, Any]:
        return _usda_fragment_result(frag, api_key, settings)

    results_by_idx: dict[int, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        future_to_index: dict[Any, int] = {}
        for idx, frag in enumerate(fragments):
            fut = pool.submit(worker, frag)
            future_to_index[fut] = idx
        for fut in as_completed(future_to_index):
            idx = future_to_index[fut]
            try:
                results_by_idx[idx] = fut.result()
            except Exception:
                results_by_idx[idx] = {
                    "part": {},
                    "usda_search_query": _usda_fdc_search_query(fragments[idx]),
                    "grams_full_recipe": max(_estimate_grams(fragments[idx]), 0.05),
                }

    for i in range(len(fragments)):
        row = results_by_idx.get(i, {})
        part = row.get("part", {}) if isinstance(row, dict) else {}
        for k, v in part.items():
            totals[k] = totals.get(k, 0.0) + v

    if not totals:
        return {
            "ok": False,
            "source": "usda",
            "message": "Could not match ingredients to USDA foods. Try shorter ingredient names.",
            "nutrients": [],
            "calories": None,
            "calorie_breakdown": [],
            "ingredient_nutrient_breakdown": [],
            "estimated": True,
        }

    calorie_breakdown: list[dict[str, Any]] = []
    line_kcals: list[float | None] = []
    ingredient_nutrient_breakdown: list[dict[str, Any]] = []
    for i, frag in enumerate(fragments):
        row = results_by_idx.get(i, {})
        part = row.get("part", {}) if isinstance(row, dict) else {}
        sq = str(row.get("usda_search_query") or "") if isinstance(row, dict) else ""
        grams_fr = row.get("grams_full_recipe") if isinstance(row, dict) else None
        kcal = _line_kcal_from_part(part)
        line_kcals.append(kcal)
        calorie_breakdown.append(
            {"ingredient": frag, "calories": float(kcal) if kcal is not None else None}
        )
        cal_out, nutrient_rows = _part_to_row_nutrients(part)
        ingredient_nutrient_breakdown.append(
            {
                "display_name": _friendly_usda_display_name(sq, frag),
                "grams_full_recipe": float(grams_fr) if grams_fr is not None else None,
                "calories": cal_out,
                "nutrients": nutrient_rows,
            }
        )

    totals.pop("ENERC_KCAL", None)
    non_null_kcals = [k for k in line_kcals if k is not None]
    calories = float(sum(non_null_kcals)) if non_null_kcals else None
    nutrients: list[dict[str, Any]] = []
    for nid, default_label, unit in NUTRIENT_DISPLAY_ORDER:
        if nid == "ENERC_KCAL":
            continue
        q = totals.get(nid)
        if q is None:
            continue
        nutrients.append({"id": nid, "label": default_label, "quantity": q, "unit": unit or ""})

    return {
        "ok": True,
        "source": "usda",
        "message": None,
        "calories": calories,
        "nutrients": nutrients,
        "calorie_breakdown": calorie_breakdown,
        "ingredient_nutrient_breakdown": ingredient_nutrient_breakdown,
        "note": (
            "USDA values are approximate: each fragment uses the top search hit; "
            "searches favor cooked rice / prepared pasta / baked bread (not raw grain or flour). "
            "Repeated ingredient lookups are cached in Postgres (per-100g profile by search key). "
            "Amounts use estimated grams (units, cups, tsp, cloves). "
            "Lines with no quantity use one typical serving. "
            "Long lines with “and” (e.g. rice and flatbread) are split into separate estimates. "
            "% Daily Value is based on a 2,000-calorie diet (FDA reference)."
        ),
        "estimated": True,
    }


def _edamam_ingredient_nutrient_breakdown(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Per-ingredient nutrients when Edamam returns totalNutrients on each ingredient."""
    out: list[dict[str, Any]] = []
    for ing in data.get("ingredients") or []:
        if not isinstance(ing, dict):
            continue
        text = str(ing.get("text") or "").strip()
        if not text:
            continue
        tnut = ing.get("totalNutrients") or {}
        if not isinstance(tnut, dict):
            continue
        display_name = _friendly_edamam_ingredient_name(text)
        nutrients: list[dict[str, Any]] = []
        for key, label, _unit_hint in EDAMAM_KEYS:
            block = tnut.get(key)
            if not isinstance(block, dict):
                continue
            q = block.get("quantity")
            u = block.get("unit") or ""
            if q is None:
                continue
            try:
                nutrients.append(
                    {
                        "id": key,
                        "label": block.get("label") or label,
                        "quantity": float(q),
                        "unit": u,
                    }
                )
            except (TypeError, ValueError):
                continue
        enerc = tnut.get("ENERC_KCAL")
        cal_out: float | None = None
        if isinstance(enerc, dict) and enerc.get("quantity") is not None:
            try:
                cal_out = float(enerc["quantity"])
            except (TypeError, ValueError):
                pass
        out.append(
            {
                "display_name": display_name,
                "grams_full_recipe": None,
                "calories": cal_out,
                "nutrients": nutrients,
            }
        )
    return out


def _edamam_calorie_breakdown(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Per-ingredient kcal when Edamam returns totalNutrients on each ingredient."""
    out: list[dict[str, Any]] = []
    for ing in data.get("ingredients") or []:
        if not isinstance(ing, dict):
            continue
        text = str(ing.get("text") or "").strip()
        if not text:
            continue
        tnut = ing.get("totalNutrients") or {}
        if not isinstance(tnut, dict):
            continue
        en = tnut.get("ENERC_KCAL")
        if not isinstance(en, dict):
            continue
        q = en.get("quantity")
        if q is None:
            continue
        try:
            out.append({"ingredient": text, "calories": float(q)})
        except (TypeError, ValueError):
            continue
    return out


def _analyze_edamam(title: str, ingr: list[str], settings: Settings) -> dict[str, Any]:
    params = {
        "app_id": settings.edamam_app_id,
        "app_key": settings.edamam_app_key,
    }
    body = {
        "title": title[:500],
        "ingr": ingr[:100],
    }

    try:
        r = curl_requests.post(
            EDAMAM_URL,
            params=params,
            json=body,
            impersonate="chrome131",
            timeout=45.0,
        )
    except RequestsError as e:
        return {
            "ok": False,
            "source": "edamam",
            "message": f"Nutrition request failed: {e!s}",
            "nutrients": [],
            "calories": None,
            "calorie_breakdown": [],
            "ingredient_nutrient_breakdown": [],
        }

    if r.status_code >= 400:
        return {
            "ok": False,
            "source": "edamam",
            "message": r.text[:500] if r.text else f"HTTP {r.status_code}",
            "nutrients": [],
            "calories": None,
            "calorie_breakdown": [],
            "ingredient_nutrient_breakdown": [],
        }

    data = r.json()
    total = data.get("totalNutrients") or {}
    calories = None
    enerc = total.get("ENERC_KCAL")
    if isinstance(enerc, dict) and "quantity" in enerc:
        calories = float(enerc["quantity"])

    nutrients: list[dict[str, Any]] = []
    for key, label, _unit_hint in EDAMAM_KEYS:
        block = total.get(key)
        if not isinstance(block, dict):
            continue
        q = block.get("quantity")
        u = block.get("unit") or ""
        if q is None:
            continue
        nutrients.append(
            {
                "id": key,
                "label": block.get("label") or label,
                "quantity": float(q),
                "unit": u,
            }
        )

    return {
        "ok": True,
        "source": "edamam",
        "message": None,
        "calories": calories,
        "nutrients": nutrients,
        "calorie_breakdown": _edamam_calorie_breakdown(data),
        "ingredient_nutrient_breakdown": _edamam_ingredient_nutrient_breakdown(data),
        "uri": data.get("uri"),
        "estimated": True,
    }
