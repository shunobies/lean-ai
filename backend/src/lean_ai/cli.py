"""CLI tool for configuration management.

Usage::

    python -m lean_ai encrypt-key <plaintext>
    python -m lean_ai decrypt-key <encrypted>
    python -m lean_ai migrate-env [--env-file .env] [--yaml-file config.yaml]
    python -m lean_ai generate-config [--yaml-file config.yaml]
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from lean_ai.crypto import decrypt_value, encrypt_value, is_encrypted

# Fields in the Settings class that hold API keys / secrets.
_SECRET_FIELDS = frozenset({"openai_api_key", "anthropic_api_key", "search_api_key"})

# Env var prefix stripped when converting to YAML field names.
_ENV_PREFIX = "LEAN_AI_"


def _default_keyfile() -> Path:
    return Path.cwd() / ".lean_ai" / ".keyfile"


# ── Commands ──────────────────────────────────────────────────────────────────


def _cmd_encrypt_key(args: argparse.Namespace) -> None:
    keyfile = Path(args.keyfile)
    encrypted = encrypt_value(args.plaintext, keyfile)
    print(encrypted)


def _cmd_decrypt_key(args: argparse.Namespace) -> None:
    keyfile = Path(args.keyfile)
    if not is_encrypted(args.encrypted):
        print(args.encrypted)
        return
    decrypted = decrypt_value(args.encrypted, keyfile)
    if not decrypted:
        print("ERROR: decryption failed (missing keyfile or corrupt value)", file=sys.stderr)
        sys.exit(1)
    print(decrypted)


def _cmd_migrate_env(args: argparse.Namespace) -> None:
    env_path = Path(args.env_file)
    yaml_path = Path(args.yaml_file)
    keyfile = Path(args.keyfile)

    if not env_path.exists():
        print(f"ERROR: {env_path} not found", file=sys.stderr)
        sys.exit(1)

    if yaml_path.exists() and not args.force:
        print(f"ERROR: {yaml_path} already exists (use --force to overwrite)", file=sys.stderr)
        sys.exit(1)

    lines: list[str] = []
    lines.append("# Lean AI Configuration")
    lines.append("# Migrated from .env")
    lines.append("")

    for raw_line in env_path.read_text().splitlines():
        stripped = raw_line.strip()

        # Blank lines
        if not stripped:
            lines.append("")
            continue

        # Comments → preserve as YAML comments
        if stripped.startswith("#"):
            # Strip LEAN_AI_ references from comments for readability
            lines.append(stripped)
            continue

        # Key=value pairs
        match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)", stripped)
        if not match:
            lines.append(f"# {stripped}")
            continue

        env_key, env_val = match.group(1), match.group(2)

        # Strip quotes from value
        if (env_val.startswith('"') and env_val.endswith('"')) or \
           (env_val.startswith("'") and env_val.endswith("'")):
            env_val = env_val[1:-1]

        # Convert env var name to YAML field name
        if env_key.startswith(_ENV_PREFIX):
            field_name = env_key[len(_ENV_PREFIX):].lower()
        else:
            field_name = env_key.lower()

        # Encrypt API key values
        if field_name in _SECRET_FIELDS and env_val:
            env_val = encrypt_value(env_val, keyfile)

        # Quote strings that contain special YAML chars
        if env_val and _needs_yaml_quoting(env_val):
            env_val = f'"{env_val}"'

        lines.append(f"{field_name}: {env_val}")

    lines.append("")  # trailing newline
    yaml_path.write_text("\n".join(lines))
    print(f"Migrated {env_path} -> {yaml_path}")
    if any(f in yaml_path.read_text() for f in _SECRET_FIELDS):
        print(f"API keys encrypted with keyfile at {keyfile}")


def _cmd_generate_config(args: argparse.Namespace) -> None:
    yaml_path = Path(args.yaml_file)

    if yaml_path.exists() and not args.force:
        print(f"ERROR: {yaml_path} already exists (use --force to overwrite)", file=sys.stderr)
        sys.exit(1)

    # Import Settings to introspect fields and defaults
    from lean_ai.config import Settings

    lines: list[str] = []
    lines.append("# Lean AI Configuration")
    lines.append("# Generated template — uncomment and modify as needed")
    lines.append("#")
    lines.append("# Priority: env vars > config.yaml > .env > defaults")
    lines.append("# Encrypt API keys: python -m lean_ai encrypt-key <key>")
    lines.append("")

    for field_name, field_info in Settings.model_fields.items():
        default = field_info.default
        if default is None:
            default_str = ""
        elif isinstance(default, bool):
            default_str = str(default).lower()
        elif isinstance(default, str):
            default_str = f'"{default}"' if _needs_yaml_quoting(default) else default
        else:
            default_str = str(default)

        desc = field_info.description or ""
        if desc:
            lines.append(f"# {desc}")
        lines.append(f"# {field_name}: {default_str}")

    lines.append("")
    yaml_path.write_text("\n".join(lines))
    print(f"Generated config template at {yaml_path}")


def _needs_yaml_quoting(value: str) -> bool:
    """Return True if *value* needs quoting in YAML."""
    if not value:
        return False
    # Contains special YAML characters
    if any(ch in value for ch in ":#{}[]|>&*!%@`"):
        return True
    # Looks like a boolean or null
    return value.lower() in ("true", "false", "null", "yes", "no", "on", "off")


# ── Main ──────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="lean-ai",
        description="Lean AI configuration management",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # encrypt-key
    p_enc = sub.add_parser("encrypt-key", help="Encrypt an API key for config.yaml")
    p_enc.add_argument("plaintext", help="The API key to encrypt")
    p_enc.add_argument("--keyfile", default=str(_default_keyfile()), help="Path to keyfile")

    # decrypt-key
    p_dec = sub.add_parser("decrypt-key", help="Decrypt an encrypted value")
    p_dec.add_argument("encrypted", help="The enc:... value to decrypt")
    p_dec.add_argument("--keyfile", default=str(_default_keyfile()), help="Path to keyfile")

    # migrate-env
    p_mig = sub.add_parser("migrate-env", help="Convert .env to config.yaml")
    p_mig.add_argument("--env-file", default=".env", help="Source .env file")
    p_mig.add_argument("--yaml-file", default="config.yaml", help="Target YAML file")
    p_mig.add_argument("--keyfile", default=str(_default_keyfile()), help="Path to keyfile")
    p_mig.add_argument("--force", action="store_true", help="Overwrite existing YAML file")

    # generate-config
    p_gen = sub.add_parser("generate-config", help="Generate a documented config.yaml template")
    p_gen.add_argument("--yaml-file", default="config.yaml", help="Output YAML file")
    p_gen.add_argument("--force", action="store_true", help="Overwrite existing file")

    args = parser.parse_args()

    dispatch = {
        "encrypt-key": _cmd_encrypt_key,
        "decrypt-key": _cmd_decrypt_key,
        "migrate-env": _cmd_migrate_env,
        "generate-config": _cmd_generate_config,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
