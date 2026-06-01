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


@dataclass(frozen=True)
class PromptScope:
    """Model-role scope for prompt resolution."""

    model_id: str
    agent_role: str


@dataclass(frozen=True)
class ScopedPromptOverride:
    """A persisted model-role specific prompt override."""

    prompt_key: str
    model_id: str
    agent_role: str
    text: str


class SyncPromptText(str):
    """String-like prompt value that can also be awaited.

    This preserves backward compatibility for old sync call sites that
    expect ``registry.get(...)`` to behave like a plain string, while
    still letting newer async call sites ``await registry.get(...)``
    even when no DB-backed version lookup is required.
    """

    def __new__(cls, value: str):
        return super().__new__(cls, value)

    def __await__(self):
        async def _wrap():
            return str(self)

        return _wrap().__await__()


class PromptRegistry:
    """Singleton registry for all LLM prompts.

    Defaults are compiled into the code. Per-project overrides are loaded
    from ``.lean_ai/prompts.yaml`` and only contain changed keys.
    """

    def __init__(self) -> None:
        self._defaults: dict[str, PromptEntry] = {}
        self._overrides: dict[str, str] = {}
        self._scoped_overrides: dict[tuple[str, str, str], str] = {}
        self._loaded_root: str | None = None
        # A/B test configs cached in memory: prompt_key -> list of variant dicts
        self._ab_configs: dict[str, list[dict[str, Any]]] = {}

    @staticmethod
    def _scoped_key(key: str, scope: PromptScope) -> tuple[str, str, str]:
        return (key, scope.model_id, scope.agent_role)

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

    def _resolve_text(self, key: str, scope: PromptScope | None = None) -> str:
        """Return the current text for *key* (internal helper)."""
        if scope is not None:
            scoped_text = self._scoped_overrides.get(self._scoped_key(key, scope))
            if scoped_text is not None:
                return scoped_text
        if key in self._overrides:
            return self._overrides[key]
        entry = self._defaults.get(key)
        if entry is None:
            raise KeyError(f"Unknown prompt key: {key!r}")
        return entry.default_text

    def get_text(self, key: str, *, scope: PromptScope | None = None) -> str:
        """Synchronously return prompt text for non-versioned call sites."""
        return self._resolve_text(key, scope=scope)

    def get_with_version(self, key: str) -> tuple[str, int]:
        """Synchronously return prompt text and a default local version."""
        return self._resolve_text(key), 0

    def format_text(
        self,
        key: str,
        *,
        scope: PromptScope | None = None,
        **kwargs: str,
    ) -> str:
        """Synchronously format prompt text for non-versioned call sites."""
        text = self._resolve_text(key, scope=scope)
        return text.format_map(defaultdict(str, **kwargs)) if kwargs else text

    def format_with_version(self, key: str, **kwargs: str) -> tuple[str, int]:
        """Synchronously format prompt text and return a default local version."""
        return self.format_text(key, **kwargs), 0

    def get(
        self,
        db: aiosqlite.Connection | str | None,
        key: str | None = None,
        session_id: str | None = None,
        *,
        scope: PromptScope | None = None,
    ) -> PromptVersionResult | SyncPromptText | Any:
        """Return the resolved prompt text with version metadata.

        When *session_id* is provided, performs A/B variant selection and
        returns a ``PromptVersionResult`` with text, version, and variant
        label.  Without *session_id*, returns a plain ``str`` for backward
        compatibility.
        """
        if isinstance(db, str):
            key, db = db, None
        if key is None:
            raise TypeError("PromptRegistry.get() missing required argument: 'key'")
        base_text = self._resolve_text(key, scope=scope)

        if session_id is None:
            return SyncPromptText(base_text)

        async def _resolve_versioned() -> PromptVersionResult:
            variant = self._select_variant(key, session_id)
            if variant is not None and db is not None:
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

            try:
                if db is None:
                    raise RuntimeError("No prompt DB available for variant lookup")
                cursor = await db.execute(
                    "SELECT DISTINCT variant_label FROM prompt_versions "
                    "WHERE prompt_key = ? AND is_active = 1",
                    (key,),
                )
                rows = await cursor.fetchall()
                variant_labels = [dict(r)["variant_label"] for r in rows]
                if variant_labels:
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

            return PromptVersionResult(
                text=base_text,
                version=0,
                variant_label=variant["variant_label"] if variant else None,
            )

        return _resolve_versioned()

    def get_all(self) -> list[dict[str, Any]]:
        """Return all prompts with metadata for the API."""
        result: list[dict[str, Any]] = []
        for entry in self._defaults.values():
            scoped_override_count = sum(
                1 for prompt_key, _, _ in self._scoped_overrides if prompt_key == entry.key
            )
            result.append(
                {
                    "key": entry.key,
                    "category": entry.category,
                    "name": entry.name,
                    "description": entry.description,
                    "default_text": entry.default_text,
                    "current_text": self._overrides.get(entry.key, entry.default_text),
                    "is_overridden": entry.key in self._overrides,
                    "has_scoped_overrides": scoped_override_count > 0,
                    "scoped_override_count": scoped_override_count,
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
        self._scoped_overrides.clear()

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
            scoped_data = data.get("_scoped_overrides", [])
            if isinstance(scoped_data, list):
                for item in scoped_data:
                    if not isinstance(item, dict):
                        continue
                    prompt_key = item.get("prompt_key")
                    model_id = item.get("model")
                    agent_role = item.get("role")
                    text = item.get("text")
                    if (
                        isinstance(prompt_key, str)
                        and prompt_key in self._defaults
                        and isinstance(model_id, str)
                        and isinstance(agent_role, str)
                        and isinstance(text, str)
                    ):
                        self._scoped_overrides[(prompt_key, model_id, agent_role)] = text
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
        self._write_yaml(yaml_path, merged, self._scoped_overrides)
        self._overrides = merged
        self._loaded_root = repo_root

    def save_scoped_overrides(
        self,
        repo_root: str,
        overrides: list[ScopedPromptOverride],
    ) -> None:
        """Write model-role specific overrides to ``.lean_ai/prompts.yaml``."""
        root = Path(repo_root)
        yaml_dir = root / ".lean_ai"
        yaml_dir.mkdir(parents=True, exist_ok=True)
        yaml_path = yaml_dir / "prompts.yaml"

        merged = dict(self._scoped_overrides)
        for override in overrides:
            merged[(override.prompt_key, override.model_id, override.agent_role)] = override.text

        self._write_yaml(yaml_path, self._overrides, merged)
        self._scoped_overrides = merged
        self._loaded_root = repo_root

    def get_scoped_override(self, key: str, scope: PromptScope) -> str | None:
        """Return the matching scoped override text, if any."""
        return self._scoped_overrides.get(self._scoped_key(key, scope))

    def reset_scoped_overrides(
        self,
        repo_root: str,
        *,
        scope: PromptScope | None = None,
        keys: list[str] | None = None,
    ) -> None:
        """Reset scoped overrides, optionally filtering by scope and prompt keys."""
        retained: dict[tuple[str, str, str], str] = {}
        for scoped_key, text in self._scoped_overrides.items():
            prompt_key, model_id, agent_role = scoped_key
            if keys is not None and prompt_key not in keys:
                retained[scoped_key] = text
                continue
            if scope is not None and (model_id, agent_role) != (scope.model_id, scope.agent_role):
                retained[scoped_key] = text
        self._scoped_overrides = retained
        root = Path(repo_root)
        yaml_path = root / ".lean_ai" / "prompts.yaml"
        if not self._overrides and not self._scoped_overrides:
            if yaml_path.exists():
                yaml_path.unlink()
            return
        self._write_yaml(yaml_path, self._overrides, self._scoped_overrides)

    def _write_yaml(
        self,
        yaml_path: Path,
        global_overrides: dict[str, str],
        scoped_overrides: dict[tuple[str, str, str], str],
    ) -> None:
        from ruamel.yaml import YAML

        yaml = YAML()
        yaml.default_flow_style = False
        yaml.width = 4096

        out: dict[str, Any] = {"_version": 2}
        for k in sorted(global_overrides):
            out[k] = global_overrides[k]
        if scoped_overrides:
            out["_scoped_overrides"] = [
                {
                    "prompt_key": prompt_key,
                    "model": model_id,
                    "role": agent_role,
                    "text": text,
                }
                for (prompt_key, model_id, agent_role), text in sorted(scoped_overrides.items())
            ]
        yaml.dump(out, yaml_path)

    def reset(self, repo_root: str, keys: list[str] | None = None) -> None:
        """Reset overrides. *keys=None* resets all."""
        if keys is None:
            self._overrides.clear()
            self._scoped_overrides.clear()
        else:
            for k in keys:
                self._overrides.pop(k, None)
            self._scoped_overrides = {
                scoped_key: text
                for scoped_key, text in self._scoped_overrides.items()
                if scoped_key[0] not in keys
            }

        root = Path(repo_root)
        yaml_path = root / ".lean_ai" / "prompts.yaml"

        if not self._overrides and not self._scoped_overrides:
            if yaml_path.exists():
                yaml_path.unlink()
        else:
            self._write_yaml(yaml_path, self._overrides, self._scoped_overrides)

    def format(
        self,
        db: aiosqlite.Connection | str | None,
        key: str | None = None,
        session_id: str | None = None,
        *,
        scope: PromptScope | None = None,
        **kwargs: str,
    ) -> PromptVersionResult | SyncPromptText | Any:
        """Get a prompt, apply template substitution, preserving async compatibility."""
        if isinstance(db, str):
            key, db = db, None
        if key is None:
            raise TypeError("PromptRegistry.format() missing required argument: 'key'")

        result = self.get(db, key, session_id=session_id, scope=scope)
        if isinstance(result, SyncPromptText):
            text = str(result)
            if kwargs:
                text = text.format_map(defaultdict(str, **kwargs))
            return SyncPromptText(text)

        async def _format_async():
            awaited = await result
            if isinstance(awaited, PromptVersionResult):
                text = awaited.text
                if kwargs:
                    text = text.format_map(defaultdict(str, **kwargs))
                return PromptVersionResult(
                    text=text,
                    version=awaited.version,
                    variant_label=awaited.variant_label,
                )
            text = str(awaited)
            if kwargs:
                text = text.format_map(defaultdict(str, **kwargs))
            return SyncPromptText(text)

        return _format_async()

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
