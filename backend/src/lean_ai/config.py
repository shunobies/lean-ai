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
_CONTEXT_WINDOW_FIELDS = frozenset(
    {
        "ollama_context_window",
        "ollama_expert_context_window",
        "ollama_request_context_window",
        "ollama_worker_context_window",
        "openai_context_window",
        "anthropic_context_window",
        "gemini_context_window",
        "serve_context_window",
        "inline_context_window",
        "embedding_context_window",
    }
)


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


_REASONING_EFFORT_VALUES = frozenset({"", "low", "medium", "high", "max"})


def reasoning_effort_to_ollama_limit(effort: str) -> int | None:
    """Ollama client-side interrupt threshold in approximate tokens.

    Returns None for ``""`` (off) and ``"max"`` — no soft limit applied.
    The universal ``max_thinking_tokens`` still caps these cases as a
    safety rail.
    """
    return {"low": 768, "medium": 3072, "high": 8192}.get(effort)


def reasoning_effort_to_openai_param(effort: str) -> str | None:
    """Value for OpenAI ``reasoning_effort`` API parameter.

    Returns None for ``""`` and ``"max"`` so the param is omitted and
    the provider uses its default behavior.  OpenAI reasoning models
    accept ``"low" | "medium" | "high"``.
    """
    return {"low": "low", "medium": "medium", "high": "high"}.get(effort)


def reasoning_effort_to_anthropic_budget(effort: str) -> int | None:
    """Value for Anthropic ``thinking.budget_tokens``.

    Anthropic requires budget_tokens >= 1024 when thinking is enabled.
    Returns None for ``""`` and ``"max"`` so the field is omitted and
    Anthropic uses its default (thinking against ``max_tokens``).
    """
    return {"low": 1024, "medium": 4096, "high": 16384}.get(effort)


def reasoning_effort_to_gemini_budget(effort: str) -> int:
    """Value for Gemini 2.5 ``ThinkingConfig.thinking_budget``.

    ``-1`` = dynamic (let the model decide); used for ``""`` and
    ``"max"`` so the feature is unobtrusive by default.
    """
    return {"low": 1024, "medium": 4096, "high": 16384, "max": -1, "": -1}.get(effort, -1)


def _default_keyfile_path() -> Path:
    """Resolve the default keyfile path relative to cwd."""
    return Path.cwd() / ".lean_ai" / ".keyfile"


