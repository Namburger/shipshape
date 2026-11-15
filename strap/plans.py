"""Training plan PDF upload, removal, and dashboard state assembly."""

import base64
import datetime
import os
from pathlib import Path
from typing import Optional, Tuple

from .config import (
    DATA_DIR,
    LEGACY_UPLOADED_PLAN_IMG,
    PLAN_PDF_FILE,
    UPLOADED_PLAN_PDF,
    get_app_password,
    has_strava_credentials,
    load_env,
)
from .pdf_parser import parse_training_plan_pdf
from .storage import (
    get_user_uploaded_plan_file,
    load_app_state,
    load_cache,
    load_tokens,
    save_app_state,
)

# In-memory cache keyed by path string to avoid reparsing unchanged PDFs across users
_PARSED_PLAN_CACHE: dict = {}


def _get_plan_cache() -> dict:
    global _PARSED_PLAN_CACHE
    if _PARSED_PLAN_CACHE is None or not isinstance(_PARSED_PLAN_CACHE, dict):
        _PARSED_PLAN_CACHE = {}
    return _PARSED_PLAN_CACHE


def get_active_plan_pdf(user_id: Optional[str] = None) -> Tuple[Path, str]:
    """Return the active training plan PDF path and sanitized inline filename for a user."""
    target_pdf = get_user_uploaded_plan_file(user_id)
    if not target_pdf.exists():
        target_pdf = PLAN_PDF_FILE
    app_st = load_app_state(user_id=user_id)
    raw_name = app_st.get("uploaded_filename") or target_pdf.name
    safe_name = os.path.basename(raw_name).replace('"', "")
    return target_pdf, safe_name


def save_uploaded_plan_pdf(filename: str, data_url: str, user_id: Optional[str] = None) -> dict:
    """Validate, parse, and save a base64-encoded PDF upload and return plan state for a user."""
    if not data_url or "," not in data_url:
        raise ValueError("Invalid PDF upload payload.")

    _, b64_data = data_url.split(",", 1)
    pdf_bytes = base64.b64decode(b64_data)
    if not pdf_bytes.startswith(b"%PDF"):
        raise ValueError("Uploaded file must be a valid PDF (.pdf).")

    # Parse before persisting so an unrecognized PDF does not overwrite existing state
    parsed_plan = parse_training_plan_pdf(pdf_bytes)

    target_pdf = get_user_uploaded_plan_file(user_id)
    target_pdf.parent.mkdir(parents=True, exist_ok=True)
    target_pdf.write_bytes(pdf_bytes)
    try:
        target_pdf.chmod(0o600)
    except OSError:
        pass

    if user_id is None and LEGACY_UPLOADED_PLAN_IMG.exists():
        LEGACY_UPLOADED_PLAN_IMG.unlink()

    stat = target_pdf.stat()
    cache = _get_plan_cache()
    cache[str(target_pdf)] = (stat.st_mtime_ns, stat.st_size, parsed_plan)

    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    state = {
        "plan_uploaded": True,
        "uploaded_filename": filename,
        "uploaded_at": now_iso,
    }
    save_app_state(state, user_id=user_id)

    return {
        "ok": True,
        **state,
        "plan": parsed_plan,
    }


def remove_uploaded_plan_pdf(user_id: Optional[str] = None) -> None:
    """Delete any uploaded training plan files and reset upload state for a user."""
    target_pdf = get_user_uploaded_plan_file(user_id)
    cache = _get_plan_cache()
    cache.pop(str(target_pdf), None)
    if target_pdf.exists():
        target_pdf.unlink()
    if user_id is None and LEGACY_UPLOADED_PLAN_IMG.exists():
        LEGACY_UPLOADED_PLAN_IMG.unlink()
    save_app_state({"plan_uploaded": False}, user_id=user_id)


def _resolve_active_plan(user_id: Optional[str] = None) -> Optional[dict]:
    """Parse the active training plan PDF, caching in memory by file path, mtime and size."""
    target_pdf = get_user_uploaded_plan_file(user_id)
    if not target_pdf.exists():
        target_pdf = PLAN_PDF_FILE
    if not target_pdf.exists():
        return None

    stat = target_pdf.stat()
    cache_key = str(target_pdf)
    cache = _get_plan_cache()
    cached = cache.get(cache_key)
    if cached and cached[0] == stat.st_mtime_ns and cached[1] == stat.st_size:
        return cached[2]

    parsed_plan = parse_training_plan_pdf(target_pdf.read_bytes())
    cache[cache_key] = (stat.st_mtime_ns, stat.st_size, parsed_plan)
    return parsed_plan


def build_dashboard_state(unlocked: bool = True, user_id: Optional[str] = None) -> dict:
    """Assemble the frontend bootstrap state for /api/state for a specific user."""
    app_pwd = get_app_password()
    password_required = bool(app_pwd)
    if password_required and not unlocked:
        return {
            "password_required": True,
            "unlocked": False,
            "configured": False,
            "authenticated": False,
            "activities": [],
            "plan_uploaded": False,
            "plan": None,
        }

    env = load_env()
    tokens = load_tokens(user_id=user_id)
    cache = load_cache(user_id=user_id)
    app_st = load_app_state(user_id=user_id)
    configured = has_strava_credentials(env)
    plan_uploaded = bool(app_st.get("plan_uploaded"))

    return {
        "password_required": password_required,
        "unlocked": True,
        "configured": configured,
        "client_id": env.get("STRAVA_CLIENT_ID", "") if configured else "",
        "authenticated": bool(tokens.get("access_token")),
        "athlete": tokens.get("athlete"),
        "synced_at": cache.get("synced_at"),
        "activities": cache.get("activities", []),
        "plan_uploaded": plan_uploaded,
        "uploaded_filename": app_st.get("uploaded_filename"),
        "uploaded_at": app_st.get("uploaded_at"),
        "plan": _resolve_active_plan(user_id=user_id) if plan_uploaded else None,
    }
