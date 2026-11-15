"""Unit tests for strap.strava OAuth2 and v3 API helpers."""

import io
import json
import tempfile
import time
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

from strap import config, storage, strava


class _FakeHTTPResponse:
    def __init__(self, payload: object):
        self._buf = io.BytesIO(json.dumps(payload).encode("utf-8"))

    def read(self) -> bytes:
        return self._buf.read()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class StravaClientTests(unittest.TestCase):
    def test_build_authorize_url_uses_force_prompt_and_activity_scope(self):
        url = strava.build_authorize_url("4242", "http://localhost:8501/oauth/callback")
        parsed = urllib.parse.urlparse(url)
        qs = urllib.parse.parse_qs(parsed.query)
        self.assertEqual(f"{parsed.scheme}://{parsed.netloc}{parsed.path}", strava.STRAVA_AUTH_URL)
        self.assertEqual(qs["client_id"], ["4242"])
        self.assertEqual(qs["redirect_uri"], ["http://localhost:8501/oauth/callback"])
        self.assertEqual(qs["approval_prompt"], ["force"])
        self.assertEqual(qs["scope"], ["read,activity:read_all"])

    def test_get_valid_access_token_refreshes_expired_token_preserving_athlete(self):
        with tempfile.TemporaryDirectory() as tmp:
            tdir = Path(tmp)
            env_file = tdir / ".env"
            tok_file = tdir / ".strava_tokens.json"
            env_file.write_text("STRAVA_CLIENT_ID=111\nSTRAVA_CLIENT_SECRET=sec\n", encoding="utf-8")

            with mock.patch.object(config, "ENV_FILE", env_file), mock.patch.multiple(
                storage, DATA_DIR=tdir, TOKENS_FILE=tok_file
            ):
                # Fresh token returns without network call
                storage.save_tokens(
                    {"access_token": "fresh-tok", "expires_at": int(time.time()) + 3600}
                )
                with mock.patch("urllib.request.urlopen") as m_open:
                    self.assertEqual(strava.get_valid_access_token(), "fresh-tok")
                    m_open.assert_not_called()

                # Expired token triggers refresh and keeps athlete profile
                storage.save_tokens(
                    {
                        "access_token": "old-tok",
                        "refresh_token": "ref-1",
                        "expires_at": int(time.time()) - 10,
                        "athlete": {"id": 7, "firstname": "Jane"},
                    }
                )
                with mock.patch(
                    "urllib.request.urlopen",
                    return_value=_FakeHTTPResponse(
                        {
                            "access_token": "new-tok",
                            "refresh_token": "ref-2",
                            "expires_at": int(time.time()) + 7200,
                        }
                    ),
                ) as m_open:
                    tok = strava.get_valid_access_token()
                    self.assertEqual(tok, "new-tok")
                    m_open.assert_called_once()
                    saved = storage.load_tokens()
                    self.assertEqual(saved["access_token"], "new-tok")
                    self.assertEqual(saved["athlete"], {"id": 7, "firstname": "Jane"})

    def test_sync_activities_pagination_and_fetch_activity_detail_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            tdir = Path(tmp)
            tok_file = tdir / ".strava_tokens.json"
            cache_file = tdir / ".strava_cache.json"

            with mock.patch.multiple(
                storage, DATA_DIR=tdir, TOKENS_FILE=tok_file, CACHE_FILE=cache_file
            ):
                storage.save_tokens(
                    {"access_token": "valid-tok", "expires_at": int(time.time()) + 3600}
                )
                page1 = [{"id": i} for i in range(200)]
                page2 = [{"id": 200}, {"id": 201}]

                with mock.patch(
                    "urllib.request.urlopen",
                    side_effect=[_FakeHTTPResponse(page1), _FakeHTTPResponse(page2)],
                ) as m_open:
                    cache = strava.sync_strava_activities()
                    self.assertEqual(len(cache["activities"]), 202)
                    self.assertIsNotNone(cache["synced_at"])
                    self.assertEqual(m_open.call_count, 2)
                    first_req = m_open.call_args_list[0].args[0]
                    qs = urllib.parse.parse_qs(urllib.parse.urlparse(first_req.full_url).query)
                    expected_after = int(time.time()) - 365 * 86400
                    self.assertLess(abs(int(qs["after"][0]) - expected_after), 60)

                # First detail call fetches from API; second hits local cache
                detail_payload = {"id": 200, "laps": [{"lap_index": 1}]}
                with mock.patch(
                    "urllib.request.urlopen", return_value=_FakeHTTPResponse(detail_payload)
                ) as m_open:
                    first = strava.fetch_activity_detail("200")
                    second = strava.fetch_activity_detail("200")
                    self.assertEqual(first, detail_payload)
                    self.assertEqual(second, detail_payload)
                    m_open.assert_called_once()


if __name__ == "__main__":
    unittest.main()
