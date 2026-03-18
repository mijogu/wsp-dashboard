"""
Encrypted configuration manager.
Stores API keys in an AES-256-GCM encrypted file on disk.
The passphrase is held in memory only for the duration of the server session.
"""

import json
import os
import base64
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(_DIR, "config.enc")
SESSION_PATH = os.path.join(_DIR, ".session")
SALT_LEN = 16
NONCE_LEN = 12
ITERATIONS = 100_000


def _derive_key(passphrase: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=ITERATIONS,
    )
    return kdf.derive(passphrase.encode("utf-8"))


def encrypt_config(settings: dict, passphrase: str) -> bytes:
    salt = os.urandom(SALT_LEN)
    nonce = os.urandom(NONCE_LEN)
    key = _derive_key(passphrase, salt)
    aesgcm = AESGCM(key)
    plaintext = json.dumps(settings).encode("utf-8")
    ciphertext = aesgcm.encrypt(nonce, plaintext, None)
    # Pack: salt + nonce + ciphertext
    return salt + nonce + ciphertext


def decrypt_config(data: bytes, passphrase: str) -> dict:
    salt = data[:SALT_LEN]
    nonce = data[SALT_LEN : SALT_LEN + NONCE_LEN]
    ciphertext = data[SALT_LEN + NONCE_LEN :]
    key = _derive_key(passphrase, salt)
    aesgcm = AESGCM(key)
    plaintext = aesgcm.decrypt(nonce, ciphertext, None)
    return json.loads(plaintext.decode("utf-8"))


def save_config(settings: dict, passphrase: str):
    encrypted = encrypt_config(settings, passphrase)
    with open(CONFIG_PATH, "wb") as f:
        f.write(encrypted)


def load_config(passphrase: str) -> dict:
    if not os.path.exists(CONFIG_PATH):
        return {}
    with open(CONFIG_PATH, "rb") as f:
        data = f.read()
    return decrypt_config(data, passphrase)


def config_exists() -> bool:
    return os.path.exists(CONFIG_PATH)


def export_config(passphrase: str) -> str:
    """Returns base64-encoded encrypted config for file export."""
    if not os.path.exists(CONFIG_PATH):
        return ""
    with open(CONFIG_PATH, "rb") as f:
        return base64.b64encode(f.read()).decode("ascii")


def import_config(b64_data: str, passphrase: str) -> dict:
    """Import from base64 data, verify it decrypts, then save."""
    data = base64.b64decode(b64_data)
    settings = decrypt_config(data, passphrase)  # will raise on wrong passphrase
    with open(CONFIG_PATH, "wb") as f:
        f.write(data)
    return settings


# ──────────────────────────────────────────────────────────────
# Session persistence ("remember me")
# Stores the passphrase in a local dotfile so the server can
# auto-unlock on restart. The passphrase is obfuscated (not
# plaintext) but this is convenience, not high security — it
# lives on your own machine next to the encrypted config.
# ──────────────────────────────────────────────────────────────

def save_session(passphrase: str):
    """Save passphrase to .session file (base64-obfuscated)."""
    encoded = base64.b64encode(passphrase.encode("utf-8")).decode("ascii")
    with open(SESSION_PATH, "w") as f:
        f.write(encoded)


def load_session() -> str | None:
    """Load passphrase from .session file, or None if not found."""
    if not os.path.exists(SESSION_PATH):
        return None
    try:
        with open(SESSION_PATH, "r") as f:
            encoded = f.read().strip()
        return base64.b64decode(encoded).decode("utf-8")
    except Exception:
        return None


def clear_session():
    """Remove the .session file."""
    if os.path.exists(SESSION_PATH):
        os.remove(SESSION_PATH)


def session_exists() -> bool:
    return os.path.exists(SESSION_PATH)
