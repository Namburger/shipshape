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
    load_app_state,
    load_cache,
    load_tokens,
    save_app_state,
)

# In-memory cache keyed by (pdf_path, st_mtime_ns, st_size) to avoid reparsing unchanged PDFs
_PARSED_PLAN_CACHE: Optional[Tuple[Path, int, int, dict]] = None


def get_active_plan_pdf() -> Tuple[Path, str]:
    """Return the active training plan PDF path and sanitized inline filename."""
    target_pdf = UPLOADED_PLAN_PDF if UPLOADED_PLAN_PDF.exists() else PLAN_PDF_FILE
    app_st = load_app_state()
    raw_name = app_st.get("uploaded_filename") or target_pdf.name
    safe_name = os.path.basename(raw_name).replace('"', "")
    return target_pdf, safe_name


def save_uploaded_plan_pdf(filename: str, data_url: str) -> dict:
    """Validate, parse, and save a base64-encoded PDF upload and return plan state."""
    if not data_url or "," not in data_url:
        raise ValueError("Invalid PDF upload payload.")

    _, b64_data = data_url.split(",", 1)
    pdf_bytes = base64.b64decode(b64_data)
    if not pdf_bytes.startswith(b"%PDF"):
        raise ValueError("Uploaded file must be a valid PDF (.pdf).")

    # Parse before persisting so an unrecognized PDF does not overwrite existing state
    parsed_plan = parse_training_plan_pdf(pdf_bytes)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOADED_PLAN_PDF.write_bytes(pdf_bytes)
    if LEGACY_UPLOADED_PLAN_IMG.exists():
        LEGACY_UPLOADED_PLAN_IMG.unlink()

    stat = UPLOADED_PLAN_PDF.stat()
    global _PARSED_PLAN_CACHE
    _PARSED_PLAN_CACHE = (UPLOADED_PLAN_PDF, stat.st_mtime_ns, stat.st_size, parsed_plan)

    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    state = {
        "plan_uploaded": True,
        "uploaded_filename": filename,
        "uploaded_at": now_iso,
    }
    save_app_state(state)

    return {
        "ok": True,
        **state,
        "plan": parsed_plan,
    }


def remove_uploaded_plan_pdf() -> None:
    """Delete any uploaded training plan files and reset upload state."""
    global _PARSED_PLAN_CACHE
    _PARSED_PLAN_CACHE = None
    if UPLOADED_PLAN_PDF.exists():
        UPLOADED_PLAN_PDF.unlink()
    if LEGACY_UPLOADED_PLAN_IMG.exists():
        LEGACY_UPLOADED_PLAN_IMG.unlink()
    save_app_state({"plan_uploaded": False})


def _resolve_active_plan() -> Optional[dict]:
    """Parse the active training plan PDF, caching in memory by file mtime and size."""
    global _PARSED_PLAN_CACHE
    pdf_path = UPLOADED_PLAN_PDF if UPLOADED_PLAN_PDF.exists() else PLAN_PDF_FILE
    if not pdf_path.exists():
        return None

    stat = pdf_path.stat()
    if (
        _PARSED_PLAN_CACHE is not None
        and _PARSED_PLAN_CACHE[0] == pdf_path
        and _PARSED_PLAN_CACHE[1] == stat.st_mtime_ns
        and _PARSED_PLAN_CACHE[2] == stat.st_size
    ):
        return _PARSED_PLAN_CACHE[3]

    parsed_plan = parse_training_plan_pdf(pdf_path.read_bytes())
    _PARSED_PLAN_CACHE = (pdf_path, stat.st_mtime_ns, stat.st_size, parsed_plan)
    return parsed_plan


def build_dashboard_state(unlocked: bool = True) -> dict:
    """Assemble the frontend bootstrap state for /api/state, protecting sensitive info when locked."""
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
    tokens = load_tokens()
    cache = load_cache()
    app_st = load_app_state()
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
        "plan": _resolve_active_plan() if plan_uploaded else None,
    }
