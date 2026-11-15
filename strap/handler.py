"""HTTP request handler and server runner for the Ship Shape dashboard."""

import http.server
import json
import socketserver
import sys
import urllib.error
import urllib.parse
from pathlib import Path
from typing import Optional

from .config import HOST, INDEX_FILE, PORT, load_env, save_env
from .plans import (
    build_dashboard_state,
    get_active_plan_pdf,
    remove_uploaded_plan_pdf,
    save_uploaded_plan_pdf,
)
from .storage import clear_tokens
from .strava import (
    build_authorize_url,
    exchange_code_for_tokens,
    fetch_activity_detail,
    sync_strava_activities,
)


class ReusableTCPServer(socketserver.TCPServer):
    """TCPServer allowing immediate port reuse after restarts."""

    allow_reuse_address = True


class DashboardHandler(http.server.BaseHTTPRequestHandler):
    """HTTP router serving dashboard assets, Strava OAuth2 callbacks, and JSON APIs."""

    def log_message(self, fmt, *args):
        sys.stderr.write(f"[Strap] {self.address_string()} - {fmt % args}\n")

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

    def _read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        raw_body = self.rfile.read(length).decode("utf-8")
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

        if route in ("/", "/index.html"):
            self.send_file(INDEX_FILE, "text/html; charset=utf-8")
        elif route == "/api/plan-pdf":
            target_pdf, fname = get_active_plan_pdf()
            self.send_file(target_pdf, "application/pdf", inline_filename=fname)
        elif route == "/api/state":
            self.send_json(build_dashboard_state())
        elif route == "/auth/strava":
            self._handle_auth_strava()
        elif route == "/oauth/callback":
            self._handle_oauth_callback(query)
        elif route.startswith("/api/activity/"):
            self._handle_activity_detail(route)
        else:
            self._send_not_found(route)

    def do_POST(self):
        route = urllib.parse.urlparse(self.path).path
        body = self._read_json_body()

        post_routes = {
            "/api/upload-plan": self._handle_upload_plan,
            "/api/remove-plan": self._handle_remove_plan,
            "/api/sync": self._handle_sync,
            "/api/config": self._handle_config,
            "/api/disconnect": self._handle_disconnect,
        }
        handler_fn = post_routes.get(route)
        if handler_fn:
            handler_fn(body)
        else:
            self._send_not_found(route)

    def _handle_auth_strava(self) -> None:
        env = load_env()
        client_id = env.get("STRAVA_CLIENT_ID", "")
        if not client_id or client_id == "your_client_id_here":
            self.send_error(400, "STRAVA_CLIENT_ID is not configured in .env")
            return
        host_hdr = self.headers.get("Host") or f"localhost:{self.server.server_address[1]}"
        if host_hdr.startswith("127.0.0.1"):
            host_hdr = host_hdr.replace("127.0.0.1", "localhost", 1)
        redirect_uri = f"http://{host_hdr}/oauth/callback"
        self.send_redirect(build_authorize_url(client_id, redirect_uri))

    def _handle_oauth_callback(self, query: dict) -> None:
        if "error" in query:
            self.send_redirect(f"/?auth_error={urllib.parse.quote(query['error'][0])}")
            return
        code = query.get("code", [None])[0]
        if not code:
            self.send_error(400, "Missing authorization code from Strava")
            return
        env = load_env()
        try:
            exchange_code_for_tokens(
                env.get("STRAVA_CLIENT_ID", ""),
                env.get("STRAVA_CLIENT_SECRET", ""),
                code,
            )
            sync_strava_activities()
            self.send_redirect("/?synced=1")
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode("utf-8", errors="replace")
            self.send_redirect(f"/?auth_error={urllib.parse.quote(err_body[:200])}")
        except Exception as exc:
            self.send_redirect(f"/?auth_error={urllib.parse.quote(str(exc))}")

    def _handle_activity_detail(self, route: str) -> None:
        act_id = route.split("/api/activity/", 1)[1].strip("/")
        try:
            detail = fetch_activity_detail(act_id)
            self.send_json({"ok": True, "activity": detail})
        except Exception as exc:
            self.send_json({"ok": False, "error": str(exc)}, status=500)

    def _handle_upload_plan(self, body: dict) -> None:
        try:
            result = save_uploaded_plan_pdf(
                body.get("filename", "training_plan.pdf"),
                body.get("data_url", ""),
            )
            self.send_json(result)
        except ValueError as exc:
            self.send_json({"ok": False, "error": str(exc)}, status=400)
        except Exception as exc:
            self.send_json({"ok": False, "error": str(exc)}, status=500)

    def _handle_remove_plan(self, _body: dict) -> None:
        remove_uploaded_plan_pdf()
        self.send_json({"ok": True})

    def _handle_sync(self, _body: dict) -> None:
        try:
            cache = sync_strava_activities()
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
        cid = body.get("client_id", "").strip()
        csec = body.get("client_secret", "").strip()
        if not cid or not csec:
            self.send_json({"ok": False, "error": "Both Client ID and Client Secret are required."}, status=400)
            return
        save_env(cid, csec)
        self.send_json({"ok": True})

    def _handle_disconnect(self, _body: dict) -> None:
        clear_tokens()
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
