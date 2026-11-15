"""JSON persistence helpers for app state, Strava tokens, and activity caches."""

import copy
import json
from pathlib import Path
from typing import Any

from .config import APP_STATE_FILE, CACHE_FILE, DATA_DIR, TOKENS_FILE

_DEFAULT_CACHE = {"activities": [], "details": {}, "synced_at": None}


def _load_json(path: Path, default: Any) -> Any:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return copy.deepcopy(default)
    return copy.deepcopy(default)


def _save_json(path: Path, payload: Any) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def load_app_state() -> dict:
    """Load persisted training-plan upload metadata."""
    return _load_json(APP_STATE_FILE, {"plan_uploaded": False})


def save_app_state(state: dict) -> None:
    """Save training-plan upload metadata."""
    _save_json(APP_STATE_FILE, state)


def load_tokens() -> dict:
    """Load cached Strava OAuth2 tokens."""
    return _load_json(TOKENS_FILE, {})


def save_tokens(tokens: dict) -> None:
    """Persist Strava OAuth2 tokens."""
    _save_json(TOKENS_FILE, tokens)


def clear_tokens() -> None:
    """Remove stored Strava OAuth2 tokens."""
    if TOKENS_FILE.exists():
        TOKENS_FILE.unlink()


def load_cache() -> dict:
    """Load cached Strava activities and activity detail laps."""
    return _load_json(CACHE_FILE, _DEFAULT_CACHE)


def save_cache(cache_data: dict) -> None:
    """Persist Strava activities and activity detail laps."""
    _save_json(CACHE_FILE, cache_data)
