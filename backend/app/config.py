from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Always load backend/.env regardless of cwd (e.g. `uvicorn` started from repo root).
_BACKEND_DIR = Path(__file__).resolve().parent.parent
_ENV_FILE = _BACKEND_DIR / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE) if _ENV_FILE.is_file() else None,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "postgresql+psycopg://recipe:recipe@localhost:5432/recipe"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    fetch_timeout_seconds: float = 20.0
    fetch_max_bytes: int = 2_000_000
    fetch_max_redirects: int = 5
    # Many sites block non-browser clients; a current Chrome UA + full headers works more often.
    fetch_user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    )

    # Optional — USDA FoodData Central (free): https://fdc.nal.usda.gov/api-key-signup
    usda_api_key: str = ""

    # Optional — Edamam Nutrition Analysis API (free developer tier): https://developer.edamam.com/
    edamam_app_id: str = ""
    edamam_app_key: str = ""

    # Cache USDA per-100g profiles in Postgres (ingredient_nutrition_cache table).
    nutrition_cache_enabled: bool = True


def get_settings() -> Settings:
    return Settings()
