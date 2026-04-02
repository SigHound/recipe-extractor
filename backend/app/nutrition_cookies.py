"""HttpOnly cookies for optional per-browser nutrition API keys (not readable by JS)."""

from __future__ import annotations

from fastapi import Request, Response

COOKIE_USDA = "recipe_extractor_v1_nutrition_usda"
COOKIE_EDAMAM_ID = "recipe_extractor_v1_nutrition_edamam_id"
COOKIE_EDAMAM_KEY = "recipe_extractor_v1_nutrition_edamam_key"

# ~6 months; keys can be re-saved anytime.
MAX_AGE_SECONDS = 60 * 60 * 24 * 180


def cookie_secure_flag(request: Request) -> bool:
    if request.headers.get("x-forwarded-proto", "").lower().startswith("https"):
        return True
    return request.url.scheme == "https"


def read_nutrition_key_cookies(request: Request) -> tuple[str | None, str | None, str | None]:
    u = request.cookies.get(COOKIE_USDA)
    i = request.cookies.get(COOKIE_EDAMAM_ID)
    k = request.cookies.get(COOKIE_EDAMAM_KEY)
    usda = u.strip() if u and str(u).strip() else None
    eid = i.strip() if i and str(i).strip() else None
    ek = k.strip() if k and str(k).strip() else None
    return usda, eid, ek


def _set_cookie(response: Response, name: str, value: str, *, secure: bool) -> None:
    response.set_cookie(
        key=name,
        value=value,
        max_age=MAX_AGE_SECONDS,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )


def _clear_cookie(response: Response, name: str, *, secure: bool) -> None:
    response.delete_cookie(key=name, path="/", httponly=True, secure=secure, samesite="lax")


def write_nutrition_key_cookies(
    response: Response,
    request: Request,
    *,
    usda_api_key: str | None,
    edamam_app_id: str | None,
    edamam_app_key: str | None,
) -> None:
    secure = cookie_secure_flag(request)
    if usda_api_key:
        _set_cookie(response, COOKIE_USDA, usda_api_key, secure=secure)
    else:
        _clear_cookie(response, COOKIE_USDA, secure=secure)
    if edamam_app_id and edamam_app_key:
        _set_cookie(response, COOKIE_EDAMAM_ID, edamam_app_id, secure=secure)
        _set_cookie(response, COOKIE_EDAMAM_KEY, edamam_app_key, secure=secure)
    else:
        _clear_cookie(response, COOKIE_EDAMAM_ID, secure=secure)
        _clear_cookie(response, COOKIE_EDAMAM_KEY, secure=secure)
