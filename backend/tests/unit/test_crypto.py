"""Tests for lean_ai.crypto — Fernet encryption for API keys."""

import os
import stat

import pytest

from lean_ai.crypto import (
    ENCRYPTED_PREFIX,
    decrypt_value,
    encrypt_value,
    get_or_create_key,
    is_encrypted,
)


def test_encrypt_decrypt_roundtrip(tmp_path):
    keyfile = tmp_path / ".keyfile"
    plaintext = "sk-test-key-12345"
    encrypted = encrypt_value(plaintext, keyfile)
    assert encrypted.startswith(ENCRYPTED_PREFIX)
    assert decrypt_value(encrypted, keyfile) == plaintext


def test_decrypt_plaintext_passthrough(tmp_path):
    keyfile = tmp_path / ".keyfile"
    assert decrypt_value("plain-value", keyfile) == "plain-value"
    assert decrypt_value("", keyfile) == ""


def test_is_encrypted():
    assert is_encrypted("enc:gAAAAABf") is True
    assert is_encrypted("plain-value") is False
    assert is_encrypted("") is False
    assert is_encrypted("ENC:nope") is False  # case-sensitive


def test_keyfile_auto_creation(tmp_path):
    keyfile = tmp_path / "subdir" / ".keyfile"
    assert not keyfile.exists()
    key = get_or_create_key(keyfile)
    assert keyfile.exists()
    assert len(key) > 0


@pytest.mark.skipif(os.name == "nt", reason="Unix permissions only")
def test_keyfile_permissions(tmp_path):
    keyfile = tmp_path / ".keyfile"
    get_or_create_key(keyfile)
    mode = os.stat(keyfile).st_mode
    assert mode & stat.S_IRUSR  # owner read
    assert mode & stat.S_IWUSR  # owner write
    assert not (mode & stat.S_IRGRP)  # no group read
    assert not (mode & stat.S_IROTH)  # no others read


def test_keyfile_reuse(tmp_path):
    keyfile = tmp_path / ".keyfile"
    key1 = get_or_create_key(keyfile)
    key2 = get_or_create_key(keyfile)
    assert key1 == key2


def test_missing_keyfile_graceful(tmp_path):
    keyfile = tmp_path / "nonexistent" / ".keyfile"
    # Encrypt with a different keyfile, then try to decrypt with missing one
    real_keyfile = tmp_path / ".keyfile"
    encrypted = encrypt_value("secret", real_keyfile)
    result = decrypt_value(encrypted, keyfile)
    assert result == ""


def test_corrupt_ciphertext(tmp_path):
    keyfile = tmp_path / ".keyfile"
    get_or_create_key(keyfile)
    result = decrypt_value("enc:not-valid-fernet-token", keyfile)
    assert result == ""


def test_wrong_key(tmp_path):
    keyfile_a = tmp_path / "a" / ".keyfile"
    keyfile_b = tmp_path / "b" / ".keyfile"
    encrypted = encrypt_value("secret", keyfile_a)
    # Decrypting with a different key should fail gracefully
    result = decrypt_value(encrypted, keyfile_b)
    assert result == ""
