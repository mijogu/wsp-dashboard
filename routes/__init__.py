"""Shared state and helpers for all route modules."""
import sys
import threading
import time

# ─── In-memory session state ──────────────────────────────────
_settings = {}
_passphrase = None
_lock = threading.Lock()
_logs = []       # recent API call logs
_logs_lock = threading.Lock()

MAX_LOGS = 200


def add_log(source, level, message, detail=None):
    """Add a log entry. level: 'info', 'ok', 'warn', 'error'"""
    entry = {
        "ts": time.time(),
        "time": time.strftime("%H:%M:%S"),
        "source": source,
        "level": level,
        "message": message,
    }
    if detail:
        entry["detail"] = str(detail)[:500]
    with _logs_lock:
        _logs.append(entry)
        if len(_logs) > MAX_LOGS:
            _logs[:] = _logs[-MAX_LOGS:]
    # Also print to server console
    prefix = {"ok": "+", "info": "~", "warn": "!", "error": "X"}.get(level, "?")
    sys.stderr.write(f"  [{prefix}] {source}: {message}\n")
    if detail:
        sys.stderr.write(f"      {str(detail)[:200]}\n")


def get_settings():
    with _lock:
        return dict(_settings)


def set_settings(s):
    global _settings
    with _lock:
        _settings = dict(s)


def get_passphrase():
    return _passphrase


def set_passphrase(p):
    global _passphrase
    _passphrase = p
