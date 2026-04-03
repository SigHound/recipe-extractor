"""Fetch a page and extract a recipe (JSON-LD first, then light HTML fallback)."""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Tag
from curl_cffi import requests as curl_requests
from curl_cffi.requests import RequestsError

from app.config import Settings
from app.text_dedupe import dedupe_note_paragraphs, merge_note_sources
from app.url_safety import assert_safe_public_url

RECIPE_TYPE = "Recipe"

# Cooking / instruction cues — used to reject live-chat <ol> and other non-recipe lists.
_RECIPE_STEP_CUE = re.compile(
    r"(?i)\b(heat|add|stir|pour|bake|cook|simmer|boil|mix|whisk|fold|serve|season|slice|chop|dice|"
    r"mince|remove|transfer|cover|drain|reduce|roast|brown|melt|skillet|oven|pot|pan|broil|grill|"
    r"simmer|deglaze|discard|thicken|juices?|gravy|slow\s*cooker|minutes?|hours?|degrees?|°[fc]?|\d+\s*°)\b"
)


_EXCLUDE_CLASS_ID_TOKENS = frozenset(
    {
        "chat",
        "messenger",
        "livechat",
        "disqus",
        "comment",
        "comments",
        "intercom",
        "tawk",
        "crisp",
        "zendesk",
        "drift",
    }
)


def _node_in_excluded_chrome(tag: Tag) -> bool:
    """True if node is inside live chat, comments, footer/nav chrome — not recipe instructions."""
    for p in tag.parents:
        if not isinstance(p, Tag):
            continue
        name = (p.name or "").lower()
        if name in ("footer", "nav"):
            return True
        cls = " ".join(p.get("class") or []).lower()
        pid = (p.get("id") or "").lower()
        blob = re.sub(r"[-_]+", " ", f"{cls} {pid}")
        tokens = {t for t in re.split(r"\s+", blob) if t}
        if tokens & _EXCLUDE_CLASS_ID_TOKENS:
            return True
        if re.search(
            r"(?i)(^|[-_])(chat|livechat|disqus|tawk|zendesk)([-_]|$)",
            f"{cls}-{pid}",
        ):
            return True
    return False


def _ol_li_texts(ol: Tag) -> list[str]:
    texts: list[str] = []
    for li in ol.find_all("li", recursive=False):
        t = li.get_text(" ", strip=True)
        if t and len(t) > 10:
            texts.append(t)
    return texts


