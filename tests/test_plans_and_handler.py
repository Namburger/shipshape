"""Unit tests for strap.plans and strap.handler HTTP endpoints."""

import base64
import io
import json
import os
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from strap import config, handler, plans, storage

REPO_ROOT = Path(__file__).resolve().parent.parent
JANE_PDF = REPO_ROOT / "assets" / "jane_doe_training.pdf"
JOHN_PDF = REPO_ROOT / "assets" / "john_doe_training.pdf"
SAMPLE_PDF = JANE_PDF


class _FakeSocket:
    """In-memory socket replacement for driving DashboardHandler without TCP bind."""

    def __init__(self, raw_request: bytes):
        self._rfile = io.BytesIO(raw_request)
        self.wfile = io.BytesIO()

    def makefile(self, mode: str, *args, **kwargs):
        if "r" in mode:
            return self._rfile
        return self.wfile

    def sendall(self, data: bytes) -> None:
        self.wfile.write(data)

    def close(self) -> None:
        pass


class _SilentHandler(handler.DashboardHandler):
    def log_message(self, format, *args):
        pass


def _dispatch_http(method: str, path: str, body: dict = None, headers: dict = None):
    raw_body = json.dumps(body).encode("utf-8") if body is not None else b""
    hdr_lines = [f"{method} {path} HTTP/1.1", "Host: 127.0.0.1:8501"]
    if headers:
        for k, v in headers.items():
            hdr_lines.append(f"{k}: {v}")
    if body is not None:
        hdr_lines.append("Content-Type: application/json")
        hdr_lines.append(f"Content-Length: {len(raw_body)}")
    raw_req = ("\r\n".join(hdr_lines) + "\r\n\r\n").encode("utf-8") + raw_body

    sock = _FakeSocket(raw_req)
    server_stub = types.SimpleNamespace(server_address=("127.0.0.1", 8501))
    _SilentHandler(sock, ("127.0.0.1", 55000), server_stub)

    raw_resp = sock.wfile.getvalue()
    head_bytes, _, resp_body = raw_resp.partition(b"\r\n\r\n")
    head_lines = head_bytes.decode("latin1").split("\r\n")
    status_code = int(head_lines[0].split(" ", 2)[1])
    resp_headers = {}
    for line in head_lines[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            resp_headers[k.strip().lower()] = v.strip()
    return status_code, resp_headers, resp_body


class PlansAndHandlerTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tdir = Path(self._tmp.name)
        self.pdf_bytes = SAMPLE_PDF.read_bytes()
        plans._PARSED_PLAN_CACHE = None

        self.patches = [
            mock.patch.object(config, "ENV_FILE", self.tdir / ".env"),
            mock.patch.object(plans, "DATA_DIR", self.tdir),
            mock.patch.object(plans, "UPLOADED_PLAN_PDF", self.tdir / "uploaded_plan.pdf"),
            mock.patch.object(plans, "LEGACY_UPLOADED_PLAN_IMG", self.tdir / "uploaded_plan.png"),
            mock.patch.object(storage, "DATA_DIR", self.tdir),
            mock.patch.object(storage, "APP_STATE_FILE", self.tdir / ".app_state.json"),
            mock.patch.object(storage, "TOKENS_FILE", self.tdir / ".strava_tokens.json"),
            mock.patch.object(storage, "CACHE_FILE", self.tdir / ".strava_cache.json"),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        plans._PARSED_PLAN_CACHE = None
        for p in reversed(self.patches):
            p.stop()
        self._tmp.cleanup()

    def test_upload_parse_cache_and_remove_lifecycle(self):
        # Empty state before plan upload
        with mock.patch.dict(os.environ, {}, clear=True):
            st0 = plans.build_dashboard_state()
            self.assertFalse(st0["configured"])
            self.assertFalse(st0["plan_uploaded"])
            self.assertIsNone(st0["plan"])

        data_url = "data:application/pdf;base64," + base64.b64encode(self.pdf_bytes).decode("ascii")
        res = plans.save_uploaded_plan_pdf("my_plan.pdf", data_url)
        self.assertTrue(res["ok"])
        self.assertEqual(len(res["plan"]["weeks"]), 16)
        self.assertEqual(plans.get_active_plan_pdf()[1], "my_plan.pdf")

        # Subsequent /api/state build uses in-memory mtime/size cache rather than reparsing
        with mock.patch("strap.plans.parse_training_plan_pdf") as m_parse:
            st1 = plans.build_dashboard_state()
            self.assertTrue(st1["plan_uploaded"])
            self.assertEqual(st1["plan"]["metadata"]["athlete"], "Jane Doe")
            self.assertEqual(st1["plan"]["metadata"]["race_name"], "Houston Marathon")
            m_parse.assert_not_called()

        # Remove cleans file and resets state
        plans.remove_uploaded_plan_pdf()
        st2 = plans.build_dashboard_state()
        self.assertFalse(st2["plan_uploaded"])
        self.assertIsNone(st2["plan"])
        self.assertFalse((self.tdir / "uploaded_plan.pdf").exists())

    def test_http_routes_get_and_post(self):
        # GET / serves index.html
        code, hdrs, body = _dispatch_http("GET", "/")
        self.assertEqual(code, 200)
        self.assertIn("text/html", hdrs["content-type"])
        self.assertIn(b"Ship Shape Marathon Tracker", body)

        # GET /api/state
        code, _, body = _dispatch_http("GET", "/api/state")
        self.assertEqual(code, 200)
        self.assertFalse(json.loads(body)["plan_uploaded"])

        # POST /api/upload-plan rejects non-PDF cleanly with 400
        bad_url = "data:application/pdf;base64," + base64.b64encode(b"not a pdf").decode("ascii")
        code, _, body = _dispatch_http(
            "POST", "/api/upload-plan", {"filename": "bad.pdf", "data_url": bad_url}
        )
        self.assertEqual(code, 400)
        self.assertFalse(json.loads(body)["ok"])

        # POST /api/upload-plan succeeds on valid PDF and serves /api/plan-pdf
        good_url = "data:application/pdf;base64," + base64.b64encode(self.pdf_bytes).decode("ascii")
        code, _, body = _dispatch_http(
            "POST", "/api/upload-plan", {"filename": "jane.pdf", "data_url": good_url}
        )
        self.assertEqual(code, 200)
        payload = json.loads(body)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["plan"]["metadata"]["athlete"], "Jane Doe")
        self.assertEqual(len(payload["plan"]["weeks"]), 16)

        # Replacing with John Doe plan updates /api/state and /api/plan-pdf cleanly
        john_url = "data:application/pdf;base64," + base64.b64encode(JOHN_PDF.read_bytes()).decode("ascii")
        code, _, body = _dispatch_http(
            "POST", "/api/upload-plan", {"filename": "john.pdf", "data_url": john_url}
        )
        self.assertEqual(code, 200)
        john_payload = json.loads(body)
        self.assertTrue(john_payload["ok"])
        self.assertEqual(john_payload["plan"]["metadata"]["athlete"], "John Doe")
        self.assertEqual(john_payload["plan"]["metadata"]["race_name"], "Austin Marathon")

        code, hdrs, body = _dispatch_http("GET", "/api/plan-pdf")
        self.assertEqual(code, 200)
        self.assertEqual(hdrs["content-type"], "application/pdf")
        self.assertIn("john.pdf", hdrs.get("content-disposition", ""))
        self.assertTrue(body.startswith(b"%PDF"))

        # POST /api/config + GET /auth/strava normalizes 127.0.0.1 -> localhost
        code, _, body = _dispatch_http(
            "POST", "/api/config", {"client_id": "5555", "client_secret": "sec-5555"}
        )
        self.assertEqual(code, 200)
        with mock.patch.dict(os.environ, {}, clear=True):
            code, hdrs, _ = _dispatch_http("GET", "/auth/strava")
            self.assertEqual(code, 302)
            self.assertIn("redirect_uri=http%3A%2F%2Flocalhost%3A8501%2Foauth%2Fcallback", hdrs["location"])

        # POST /api/disconnect + POST /api/remove-plan
        storage.save_tokens({"access_token": "abc"})
        code, _, _ = _dispatch_http("POST", "/api/disconnect", {})
        self.assertEqual(code, 200)
        self.assertEqual(storage.load_tokens(), {})

        code, _, _ = _dispatch_http("POST", "/api/remove-plan", {})
        self.assertEqual(code, 200)

        # Unknown API route returns JSON 404; unknown non-API route returns 404
        code, _, body = _dispatch_http("GET", "/api/nonexistent")
        self.assertEqual(code, 404)
        self.assertEqual(json.loads(body)["error"], "API endpoint not found: /api/nonexistent")

        code, _, _ = _dispatch_http("GET", "/nonexistent")
        self.assertEqual(code, 404)

    def test_security_headers_and_passcode_gate(self):
        # 1. Responses include standard security headers
        code, hdrs, _ = _dispatch_http("GET", "/")
        self.assertEqual(code, 200)
        self.assertEqual(hdrs.get("x-content-type-options"), "nosniff")
        self.assertEqual(hdrs.get("x-frame-options"), "DENY")
        self.assertEqual(hdrs.get("referrer-policy"), "strict-origin-when-cross-origin")

        # 2. Activity ID sanitization rejects traversal attacks
        code, _, body = _dispatch_http("GET", "/api/activity/..%2F..%2Fathlete")
        self.assertEqual(code, 400)
        self.assertFalse(json.loads(body)["ok"])

        code, _, body = _dispatch_http("GET", "/api/activity/bad_id_abc")
        self.assertEqual(code, 400)

        # 3. Passcode gating when APP_PASSWORD is configured
        with mock.patch.dict(os.environ, {"APP_PASSWORD": "crewsecret123"}, clear=False):
            # Unauthenticated state does not leak private info
            code, _, body = _dispatch_http("GET", "/api/state")
            self.assertEqual(code, 200)
            st = json.loads(body)
            self.assertTrue(st["password_required"])
            self.assertFalse(st["unlocked"])
            self.assertEqual(st["activities"], [])
            self.assertIsNone(st["plan"])

            # Mutating and protected endpoints require authorization
            code, _, _ = _dispatch_http("POST", "/api/sync", {})
            self.assertEqual(code, 401)
            code, _, _ = _dispatch_http("GET", "/api/plan-pdf")
            self.assertEqual(code, 401)

            # Incorrect passcode fails
            code, _, login_err = _dispatch_http("POST", "/api/login", {"password": "wrong"})
            self.assertEqual(code, 401)
            self.assertFalse(json.loads(login_err)["ok"])

            # Correct passcode logs in and issues session cookie
            code, login_hdrs, login_ok = _dispatch_http("POST", "/api/login", {"password": "crewsecret123"})
            self.assertEqual(code, 200)
            self.assertTrue(json.loads(login_ok)["ok"])
            cookie_hdr = login_hdrs.get("set-cookie")
            self.assertIn("shipshape_session=", cookie_hdr)
            self.assertIn("HttpOnly", cookie_hdr)

            # Authenticated request with session cookie succeeds
            cookie_val = cookie_hdr.split(";")[0]
            code, _, unlocked_body = _dispatch_http("GET", "/api/state", headers={"Cookie": cookie_val})
            self.assertEqual(code, 200)
            unlocked_st = json.loads(unlocked_body)
            self.assertTrue(unlocked_st["password_required"])
            self.assertTrue(unlocked_st["unlocked"])

            # Header-based auth (X-Shipshape-Password) also works
            code, _, hdr_body = _dispatch_http(
                "GET", "/api/state", headers={"X-Shipshape-Password": "crewsecret123"}
            )
            self.assertEqual(code, 200)
            self.assertTrue(json.loads(hdr_body)["unlocked"])

            # Logout clears session cookie
            code, logout_hdrs, _ = _dispatch_http("POST", "/api/logout", {})
            self.assertEqual(code, 200)
            self.assertIn("Max-Age=0", logout_hdrs.get("set-cookie", ""))


if __name__ == "__main__":
    unittest.main()
