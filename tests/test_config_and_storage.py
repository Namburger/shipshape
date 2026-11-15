"""Unit tests for strap.config and strap.storage."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from strap import config, storage


class ConfigTests(unittest.TestCase):
    def test_load_env_parses_quotes_comments_and_env_overrides(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text(
                '# comment line\n'
                'STRAVA_CLIENT_ID="12345"\n'
                "STRAVA_CLIENT_SECRET='secret-from-file'\n"
                "IGNORED_INVALID_LINE\n",
                encoding="utf-8",
            )
            with mock.patch.object(config, "ENV_FILE", env_path), mock.patch.dict(
                os.environ, {"STRAVA_CLIENT_SECRET": "override-secret"}, clear=False
            ):
                loaded = config.load_env()
                self.assertEqual(loaded["STRAVA_CLIENT_ID"], "12345")
                self.assertEqual(loaded["STRAVA_CLIENT_SECRET"], "override-secret")

    def test_save_env_and_has_strava_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            with mock.patch.object(config, "ENV_FILE", env_path), mock.patch.dict(
                os.environ, {}, clear=True
            ):
                config.save_env(" 9988 ", " supersecret ")
                loaded = config.load_env()
                self.assertEqual(loaded["STRAVA_CLIENT_ID"], "9988")
                self.assertEqual(loaded["STRAVA_CLIENT_SECRET"], "supersecret")
                self.assertTrue(config.has_strava_credentials(loaded))

        self.assertFalse(config.has_strava_credentials({}))
        self.assertFalse(
            config.has_strava_credentials(
                {"STRAVA_CLIENT_ID": "your_client_id_here", "STRAVA_CLIENT_SECRET": "abc"}
            )
        )
        self.assertFalse(
            config.has_strava_credentials(
                {"STRAVA_CLIENT_ID": "123", "STRAVA_CLIENT_SECRET": "your_client_secret_here"}
            )
        )


class StorageTests(unittest.TestCase):
    def test_app_state_tokens_and_cache_roundtrip_and_corruption_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            tdir = Path(tmp)
            app_file = tdir / ".app_state.json"
            tok_file = tdir / ".strava_tokens.json"
            cache_file = tdir / ".strava_cache.json"

            with mock.patch.multiple(
                storage,
                DATA_DIR=tdir,
                APP_STATE_FILE=app_file,
                TOKENS_FILE=tok_file,
                CACHE_FILE=cache_file,
            ):
                # Defaults when files do not exist
                self.assertEqual(storage.load_app_state(), {"plan_uploaded": False})
                self.assertEqual(storage.load_tokens(), {})
                self.assertEqual(
                    storage.load_cache(), {"activities": [], "details": {}, "synced_at": None}
                )

                # Roundtrip persistence
                storage.save_app_state({"plan_uploaded": True, "uploaded_filename": "plan.pdf"})
                self.assertEqual(
                    storage.load_app_state(),
                    {"plan_uploaded": True, "uploaded_filename": "plan.pdf"},
                )

                storage.save_tokens({"access_token": "tok-1", "refresh_token": "ref-1"})
                self.assertEqual(storage.load_tokens()["access_token"], "tok-1")
                storage.clear_tokens()
                self.assertFalse(tok_file.exists())
                self.assertEqual(storage.load_tokens(), {})

                storage.save_cache({"activities": [{"id": 1}], "details": {}, "synced_at": "now"})
                self.assertEqual(storage.load_cache()["activities"], [{"id": 1}])

                # Corrupted JSON falls back gracefully instead of crashing
                cache_file.write_text("{bad json", encoding="utf-8")
                self.assertEqual(
                    storage.load_cache(), {"activities": [], "details": {}, "synced_at": None}
                )


if __name__ == "__main__":
    unittest.main()