def _list_looks_like_recipe_steps(texts: list[str]) -> bool:
    """Filter out chat/comment <ol> lists that rarely use cooking vocabulary."""
    if not texts:
        return False
    if len(texts) == 1:
        return bool(_RECIPE_STEP_CUE.search(texts[0]))
    hits = sum(1 for t in texts if _RECIPE_STEP_CUE.search(t))
    avg_len = sum(len(t) for t in texts) / len(texts)
    need = max(2, (len(texts) + 1) // 2)
    if hits >= need:
        return True
    if hits >= 1 and avg_len > 52:
        return True
    return False


def _step_smells_like_chat_or_spam(text: str) -> bool:
    low = text.lower()
    if any(
        x in low
        for x in (
            "live chat",
            "customer support",
            "leave a reply",
            "send a message",
            "start chatting",
            "chat with",
            "agent will",
            "powered by tawk",
        )
    ):
        return True
    return False


def _browser_like_headers(url: str, user_agent: str) -> dict[str, str]:
    """
    Minimal document headers. curl_cffi impersonate=chrome* supplies TLS + consistent
    defaults; avoid Sec-Fetch-* / Client Hints here—mismatches vs the impersonated
    client sometimes trigger 403 on large recipe CDNs.
    """
    origin = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
    return {
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Upgrade-Insecure-Requests": "1",
        "Referer": origin + "/",
    }


def _http_error_message(status_code: int) -> str:
    if status_code in (401, 402, 403):
        return (
            f"The site refused this request (HTTP {status_code}). "
            "Publishers often block automated fetches; try another recipe URL or a site without bot protection."
        )
    if status_code == 429:
        return "Too many requests (HTTP 429). Wait a bit and try again."
    if status_code == 503:
        return "Site temporarily unavailable (HTTP 503). Try again later."
    return f"Page returned HTTP {status_code}"


def fetch_and_extract(url: str, settings: Settings) -> dict[str, Any]:
    assert_safe_public_url(url)

    headers = _browser_like_headers(url, settings.fetch_user_agent)

    # curl_cffi impersonates Chrome's TLS fingerprint; many recipe CDNs return 402/403 to plain httpx.
    try:
        r = curl_requests.get(
            url,
            headers=headers,
            impersonate="chrome131",
            timeout=settings.fetch_timeout_seconds,
            allow_redirects=True,
            max_redirects=settings.fetch_max_redirects,
        )
    except RequestsError as e:
        msg = str(e).lower()
        if "timeout" in msg or "timed out" in msg:
            raise ValueError("Request timed out") from e
        raise ValueError("Could not fetch URL") from e

    final_url = str(r.url)
    assert_safe_public_url(final_url)

    if r.status_code >= 400:
        raise ValueError(_http_error_message(r.status_code))

    content_type = (r.headers.get("content-type") or "").lower()
    if "text/html" not in content_type and "application/xhtml" not in content_type:
        if "text/" not in content_type:
            raise ValueError("URL did not return HTML")

    raw = r.content
    if len(raw) > settings.fetch_max_bytes:
        raise ValueError("Page is too large to download")

    charset = _charset_from_headers(r.headers.get("content-type"))
    html = raw.decode(charset, errors="replace")

    recipe, method, warnings = _parse_html(html, final_url)
    recipe["source"] = _source_from_url(final_url)
    return {"method": method, "warnings": warnings, "recipe": recipe}


def _charset_from_headers(content_type: str | None) -> str:
    if not content_type:
        return "utf-8"
    m = re.search(r"charset=([\w-]+)", content_type, re.I)
    if m:
        return m.group(1).strip().lower()
    return "utf-8"


def _source_from_url(url: str) -> dict[str, Any]:
    host = urlparse(url).hostname or ""
    kind = "youtube" if "youtube.com" in host or "youtu.be" in host else "web"
    return {"kind": kind, "canonicalUrl": url, "displayName": host}


def _parse_html(html: str, page_url: str) -> tuple[dict[str, Any], str, list[str]]:
    warnings: list[str] = []
    soup = BeautifulSoup(html, "html.parser")

    for node in _iter_jsonld_nodes(soup):
        if _types_include_recipe(node):
            built = _recipe_from_jsonld(node, page_url, warnings)
            if built:
                _augment_recipe_from_html(soup, built, warnings)
                return built, "json-ld", warnings

    built = _fallback_og(soup, page_url, warnings)
    return built, "fallback", warnings


def _strip_ld_json_noise(raw: str) -> str:
    """Some CMS wrap JSON-LD in HTML comments; strip only outer wrappers."""
    s = raw.strip()
    if s.startswith("<!--"):
        s = re.sub(r"^<!--\s*", "", s, count=1).strip()
    if s.endswith("-->"):
        s = re.sub(r"\s*-->\s*$", "", s, count=1).strip()
    return s


def _iter_jsonld_nodes(soup: BeautifulSoup):
    for script in soup.find_all("script"):
        t = (script.get("type") or "").lower()
        if "ld+json" not in t:
            continue
        raw = script.string or script.get_text() or ""
        raw = _strip_ld_json_noise(raw.strip())
        if not raw:
            continue
        for data in _loads_jsonld_blocks(raw):
            yield from _walk_ld(data)


def _loads_jsonld_blocks(raw: str) -> list[Any]:
    out: list[Any] = []
    for block in _split_json_blocks(raw):
        block = block.strip()
        if not block:
            continue
        try:
            out.append(json.loads(block))
        except json.JSONDecodeError:
            continue
    return out


def _split_json_blocks(raw: str) -> list[str]:
    raw = raw.strip()
    if not raw:
        return []
    if raw.startswith("["):
        try:
            data = json.loads(raw)
            return [json.dumps(item) for item in data] if isinstance(data, list) else [raw]
        except json.JSONDecodeError:
            pass
    return [raw]


def _walk_ld(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, dict):
        if "@graph" in data and isinstance(data["@graph"], list):
            nodes: list[dict[str, Any]] = []
            for item in data["@graph"]:
                nodes.extend(_walk_ld(item))
            return nodes
        nested: list[dict[str, Any]] = []
        for key in ("mainEntity", "about"):
            ent = data.get(key)
            if isinstance(ent, dict):
                nested.extend(_walk_ld(ent))
            elif isinstance(ent, list):
                for e in ent:
                    nested.extend(_walk_ld(e))
        return nested + [data]
    if isinstance(data, list):
        nodes = []
        for item in data:
            nodes.extend(_walk_ld(item))
        return nodes
    return []


def _type_token_is_recipe(token: str) -> bool:
    """True for Recipe, https://schema.org/Recipe, compact Recipe IRIs, etc."""
    if not token or not isinstance(token, str):
        return False
    tl = token.strip().lower()
    if tl == "recipe":
        return True
    if "schema.org" in tl and tl.rstrip("/").endswith("recipe"):
        return True
    return False


def _types_include_recipe(node: dict[str, Any]) -> bool:
    t = node.get("@type")
    if isinstance(t, str):
        return _type_token_is_recipe(t)
    if isinstance(t, list):
        return any(isinstance(x, str) and _type_token_is_recipe(x) for x in t)
    return False


def _recipe_from_jsonld(
    node: dict[str, Any], page_url: str, warnings: list[str]
) -> dict[str, Any] | None:
    title = _as_str(node.get("name")) or _as_str(node.get("headline"))
    if not title:
        title = "Untitled recipe"
        warnings.append("Recipe name was missing; using a placeholder title.")

    description = _as_str(node.get("description"))
    ingredients = _normalize_ingredients(node.get("recipeIngredient"))
    steps = _normalize_instructions(node.get("recipeInstructions"))

    if not ingredients:
        warnings.append("No ingredients were found in structured data.")
    if not steps:
        warnings.append("No steps were found in structured data.")

    servings = _parse_yield(node.get("recipeYield"))

    return {
        "schemaVersion": 1,
        "title": title[:500],
        "description": description,
        "ingredients": ingredients,
        "steps": steps,
        "notes": None,
        "servings": servings,
        "prepTime": _as_str(node.get("prepTime")),
        "cookTime": _as_str(node.get("cookTime")),
        "totalTime": _as_str(node.get("totalTime")),
        "imageUrl": _pick_image(node.get("image"), page_url),
    }


def _normalize_ingredients(raw: Any) -> list[dict[str, Any]]:
    lines: list[str] = []
    if raw is None:
        return []
    if isinstance(raw, str):
        lines = [raw]
    elif isinstance(raw, list):
        for item in raw:
            lines.extend(_ingredient_lines_from_item(item))
    else:
        lines = _ingredient_lines_from_item(raw)

    out: list[dict[str, Any]] = []
    for i, text in enumerate(lines):
        t = text.strip()
        if t:
            out.append({"order": i, "raw": t})
    return out


def _ingredient_lines_from_item(item: Any) -> list[str]:
    if isinstance(item, str):
        return [item]
    if isinstance(item, dict):
        if isinstance(item.get("text"), str):
            return [item["text"]]
        if isinstance(item.get("name"), str):
            return [item["name"]]
        if isinstance(item.get("value"), str):
            return [item["value"]]
        nested = item.get("itemListElement")
        if isinstance(nested, list):
            lines: list[str] = []
            for el in nested:
                if isinstance(el, dict) and isinstance(el.get("item"), dict):
                    lines.extend(_ingredient_lines_from_item(el["item"]))
                else:
                    lines.extend(_ingredient_lines_from_item(el))
            return lines
    return []


def _normalize_instructions(raw: Any) -> list[dict[str, Any]]:
    texts: list[str] = []
    if raw is None:
        return []
    if isinstance(raw, str):
        texts = [s.strip() for s in re.split(r"[\r\n]+", raw) if s.strip()]
    elif isinstance(raw, list):
        for block in raw:
            texts.extend(_instruction_texts(block))
    elif isinstance(raw, dict):
        dt = raw.get("@type")
        dtl = dt.strip().lower() if isinstance(dt, str) else ""
        if dtl == "howto":
            step = raw.get("step")
            if isinstance(step, list):
                for s in step:
                    texts.extend(_instruction_texts(s))
            elif step is not None:
                texts.extend(_instruction_texts(step))
        else:
            texts.extend(_instruction_texts(raw))
    else:
        texts = _instruction_texts(raw)

    return [{"order": i, "text": t[:8000]} for i, t in enumerate(texts) if t.strip()]


def _instruction_texts(item: Any) -> list[str]:
    if isinstance(item, str):
        return [item.strip()] if item.strip() else []
    if not isinstance(item, dict):
        return []

    t = item.get("@type")
    tl = t.strip().lower() if isinstance(t, str) else ""
    type_ok = tl in ("howtostep", "howtosection", "itemlist", "howto")

    if tl == "howto":
        step = item.get("step")
        if isinstance(step, list):
            out_ht: list[str] = []
            for s in step:
                out_ht.extend(_instruction_texts(s))
            return out_ht
        if step is not None:
            return _instruction_texts(step)

    if isinstance(item.get("text"), str):
        return [item["text"].strip()] if item["text"].strip() else []

    if isinstance(item.get("name"), str) and not type_ok:
        return [item["name"].strip()] if item["name"].strip() else []

    nested = item.get("itemListElement")
    if isinstance(nested, list):
        out: list[str] = []
        for el in nested:
            if isinstance(el, dict) and isinstance(el.get("item"), dict):
                out.extend(_instruction_texts(el["item"]))
            else:
                out.extend(_instruction_texts(el))
        return out

    return []


def _parse_yield(raw: Any) -> float | None:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, dict):
        val = raw.get("value") or raw.get("minValue")
        if val is not None:
            return _parse_yield(val)
        rep = raw.get("text") or raw.get("name")
        if isinstance(rep, str):
            return _parse_yield(rep)
        return None
    if isinstance(raw, str):
        s = raw.strip().lower()
        rng = re.search(r"(\d+(?:\.\d+)?)\s*[-–]\s*(\d+(?:\.\d+)?)", s)
        if rng:
            try:
                a, b = float(rng.group(1)), float(rng.group(2))
                return round((a + b) / 2, 2)
            except ValueError:
                pass
        m = re.search(
            r"(\d+(?:\.\d+)?)\s*(?:servings?|serves|people|portions?|makes|yield)\b",
            s,
        ) or re.search(r"(\d+(?:\.\d+)?)", s)
        if m:
            try:
                v = float(m.group(1))
                return v if 0 < v <= 100 else None
            except ValueError:
                return None
    if isinstance(raw, list) and raw:
        return _parse_yield(raw[0])
    return None


