#!/usr/bin/env python3
"""
Optional manual check: extract ingredients + steps from major recipe sites.

Run from repo root or backend (with PYTHONPATH=backend):

  cd backend && set PYTHONPATH=. && python scripts/validate_recipe_sites.py

URLs drift (404s) — adjust the list when sites change slugs.
"""

from __future__ import annotations

import os
import sys

# Ensure `app` is importable when run as `python scripts/validate_recipe_sites.py`
_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from app.config import get_settings  # noqa: E402
from app.extract_service import fetch_and_extract  # noqa: E402

SITES: list[tuple[str, str]] = [
    ("Allrecipes", "https://www.allrecipes.com/recipe/240522/easy-rib-eye-roast/"),
    ("Food Network", "https://www.foodnetwork.com/recipes/alton-brown/baked-macaroni-and-cheese-recipe-1939524"),
    ("Serious Eats", "https://www.seriouseats.com/the-best-roast-potatoes-ever-recipe"),
    ("Bon Appetit", "https://www.bonappetit.com/recipe/bas-best-chocolate-chip-cookies"),
    ("Taste of Home", "https://www.tasteofhome.com/recipes/favorite-chicken-potpie/"),
    ("King Arthur", "https://www.kingarthurbaking.com/recipes/extra-tangy-sourdough-bread-recipe"),
    ("Simply Recipes", "https://www.simplyrecipes.com/recipes/homemade_pizza/"),
    ("RecipeTin Eats", "https://www.recipetineats.com/honey-garlic-chicken/"),
    ("NYT Cooking", "https://cooking.nytimes.com/recipes/1015819-classic-chocolate-chip-cookies"),
    ("BBC Good Food", "https://www.bbcgoodfood.com/recipes/easy-chocolate-cake"),
]


def main() -> int:
    s = get_settings()
    ok = 0
    for label, url in SITES:
        try:
            out = fetch_and_extract(url, s)
            r = out["recipe"]
            ni, ns = len(r["ingredients"]), len(r["steps"])
            if ni and ns:
                ok += 1
                print(f"OK      {ni:2} ing {ns:2} st  {label}")
            else:
                print(f"PARTIAL {ni:2} ing {ns:2} st  {label} ({out['method']})")
        except Exception as e:
            print(f"FAIL    {label}: {e}")
    print(f"\nStrong (ingredients + steps): {ok}/{len(SITES)}")
    return 0 if ok == len(SITES) else 1


if __name__ == "__main__":
    raise SystemExit(main())
