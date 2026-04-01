"""Fernet-based encryption for API keys stored in config.yaml.

Encrypted values use an ``enc:`` prefix so the config loader can distinguish
plain-text from encrypted values.  The symmetric key lives in
``.lean_ai/.keyfile`` with owner-only permissions (0o600).
"""

from __future__ import annotations

import logging
import os
import stat
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

log = logging.getLogger(__name__)

ENCRYPTED_PREFIX = "enc:"


def is_encrypted(value: str) -> bool:
    """Return *True* if *value* carries the encrypted prefix."""
    return value.startswith(ENCRYPTED_PREFIX)


def get_or_create_key(keyfile_path: Path) -> bytes:
    """Load or auto-generate a Fernet key at *keyfile_path*.

    On creation the file is set to ``0o600`` (owner read/write only).
    """
    if keyfile_path.exists():
        return keyfile_path.read_bytes().strip()

    keyfile_path.parent.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key()
    keyfile_path.write_bytes(key)

    # Best-effort permission restriction (no-op on Windows).
    try:
        os.chmod(keyfile_path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass

    log.info("Generated new encryption key at %s", keyfile_path)
    return key


def encrypt_value(plaintext: str, keyfile_path: Path) -> str:
    """Encrypt *plaintext* and return ``enc:<fernet_token>``."""
    key = get_or_create_key(keyfile_path)
    token = Fernet(key).encrypt(plaintext.encode()).decode()
    return f"{ENCRYPTED_PREFIX}{token}"


def decrypt_value(value: str, keyfile_path: Path) -> str:
    """Decrypt *value* if it starts with ``enc:``, otherwise return as-is.

    Returns an empty string (with a warning) when the keyfile is missing or
    the ciphertext is corrupt — never raises.
    """
    if not is_encrypted(value):
        return value

    token = value[len(ENCRYPTED_PREFIX) :]
    if not keyfile_path.exists():
        log.warning(
            "Keyfile %s not found — cannot decrypt API key. "
            "Run 'python -m lean_ai encrypt-key' to create one.",
            keyfile_path,
        )
        return ""

    try:
        key = keyfile_path.read_bytes().strip()
        return Fernet(key).decrypt(token.encode()).decode()
    except (InvalidToken, ValueError, Exception) as exc:
        log.warning("Failed to decrypt API key: %s", exc)
        return ""
