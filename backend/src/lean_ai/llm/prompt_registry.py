"""Centralized prompt registry with YAML override support.

All LLM prompts are registered with metadata (category, description,
template variables).  The registry loads per-project overrides from
``.lean_ai/prompts.yaml`` so users can edit prompts without touching source
code.  Missing overrides fall back to compiled defaults.

The VS Code prompt editor depends on this module's public API:
``CATEGORY_ORDER`` and the module-level ``registry`` object.

Versioning and A/B Testing
--------------------------
Prompts can be versioned and A/B tested.  The registry caches A/B test
configs in memory and uses deterministic hashing to assign sessions to
variants.  ``get()`` and ``format()`` return ``PromptVersionResult`` objects
that carry both the resolved text and the version/variant metadata.
"""

from __future__ import annotations

import aiosqlite
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
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


@dataclass
class PromptVersionResult:
    """Result of resolving a prompt, carrying text and version metadata."""

    text: str
    version: int
    variant_label: str | None = None


class PromptRegistry:
    """Singleton registry for all LLM prompts.

    Defaults are compiled into the code. Per-project overrides are loaded
    from ``.lean_ai/prompts.yaml`` and only contain changed keys.
    """

    def __init__(self) -> None:
        self._defaults: dict[str, PromptEntry] = {}
        self._overrides: dict[str, str] = {}
        self._loaded_root: str | None = None
        # A/B test configs cached in memory: prompt_key -> list of variant dicts
        self._ab_configs: dict[str, list[dict[str, Any]]] = {}

    async def _load_ab_configs(self, db: aiosqlite.Connection) -> None:
        """Load active A/B tests and their variants into memory.

        Populates ``self._ab_configs`` with variant info for each prompt key
        that has an active A/B test.
        """
        self._ab_configs.clear()
        try:
            cursor = await db.execute(
                "SELECT ab_tests.prompt_key, ab_tests.name, ab_tests.status, "
                "prompt_variants.variant_label, prompt_variants.weight "
                "FROM ab_tests "
                "JOIN prompt_variants ON ab_tests.prompt_key = prompt_variants.prompt_key "
                "WHERE ab_tests.status = 'active'"
            )
            rows = await cursor.fetchall()
        except Exception:
            # Tables may not exist yet; A/B testing is a no-op
            return

        for row in rows:
            row_dict = dict(row)
            key = row_dict["prompt_key"]
            if key not in self._ab_configs:
                self._ab_configs[key] = []
            self._ab_configs[key].append(
                {
                    "variant_label": row_dict["variant_label"],
                    "weight": row_dict["weight"],
                }
            )

    def _select_variant(
        self,
        prompt_key: str,
        session_id: str,
    ) -> dict[str, Any] | None:
        """Deterministically select a variant for a session.

        Uses ``hash(session_id + prompt_key) % 100`` with weight-based
        bucket selection to assign sessions to variants.
        """
        variants = self._ab_configs.get(prompt_key)
        if not variants:
            return None

        bucket = hash(session_id + prompt_key) % 100
        cumulative = 0.0
        for variant in variants:
            cumulative += variant["weight"] * 100
            if bucket < cumulative:
                return variant
        # Fallback to last variant
        return variants[-1]

    def register(self, entry: PromptEntry) -> None:
        """Register a prompt entry."""
        self._defaults[entry.key] = entry

    def _resolve_text(self, key: str) -> str:
        """Return the current text for *key* (internal helper)."""
        if key in self._overrides:
            return self._overrides[key]
        entry = self._defaults.get(key)
        if entry is None:
            raise KeyError(f"Unknown prompt key: {key!r}")
        return entry.default_text

    async def get(
        self,
        db: aiosqlite.Connection,
        key: str,
        session_id: str | None = None,
    ) -> PromptVersionResult | str:
        """Return the resolved prompt text with version metadata.

        When *session_id* is provided, performs A/B variant selection and
        returns a ``PromptVersionResult`` with text, version, and variant
        label.  Without *session_id*, returns a plain ``str`` for backward
        compatibility.
        """
        base_text = self._resolve_text(key)

        if session_id is None:
            return base_text

        # Try A/B variant selection first
        variant = self._select_variant(key, session_id)
        if variant is not None:
            variant_label = variant["variant_label"]
            try:
                cursor = await db.execute(
                    "SELECT id, version, text FROM prompt_versions "
                    "WHERE prompt_key = ? AND variant_label = ? AND is_active = 1 "
                    "ORDER BY version DESC LIMIT 1",
                    (key, variant_label),
                )
                row = await cursor.fetchone()
                if row is not None:
                    row_dict = dict(row)
                    return PromptVersionResult(
                        text=row_dict["text"],
                        version=row_dict["version"],
                        variant_label=variant_label,
                    )
            except Exception:
                pass

        # Fallback: query all active variants for this prompt key and distribute
        try:
            cursor = await db.execute(
                "SELECT DISTINCT variant_label FROM prompt_versions "
                "WHERE prompt_key = ? AND is_active = 1",
                (key,),
            )
            rows = await cursor.fetchall()
            variant_labels = [dict(r)["variant_label"] for r in rows]
            if variant_labels:
                # Use hash to deterministically pick a variant
                idx = hash(session_id + key) % len(variant_labels)
                chosen_label = variant_labels[idx]
                cursor = await db.execute(
                    "SELECT id, version, text FROM prompt_versions "
                    "WHERE prompt_key = ? AND variant_label = ? AND is_active = 1 "
                    "ORDER BY version DESC LIMIT 1",
                    (key, chosen_label),
                )
                row = await cursor.fetchone()
                if row is not None:
                    row_dict = dict(row)
                    return PromptVersionResult(
                        text=row_dict["text"],
                        version=row_dict["version"],
                        variant_label=chosen_label,
                    )
        except Exception:
            pass

        # Final fallback: no versioned data, return base text with version 0
        return PromptVersionResult(
            text=base_text,
            version=0,
            variant_label=variant["variant_label"] if variant else None,
        )

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

    async def format(
        self,
        db: aiosqlite.Connection,
        key: str,
        session_id: str | None = None,
        **kwargs: str,
    ) -> PromptVersionResult | str:
        """Get a prompt, apply template variable substitution, return with version metadata.

        When *session_id* is provided, performs A/B variant selection and
        returns a ``PromptVersionResult``.  Without *session_id*, returns
        a plain ``str`` for backward compatibility.
        """
        result = await self.get(db, key, session_id=session_id)
        if isinstance(result, PromptVersionResult):
            text = result.text
            if kwargs:
                text = text.format_map(defaultdict(str, **kwargs))
            return PromptVersionResult(
                text=text,
                version=result.version,
                variant_label=result.variant_label,
            )
        # Plain string path (backward compat)
        text = result
        if kwargs:
            text = text.format_map(defaultdict(str, **kwargs))
        return text

    async def save_version(
        self,
        db: aiosqlite.Connection,
        prompt_key: str,
        version: int,
        text: str,
        variant_label: str | None = None,
    ) -> None:
        """Persist a new prompt version to the database.

        Inserts a row into the ``prompt_versions`` table with the given
        key, version number, text content, and optional variant label.
        """
        now = datetime.now(timezone.utc).isoformat()
        try:
            await db.execute(
                "INSERT INTO prompt_versions "
                "(prompt_key, version, text, variant_label, is_active, created_at) "
                "VALUES (?, ?, ?, ?, 1, ?)",
                (prompt_key, version, text, variant_label, now),
            )
            await db.commit()
        except Exception:
            logger.exception(
                "Failed to save prompt version for key=%r version=%d",
                prompt_key,
                version,
            )


registry = PromptRegistry()


def _populate_defaults() -> None:
    """Load compiled prompt defaults into the module singleton."""
    from lean_ai.llm.prompt_defaults import register_prompt_defaults

    register_prompt_defaults(registry)


_populate_defaults()
