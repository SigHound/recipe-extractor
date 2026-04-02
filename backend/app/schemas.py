from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl


class IngredientLine(BaseModel):
    order: int
    raw: str
    quantity: float | None = None
    quantity_text: str | None = Field(None, alias="quantityText")
    unit: str | None = None
    item: str | None = None
    section: str | None = None
    user_edited: bool | None = Field(None, alias="userEdited")

    model_config = {"populate_by_name": True}


class Step(BaseModel):
    order: int
    text: str


class RecipeSource(BaseModel):
    kind: Literal["web", "youtube", "manual", "other"]
    canonical_url: str = Field(..., alias="canonicalUrl")
    display_name: str | None = Field(None, alias="displayName")
    external_id: str | None = Field(None, alias="externalId")

    model_config = {"populate_by_name": True}


class RecipeRead(BaseModel):
    schema_version: int = Field(..., alias="schemaVersion")
    id: UUID
    title: str
    description: str | None = None
    ingredients: list[IngredientLine]
    steps: list[Step]
    servings: float | None = None
    prep_time: str | None = Field(None, alias="prepTime")
    cook_time: str | None = Field(None, alias="cookTime")
    total_time: str | None = Field(None, alias="totalTime")
    image_url: str | None = Field(None, alias="imageUrl")
    source: RecipeSource
    created_at: datetime = Field(..., alias="createdAt")
    updated_at: datetime = Field(..., alias="updatedAt")

    model_config = {"populate_by_name": True, "from_attributes": True}


class ExtractUrlBody(BaseModel):
    url: HttpUrl


class ExtractPasteBody(BaseModel):
    text: str = Field(..., min_length=10, max_length=500_000)


class NutritionRequest(BaseModel):
    title: str = Field(..., max_length=500)
    ingredients: list[str] = Field(..., min_length=1)
    notes: str | None = Field(None, max_length=120_000)
    description: str | None = Field(None, max_length=32_000)
    # Optional browser-stored overrides; omitted → use server .env.
    usda_api_key: str | None = Field(None, max_length=512, alias="usdaApiKey")
    edamam_app_id: str | None = Field(None, max_length=256, alias="edamamAppId")
    edamam_app_key: str | None = Field(None, max_length=512, alias="edamamAppKey")

    model_config = {"populate_by_name": True}


class NutritionKeysValidateBody(BaseModel):
    usda_api_key: str | None = Field(None, max_length=512, alias="usdaApiKey")
    edamam_app_id: str | None = Field(None, max_length=256, alias="edamamAppId")
    edamam_app_key: str | None = Field(None, max_length=512, alias="edamamAppKey")

    model_config = {"populate_by_name": True}


class RecipeCreate(BaseModel):
    """Payload to persist a recipe (e.g. after extraction + edits)."""

    title: str = Field(..., max_length=500)
    description: str | None = None
    ingredients: list[dict[str, Any]]
    steps: list[dict[str, Any]]
    servings: float | None = None
    prep_time: str | None = Field(None, max_length=64, alias="prepTime")
    cook_time: str | None = Field(None, max_length=64, alias="cookTime")
    total_time: str | None = Field(None, max_length=64, alias="totalTime")
    image_url: str | None = Field(None, alias="imageUrl")
    source: dict[str, Any]

    model_config = {"populate_by_name": True}
