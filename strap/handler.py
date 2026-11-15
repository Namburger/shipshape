"""HTTP request handler and server runner for the Ship Shape dashboard."""

import hashlib
import http.cookies
import http.server
import json
import re
import secrets
import socketserver
import sys
import urllib.error
import urllib.parse
from pathlib import Path
from typing import Dict, Optional

from .config import HOST, INDEX_FILE, PORT, get_app_password, has_strava_credentials, load_env, save_env
from .plans import (
    build_dashboard_state,
    get_active_plan_pdf,
    remove_uploaded_plan_pdf,
    save_uploaded_plan_pdf,
)
from .storage import clear_tokens, migrate_user_data
from .strava import (
    build_authorize_url,
    exchange_code_for_tokens,
    fetch_activity_detail,
    sync_strava_activities,
)

MAX_JSON_PAYLOAD_BYTES = 15 * 1024 * 1024  # 15 MB payload guard
_SERVER_SECRET = secrets.token_bytes(32)
_ACTIVE_OAUTH_STATES: Dict[str, str] = {}


def _hash_session_token(password: str) -> str:
    return hashlib.sha256(_SERVER_SECRET + password.encode("utf-8")).hexdigest()


def _is_session_valid(cookie_header: str, password: str) -> bool:
    if not password:
        return True
    expected = _hash_session_token(password)
    cookie = http.cookies.SimpleCookie()
    try:
        cookie.load(cookie_header)
        token = cookie.get("shipshape_session")
        if token and secrets.compare_digest(token.value, expected):
            return True
    except Exception:
        pass
    return False


class ReusableTCPServer(socketserver.TCPServer):
    """TCPServer allowing immediate port reuse after restarts."""

    allow_reuse_address = True


