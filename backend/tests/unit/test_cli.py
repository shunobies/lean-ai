"""Tests for lean_ai.cli — configuration management CLI."""

import subprocess
import sys
from pathlib import Path

from lean_ai.crypto import decrypt_value, is_encrypted


def _run_cli(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "lean_ai", *args],
        capture_output=True,
        text=True,
        cwd=str(cwd) if cwd else None,
    )


def test_encrypt_key_command(tmp_path):
    keyfile = tmp_path / ".lean_ai" / ".keyfile"
    result = _run_cli("encrypt-key", "test-secret", "--keyfile", str(keyfile), cwd=tmp_path)
    assert result.returncode == 0
    encrypted = result.stdout.strip()
    assert is_encrypted(encrypted)
    assert decrypt_value(encrypted, keyfile) == "test-secret"


def test_decrypt_key_command(tmp_path):
    from lean_ai.crypto import encrypt_value
    keyfile = tmp_path / ".lean_ai" / ".keyfile"
    encrypted = encrypt_value("my-api-key", keyfile)
    result = _run_cli("decrypt-key", encrypted, "--keyfile", str(keyfile), cwd=tmp_path)
    assert result.returncode == 0
    assert result.stdout.strip() == "my-api-key"


def test_decrypt_plain_value(tmp_path):
    result = _run_cli("decrypt-key", "not-encrypted", cwd=tmp_path)
    assert result.returncode == 0
    assert result.stdout.strip() == "not-encrypted"


def test_migrate_env_command(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# My config\n"
        "LEAN_AI_OLLAMA_URL=http://localhost:11434\n"
        "LEAN_AI_OLLAMA_MODEL=test-model\n"
        'LEAN_AI_OPENAI_API_KEY=sk-secret\n'
    )
    keyfile = tmp_path / ".lean_ai" / ".keyfile"
    result = _run_cli(
        "migrate-env",
        "--env-file", str(env_file),
        "--yaml-file", str(tmp_path / "config.yaml"),
        "--keyfile", str(keyfile),
        cwd=tmp_path,
    )
    assert result.returncode == 0
    yaml_content = (tmp_path / "config.yaml").read_text()
    # URL contains ":" so it gets quoted in YAML
    assert 'ollama_url: "http://localhost:11434"' in yaml_content
    assert "ollama_model: test-model" in yaml_content
    # API key should be encrypted (quoted because enc: contains ":")
    assert 'openai_api_key: "enc:' in yaml_content
    # Should not contain the raw key
    assert "sk-secret" not in yaml_content


def test_migrate_env_missing_file(tmp_path):
    result = _run_cli(
        "migrate-env",
        "--env-file", str(tmp_path / "nonexistent.env"),
        cwd=tmp_path,
    )
    assert result.returncode != 0


def test_migrate_env_no_overwrite(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("LEAN_AI_OLLAMA_URL=http://localhost:11434\n")
    yaml_file = tmp_path / "config.yaml"
    yaml_file.write_text("# existing\n")
    result = _run_cli(
        "migrate-env",
        "--env-file", str(env_file),
        "--yaml-file", str(yaml_file),
        cwd=tmp_path,
    )
    assert result.returncode != 0
    assert "already exists" in result.stderr


def test_migrate_env_force_overwrite(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("LEAN_AI_OLLAMA_URL=http://localhost:11434\n")
    yaml_file = tmp_path / "config.yaml"
    yaml_file.write_text("# existing\n")
    result = _run_cli(
        "migrate-env",
        "--env-file", str(env_file),
        "--yaml-file", str(yaml_file),
        "--force",
        cwd=tmp_path,
    )
    assert result.returncode == 0
    assert "ollama_url" in yaml_file.read_text()


def test_generate_config_command(tmp_path):
    result = _run_cli(
        "generate-config",
        "--yaml-file", str(tmp_path / "config.yaml"),
        cwd=tmp_path,
    )
    assert result.returncode == 0
    content = (tmp_path / "config.yaml").read_text()
    assert "# Lean AI Configuration" in content
    # Should contain commented-out field names
    assert "# ollama_url:" in content
    assert "# llm_provider:" in content