def _pick_image(raw: Any, page_url: str) -> str | None:
    url = _coerce_image_url(raw)
    if not url:
        return None
    return urljoin(page_url, url)


def _coerce_image_url(raw: Any) -> str | None:
    if isinstance(raw, str):
        return raw.strip() or None
    if isinstance(raw, dict):
        if isinstance(raw.get("url"), str):
            return raw["url"].strip() or None
        if isinstance(raw.get("contentUrl"), str):
            return raw["contentUrl"].strip() or None
    if isinstance(raw, list) and raw:
        return _coerce_image_url(raw[0])
    return None


def _as_str(raw: Any) -> str | None:
    if raw is None:
        return None
    if isinstance(raw, str):
        s = raw.strip()
        return s or None
    if isinstance(raw, dict):
        if isinstance(raw.get("text"), str):
            return _as_str(raw["text"])
    return None


def _fallback_og(soup: BeautifulSoup, page_url: str, warnings: list[str]) -> dict[str, Any]:
    warnings.append("No Recipe JSON-LD found; using Open Graph / title fallback only.")

    title = None
    desc = None
    image = None

    og_title = soup.find("meta", attrs={"property": "og:title"})
    if og_title and og_title.get("content"):
        title = og_title["content"].strip()

    if not title and soup.title and soup.title.string:
        title = soup.title.string.strip()

    og_desc = soup.find("meta", attrs={"property": "og:description"})
    if og_desc and og_desc.get("content"):
        desc = og_desc["content"].strip()

    og_image = soup.find("meta", attrs={"property": "og:image"})
    if og_image and og_image.get("content"):
        image = urljoin(page_url, og_image["content"].strip())

    return {
        "schemaVersion": 1,
        "title": (title or "Untitled page")[:500],
        "description": desc,
        "ingredients": [],
        "steps": [],
        "notes": None,
        "servings": None,
        "prepTime": None,
        "cookTime": None,
        "totalTime": None,
        "imageUrl": image,
    }