class _DecryptingYamlSource(PydanticBaseSettingsSource):
    """YAML settings source that decrypts ``enc:``-prefixed API key values.

    Wraps pydantic-settings' built-in ``YamlConfigSettingsSource`` and
    post-processes secret fields through :func:`decrypt_value`.
    """

    _SECRET_FIELDS = frozenset(
        {
            "openai_api_key",
            "anthropic_api_key",
            "gemini_api_key",
            "serve_api_key",
            "search_api_key",
            "jira_api_token",
            "servicenow_password",
            "wiki_password",
            "export_api_key",
        }
    )

    def __init__(self, settings_cls: type[BaseSettings], yaml_file: str = "config.yaml") -> None:
        super().__init__(settings_cls)
        from pydantic_settings import YamlConfigSettingsSource

        self._yaml_source = YamlConfigSettingsSource(settings_cls, yaml_file=yaml_file)

    def get_field_value(self, field: Any, field_name: str) -> tuple[Any, str, bool]:
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
    # Optional sampling params — blank/None means "omit from options dict"
    # entirely so text-only models that don't implement these aren't confused.
    ollama_min_p: float | None = None  # e.g. 0.05 to tighten nucleus
    ollama_presence_penalty: float | None = None  # e.g. 1.5 to reduce repetition

    # ── Ollama — expert model (reasoning-heavy phases) ──
    ollama_model_expert: str = ""  # Empty = use standard model everywhere
    ollama_expert_temperature: float | None = None  # Falls back to ollama_temperature
    ollama_expert_top_p: float | None = None  # Falls back to ollama_top_p
    ollama_expert_top_k: int | None = None  # Falls back to ollama_top_k
    ollama_expert_repeat_penalty: float | None = None  # Falls back to ollama_repeat_penalty
    ollama_expert_context_window: int | None = None  # Accepts shorthand; falls back
    ollama_expert_max_tokens: int | None = None  # Derived: 25% of expert context window
    ollama_expert_min_p: float | None = None  # Falls back to ollama_min_p (None = omit)
    ollama_expert_presence_penalty: float | None = None  # Falls back; None = omit

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
    ollama_request_min_p: float | None = None  # Falls back to ollama_min_p (None = omit)
    ollama_request_presence_penalty: float | None = None  # Falls back; None = omit
    openai_request_model: str = ""
    anthropic_request_model: str = ""

    # ── Worker model — lightweight auxiliary tasks (summarization, compression) ──
    worker_llm_provider: str = ""  # ollama/openai/anthropic/gemini/serve/""
    ollama_model_worker: str = ""  # e.g. "qwen3.5:2b-q8_0". Empty = falls back to primary
    ollama_worker_temperature: float | None = None  # Falls back to ollama_temperature
    ollama_worker_top_p: float | None = None  # Falls back to ollama_top_p
    ollama_worker_top_k: int | None = None  # Falls back to ollama_top_k
    ollama_worker_repeat_penalty: float | None = None  # Falls back to ollama_repeat_penalty
    ollama_worker_context_window: int | None = None  # Accepts shorthand; falls back
    ollama_worker_max_tokens: int | None = None  # Derived: 25% of worker context window
    ollama_worker_min_p: float | None = None  # Falls back to ollama_min_p (None = omit)
    ollama_worker_presence_penalty: float | None = None  # Falls back; None = omit
    openai_worker_model: str = ""  # Falls back to openai_model when empty
    anthropic_worker_model: str = ""  # Falls back to anthropic_model when empty
    gemini_worker_model: str = ""  # Falls back to gemini_model when empty
    serve_worker_model: str = ""  # Falls back to serve_model when empty

    # ── Thinking mode ──
    enable_thinking: bool = True  # Pass think=True to Ollama for reasoning models (Qwen3, Qwen3.5)
    enable_thinking_expert: bool = True  # Independent per-model thinking toggle
    enable_thinking_request: bool = True  # Independent per-model thinking toggle
    enable_thinking_worker: bool = False  # Disabled by default — worker model prioritizes speed

    # ── Preserve thinking across turns ──
    # Qwen3.6+ / vLLM feature: retain chain-of-thought blocks in the message
    # history so the model doesn't re-derive the same reasoning on every
    # tool-loop iteration.  Reduces redundant thinking and improves KV-cache
    # reuse.  Ignored by providers that don't honor chat_template_kwargs.
    preserve_thinking_primary: bool = False
    preserve_thinking_expert: bool = False
    preserve_thinking_request: bool = False
    preserve_thinking_worker: bool = False

    # ── Reasoning effort (per-role soft cap on thinking tokens) ──
    # Values: "" (off) | "low" | "medium" | "high" | "max".  Each provider
    # enforces via its native mechanism:
    #   - Ollama → client-side stream interrupt (counts thinking tokens)
    #   - OpenAI / Serve → extra_body.reasoning_effort (native on o1/o3/
    #     o4/gpt-5; vLLM forwards)
    #   - Anthropic → thinking.budget_tokens
    #   - Gemini → ThinkingConfig.thinking_budget
    # "max" = no soft limit; "" = provider default (no param sent).
    reasoning_effort_primary: str = ""
    reasoning_effort_expert: str = ""
    reasoning_effort_request: str = ""
    reasoning_effort_worker: str = ""
    # Universal client-side safety rail (Ollama only — cloud providers
    # enforce natively).  Fires even on effort="max"/"".  32k chosen to
    # catch truly pathological runaway loops; real-world reasoning rarely
    # exceeds ~16k.
    max_thinking_tokens: int = 32768

    # ── Per-role capability declarations ──
    # Independent booleans — a role may be flagged for one, both, or neither.
    # At runtime, the active role for the current flow is tried first; if the
    # role lacks the capability (unflagged OR provider rejects with
    # CapabilityError), the dedicated fallback kicks in (vision_model for
    # image, faster-whisper for audio).  Keeps a vision-capable primary from
    # thrashing with a separate vision model on VRAM-constrained hosts.
    supports_image_primary: bool = False
    supports_image_expert: bool = False
    supports_image_request: bool = False
    supports_image_worker: bool = False
    supports_image_inline: bool = False
    supports_audio_primary: bool = False
    supports_audio_expert: bool = False
    supports_audio_request: bool = False
    supports_audio_worker: bool = False
    supports_audio_inline: bool = False

    # ── Ollama — inline prediction model (always Ollama) ──
    inline_model: str = ""
    inline_max_tokens: int = 256
    inline_context_window: int | None = None  # Derived: 12.5% of context window (accepts shorthand)
    inline_ollama_url: str | None = None

    # ── Embedding model (always Ollama) ──
    embedding_model: str = "qwen3-embedding:0.6b"
    enable_embeddings: bool = True
    embedding_ollama_url: str | None = None
    embedding_batch_size: int = 0  # 0 = auto (50% of model context window). Positive = override
    # Context window used to size embedding batches. Defaults to a safe 8192
    # tokens so /init never blocks on an Ollama `show` call (previous default
    # of 0 enabled auto-detect, which could hang indefinitely if Ollama was
    # slow or mid-cold-load). Users on embedding models with larger windows
    # can raise this (accepts shorthand). Set to 0 to re-enable auto-detect.
    embedding_context_window: int = 8192

    # ── Vision model (always Ollama, on-demand) ──
    vision_model: str = ""  # e.g. "qwen3-vl:8b". Empty = vision disabled
    vision_ollama_url: str | None = None  # Falls back to ollama_url
    vision_max_tokens: int = 1024  # Max tokens for image description
    vision_timeout: float = 120.0  # Timeout per image description (seconds)

    # ── UI Verification (vision-backed screenshot + analysis tools) ──
    enable_ui_verification: bool = False  # Master switch for verify_web_ui + verify_desktop_ui
    ui_verification_timeout: float = 180.0  # Outer timeout wrapping whole tool call (seconds)
    ui_verification_wait_seconds: float = 3.0  # Post-render settling time before capture
    ui_verification_viewport: str = "1280x800"  # Default browser viewport (WxH)
    # Per-pass vision timeout (overrides vision_timeout for structured passes)
    ui_verification_vision_timeout: float = 180.0
    ui_verification_max_color_samples: int = 5  # Dominant colors returned by Pillow/k-means
    ui_verification_capture_backend_override: str = ""  # "" = auto-detect
    # Override values: "mss", "pywin32-print", "mac-screencapture", "mss-x11", "xdg-portal", "grim"

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
    wiki_url: str = ""  # e.g. "https://wiki.company.com" — empty = disabled
    wiki_api_path: str = "/w/api.php"  # API endpoint path (MediaWiki default)
    wiki_username: str = ""  # For authenticated wikis (bot account)
    wiki_password: str = ""  # Bot password or user password (stored in keychain)

    # ── Project context ──
    enable_project_context: bool = True
    enable_multi_round_context: bool = True
    enable_required_citations: bool = True  # Mandate documentation citations for external APIs

    # ── Reference library ──
    reference_dir: str = ".lean_ai/reference"
    reference_index_dir: str = ".lean_ai_reference_index"
    reference_chunk_chars: int = 1800  # Target characters per prose chunk (~450 tokens)
    reference_neighbor_window: int = 2  # ± chunks to include around each hit (0 = disabled)
    reference_search_default_limit: int = 5  # Default hits returned by search_reference

    # ── Local Refiner (cloud pre-processing) ──
    enable_refiner: bool = True  # Active only with cloud providers
    refiner_ollama_url: str | None = None  # Falls back to ollama_url
    refiner_model: str | None = None  # Falls back to ollama_model
    refiner_timeout: float = 30.0  # Max seconds for refinement pipeline
    refiner_enable_reference: bool = True  # Inject reference library context
    refiner_enable_privacy: bool = True  # Strip sensitive data
    refiner_reference_chunks: int = 5  # Max reference chunks to inject

    # ── Implementation ──
    implementation_max_tokens: int | None = None  # Derived: 25% of active context window
    implementation_max_turns: int = 0  # 0 = unlimited
    reminder_interval: int = 10  # Re-inject task every N tool-calling turns
    loop_detection_threshold: int = 3  # Consecutive identical tool calls before warning (0 = off)
    refresh_threshold: float = 0.7  # Refresh context at this % of context window

    # ── Planning Phase 1 ──
    plan_phase1_max_turns: int = 5  # Max tool-calling turns for scope analysis (0 = no tools)

    # ── Parallel LLM requests ──
    num_parallel: int = 1  # Max concurrent LLM calls (match OLLAMA_NUM_PARALLEL)

    # ── Fix-mode investigation phase ──
    enable_fix_investigation: bool = True  # Read-only investigation before editing

    # ── Claim verification ──
    enable_claim_verification: bool = True  # Nudge LLM to verify external claims via web search

    # ── TDD mode ──
    enable_tdd: bool = False  # Expert writes tests first, primary implements
    tdd_max_disputes_per_step: int = 3  # Max test disputes per implementation step

    # ── Phase 5 strict-test contract (programmatic-only, hooks required) ──
    enable_strict_test_contract: bool = True
    # Opt-in tool-backed exploration turn inside Phase 5 when Phase 2's
    # testing_inventory is thin. Costs extra turns; disabled by default.
    enable_phase5_investigation: bool = False
    # Regression file convention — path-component regex. Case-insensitive
    # match via re.search. Files matching this pattern are guarded from
    # edits by the regression guard once finalized.
    regression_file_pattern: str = (
        r"(?i)(?:^|[/\\])regressions?(?:[/\\]|[_-][^/\\]*\.[A-Za-z0-9]+$)"
    )
    # Layer 9 — Core-functionality detection. When enabled, Phase 3 tags
    # load-bearing entities and Phase 5 MUST produce regression tests for
    # each tag. Prune via approval UI.
    enable_core_functionality_tagging: bool = True
    # Minimum confidence to auto-promote a tag into Phase 5's mandatory
    # regression list. "low" lets the user prune aggressively; "high"
    # only enforces high-confidence tags.
    core_functionality_min_confidence: str = "medium"  # "low" | "medium" | "high"

    # ── Post-execution validation ──
    enable_post_validation: bool = True  # Master switch
    post_format_command: str = ""  # e.g. "ruff format src/"
    post_lint_fix_command: str = ""  # e.g. "ruff check --fix src/"
    post_lint_command: str = ""  # e.g. "ruff check src/"
    post_test_command: str = ""  # e.g. "pytest tests/ -x -q"
    post_validation_max_retries: int = 2  # Max LLM fix attempts (0 = no retries)
    post_validation_fix_turns: int = 30  # Tool-calling turns per fix attempt

    # ── Cross-session memory ──
    enable_session_memory: bool = True  # Extract and reuse memories across sessions
    # Curation — which statuses are allowed during retrieval-time filtering.
    # Comma-separated. Default excludes raw `auto` memories so unconfirmed
    # extractions don't poison planning until a user (or auto-promotion)
    # upgrades them.
    memory_retrieval_statuses: str = "user_confirmed,high_confidence_auto"
    memory_confidence_ttl_days: int = 90
    memory_autopromote_threshold: int = 3  # seen_count to auto→high_confidence_auto
    enable_phase3_memory: bool = True  # Inject memories into Phase 3 design
    enable_fix_loop_memory: bool = True  # Inject fix_patterns into validation fix loop
    phase3_memory_budget_percent: float = 0.02
    fix_loop_memory_budget_percent: float = 0.02

    # ── Self-improvement training pipeline (Phase B+; Layer 1 defaults) ──
    enable_training_capture: bool = True  # Local capture runs; export gated below
    training_db_path: str = ".lean_ai/training.db"
    training_retention_days: int = 365
    capture_thinking: bool = True  # Preserve <think> blocks for reasoning LoRA
    scrubbing_strict: bool = True  # Fail-closed on scrubber exception
    # Empty string disables the /api/export endpoints (returns 503 until set).
    export_api_key: str = ""
    export_workspace_salt: str = ""  # Optional stable salt for workspace_id hash
    memory_export_drop_threshold: float = 0.40  # Drop memories >40% redacted

    # ── Integrations (Jira, ServiceNow, etc.) ──
    enable_integrations: bool = False  # Master switch for external integrations
    integration_auto_push: bool = True  # Auto-push session summaries on completion

    # Jira Cloud
    jira_url: str = ""  # e.g. "https://yourcompany.atlassian.net"
    jira_email: str = ""  # Jira account email
    jira_api_token: str = ""  # Jira API token (stored in OS keychain)

    # ServiceNow
    servicenow_url: str = ""  # e.g. "https://yourinstance.service-now.com"
    servicenow_username: str = ""  # ServiceNow username
    servicenow_password: str = ""  # ServiceNow password (stored in OS keychain)
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
            "ollama_context_window",
            "openai_context_window",
            "anthropic_context_window",
            "gemini_context_window",
            "serve_context_window",
            "tool_timeout_seconds",
            "stt_cpu_threads",
        ):
            val = getattr(self, field_name, None)
            if val is not None and val <= 0:
                raise ValueError(f"{field_name} must be positive, got {val}")
        for field_name in (
            "stt_silence_threshold",
            "tts_speed",
            "refresh_threshold",
        ):
            val = getattr(self, field_name, None)
            if val is not None and val <= 0:
                raise ValueError(f"{field_name} must be positive, got {val}")
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
        if self.ollama_model_worker:
            if self.ollama_worker_context_window is None:
                self.ollama_worker_context_window = self.ollama_context_window
            if self.ollama_worker_max_tokens is None:
                self.ollama_worker_max_tokens = self.ollama_worker_context_window // 4
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
    def effective_expert_min_p(self) -> float | None:
        """Returns None when neither role nor primary set it — caller should omit."""
        return self._ollama_param_with_fallback("expert", "min_p")

    @property
    def effective_expert_presence_penalty(self) -> float | None:
        """Returns None when neither role nor primary set it — caller should omit."""
        return self._ollama_param_with_fallback("expert", "presence_penalty")

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
    def effective_request_min_p(self) -> float | None:
        return self._ollama_param_with_fallback("request", "min_p")

    @property
    def effective_request_presence_penalty(self) -> float | None:
        return self._ollama_param_with_fallback("request", "presence_penalty")

    @property
    def effective_request_max_tokens(self) -> int:
        """Max tokens for request model — derived from the request provider's settings."""
        rp = (self.request_llm_provider or "").lower()
        if not rp or rp == "ollama":
            return self.ollama_request_max_tokens or (self.ollama_context_window // 4)
        return self._provider_max_tokens(rp)

    @property
    def effective_worker_temperature(self) -> float:
        return self._ollama_param_with_fallback("worker", "temperature")

    @property
    def effective_worker_top_p(self) -> float:
        return self._ollama_param_with_fallback("worker", "top_p")

    @property
    def effective_worker_top_k(self) -> int:
        return self._ollama_param_with_fallback("worker", "top_k")

    @property
    def effective_worker_repeat_penalty(self) -> float:
        return self._ollama_param_with_fallback("worker", "repeat_penalty")

    @property
    def effective_worker_min_p(self) -> float | None:
        return self._ollama_param_with_fallback("worker", "min_p")

    @property
    def effective_worker_presence_penalty(self) -> float | None:
        return self._ollama_param_with_fallback("worker", "presence_penalty")

    # ── Reasoning effort effective fallback (string field, custom chain) ──

    def _effective_reasoning_effort(self, role: str) -> str:
        val = getattr(self, f"reasoning_effort_{role}", "")
        if val:
            return val
        return self.reasoning_effort_primary

    @property
    def effective_expert_reasoning_effort(self) -> str:
        return self._effective_reasoning_effort("expert")

    @property
    def effective_request_reasoning_effort(self) -> str:
        return self._effective_reasoning_effort("request")

    @property
    def effective_worker_reasoning_effort(self) -> str:
        return self._effective_reasoning_effort("worker")

    @property
    def effective_worker_context_window(self) -> int:
        """Context window for the worker model provider."""
        wp = (self.worker_llm_provider or "").lower()
        if not wp or wp == "ollama":
            return self.ollama_worker_context_window or self.ollama_context_window
        return self._provider_context_window(wp)

    @property
    def effective_worker_max_tokens(self) -> int:
        """Max tokens for worker model — derived from the worker provider's settings."""
        wp = (self.worker_llm_provider or "").lower()
        if not wp or wp == "ollama":
            return self.ollama_worker_max_tokens or (self.ollama_context_window // 4)
        return self._provider_max_tokens(wp)

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
