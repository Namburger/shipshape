"""JSON persistence helpers for app state, Strava tokens, and activity caches."""

import copy
import json
import re
from pathlib import Path
from typing import Any, Optional

from .config import APP_STATE_FILE, CACHE_FILE, DATA_DIR, TOKENS_FILE, UPLOADED_PLAN_PDF

_DEFAULT_CACHE = {"activities": [], "details": {}, "synced_at": None}


def _sanitize_user_id(user_id: Optional[str]) -> Optional[str]:
    if not user_id:
        return None
    cleaned = re.sub(r"[^a-zA-Z0-9_\-]", "", str(user_id).strip())[:64]
    return cleaned if cleaned else None


def get_user_dir(user_id: Optional[str] = None) -> Path:
    uid = _sanitize_user_id(user_id)
    target = (DATA_DIR / "users" / uid) if uid else DATA_DIR
    target.mkdir(parents=True, exist_ok=True)
    return target


def get_user_tokens_file(user_id: Optional[str] = None) -> Path:
    uid = _sanitize_user_id(user_id)
    return (DATA_DIR / "users" / uid / ".strava_tokens.json") if uid else TOKENS_FILE


def get_user_cache_file(user_id: Optional[str] = None) -> Path:
    uid = _sanitize_user_id(user_id)
    return (DATA_DIR / "users" / uid / ".strava_cache.json") if uid else CACHE_FILE


def get_user_app_state_file(user_id: Optional[str] = None) -> Path:
    uid = _sanitize_user_id(user_id)
    return (DATA_DIR / "users" / uid / ".app_state.json") if uid else APP_STATE_FILE


def get_user_uploaded_plan_file(user_id: Optional[str] = None) -> Path:
    uid = _sanitize_user_id(user_id)
    return (DATA_DIR / "users" / uid / "uploaded_plan.pdf") if uid else UPLOADED_PLAN_PDF


def migrate_user_data(from_user_id: str, to_user_id: str) -> None:
    """Migrate data from a temporary session id to an authenticated athlete id."""
    src_uid = _sanitize_user_id(from_user_id)
    dst_uid = _sanitize_user_id(to_user_id)
    if not src_uid or not dst_uid or src_uid == dst_uid:
        return
    src_dir = DATA_DIR / "users" / src_uid
    dst_dir = DATA_DIR / "users" / dst_uid
    if not src_dir.exists():
        return
    dst_dir.mkdir(parents=True, exist_ok=True)
    for src_file in src_dir.iterdir():
        if src_file.is_file():
            dst_file = dst_dir / src_file.name
            if not dst_file.exists():
                src_file.rename(dst_file)


def _load_json(path: Path, default: Any) -> Any:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return copy.deepcopy(default)
    return copy.deepcopy(default)


def _save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def load_app_state(user_id: Optional[str] = None) -> dict:
    """Load persisted training-plan upload metadata for a specific user."""
    return _load_json(get_user_app_state_file(user_id), {"plan_uploaded": False})


def save_app_state(state: dict, user_id: Optional[str] = None) -> None:
    """Save training-plan upload metadata for a specific user."""
    _save_json(get_user_app_state_file(user_id), state)


def load_tokens(user_id: Optional[str] = None) -> dict:
    """Load cached Strava OAuth2 tokens for a specific user."""
    return _load_json(get_user_tokens_file(user_id), {})


def save_tokens(tokens: dict, user_id: Optional[str] = None) -> None:
    """Persist Strava OAuth2 tokens for a specific user."""
    _save_json(get_user_tokens_file(user_id), tokens)


def clear_tokens(user_id: Optional[str] = None) -> None:
    """Remove stored Strava OAuth2 tokens for a specific user."""
    target = get_user_tokens_file(user_id)
    if target.exists():
        target.unlink()
    if user_id and TOKENS_FILE.exists():
        TOKENS_FILE.unlink()


def load_cache(user_id: Optional[str] = None) -> dict:
    """Load cached Strava activities and activity detail laps for a specific user."""
    return _load_json(get_user_cache_file(user_id), _DEFAULT_CACHE)


def save_cache(cache_data: dict, user_id: Optional[str] = None) -> None:
    """Persist Strava activities and activity detail laps for a specific user."""
    _save_json(get_user_cache_file(user_id), cache_data)
