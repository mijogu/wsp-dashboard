"""Auth / Config route handlers."""
from routes import get_passphrase, set_passphrase, get_settings, set_settings, add_log
from config import (
    save_config, load_config, config_exists,
    export_config, import_config,
    save_session,
)


class AuthMixin:
    """Mixin for authentication and config endpoints."""

    def _unlock(self, body):
        passphrase = body.get("passphrase", "")
        remember = body.get("remember", False)

        if not passphrase:
            self._json_response({"error": "Passphrase required"}, 400)
            return

        if config_exists():
            try:
                settings = load_config(passphrase)
                set_passphrase(passphrase)
                set_settings(settings)
                if remember:
                    save_session(passphrase)
                    add_log("Auth", "ok", "Session saved — will auto-unlock on next restart")
                self._json_response({"ok": True, "settings": settings})
            except Exception:
                self._json_response({"error": "Wrong passphrase"}, 401)
        else:
            # First time — set passphrase, empty config
            set_passphrase(passphrase)
            set_settings({})
            if remember:
                save_session(passphrase)
            self._json_response({"ok": True, "settings": {}})

    def _get_settings(self):
        if not get_passphrase():
            self._json_response({"error": "Locked"}, 401)
            return
        self._json_response(get_settings())

    def _save_settings(self, body):
        if not get_passphrase():
            self._json_response({"error": "Locked"}, 401)
            return
        settings = body.get("settings", {})
        set_settings(settings)
        try:
            save_config(settings, get_passphrase())
            self._json_response({"ok": True})
        except Exception as e:
            self._json_response({"error": str(e)}, 500)

    def _export_config(self):
        if not get_passphrase():
            self._json_response({"error": "Locked"}, 401)
            return
        data = export_config(get_passphrase())
        self._json_response({"data": data})

    def _import_config(self, body):
        if not get_passphrase():
            self._json_response({"error": "Locked"}, 401)
            return
        try:
            settings = import_config(body.get("data", ""), get_passphrase())
            set_settings(settings)
            self._json_response({"ok": True, "settings": settings})
        except Exception as e:
            self._json_response({"error": f"Import failed: {e}"}, 400)
