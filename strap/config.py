"""Configuration paths and Strava environment credential helpers."""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
ASSETS_DIR = BASE_DIR / "assets"

ENV_FILE = BASE_DIR / ".env"
TOKENS_FILE = DATA_DIR / ".strava_tokens.json"
CACHE_FILE = DATA_DIR / ".strava_cache.json"
APP_STATE_FILE = DATA_DIR / ".app_state.json"
UPLOADED_PLAN_PDF = DATA_DIR / "uploaded_plan.pdf"
LEGACY_UPLOADED_PLAN_IMG = DATA_DIR / "uploaded_plan.png"

INDEX_FILE = BASE_DIR / "index.html"
PLAN_PDF_FILE = ASSETS_DIR / "jane_doe_training.pdf"

PORT = int(os.environ.get("PORT", "8000"))
HOST = "127.0.0.1"

_PLACEHOLDER_IDS = {"", "your_client_id_here"}
_PLACEHOLDER_SECRETS = {"", "your_client_secret_here"}


def load_env() -> dict:
    """Load Strava credentials from .env with environment variable overrides."""
    env = {}
    if ENV_FILE.exists():
        for raw_line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip().strip('"').strip("'")
    for key in ("STRAVA_CLIENT_ID", "STRAVA_CLIENT_SECRET"):
        if os.environ.get(key):
            env[key] = os.environ[key]
    return env


def save_env(client_id: str, client_secret: str) -> None:
    """Persist Strava API credentials to .env."""
    ENV_FILE.write_text(
        f"STRAVA_CLIENT_ID={client_id.strip()}\nSTRAVA_CLIENT_SECRET={client_secret.strip()}\n",
        encoding="utf-8",
    )


def has_strava_credentials(env: dict) -> bool:
    """Return True when real Strava OAuth credentials are configured."""
    client_id = env.get("STRAVA_CLIENT_ID", "")
    client_secret = env.get("STRAVA_CLIENT_SECRET", "")
    return client_id not in _PLACEHOLDER_IDS and client_secret not in _PLACEHOLDER_SECRETS
