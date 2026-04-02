"""Postgres cache for USDA FDC per-100g nutrient profiles (by normalized search query)."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import IngredientNutritionCache

logger = logging.getLogger(__name__)

# Bump when nutrient mapping or search-key rules change so stale rows are ignored.
NUTRITION_CACHE_VERSION = 2


def normalize_cache_key(usda_search_query: str) -> str:
    """Stable key for the exact USDA search string we use (after refinement)."""
    return " ".join(usda_search_query.strip().lower().split())[:512]


def get_cached_per_100g(db: Session, cache_key: str) -> dict[str, float] | None:
    """Return per-100g nutrient dict if cache hit; increment hit_count."""
    row = db.scalar(
        select(IngredientNutritionCache).where(
            IngredientNutritionCache.cache_key == cache_key,
            IngredientNutritionCache.cache_version == NUTRITION_CACHE_VERSION,
        )
    )
    if row is None:
        return None
    data = row.nutrients_per_100g
    if not isinstance(data, dict):
        return None
    row.hit_count = int(row.hit_count) + 1
    row.updated_at = datetime.now(timezone.utc)
    db.commit()
    out: dict[str, float] = {}
    for k, v in data.items():
        try:
            out[str(k)] = float(v)
        except (TypeError, ValueError):
            continue
    return out


def save_nutrition_cache(
    db: Session,
    cache_key: str,
    fdc_id: int,
    nutrients_per_100g: dict[str, float],
) -> None:
    """Store or replace cached per-100g profile for this search key."""
    if not nutrients_per_100g:
        return
    row = db.scalar(select(IngredientNutritionCache).where(IngredientNutritionCache.cache_key == cache_key))
    payload: dict[str, Any] = {k: float(v) for k, v in nutrients_per_100g.items()}
    if row is None:
        db.add(
            IngredientNutritionCache(
                id=uuid.uuid4(),
                cache_key=cache_key,
                fdc_id=fdc_id,
                nutrients_per_100g=payload,
                cache_version=NUTRITION_CACHE_VERSION,
                hit_count=0,
            )
        )
    else:
        row.fdc_id = fdc_id
        row.nutrients_per_100g = payload
        row.cache_version = NUTRITION_CACHE_VERSION
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        row2 = db.scalar(select(IngredientNutritionCache).where(IngredientNutritionCache.cache_key == cache_key))
        if row2 is not None:
            row2.fdc_id = fdc_id
            row2.nutrients_per_100g = payload
            row2.cache_version = NUTRITION_CACHE_VERSION
            try:
                db.commit()
            except Exception as e2:
                db.rollback()
                logger.debug("nutrition cache retry failed: %s", e2)


def try_get_cached_per_100g(cache_key: str) -> dict[str, float] | None:
    """Thread-safe: opens its own Session (for use inside ThreadPoolExecutor workers)."""
    db = SessionLocal()
    try:
        return get_cached_per_100g(db, cache_key)
    except Exception as e:
        logger.warning("nutrition cache read failed: %s", e)
        db.rollback()
        return None
    finally:
        db.close()


def try_save_nutrition_cache(cache_key: str, fdc_id: int, nutrients_per_100g: dict[str, float]) -> None:
    db = SessionLocal()
    try:
        save_nutrition_cache(db, cache_key, fdc_id, nutrients_per_100g)
    except Exception as e:
        logger.warning("nutrition cache write failed: %s", e)
        db.rollback()
    finally:
        db.close()
