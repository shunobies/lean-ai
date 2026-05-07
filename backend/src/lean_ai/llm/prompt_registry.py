"""Centralized prompt registry with YAML override support.

All LLM prompts are registered with metadata (category, description,
template variables).  The registry loads per-project overrides from
``.lean_ai/prompts.yaml`` so users can edit prompts without touching source
code.  Missing overrides fall back to compiled defaults.

The VS Code prompt editor depends on this module's public API:
``CATEGORY_ORDER`` and the module-level ``registry`` object.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Category display order for the UI.
CATEGORY_ORDER = [
    "Core Policy",
    "Planning",
    "Execution",
    "Fix Mode",
    "Chat & Refinement",
    "Context Generation",
    "Framework Guide",
    "TDD & Vision",
    "Advanced",
]


@dataclass
class PromptEntry:
    """A single registered prompt with metadata."""

    key: str
    category: str
    name: str
    description: str
    default_text: str
    template_vars: list[str] = field(default_factory=list)
    warning: str = ""


class PromptRegistry:
    """Singleton registry for all LLM prompts.

    Defaults are compiled into the code. Per-project overrides are loaded
    from ``.lean_ai/prompts.yaml`` and only contain changed keys.
    """

    def __init__(self) -> None:
        self._defaults: dict[str, PromptEntry] = {}
        self._overrides: dict[str, str] = {}
        self._loaded_root: str | None = None

    def register(self, entry: PromptEntry) -> None:
        """Register a prompt entry."""
        self._defaults[entry.key] = entry

    def get(self, key: str) -> str:
        """Return the current text for *key*."""
        if key in self._overrides:
            return self._overrides[key]
        entry = self._defaults.get(key)
        if entry is None:
            raise KeyError(f"Unknown prompt key: {key!r}")
        return entry.default_text

    def get_all(self) -> list[dict[str, Any]]:
        """Return all prompts with metadata for the API."""
        result: list[dict[str, Any]] = []
        for entry in self._defaults.values():
            result.append(
                {
                    "key": entry.key,
                    "category": entry.category,
                    "name": entry.name,
                    "description": entry.description,
                    "default_text": entry.default_text,
                    "current_text": self._overrides.get(entry.key, entry.default_text),
                    "is_overridden": entry.key in self._overrides,
                    "template_vars": entry.template_vars,
                    "warning": entry.warning,
                }
            )
        return result

    def validate(self, key: str, text: str) -> list[str]:
        """Check that *text* preserves required template variables."""
        entry = self._defaults.get(key)
        if entry is None:
            return [f"Unknown prompt key: {key!r}"]
        errors: list[str] = []
        for var in entry.template_vars:
            if f"{{{var}}}" not in text:
                errors.append(f"Missing required placeholder: {{{var}}}")
        return errors

    def load(self, repo_root: str) -> None:
        """Load overrides from ``.lean_ai/prompts.yaml``."""
        root = Path(repo_root)
        yaml_path = root / ".lean_ai" / "prompts.yaml"
        self._loaded_root = repo_root
        self._overrides.clear()

        if not yaml_path.exists():
            return

        try:
            from ruamel.yaml import YAML

            yaml = YAML()
            yaml.preserve_quotes = True
            data = yaml.load(yaml_path)
            if not isinstance(data, dict):
                return
            for k, v in data.items():
                if k.startswith("_"):
                    continue
                if isinstance(v, str) and k in self._defaults:
                    self._overrides[k] = v
                elif k not in self._defaults:
                    logger.warning("prompts.yaml: unknown key %r (ignored)", k)
        except Exception:
            logger.exception("Failed to load prompts.yaml from %s", yaml_path)

    def save_overrides(self, repo_root: str, overrides: dict[str, str]) -> None:
        """Write overrides to ``.lean_ai/prompts.yaml``."""
        root = Path(repo_root)
        yaml_dir = root / ".lean_ai"
        yaml_dir.mkdir(parents=True, exist_ok=True)
        yaml_path = yaml_dir / "prompts.yaml"

        merged = dict(self._overrides)
        merged.update(overrides)

        from ruamel.yaml import YAML

        yaml = YAML()
        yaml.default_flow_style = False
        yaml.width = 4096

        out: dict[str, Any] = {"_version": 1}
        for k in sorted(merged):
            out[k] = merged[k]

        yaml.dump(out, yaml_path)
        self._overrides = merged
        self._loaded_root = repo_root

    def reset(self, repo_root: str, keys: list[str] | None = None) -> None:
        """Reset overrides. *keys=None* resets all."""
        if keys is None:
            self._overrides.clear()
        else:
            for k in keys:
                self._overrides.pop(k, None)

        root = Path(repo_root)
        yaml_path = root / ".lean_ai" / "prompts.yaml"

        if not self._overrides:
            if yaml_path.exists():
                yaml_path.unlink()
        else:
            self.save_overrides(repo_root, {})

    def format(self, key: str, **kwargs: str) -> str:
        """Get a prompt and apply template variable substitution."""
        text = self.get(key)
        if kwargs:
            text = text.format_map(defaultdict(str, **kwargs))
        return text


registry = PromptRegistry()


def _populate_defaults() -> None:
    """Load compiled prompt defaults into the module singleton."""
    from lean_ai.llm.prompt_defaults import register_prompt_defaults

    register_prompt_defaults(registry)


_populate_defaults()
