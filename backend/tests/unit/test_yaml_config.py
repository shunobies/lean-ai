"""Tests for YAML config source in lean_ai.config."""

from lean_ai.crypto import encrypt_value


def test_yaml_source_loads_values(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.yaml").write_text(
        "ollama_url: http://custom:11434\nollama_model: test-model\n"
    )
    from lean_ai.config import Settings

    s = Settings()
    assert s.ollama_url == "http://custom:11434"
    assert s.ollama_model == "test-model"


def test_env_overrides_yaml(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.yaml").write_text("ollama_url: http://yaml:11434\n")
    monkeypatch.setenv("LEAN_AI_OLLAMA_URL", "http://env:11434")
    from lean_ai.config import Settings

    s = Settings()
    assert s.ollama_url == "http://env:11434"


def test_yaml_overrides_dotenv(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("LEAN_AI_OLLAMA_MODEL=dotenv-model\n")
    (tmp_path / "config.yaml").write_text("ollama_model: yaml-model\n")
    from lean_ai.config import Settings

    s = Settings()
    assert s.ollama_model == "yaml-model"


def test_encrypted_api_key_in_yaml(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    keyfile = tmp_path / ".lean_ai" / ".keyfile"
    encrypted = encrypt_value("sk-secret-123", keyfile)
    (tmp_path / "config.yaml").write_text(f'openai_api_key: "{encrypted}"\n')
    from lean_ai.config import Settings

    s = Settings()
    assert s.openai_api_key == "sk-secret-123"


def test_yaml_missing_file_uses_defaults(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # No config.yaml, no .env
    from lean_ai.config import Settings

    s = Settings()
    assert s.ollama_url == "http://localhost:11434"
    assert s.llm_provider == "ollama"


def test_yaml_context_shorthand(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.yaml").write_text("ollama_context_window: 128\n")
    from lean_ai.config import Settings

    s = Settings()
    assert s.ollama_context_window == 131072


def test_yaml_partial_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.yaml").write_text("ollama_temperature: 0.5\n")
    from lean_ai.config import Settings

    s = Settings()
    assert s.ollama_temperature == 0.5
    # All other fields should have their defaults
    assert s.ollama_url == "http://localhost:11434"
    assert s.llm_provider == "ollama"


def test_yaml_chat_turn_settings(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.yaml").write_text(
        "chat_max_turns: 100\nchat_extended_turns_max: 120\n"
    )
    from lean_ai.config import Settings

    s = Settings()
    assert s.chat_max_turns == 100
    assert s.chat_extended_turns_max == 120


def test_plain_api_key_in_yaml(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.yaml").write_text('openai_api_key: "sk-plain-key"\n')
    from lean_ai.config import Settings

    s = Settings()
    assert s.openai_api_key == "sk-plain-key"


def test_yaml_ignores_removed_tdd_fields(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.yaml").write_text(
        'enable_tdd: "true"\n'
        "tdd_max_disputes_per_step: 7\n"
        "ollama_model: yaml-model\n"
    )
    from lean_ai.config import Settings

    s = Settings()
    assert s.ollama_model == "yaml-model"
    assert not hasattr(s, "enable_tdd")
