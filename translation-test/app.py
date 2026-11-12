import os
import logging
import hmac
from functools import lru_cache
from typing import Literal

from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("madloba.translation")

app = FastAPI(title="Madloba Translation Test")

API_KEY = os.getenv("TRANSLATION_API_KEY", "").strip()
MAX_CHARS = 8000


class TranslateRequest(BaseModel):
    text: str = Field(min_length=1, max_length=8000)
    source_language: Literal["ru", "en", "uk"]


@lru_cache(maxsize=1)
def get_languages():
    import argostranslate.translate

    return {
        lang.code: lang
        for lang in argostranslate.translate.get_installed_languages()
    }


def translate_text(text, src, dst):
    languages = get_languages()

    if src not in languages or dst not in languages:
        raise RuntimeError(f"Missing model: {src}->{dst}")

    result = languages[src].get_translation(
        languages[dst]
    ).translate(text)

    if not result or not result.strip():
        raise RuntimeError("Empty translation")

    return result.strip()


def translate_description(text, source):
    text = text.strip()

    if not text or len(text) > MAX_CHARS:
        raise ValueError("Invalid text length")

    if source == "en":
        return {
            "en": text,
            "ru": translate_text(text, "en", "ru"),
            "uk": translate_text(text, "en", "uk"),
        }

    english = translate_text(text, source, "en")

    if source == "ru":
        return {
            "ru": text,
            "en": english,
            "uk": translate_text(english, "en", "uk"),
        }

    return {
        "uk": text,
        "en": english,
        "ru": translate_text(english, "en", "ru"),
    }


@app.get("/")
def root():
    return {
        "service": "madloba-translation-test",
        "status": "ok",
        "integration": "disabled",
    }


@app.get("/health")
def health():
    try:
        languages = get_languages()
        return {
            "status": "ok" if all(
                code in languages for code in ("ru", "en", "uk")
            ) else "models_missing",
            "installed_languages": sorted(languages),
            "integration": "disabled",
        }
    except Exception:
        raise HTTPException(
            status_code=503,
            detail="Translation models unavailable",
        )


@app.post("/translate")
def translate(
    request: TranslateRequest,
    authorization: str | None = Header(default=None, alias="Authorization"),
):
    if not API_KEY:
        raise HTTPException(
            status_code=503,
            detail="API key is not configured",
        )

    scheme, separator, supplied_key = (authorization or "").partition(" ")
    if not separator or scheme.lower() != "bearer" or not hmac.compare_digest(
        supplied_key.strip(), API_KEY
    ):
        raise HTTPException(status_code=401, detail="Unauthorized")

    try:
        translations = translate_description(
            request.text,
            request.source_language,
        )
        return {
            "source_language": request.source_language,
            "translations": translations,
            "georgian_ui_description_language": "en",
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception:
        log.exception("Translation failed")
        raise HTTPException(
            status_code=503,
            detail="Translation unavailable",
