"""Application configuration via pydantic-settings.

Token limits are derived from the active provider's context window so that
changing a single value (or upgrading a GPU) automatically scales all limits.

Context window shorthand
~~~~~~~~~~~~~~~~~~~~~~~~
Context window values accept a compact notation so users can write ``128``
instead of ``131072``.  The rules:

- Values ≤ 10 000 are treated as **k** (× 1024).  ``128`` → 131 072.
- An explicit ``k`` suffix also works: ``"128k"`` → 131 072.
- Values > 10 000 are used as-is for backwards compatibility.

Configuration sources (highest priority first)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
1. Environment variables (``LEAN_AI_*``)
2. ``config.yaml`` (YAML field names, e.g. ``ollama_url``)
3. ``.env`` file (legacy fallback, ``LEAN_AI_*`` names)
4. Field defaults

API keys in ``config.yaml`` may be Fernet-encrypted with an ``enc:`` prefix.
Use ``python -m lean_ai encrypt-key <key>`` to generate encrypted values.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import model_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

from lean_ai.crypto import decrypt_value

# Fields that accept the k-shorthand notation.
_CONTEXT_WINDOW_FIELDS = frozenset({
    "ollama_context_window",
    "ollama_expert_context_window",
    "ollama_request_context_window",
    "openai_context_window",
    "anthropic_context_window",
    "gemini_context_window",
    "serve_context_window",
    "inline_context_window",
})


def _expand_ctx(raw: int | str) -> int:
    """Expand a context-window shorthand value to raw token count.

    >>> _expand_ctx(128)
    131072
    >>> _expand_ctx("128k")
    131072
    >>> _expand_ctx(131072)
    131072
    """
    s = str(raw).strip().lower()
    if s.endswith("k"):
        return int(float(s[:-1]) * 1024)
    n = int(s)
    return n * 1024 if n <= 10_000 else n


def _default_keyfile_path() -> Path:
    """Resolve the default keyfile path relative to cwd."""
    return Path.cwd() / ".lean_ai" / ".keyfile"


class _DecryptingYamlSource(PydanticBaseSettingsSource):
    """YAML settings source that decrypts ``enc:``-prefixed API key values.

    Wraps pydantic-settings' built-in ``YamlConfigSettingsSource`` and
    post-processes secret fields through :func:`decrypt_value`.
    """

    _SECRET_FIELDS = frozenset({
        "openai_api_key", "anthropic_api_key", "gemini_api_key",
        "serve_api_key", "search_api_key",
        "jira_api_token", "servicenow_password",
        "wiki_password",
    })

    def __init__(self, settings_cls: type[BaseSettings], yaml_file: str = "config.yaml") -> None:
        super().__init__(settings_cls)
        from pydantic_settings import YamlConfigSettingsSource
        self._yaml_source = YamlConfigSettingsSource(settings_cls, yaml_file=yaml_file)

    def get_field_value(
        self, field: Any, field_name: str
    ) -> tuple[Any, str, bool]:
        return self._yaml_source.get_field_value(field, field_name)

    def __call__(self) -> dict[str, Any]:
        data = self._yaml_source()
        keyfile = _default_keyfile_path()
        for field_name in self._SECRET_FIELDS:
            if field_name in data and isinstance(data[field_name], str):
                data[field_name] = decrypt_value(data[field_name], keyfile)
        return data


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="LEAN_AI_",
        env_file=".env",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Priority: env vars > config.yaml > .env > defaults."""
        return (
            init_settings,
            env_settings,
            _DecryptingYamlSource(settings_cls),
            dotenv_settings,
            file_secret_settings,
        )

    # ── LLM Provider ──
    llm_provider: str = "ollama"  # "ollama", "openai", "anthropic"

    # ── Ollama — primary model ──
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "qwen3-coder:30b"
    ollama_temperature: float = 0.7  # Qwen3 warns against greedy decoding (0.0)
    ollama_top_p: float = 0.8
    ollama_top_k: int = 20
    ollama_repeat_penalty: float = 1.05
    ollama_context_window: int = 131072  # Accepts shorthand: 128 = 128k = 131072
    ollama_max_tokens: int | None = None  # Derived: 25% of context window

    # ── Ollama — expert model (reasoning-heavy phases) ──
    ollama_model_expert: str = ""  # Empty = use standard model everywhere
    ollama_expert_temperature: float | None = None  # Falls back to ollama_temperature
    ollama_expert_top_p: float | None = None  # Falls back to ollama_top_p
    ollama_expert_top_k: int | None = None  # Falls back to ollama_top_k
    ollama_expert_repeat_penalty: float | None = None  # Falls back to ollama_repeat_penalty
    ollama_expert_context_window: int | None = None  # Accepts shorthand; falls back
    ollama_expert_max_tokens: int | None = None  # Derived: 25% of expert context window

    # ── OpenAI ──
    openai_api_key: str = ""
    openai_model: str = "gpt-4o"
    openai_base_url: str = ""  # For OpenAI-compatible APIs (Together, Groq, vLLM, etc.)
    openai_temperature: float = 0.7
    openai_context_window: int = 128000  # Accepts shorthand: 125 ≈ 128000
    openai_max_tokens: int | None = None  # Derived: 25% of context window

    # ── Anthropic ──
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-20250514"
    anthropic_temperature: float = 0.7
    anthropic_context_window: int = 200000  # Accepts shorthand: 200 = 204800
    anthropic_max_tokens: int | None = None  # Derived: 25% of context window

    # ── Gemini ──
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    gemini_temperature: float = 0.7
    gemini_context_window: int = 1048576  # ~1M tokens, accepts shorthand
    gemini_max_tokens: int | None = None  # Derived: 25% of context window

    # ── Gemini — expert/request model overrides ──
    gemini_expert_model: str = ""  # Falls back to gemini_model when empty
    gemini_request_model: str = ""  # Falls back to gemini_model when empty

    # ── Lean AI Serve (vLLM wrapper, OpenAI-compatible) ──
    serve_url: str = "http://localhost:8420"
    serve_api_key: str = ""
    serve_model: str = ""
    serve_temperature: float = 0.7
    serve_context_window: int = 131072  # Accepts shorthand: 128 = 128k = 131072
    serve_max_tokens: int | None = None  # Derived: 25% of context window

    # ── Lean AI Serve — expert/request model overrides ──
    serve_expert_model: str = ""  # Falls back to serve_model when empty
    serve_request_model: str = ""  # Falls back to serve_model when empty

    # ── Expert model — provider selection ──
    # "openai", "anthropic", "ollama", "serve", or "" (auto-detect from OLLAMA_MODEL_EXPERT)
    expert_llm_provider: str = ""

    # ── OpenAI — expert model ──
    # Falls back to openai_model when empty
    openai_expert_model: str = ""

    # ── Anthropic — expert model ──
    # Falls back to anthropic_model when empty
    anthropic_expert_model: str = ""

    # ── Request model — for /request mode ──
    request_llm_provider: str = ""  # "ollama", "openai", "anthropic", "serve", or "" (auto-detect)
    ollama_model_request: str = ""  # e.g. "qwen3.5:27b"
    ollama_request_temperature: float | None = None  # Falls back to ollama_temperature
    ollama_request_top_p: float | None = None  # Falls back to ollama_top_p
    ollama_request_top_k: int | None = None  # Falls back to ollama_top_k
    ollama_request_repeat_penalty: float | None = None  # Falls back to ollama_repeat_penalty
    ollama_request_context_window: int | None = None  # Accepts shorthand; falls back
    ollama_request_max_tokens: int | None = None  # Derived: 25% of request context window
    openai_request_model: str = ""
    anthropic_request_model: str = ""

    # ── Thinking mode ──
    enable_thinking: bool = True  # Pass think=True to Ollama for reasoning models (Qwen3, Qwen3.5)
    enable_thinking_expert: bool = True  # Independent per-model thinking toggle
    enable_thinking_request: bool = True  # Independent per-model thinking toggle

    # ── Ollama — inline prediction model (always Ollama) ──
    inline_model: str = ""
    inline_max_tokens: int = 256
    inline_context_window: int | None = None  # Derived: 12.5% of context window (accepts shorthand)
    inline_ollama_url: str | None = None

    # ── Embedding model (always Ollama) ──
    embedding_model: str = "qwen3-embedding:0.6b"
    enable_embeddings: bool = True
    embedding_ollama_url: str | None = None

    # ── Vision model (always Ollama, on-demand) ──
    vision_model: str = ""  # e.g. "qwen3-vl:8b". Empty = vision disabled
    vision_ollama_url: str | None = None  # Falls back to ollama_url
    vision_max_tokens: int = 1024  # Max tokens for image description
    vision_timeout: float = 120.0  # Timeout per image description (seconds)

    # ── Voice — STT (faster-whisper, always local) ──
    enable_stt: bool = False
    stt_model: str = "turbo"  # tiny|base|small|medium|large-v3|turbo
    stt_language: str = ""  # ISO 639-1, empty = auto-detect
    stt_silence_threshold: float = 4.0  # Seconds of silence before auto-stop
    stt_beam_size: int = 1  # 1=greedy (fastest), 5=beam search (most accurate)
    stt_cpu_threads: int = 6  # CPU threads for faster-whisper inference

    # ── Voice — TTS (kokoro-onnx, always local) ──
    enable_tts: bool = False
    tts_voice: str = "af_heart"  # kokoro-onnx voice ID
    tts_speed: float = 1.0  # 0.5–2.0
    tts_model_quality: str = "fp16"  # fp32, fp16, or int8
    tts_cpu_threads: int = 0  # ONNX intra-op threads (0 = auto: min(cpu_count, 8))

    # ── Voice — Wake word (openWakeWord, always local) ──
    enable_wake_word: bool = False

    # ── Indexer ──
    index_dir: str = ".lean_ai_index"
    chunk_max_lines: int = 50
    chunk_overlap_lines: int = 10

    # ── Internet / Search ──
    search_provider: str = "duckduckgo"  # "duckduckgo", "searxng", "google", or "bing"
    search_api_url: str = ""
    search_api_key: str = ""
    search_delay: float = 2.0  # Min seconds between all searches (jitter adds 0–100%)
    internet_timeout_seconds: int = 30

    # ── MediaWiki ──
    wiki_url: str = ""              # e.g. "https://wiki.company.com" — empty = disabled
    wiki_api_path: str = "/w/api.php"  # API endpoint path (MediaWiki default)
    wiki_username: str = ""         # For authenticated wikis (bot account)
    wiki_password: str = ""         # Bot password or user password (stored in keychain)

    # ── Project context ──
    enable_project_context: bool = True
    enable_multi_round_context: bool = True
    enable_framework_guide: bool = True  # Generate .lean_ai/framework_guide.md
    framework_guide_depth: str = "deep"  # "basic" (single-pass) or "deep" (multi-pass)

    # ── Knowledge base ──
    knowledge_dir: str = ".lean_ai/knowledge"
    knowledge_index_dir: str = ".lean_ai_knowledge_index"

    # ── Local Refiner (cloud pre-processing) ──
    enable_refiner: bool = True           # Active only with cloud providers
    refiner_ollama_url: str | None = None  # Falls back to ollama_url
    refiner_model: str | None = None       # Falls back to ollama_model
    refiner_timeout: float = 30.0          # Max seconds for refinement pipeline
    refiner_enable_knowledge: bool = True  # Inject knowledge base context
    refiner_enable_privacy: bool = True    # Strip sensitive data
    refiner_knowledge_chunks: int = 5      # Max knowledge chunks to inject

    # ── Implementation ──
    implementation_max_tokens: int | None = None  # Derived: 25% of active context window
    implementation_max_turns: int = 0  # 0 = unlimited
    reminder_interval: int = 10  # Re-inject task every N tool-calling turns
    loop_detection_threshold: int = 3  # Consecutive identical tool calls before warning (0 = off)
    refresh_threshold: float = 0.7  # Refresh context at this % of context window

    # ── Parallel LLM requests ──
    num_parallel: int = 1  # Max concurrent LLM calls (match OLLAMA_NUM_PARALLEL)

    # ── Fix-mode investigation phase ──
    enable_fix_investigation: bool = True  # Read-only investigation before editing

    # ── Claim verification ──
    enable_claim_verification: bool = True  # Nudge LLM to verify external claims via web search

    # ── Confidence verification ──
    confidence_threshold: float = 0.7  # 0.0-1.0, below triggers search nudge in confidence audit

    # ── TDD mode ──
    enable_tdd: bool = False  # Expert writes tests first, primary implements
    tdd_max_disputes_per_step: int = 3  # Max test disputes per implementation step

    # ── Post-execution validation ──
    enable_post_validation: bool = True  # Master switch
    post_format_command: str = ""     # e.g. "ruff format src/"
    post_lint_fix_command: str = ""   # e.g. "ruff check --fix src/"
    post_lint_command: str = ""       # e.g. "ruff check src/"
    post_test_command: str = ""       # e.g. "pytest tests/ -x -q"
    post_validation_max_retries: int = 2  # Max LLM fix attempts (0 = no retries)
    post_validation_fix_turns: int = 30  # Tool-calling turns per fix attempt

    # ── Integrations (Jira, ServiceNow, etc.) ──
    enable_integrations: bool = False  # Master switch for external integrations
    integration_auto_push: bool = True  # Auto-push session summaries on completion

    # Jira Cloud
    jira_url: str = ""          # e.g. "https://yourcompany.atlassian.net"
    jira_email: str = ""        # Jira account email
    jira_api_token: str = ""    # Jira API token (stored in OS keychain)

    # ServiceNow
    servicenow_url: str = ""         # e.g. "https://yourinstance.service-now.com"
    servicenow_username: str = ""    # ServiceNow username
    servicenow_password: str = ""    # ServiceNow password (stored in OS keychain)
    servicenow_table: str = "incident"  # Default ServiceNow table

    # ── Debug / Testing ──
    debug_planning: bool = False  # Save all planning phase outputs to disk

    # ── Tool execution ──
    tool_timeout_seconds: int = 60

    # ── LLM retry ──
    llm_retry_max: int = 3
    llm_retry_base_delay: float = 2.0

    # ── Server ──
    host: str = "127.0.0.1"
    port: int = 8422

    @model_validator(mode="before")
    @classmethod
    def _expand_context_shorthand(cls, data: dict) -> dict:
        """Allow context windows in k — e.g. 128 means 128k (131072 tokens).

        Values ≤ 10 000 are treated as multiples of 1024.  An explicit ``k``
        suffix (e.g. ``"128k"``) is also accepted.  Values > 10 000 are used
        as-is for backwards compatibility.
        """
        for field in _CONTEXT_WINDOW_FIELDS:
            raw = data.get(field)
            if raw is None:
                continue
            data[field] = _expand_ctx(raw)
        return data

    @model_validator(mode="after")
    def _validate_positive_fields(self) -> Settings:
        """Ensure critical numeric settings are positive."""
        for field_name in (
            "ollama_context_window", "openai_context_window",
            "anthropic_context_window", "gemini_context_window",
            "serve_context_window",
            "tool_timeout_seconds", "stt_cpu_threads",
        ):
            val = getattr(self, field_name, None)
            if val is not None and val <= 0:
                raise ValueError(
                    f"{field_name} must be positive, got {val}"
                )
        for field_name in (
            "stt_silence_threshold", "tts_speed",
            "refresh_threshold",
        ):
            val = getattr(self, field_name, None)
            if val is not None and val <= 0:
                raise ValueError(
                    f"{field_name} must be positive, got {val}"
                )
        return self

    @model_validator(mode="after")
    def _derive_from_context_window(self) -> Settings:
        """Fill in token limits that weren't explicitly set."""
        if self.ollama_max_tokens is None:
            self.ollama_max_tokens = self.ollama_context_window // 4
        if self.openai_max_tokens is None:
            self.openai_max_tokens = self.openai_context_window // 4
        if self.anthropic_max_tokens is None:
            self.anthropic_max_tokens = self.anthropic_context_window // 4
        if self.gemini_max_tokens is None:
            self.gemini_max_tokens = self.gemini_context_window // 4
        if self.serve_max_tokens is None:
            self.serve_max_tokens = self.serve_context_window // 4
        if self.ollama_model_expert:
            if self.ollama_expert_context_window is None:
                self.ollama_expert_context_window = self.ollama_context_window
            if self.ollama_expert_max_tokens is None:
                self.ollama_expert_max_tokens = self.ollama_expert_context_window // 4
        if self.ollama_model_request:
            if self.ollama_request_context_window is None:
                self.ollama_request_context_window = self.ollama_context_window
            if self.ollama_request_max_tokens is None:
                self.ollama_request_max_tokens = self.ollama_request_context_window // 4
        if self.inline_context_window is None:
            self.inline_context_window = self.ollama_context_window // 8
        if self.implementation_max_tokens is None:
            # Use the active provider's context window for the derived limit
            ctx = self._active_context_window
            self.implementation_max_tokens = ctx // 4
        return self

    # ── Internal helpers for DRY config resolution ──

    def _ollama_param_with_fallback(self, role: str, param: str):
        """Return ``ollama_{role}_{param}`` if set, else ``ollama_{param}``."""
        val = getattr(self, f"ollama_{role}_{param}", None)
        return val if val is not None else getattr(self, f"ollama_{param}")

    def _provider_context_window(self, provider: str) -> int:
        """Return context window for the named provider."""
        mapping = {
            "openai": self.openai_context_window,
            "anthropic": self.anthropic_context_window,
            "gemini": self.gemini_context_window,
            "serve": self.serve_context_window,
        }
        return mapping.get(provider, self.ollama_context_window)

    def _provider_max_tokens(self, provider: str, ollama_fallback: int | None = None) -> int:
        """Return max tokens for the named provider (derived from context window if unset)."""
        mapping = {
            "openai": self.openai_max_tokens or (self.openai_context_window // 4),
            "anthropic": self.anthropic_max_tokens or (self.anthropic_context_window // 4),
            "gemini": self.gemini_max_tokens or (self.gemini_context_window // 4),
            "serve": self.serve_max_tokens or (self.serve_context_window // 4),
        }
        return mapping.get(
            provider,
            (ollama_fallback or 0) or (self.ollama_context_window // 4),
        )

    # ── Public computed properties ──

    @property
    def _active_context_window(self) -> int:
        """Context window of the currently selected provider."""
        return self._provider_context_window(self.llm_provider.lower())

    @property
    def effective_inline_url(self) -> str:
        return self.inline_ollama_url or self.ollama_url

    @property
    def effective_embedding_url(self) -> str:
        return self.embedding_ollama_url or self.ollama_url

    @property
    def effective_vision_url(self) -> str:
        return self.vision_ollama_url or self.ollama_url

    @property
    def effective_expert_temperature(self) -> float:
        return self._ollama_param_with_fallback("expert", "temperature")

    @property
    def effective_expert_top_p(self) -> float:
        return self._ollama_param_with_fallback("expert", "top_p")

    @property
    def effective_expert_top_k(self) -> int:
        return self._ollama_param_with_fallback("expert", "top_k")

    @property
    def effective_expert_repeat_penalty(self) -> float:
        return self._ollama_param_with_fallback("expert", "repeat_penalty")

    @property
    def effective_expert_context_window(self) -> int:
        """Context window for the expert model provider."""
        ep = (self.expert_llm_provider or "").lower()
        if not ep or ep == "ollama":
            return self.ollama_expert_context_window or self.ollama_context_window
        return self._provider_context_window(ep)

    @property
    def effective_expert_max_tokens(self) -> int:
        """Max tokens for expert model — derived from the expert provider's settings."""
        ep = (self.expert_llm_provider or "").lower()
        if not ep or ep == "ollama":
            return self.ollama_expert_max_tokens or (self.ollama_context_window // 4)
        return self._provider_max_tokens(ep)

    @property
    def effective_request_temperature(self) -> float:
        return self._ollama_param_with_fallback("request", "temperature")

    @property
    def effective_request_top_p(self) -> float:
        return self._ollama_param_with_fallback("request", "top_p")

    @property
    def effective_request_top_k(self) -> int:
        return self._ollama_param_with_fallback("request", "top_k")

    @property
    def effective_request_repeat_penalty(self) -> float:
        return self._ollama_param_with_fallback("request", "repeat_penalty")

    @property
    def effective_request_max_tokens(self) -> int:
        """Max tokens for request model — derived from the request provider's settings."""
        rp = (self.request_llm_provider or "").lower()
        if not rp or rp == "ollama":
            return self.ollama_request_max_tokens or (self.ollama_context_window // 4)
        return self._provider_max_tokens(rp)

    @property
    def effective_refiner_url(self) -> str:
        return self.refiner_ollama_url or self.ollama_url

    @property
    def effective_refiner_model(self) -> str:
        return self.refiner_model or self.ollama_model

    @property
    def project_root(self) -> Path:
        return Path(__file__).parent

    @property
    def languages_dir(self) -> Path:
        return self.project_root / "languages"

    @property
    def scaffolds_dir(self) -> Path:
        return self.project_root / "scaffolds"


settings = Settings()