class DashboardHandler(http.server.BaseHTTPRequestHandler):
    """HTTP router serving dashboard assets, Strava OAuth2 callbacks, and JSON APIs."""

    def log_message(self, fmt, *args):
        sys.stderr.write(f"[Strap] {self.address_string()} - {fmt % args}\n")

    def end_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        proto = self.headers.get("X-Forwarded-Proto", "http")
        secure_attr = "; Secure" if proto == "https" else ""
        if hasattr(self, "_new_user_id") and self._new_user_id:
            self.send_header(
                "Set-Cookie",
                f"shipshape_uid={self._new_user_id}; Path=/; HttpOnly; SameSite=Lax; Max-Age=31536000{secure_attr}",
            )
            self._new_user_id = None
        for cookie_str in getattr(self, "_pending_cookies", []):
            self.send_header("Set-Cookie", cookie_str)
        self._pending_cookies = []
        super().end_headers()

    def _get_user_id(self) -> str:
        cookie_header = self.headers.get("Cookie", "")
        if cookie_header:
            c = http.cookies.SimpleCookie()
            try:
                c.load(cookie_header)
                if "shipshape_uid" in c:
                    val = c["shipshape_uid"].value
                    sanitized = re.sub(r"[^a-zA-Z0-9_\-]", "", val)[:64]
                    if sanitized:
                        return sanitized
            except Exception:
                pass
        if not hasattr(self, "_new_user_id") or not self._new_user_id:
            self._new_user_id = f"u_{secrets.token_urlsafe(16)}"
        return self._new_user_id

    def send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path: Path, content_type: str, inline_filename: Optional[str] = None) -> None:
        if not path.exists():
            self.send_error(404, "File not found")
            return
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        if inline_filename:
            self.send_header("Content-Disposition", f'inline; filename="{inline_filename}"')
        self.end_headers()
        self.wfile.write(data)

    def send_redirect(self, location: str) -> None:
        self.send_response(302)
        self.send_header("Location", location)
        self.end_headers()

    def _is_authorized(self) -> bool:
        pwd = get_app_password()
        if not pwd:
            return True

        # Check explicit password header
        hdr_pass = self.headers.get("X-Shipshape-Password")
        if hdr_pass and secrets.compare_digest(hdr_pass, pwd):
            return True

        # Check Authorization Bearer header
        auth_hdr = self.headers.get("Authorization", "")
        if auth_hdr.startswith("Bearer "):
            token = auth_hdr[7:].strip()
            if secrets.compare_digest(token, pwd) or secrets.compare_digest(token, _hash_session_token(pwd)):
                return True

        # Check HTTP-only session cookie
        return _is_session_valid(self.headers.get("Cookie", ""), pwd)

    def _read_json_body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return {}
        if length <= 0 or length > MAX_JSON_PAYLOAD_BYTES:
            return {}
        raw_body = self.rfile.read(length).decode("utf-8", errors="replace")
        try:
            return json.loads(raw_body) if raw_body else {}
        except Exception:
            return {}

    def _send_not_found(self, route: str) -> None:
        if route.startswith("/api/"):
            self.send_json({"ok": False, "error": f"API endpoint not found: {route}"}, status=404)
        else:
            self.send_error(404, "Not found")

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        route = parsed.path
        query = urllib.parse.parse_qs(parsed.query)
        uid = self._get_user_id()

        if route in ("/", "/index.html"):
            self.send_file(INDEX_FILE, "text/html; charset=utf-8")
        elif route == "/api/state":
            self.send_json(build_dashboard_state(unlocked=self._is_authorized(), user_id=uid))
        elif route == "/api/plan-pdf":
            if not self._is_authorized():
                self.send_json({"ok": False, "error": "Unauthorized. Passcode required."}, status=401)
                return
            target_pdf, fname = get_active_plan_pdf(user_id=uid)
            self.send_file(target_pdf, "application/pdf", inline_filename=fname)
        elif route == "/auth/strava":
            if not self._is_authorized():
                self.send_redirect("/?auth_error=Unauthorized.+Please+unlock+with+passcode+first.")
                return
            self._handle_auth_strava()
        elif route == "/oauth/callback":
            self._handle_oauth_callback(query)
        elif route.startswith("/api/activity/"):
            if not self._is_authorized():
                self.send_json({"ok": False, "error": "Unauthorized. Passcode required."}, status=401)
                return
            self._handle_activity_detail(route, user_id=uid)
        else:
            self._send_not_found(route)

    def do_POST(self):
        route = urllib.parse.urlparse(self.path).path
        body = self._read_json_body()
        uid = self._get_user_id()

        # Public auth routes
        if route == "/api/login":
            self._handle_login(body)
            return
        if route == "/api/logout":
            self._handle_logout()
            return

        # Protected routes requiring passcode if configured
        if not self._is_authorized():
            self.send_json({"ok": False, "error": "Unauthorized. Passcode required."}, status=401)
            return

        post_routes = {
            "/api/upload-plan": lambda b: self._handle_upload_plan(b, user_id=uid),
            "/api/remove-plan": lambda b: self._handle_remove_plan(b, user_id=uid),
            "/api/sync": lambda b: self._handle_sync(b, user_id=uid),
            "/api/config": self._handle_config,
            "/api/disconnect": lambda b: self._handle_disconnect(b, user_id=uid),
        }
        handler_fn = post_routes.get(route)
        if handler_fn:
            handler_fn(body)
        else:
            self._send_not_found(route)

    def _handle_login(self, body: dict) -> None:
        pwd = get_app_password()
        if not pwd:
            self.send_json({"ok": True, "message": "No passcode configured."})
            return
        submitted = str(body.get("password", ""))
        if secrets.compare_digest(submitted, pwd):
            token = _hash_session_token(pwd)
            proto = self.headers.get("X-Forwarded-Proto", "http")
            secure_attr = "; Secure" if proto == "https" else ""
            cookie_val = f"shipshape_session={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age=2592000{secure_attr}"
            if not hasattr(self, "_pending_cookies"):
                self._pending_cookies = []
            self._pending_cookies.append(cookie_val)
            self.send_json({"ok": True})
        else:
            self.send_json({"ok": False, "error": "Incorrect passcode. Please try again."}, status=401)

    def _handle_logout(self) -> None:
        cookie_val = "shipshape_session=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0"
        if not hasattr(self, "_pending_cookies"):
            self._pending_cookies = []
        self._pending_cookies.append(cookie_val)
        self.send_json({"ok": True})

    def _handle_auth_strava(self) -> None:
        env = load_env()
        client_id = env.get("STRAVA_CLIENT_ID", "")
        if not client_id or client_id == "your_client_id_here":
            self.send_error(400, "STRAVA_CLIENT_ID is not configured in .env")
            return
        host_hdr = self.headers.get("Host") or f"localhost:{self.server.server_address[1]}"
        if host_hdr.startswith("127.0.0.1"):
            host_hdr = host_hdr.replace("127.0.0.1", "localhost", 1)

        proto = self.headers.get("X-Forwarded-Proto", "http")
        redirect_uri = f"{proto}://{host_hdr}/oauth/callback"

        # Generate cryptographic anti-CSRF state token bound to user ID
        uid = self._get_user_id()
        state_token = secrets.token_urlsafe(24)
        _ACTIVE_OAUTH_STATES[state_token] = uid
        if len(_ACTIVE_OAUTH_STATES) > 100:
            oldest = next(iter(_ACTIVE_OAUTH_STATES))
            _ACTIVE_OAUTH_STATES.pop(oldest, None)

        self.send_redirect(build_authorize_url(client_id, redirect_uri, state=state_token))

    def _handle_oauth_callback(self, query: dict) -> None:
        if "error" in query:
            self.send_redirect(f"/?auth_error={urllib.parse.quote(query['error'][0])}")
            return
        code = query.get("code", [None])[0]
        if not code:
            self.send_error(400, "Missing authorization code from Strava")
            return

        state = query.get("state", [None])[0]
        if not state or state not in _ACTIVE_OAUTH_STATES:
            uid = self._get_user_id()
        else:
            uid = _ACTIVE_OAUTH_STATES.pop(state)

        env = load_env()
        try:
            token_data = exchange_code_for_tokens(
                env.get("STRAVA_CLIENT_ID", ""),
                env.get("STRAVA_CLIENT_SECRET", ""),
                code,
                user_id=uid,
            )
            # If Strava athlete profile is present, canonically bind session to athlete_<id>
            athlete_id = token_data.get("athlete", {}).get("id")
            final_uid = uid
            if athlete_id:
                athlete_uid = f"athlete_{athlete_id}"
                migrate_user_data(uid, athlete_uid)
                final_uid = athlete_uid
                proto = self.headers.get("X-Forwarded-Proto", "http")
                secure_attr = "; Secure" if proto == "https" else ""
                if not hasattr(self, "_pending_cookies"):
                    self._pending_cookies = []
                self._pending_cookies.append(
                    f"shipshape_uid={athlete_uid}; Path=/; HttpOnly; SameSite=Lax; Max-Age=31536000{secure_attr}"
                )

            sync_strava_activities(user_id=final_uid)
            self.send_redirect("/?synced=1")
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode("utf-8", errors="replace")
            self.send_redirect(f"/?auth_error={urllib.parse.quote(err_body[:200])}")
        except Exception as exc:
            self.send_redirect(f"/?auth_error={urllib.parse.quote(str(exc))}")

    def _handle_activity_detail(self, route: str, user_id: Optional[str] = None) -> None:
        act_id = route.split("/api/activity/", 1)[1].strip("/")
        if not act_id.isdigit():
            self.send_json({"ok": False, "error": "Invalid activity ID"}, status=400)
            return
        try:
            detail = fetch_activity_detail(act_id, user_id=user_id)
            self.send_json({"ok": True, "activity": detail})
        except Exception as exc:
            self.send_json({"ok": False, "error": str(exc)}, status=500)

    def _handle_upload_plan(self, body: dict, user_id: Optional[str] = None) -> None:
        try:
            result = save_uploaded_plan_pdf(
                body.get("filename", "training_plan.pdf"),
                body.get("data_url", ""),
                user_id=user_id,
            )
            self.send_json(result)
        except ValueError as exc:
            self.send_json({"ok": False, "error": str(exc)}, status=400)
        except Exception as exc:
            self.send_json({"ok": False, "error": str(exc)}, status=500)

    def _handle_remove_plan(self, _body: dict, user_id: Optional[str] = None) -> None:
        remove_uploaded_plan_pdf(user_id=user_id)
        self.send_json({"ok": True})

    def _handle_sync(self, _body: dict, user_id: Optional[str] = None) -> None:
        try:
            cache = sync_strava_activities(user_id=user_id)
            self.send_json(
                {
                    "ok": True,
                    "synced_at": cache.get("synced_at"),
                    "activities": cache.get("activities", []),
                }
            )
        except urllib.error.HTTPError as exc:
            err_text = exc.read().decode("utf-8", errors="replace")
            self.send_json({"ok": False, "error": f"Strava API error ({exc.code}): {err_text}"}, status=500)
        except Exception as exc:
            self.send_json({"ok": False, "error": str(exc)}, status=500)

    def _handle_config(self, body: dict) -> None:
        env = load_env()
        # If credentials already exist, prevent unauthorized unauthenticated overwrite
        if has_strava_credentials(env) and not self._is_authorized():
            self.send_json({"ok": False, "error": "Credentials already configured. Reconfiguration unauthorized."}, status=403)
            return
        cid = body.get("client_id", "").strip()
        csec = body.get("client_secret", "").strip()
        if not cid or not csec:
            self.send_json({"ok": False, "error": "Both Client ID and Client Secret are required."}, status=400)
            return
        try:
            save_env(cid, csec)
            self.send_json({"ok": True})
        except ValueError as exc:
            self.send_json({"ok": False, "error": str(exc)}, status=400)

    def _handle_disconnect(self, _body: dict, user_id: Optional[str] = None) -> None:
        clear_tokens(user_id=user_id)
        self.send_json({"ok": True})


def run_server() -> None:
    """Bind the local HTTP server to the configured or fallback port and serve forever."""
    candidate_ports = [PORT, 8080, 8765, 8502, 0]
    httpd = None
    for port in candidate_ports:
        try:
            httpd = ReusableTCPServer((HOST, port), DashboardHandler)
            break
        except OSError:
            continue
    if httpd is None:
        raise RuntimeError("Could not bind to any local port.")

    actual_port = httpd.server_address[1]
    print(f"Ship Shape Strava Dashboard running at http://localhost:{actual_port}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server.")
    finally:
        httpd.server_close()
