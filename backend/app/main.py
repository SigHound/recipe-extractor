from contextlib import asynccontextmanager

from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import Base, engine, get_db
from app.extract_service import fetch_and_extract
from app.paste_parse import parse_pasted_recipe_text
from app.models import IngredientNutritionCache, Recipe  # noqa: F401 — register models for create_all
from app.note_enrichment import enrich_recipe_display_from_notes
from app.nutrition_cookies import read_nutrition_key_cookies, write_nutrition_key_cookies
from app.nutrition_service import analyze_recipe_nutrition, validate_nutrition_api_keys
from app.schemas import (
    ExtractPasteBody,
    ExtractUrlBody,
    NutritionKeysValidateBody,
    NutritionRequest,
    RecipeCreate,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title="Recipe Extractor API", lifespan=lifespan)

_settings = get_settings()
_origins = [o.strip() for o in _settings.cors_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins or ["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/health")
def api_health(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"status": "ok", "database": "connected"}


@app.get("/api/recipes", tags=["recipes"])
def list_recipes(db: Session = Depends(get_db)):
    rows = db.scalars(select(Recipe).order_by(Recipe.updated_at.desc()).limit(50)).all()
    return {"items": [_recipe_to_response(r) for r in rows]}


@app.post("/api/extract", tags=["extract"])
def extract_from_url(
    body: ExtractUrlBody,
    settings: Settings = Depends(get_settings),
):
    try:
        out = fetch_and_extract(str(body.url), settings)
        enrich_recipe_display_from_notes(out["recipe"])
        return out
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.post("/api/extract/paste", tags=["extract"])
def extract_from_paste(body: ExtractPasteBody):
    try:
        recipe, warnings = parse_pasted_recipe_text(body.text)
        enrich_recipe_display_from_notes(recipe)
        return {"method": "paste", "warnings": warnings, "recipe": recipe}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


def _strip_opt(s: str | None) -> str | None:
    if s is None:
        return None
    t = s.strip()
    return t if t else None


@app.get("/api/nutrition/keys-status", tags=["nutrition"])
def nutrition_keys_status(request: Request):
    u, ei, ek = read_nutrition_key_cookies(request)
    return {"hasUsdaApiKey": bool(u), "hasEdamam": bool(ei and ek)}


@app.post("/api/nutrition/keys", tags=["nutrition"])
def nutrition_save_keys(request: Request, body: NutritionKeysValidateBody, response: Response):
    """Validate keys, then store in HttpOnly cookies (merged with existing cookies)."""
    cur_u, cur_ei, cur_ek = read_nutrition_key_cookies(request)
    draft_u = _strip_opt(body.usda_api_key)
    draft_ei = _strip_opt(body.edamam_app_id)
    draft_ek = _strip_opt(body.edamam_app_key)

    if not draft_u and not (draft_ei and draft_ek):
        raise HTTPException(
            status_code=400,
            detail="Enter a USDA API key or both Edamam App ID and App Key.",
        )

    result = validate_nutrition_api_keys(
        draft_u,
        draft_ei if draft_ei and draft_ek else None,
        draft_ek if draft_ei and draft_ek else None,
    )
    if not result.get("ok"):
        return result

    next_u = draft_u if draft_u else cur_u
    next_ei = draft_ei if (draft_ei and draft_ek) else cur_ei
    next_ek = draft_ek if (draft_ei and draft_ek) else cur_ek

    write_nutrition_key_cookies(
        response,
        request,
        usda_api_key=next_u,
        edamam_app_id=next_ei,
        edamam_app_key=next_ek,
    )
    return {"ok": True, "message": None}


@app.delete("/api/nutrition/keys", tags=["nutrition"])
def nutrition_delete_keys(
    request: Request,
    response: Response,
    scope: Literal["all", "usda", "edamam"] = "all",
):
    u, ei, ek = read_nutrition_key_cookies(request)
    if scope == "all":
        write_nutrition_key_cookies(
            response, request, usda_api_key=None, edamam_app_id=None, edamam_app_key=None
        )
    elif scope == "usda":
        write_nutrition_key_cookies(
            response, request, usda_api_key=None, edamam_app_id=ei, edamam_app_key=ek
        )
    else:
        write_nutrition_key_cookies(
            response, request, usda_api_key=u, edamam_app_id=None, edamam_app_key=None
        )
    return {"ok": True}


@app.post("/api/nutrition", tags=["nutrition"])
def nutrition_analysis(
    request: Request,
    body: NutritionRequest,
    settings: Settings = Depends(get_settings),
):
    u_c, ei_c, ek_c = read_nutrition_key_cookies(request)
    bu = _strip_opt(body.usda_api_key)
    bei = _strip_opt(body.edamam_app_id)
    bek = _strip_opt(body.edamam_app_key)
    return analyze_recipe_nutrition(
        body.title,
        body.ingredients,
        settings,
        notes=body.notes,
        description=body.description,
        client_usda_api_key=u_c or bu,
        client_edamam_app_id=ei_c or bei,
        client_edamam_app_key=ek_c or bek,
    )


@app.post("/api/recipes", tags=["recipes"])
def create_recipe(payload: RecipeCreate, db: Session = Depends(get_db)):
    row = Recipe(
        schema_version=1,
        title=payload.title.strip(),
        description=payload.description,
        ingredients=payload.ingredients,
        steps=payload.steps,
        servings=payload.servings,
        prep_time=payload.prep_time,
        cook_time=payload.cook_time,
        total_time=payload.total_time,
        image_url=payload.image_url,
        source=payload.source,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _recipe_to_response(row)


def _recipe_to_response(r: Recipe) -> dict:
    return {
        "schemaVersion": r.schema_version,
        "id": str(r.id),
        "title": r.title,
        "description": r.description,
        "ingredients": r.ingredients or [],
        "steps": r.steps or [],
        "servings": r.servings,
        "prepTime": r.prep_time,
        "cookTime": r.cook_time,
        "totalTime": r.total_time,
        "imageUrl": r.image_url,
        "source": r.source or {},
        "createdAt": r.created_at.isoformat() if r.created_at else None,
        "updatedAt": r.updated_at.isoformat() if r.updated_at else None,
    }