def _extract_wprm_notes_html(soup: BeautifulSoup) -> str | None:
    """WP Recipe Maker and similar: notes often live outside H2 ‘Notes’ headings."""
    chunks: list[str] = []
    for sel in (
        ".wprm-recipe-notes",
        ".wprm-recipe-note",
        ".wprm-note",
        "[class*='wprm-recipe-notes']",
    ):
        for node in soup.select(sel):
            if not isinstance(node, Tag) or _node_in_excluded_chrome(node):
                continue
            t = node.get_text("\n", strip=True)
            if len(t) > 25:
                chunks.append(t[:25_000])
    if not chunks:
        return None
    out: list[str] = []
    seen: set[str] = set()
    for c in chunks:
        key = re.sub(r"\s+", " ", c.strip().lower())[:400]
        if len(key) > 30 and key in seen:
            continue
        seen.add(key)
        out.append(c)
    joined = "\n\n".join(out) if out else None
    return dedupe_note_paragraphs(joined) if joined else None


def _augment_recipe_from_html(soup: BeautifulSoup, recipe: dict[str, Any], warnings: list[str]) -> None:
    """Merge visible HTML instructions / notes when JSON-LD is incomplete."""
    h_notes = _extract_notes_sections_html(soup)
    wprm_notes = _extract_wprm_notes_html(soup)
    merged_notes = merge_note_sources(h_notes, wprm_notes)
    if merged_notes:
        recipe["notes"] = dedupe_note_paragraphs(merged_notes)[:50_000]
        warnings.append(
            "Notes text was captured from the page (headings and/or recipe card notes) for placeholders like “see notes.”"
        )

    html_steps = _extract_instruction_steps_from_html(soup)
    merged = _merge_instruction_steps(recipe.get("steps") or [], html_steps)
    if len(merged) > len(recipe.get("steps") or []):
        recipe["steps"] = merged
        warnings.append("Additional recipe steps were found in the page HTML.")


