"""Tests for the PII/secrets scrubber."""

import pytest

from lean_ai.training.scrubber import (
    _shannon_entropy,
    scrub_payload,
    scrub_string,
    scrub_value,
)


class _Recorder:
    def __init__(self) -> None:
        self.matches: list[tuple[str, str, str]] = []

    def __call__(self, name: str, replacement: str, preview: str) -> None:
        self.matches.append((name, replacement, preview))


def test_openai_key_redacted():
    dirty = "api key: sk-proj-abcd1234efgh5678ijkl9012mnop3456qrst7890 tail"
    rec = _Recorder()
    clean = scrub_string(dirty, rec)
    assert "sk-proj-abcd" not in clean
    assert "<REDACTED:openai-key>" in clean
    assert rec.matches and rec.matches[0][0] == "openai_key"


def test_anthropic_key_redacted():
    dirty = "prefix sk-ant-api03-abc123def456ghi789jkl012mno345pqr678 suffix"
    clean = scrub_string(dirty)
    assert "sk-ant-api03" not in clean
    assert "<REDACTED:anthropic-key>" in clean


def test_slack_and_github_tokens():
    dirty = (
        "slack xoxb-1234567890-1234567890-abcdefghij and "
        "ghp_AAAABBBBCCCCDDDDEEEEFFFFGGGGHHHHIIII here"
    )
    clean = scrub_string(dirty)
    assert "xoxb-" not in clean
    assert "ghp_" not in clean
    assert "<REDACTED:slack>" in clean
    assert "<REDACTED:github>" in clean


def test_aws_access_key_redacted():
    dirty = "access key AKIAIOSFODNN7EXAMPLE here"
    clean = scrub_string(dirty)
    assert "AKIA" not in clean
    assert "<REDACTED:aws>" in clean


def test_bearer_header_redacted():
    dirty = "Authorization: Bearer abc.def.ghi plus other text"
    clean = scrub_string(dirty)
    assert "abc.def.ghi" not in clean
    assert "Authorization: Bearer <REDACTED>" in clean


def test_ssh_private_key_redacted():
    dirty = (
        "-----BEGIN OPENSSH PRIVATE KEY-----\n"
        "b3BlbnNzaC1rZXktdjEAAAAAB\n"
        "-----END OPENSSH PRIVATE KEY-----"
    )
    clean = scrub_string(dirty)
    assert "b3BlbnNzaC1" not in clean
    assert "<REDACTED:ssh-key>" in clean


def test_jwt_redacted():
    dirty = (
        "token=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
        "eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIn0.abcdef "
        "in log"
    )
    clean = scrub_string(dirty)
    assert "eyJhbGci" not in clean
    assert "<REDACTED:jwt>" in clean


def test_email_redacted():
    dirty = "contact alice@example.com for questions"
    clean = scrub_string(dirty)
    assert "alice@example.com" not in clean
    assert "<REDACTED:email>" in clean


def test_lean_ai_env_key_redacted():
    dirty = "LEAN_AI_SERVE_API_KEY=las-supersecret-xyz more content"
    clean = scrub_string(dirty)
    assert "las-supersecret" not in clean
    assert "LEAN_AI_SERVE_API_KEY=<REDACTED:env>" in clean


def test_env_file_line_redacted():
    dirty = "DATABASE_PASSWORD=sup3rS3cr3tPassw0rd\n"
    clean = scrub_string(dirty)
    assert "sup3rS3cr3tPassw0rd" not in clean
    assert "DATABASE_PASSWORD=<REDACTED:env>" in clean


def test_high_entropy_token_redacted():
    # Base64-looking high-entropy token
    high_entropy = "ZmFrZV9zdXBlcl9sb25nX2Jhc2U2NF9zZWNyZXRfc3RyaW5nXw"
    rec = _Recorder()
    clean = scrub_string(high_entropy, rec)
    assert clean == "<REDACTED:entropy>"
    assert rec.matches and rec.matches[-1][0] == "generic_high_entropy"


def test_low_entropy_long_string_not_redacted():
    # "aaaa..." — long but zero entropy
    low_entropy = "a" * 50
    clean = scrub_string(low_entropy)
    assert clean == low_entropy


def test_scrub_value_recurses_into_dicts_and_lists():
    payload = {
        "messages": [
            {"role": "user", "content": "my key is sk-proj-xxxxxxxxxxxxxxxxxxxxxxxx"},
            {"role": "assistant", "content": "ok"},
        ],
        "metadata": {"token": "AKIAIOSFODNN7EXAMPLE"},
    }
    clean = scrub_value(payload)
    assert "sk-proj-" not in clean["messages"][0]["content"]
    assert "AKIA" not in clean["metadata"]["token"]
    # Structure preserved
    assert clean["messages"][1]["content"] == "ok"


def test_scrub_value_descends_into_json_strings():
    # Tool-call arguments arrive as JSON-encoded strings
    payload = {
        "tool_call": {
            "arguments": '{"headers": {"authorization": "Bearer sk-proj-abc123xyz456def789"}}',
        },
    }
    clean = scrub_value(payload)
    args = clean["tool_call"]["arguments"]
    assert "sk-proj-abc123" not in args
    # Scrubber should either have redacted the Bearer header or the OpenAI key
    assert (
        "<REDACTED" in args
    ), f"expected redaction, got: {args}"


def test_scrub_payload_returns_new_dict():
    original = {"a": "sk-proj-abc123def456ghi789jkl012"}
    result = scrub_payload(original)
    assert result is not original
    assert "sk-proj-" not in result["a"]


def test_shannon_entropy_basic():
    assert _shannon_entropy("") == 0.0
    assert _shannon_entropy("aaaa") == pytest.approx(0.0)
    # Uniform alphabet → higher entropy
    assert _shannon_entropy("abcdefgh") > 2.5


def test_match_preview_is_sha256_not_secret():
    """Audit callback receives the hash, never the raw match."""
    rec = _Recorder()
    scrub_string("sk-proj-abc123def456ghi789jkl012", rec)
    assert rec.matches
    _, _, preview = rec.matches[0]
    assert "abc123" not in preview
    assert len(preview) == 12


def test_scrub_preserves_non_secret_content():
    clean = scrub_string("plain ordinary text with no secrets at all")
    assert clean == "plain ordinary text with no secrets at all"


def test_scrub_handles_none_and_empty():
    assert scrub_string("") == ""
    assert scrub_value(None) is None
    assert scrub_value(42) == 42
    assert scrub_value([]) == []
    assert scrub_value({}) == {}
