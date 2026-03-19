"""
Tests for config.py — AES-256-GCM encrypted config and session persistence.

Run with:  python -m unittest discover tests/
"""

import base64
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config


class ConfigTestBase(unittest.TestCase):
    """Redirect CONFIG_PATH and SESSION_PATH to temp files for every test."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._config_path  = os.path.join(self._tmpdir, "config.enc")
        self._session_path = os.path.join(self._tmpdir, ".session")
        self._p1 = patch.object(config, "CONFIG_PATH",  self._config_path)
        self._p2 = patch.object(config, "SESSION_PATH", self._session_path)
        self._p1.start()
        self._p2.start()

    def tearDown(self):
        self._p1.stop()
        self._p2.stop()


# ─── encrypt / decrypt ────────────────────────────────────────────────────────

class TestEncryptDecrypt(ConfigTestBase):

    def test_roundtrip(self):
        settings = {"mwpUrl": "https://example.com", "mwpApiKey": "secret"}
        enc = config.encrypt_config(settings, "my-pass")
        self.assertEqual(config.decrypt_config(enc, "my-pass"), settings)

    def test_wrong_passphrase_raises(self):
        enc = config.encrypt_config({"k": "v"}, "correct")
        with self.assertRaises(Exception):   # InvalidTag
            config.decrypt_config(enc, "wrong")

    def test_each_encryption_produces_unique_ciphertext(self):
        settings = {"x": 1}
        enc1 = config.encrypt_config(settings, "pass")
        enc2 = config.encrypt_config(settings, "pass")
        self.assertNotEqual(enc1, enc2)

    def test_empty_settings_roundtrip(self):
        enc = config.encrypt_config({}, "pass")
        self.assertEqual(config.decrypt_config(enc, "pass"), {})

    def test_nested_settings_roundtrip(self):
        settings = {"nested": {"a": [1, 2, 3], "b": True}, "n": None}
        enc = config.encrypt_config(settings, "pass")
        self.assertEqual(config.decrypt_config(enc, "pass"), settings)

    def test_unicode_passphrase_roundtrip(self):
        settings = {"k": "v"}
        enc = config.encrypt_config(settings, "pässwörd-日本語")
        self.assertEqual(config.decrypt_config(enc, "pässwörd-日本語"), settings)

    def test_truncated_passphrase_raises(self):
        enc = config.encrypt_config({"k": "v"}, "correct")
        with self.assertRaises(Exception):
            config.decrypt_config(enc, "correc")


# ─── save_config / load_config ────────────────────────────────────────────────

class TestSaveLoadConfig(ConfigTestBase):

    def test_save_and_load_roundtrip(self):
        settings = {"mwpUrl": "https://mwp.example.com", "cfToken": "abc123"}
        config.save_config(settings, "pass")
        self.assertEqual(config.load_config("pass"), settings)

    def test_load_returns_empty_dict_when_no_file(self):
        self.assertEqual(config.load_config("any"), {})

    def test_config_exists_false_before_save(self):
        self.assertFalse(config.config_exists())

    def test_config_exists_true_after_save(self):
        config.save_config({"k": "v"}, "pass")
        self.assertTrue(config.config_exists())

    def test_overwrite_updates_config(self):
        config.save_config({"k": "old"}, "pass")
        config.save_config({"k": "new"}, "pass")
        self.assertEqual(config.load_config("pass")["k"], "new")

    def test_load_wrong_passphrase_raises(self):
        config.save_config({"k": "v"}, "correct")
        with self.assertRaises(Exception):
            config.load_config("wrong")


# ─── export / import ──────────────────────────────────────────────────────────

class TestExportImport(ConfigTestBase):

    def test_export_empty_string_when_no_file(self):
        self.assertEqual(config.export_config("pass"), "")

    def test_export_returns_valid_base64(self):
        config.save_config({"k": "v"}, "pass")
        exported = config.export_config("pass")
        decoded = base64.b64decode(exported)
        self.assertGreater(len(decoded), 0)

    def test_import_roundtrip(self):
        original = {"mwpApiKey": "my-key", "uptimeKey": "uptime-key"}
        config.save_config(original, "pass")
        exported = config.export_config("pass")

        os.remove(config.CONFIG_PATH)
        self.assertFalse(config.config_exists())

        imported = config.import_config(exported, "pass")
        self.assertEqual(imported, original)
        self.assertTrue(config.config_exists())

    def test_import_wrong_passphrase_raises_and_does_not_save(self):
        config.save_config({"k": "v"}, "correct")
        exported = config.export_config("correct")
        os.remove(config.CONFIG_PATH)

        with self.assertRaises(Exception):
            config.import_config(exported, "wrong")

        self.assertFalse(config.config_exists())


# ─── Session persistence ──────────────────────────────────────────────────────

class TestSession(ConfigTestBase):

    def test_not_exists_initially(self):
        self.assertFalse(config.session_exists())

    def test_load_none_when_missing(self):
        self.assertIsNone(config.load_session())

    def test_save_and_load(self):
        config.save_session("my-secret-passphrase")
        self.assertEqual(config.load_session(), "my-secret-passphrase")

    def test_exists_after_save(self):
        config.save_session("pass")
        self.assertTrue(config.session_exists())

    def test_clear_removes_file(self):
        config.save_session("pass")
        config.clear_session()
        self.assertFalse(config.session_exists())
        self.assertIsNone(config.load_session())

    def test_clear_noop_when_missing(self):
        config.clear_session()   # should not raise

    def test_passphrase_not_stored_in_plaintext(self):
        passphrase = "super-secret-password"
        config.save_session(passphrase)
        with open(config.SESSION_PATH) as f:
            raw = f.read()
        self.assertNotIn(passphrase, raw)

    def test_unicode_passphrase_roundtrip(self):
        config.save_session("pässwörd-日本語")
        self.assertEqual(config.load_session(), "pässwörd-日本語")


if __name__ == "__main__":
    unittest.main(verbosity=2)