def _heading_level(tag) -> int | None:
    if not tag or not tag.name:
        return None
    if tag.name.startswith("h") and len(tag.name) == 2 and tag.name[1].isdigit():
        return int(tag.name[1])
    return None


def _extract_notes_sections_html(soup: BeautifulSoup) -> str | None:
    """Collect text under headings like Notes, Tips, Substitutions."""
    chunks: list[str] = []
    heading_re = re.compile(
        r"(?i)^(notes?|note|tips?|substitutions?|recipe\s+notes?|chef\x27s?\s+notes?|variations?)\b"
    )
    for h in soup.find_all(["h2", "h3", "h4", "h5", "strong", "b"]):
        if isinstance(h, Tag) and _node_in_excluded_chrome(h):
            continue
        text = h.get_text(" ", strip=True)
        if not text or len(text) > 120:
            continue
        if not heading_re.match(text.strip()):
            continue
        level = _heading_level(h)
        parts: list[str] = []
        for sib in h.next_siblings:
            if getattr(sib, "name", None):
                lv = _heading_level(sib)
                if lv is not None and level is not None and lv <= level:
                    break
                if sib.name in ("h1", "h2", "h3", "h4") and sib is not h:
                    t2 = sib.get_text(" ", strip=True)
                    if t2 and len(t2) < 100 and heading_re.match(t2):
                        break
                parts.append(sib.get_text("\n", strip=True))
            elif isinstance(sib, str) and sib.strip():
                parts.append(sib.strip())
        block = "\n".join(p for p in parts if p)
        if block.strip():
            chunks.append(f"{text}\n{block.strip()}")

    if not chunks:
        for aside in soup.find_all("aside"):
            if isinstance(aside, Tag) and _node_in_excluded_chrome(aside):
                continue
            cls = " ".join(aside.get("class") or []).lower()
            if "note" in cls or "tip" in cls:
                t = aside.get_text("\n", strip=True)
                if len(t) > 20:
                    chunks.append(t[:20_000])

    if not chunks:
        return None
    return "\n\n".join(chunks)


