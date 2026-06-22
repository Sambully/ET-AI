"""Central configuration for AirGuard AI.

Loads API keys from the environment (.env), selects the LLM provider, and loads
the city + apportionment reference data. Everything has a sensible fallback so
the app boots and demos cleanly even with zero keys configured.
"""
import json
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
CACHE_DIR = DATA_DIR / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# ── LLM provider keys ─────────────────────────────────────────────────────
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-4-8").strip()

# Provider selection: Gemini first (free tier), Claude as fallback, then a
# deterministic templated mode so the demo never hard-fails on stage.
if GEMINI_API_KEY:
    LLM_PROVIDER = "gemini"
    LLM_MODEL = GEMINI_MODEL
elif ANTHROPIC_API_KEY:
    LLM_PROVIDER = "claude"
    LLM_MODEL = CLAUDE_MODEL
else:
    LLM_PROVIDER = "fallback"
    LLM_MODEL = "templated"

# ── Geospatial / sensor keys ──────────────────────────────────────────────
OPENAQ_API_KEY = os.getenv("OPENAQ_API_KEY", "").strip()
FIRMS_MAP_KEY = os.getenv("FIRMS_MAP_KEY", "").strip()

# Feature flags — surfaced in the UI so judges see what is live vs. seeded.
HAS_LLM = LLM_PROVIDER != "fallback"
HAS_OPENAQ = bool(OPENAQ_API_KEY)
HAS_FIRMS = bool(FIRMS_MAP_KEY)

PORT = int(os.getenv("PORT", "5000"))
FLASK_DEBUG = os.getenv("FLASK_DEBUG", "1") == "1"


def _load_json(name: str):
    with open(DATA_DIR / name, "r", encoding="utf-8") as fh:
        return json.load(fh)


CITIES = _load_json("cities.json")
APPORTIONMENT = _load_json("apportionment.json")
CITIES_BY_ID = {c["id"]: c for c in CITIES}


def get_city(city_id: str):
    return CITIES_BY_ID.get(city_id)


def provider_label() -> str:
    """Human-readable label for the active LLM, shown in the UI."""
    return {
        "gemini": f"Google Gemini ({GEMINI_MODEL})",
        "claude": f"Anthropic Claude ({CLAUDE_MODEL})",
        "fallback": "Templated (no LLM key set)",
    }[LLM_PROVIDER]
