"""Strava OAuth2 and v3 API client helpers."""

import datetime
import json
import ssl
import time
import urllib.parse
import urllib.request

from .config import load_env
from .storage import load_cache, load_tokens, save_cache, save_tokens

STRAVA_AUTH_URL = "https://www.strava.com/oauth/authorize"
STRAVA_TOKEN_URL = "https://www.strava.com/oauth/token"
STRAVA_API_BASE = "https://www.strava.com/api/v3"


def _ssl_context():
    return ssl.create_default_context()


def strava_post(url: str, data: dict) -> dict:
    """Send a form-encoded POST request to Strava and decode the JSON response."""
    encoded = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=encoded,
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, context=_ssl_context(), timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def strava_get(url: str, access_token: str) -> object:
    """Send an authenticated GET request to the Strava API."""
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
        method="GET",
    )
    with urllib.request.urlopen(req, context=_ssl_context(), timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def build_authorize_url(client_id: str, redirect_uri: str) -> str:
    """Construct the Strava OAuth2 authorization redirect URL."""
    params = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "approval_prompt": "force",
            "scope": "read,activity:read_all",
        }
    )
    return f"{STRAVA_AUTH_URL}?{params}"


def exchange_code_for_tokens(client_id: str, client_secret: str, code: str) -> dict:
    """Exchange a Strava authorization code for OAuth2 access and refresh tokens."""
    token_data = strava_post(
        STRAVA_TOKEN_URL,
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "grant_type": "authorization_code",
        },
    )
    save_tokens(token_data)
    return token_data


def get_valid_access_token() -> str:
    """Return a valid Strava access token, refreshing it automatically if near expiry."""
    tokens = load_tokens()
    if not tokens or "access_token" not in tokens:
        raise RuntimeError("Not authenticated with Strava.")

    expires_at = tokens.get("expires_at", 0)
    if time.time() < expires_at - 120:
        return tokens["access_token"]

    env = load_env()
    client_id = env.get("STRAVA_CLIENT_ID", "")
    client_secret = env.get("STRAVA_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        raise RuntimeError("Missing STRAVA_CLIENT_ID or STRAVA_CLIENT_SECRET in .env")

    refreshed = strava_post(
        STRAVA_TOKEN_URL,
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "refresh_token",
            "refresh_token": tokens["refresh_token"],
        },
    )
    if "athlete" in tokens and "athlete" not in refreshed:
        refreshed["athlete"] = tokens["athlete"]
    save_tokens(refreshed)
    return refreshed["access_token"]


def sync_strava_activities(days: int = 365) -> dict:
    """Fetch Strava athlete activities from the last 12 months and update the local cache."""
    access_token = get_valid_access_token()
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    after_epoch = int((now_utc - datetime.timedelta(days=days)).timestamp())
    all_activities = []
    page = 1
    while page <= 8:
        url = f"{STRAVA_API_BASE}/athlete/activities?after={after_epoch}&per_page=200&page={page}"
        batch = strava_get(url, access_token)
        if not isinstance(batch, list) or len(batch) == 0:
            break
        all_activities.extend(batch)
        if len(batch) < 200:
            break
        page += 1

    cache = load_cache()
    cache["activities"] = all_activities
    cache["synced_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    save_cache(cache)
    return cache


def fetch_activity_detail(activity_id: str) -> dict:
    """Fetch and cache full Strava activity details (laps & splits) by activity ID."""
    cache = load_cache()
    details = cache.setdefault("details", {})
    key = str(activity_id)
    if key in details:
        return details[key]

    access_token = get_valid_access_token()
    detail = strava_get(f"{STRAVA_API_BASE}/activities/{key}", access_token)
    details[key] = detail
    save_cache(cache)
    return detail