def _extract_instruction_steps_from_html(soup: BeautifulSoup) -> list[str]:
    """
    Ordered steps from recipe-scoped HTML only (plugin containers, schema.org Recipe).

    Avoids scanning generic <main>/<article>, which often contains the longest <ol>
    (live chat, comments, etc.) and wins by character count over the real recipe list.
    """
    best: list[str] = []
    best_score = -1

    def consider(texts: list[str], priority: int) -> None:
        nonlocal best, best_score
        if len(texts) < 2 or not _list_looks_like_recipe_steps(texts):
            return
        score = sum(len(t) for t in texts) + priority * 100_000
        if score > best_score:
            best_score = score
            best = texts

    # WPRM — prefer container-scoped instruction divs (order matches recipe card)
    for root_sel in (".wprm-recipe-container", ".wprm-recipe"):
        root = soup.select_one(root_sel)
        if not root or _node_in_excluded_chrome(root):
            continue
        texts = [
            t
            for div in root.select(".wprm-recipe-instruction-text")
            for t in [div.get_text(" ", strip=True)]
            if t and len(t) > 12
        ]
        if not texts:
            for ol in root.find_all("ol"):
                if _node_in_excluded_chrome(ol):
                    continue
                texts = _ol_li_texts(ol)
                if len(texts) >= 2:
                    break
        if texts:
            consider(texts, 5)

    # schema.org Recipe microdata (ol inside recipe scope only)
    for root in soup.find_all(attrs={"itemtype": re.compile(r"schema\.org/Recipe", re.I)}):
        if _node_in_excluded_chrome(root):
            continue
        for ol in root.find_all("ol"):
            if _node_in_excluded_chrome(ol):
                continue
            texts = _ol_li_texts(ol)
            consider(texts, 3)

    # WP Tasty / similar
    for root in soup.select(".tasty-recipes, .tasty-recipes-instructions"):
        if _node_in_excluded_chrome(root):
            continue
        for ol in root.find_all("ol"):
            if _node_in_excluded_chrome(ol):
                continue
            texts = _ol_li_texts(ol)
            consider(texts, 2)

    # Generic recipe instruction wrappers (still not whole <main>)
    for root in soup.select(".recipe-instructions, .recipe-directions, .recipe-method"):
        if not isinstance(root, Tag) or _node_in_excluded_chrome(root):
            continue
        for ol in root.find_all("ol"):
            if _node_in_excluded_chrome(ol):
                continue
            texts = _ol_li_texts(ol)
            consider(texts, 1)

    # Last resort: first post body <ol> only (not entire <main> — avoids sidebar chat lists)
    if not best:
        for root in soup.select("article .entry-content, main .entry-content, .post-content"):
            if not isinstance(root, Tag) or _node_in_excluded_chrome(root):
                continue
            for ol in root.find_all("ol", recursive=False):
                if _node_in_excluded_chrome(ol):
                    continue
                texts = _ol_li_texts(ol)
                consider(texts, 0)
            break

    return best


def _merge_instruction_steps(
    existing: list[dict[str, Any]],
    html_steps: list[str],
) -> list[dict[str, Any]]:
    """Append HTML steps not already covered by JSON-LD instruction text."""
    if not html_steps:
        return existing

    jsonld_texts = [str(s.get("text", "")).strip() for s in existing if isinstance(s, dict)]
    blob = " ".join(x.lower() for x in jsonld_texts if x)

    extras: list[str] = []
    seen: set[str] = set()

    if len(html_steps) > len(jsonld_texts):
        for h in html_steps[len(jsonld_texts) :]:
            hs = h.strip()
            if (
                len(hs) >= 12
                and hs not in seen
                and not _step_smells_like_chat_or_spam(hs)
            ):
                seen.add(hs)
                extras.append(hs)

    for h in html_steps:
        hs = h.strip()
        if len(hs) < 12 or hs in seen:
            continue
        if _step_smells_like_chat_or_spam(hs):
            continue
        low = hs.lower()
        prefix = low[: min(90, len(low))]
        if prefix and prefix in blob:
            continue
        if any(
            (low in j.lower() or j.lower() in low) for j in jsonld_texts if len(j) > 20
        ):
            continue
        seen.add(hs)
        extras.append(hs)

    if not extras:
        return existing

    out = [dict(x) for x in existing if isinstance(x, dict)]
    n = len(out)
    for i, text in enumerate(extras):
        out.append({"order": n + i, "text": text[:8000]})
    return out
