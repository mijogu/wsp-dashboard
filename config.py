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

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.enc")
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
