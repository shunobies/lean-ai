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
"""

from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Fields that accept the k-shorthand notation.
_CONTEXT_WINDOW_FIELDS = frozenset({
    "ollama_context_window",
    "ollama_expert_context_window",
    "openai_context_window",
    "anthropic_context_window",
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


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LEAN_AI_", env_file=".env")

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

    # ── Expert model — provider selection ──
    expert_llm_provider: str = ""  # "openai", "anthropic", "ollama", or "" (auto-detect from OLLAMA_MODEL_EXPERT)

    # ── OpenAI — expert model ──
    openai_expert_model: str = ""  # Model to use when expert_llm_provider=openai (falls back to openai_model)

    # ── Anthropic — expert model ──
    anthropic_expert_model: str = ""  # Model to use when expert_llm_provider=anthropic (falls back to anthropic_model)

    # ── Ollama — inline prediction model (always Ollama) ──
    inline_model: str = ""
    inline_max_tokens: int = 256
    inline_context_window: int | None = None  # Derived: 12.5% of context window (accepts shorthand)
    inline_ollama_url: str | None = None

    # ── Embedding model (always Ollama) ──
    embedding_model: str = "qwen3-embedding:0.6b"
    enable_embeddings: bool = True
    embedding_ollama_url: str | None = None

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

    # ── Project context ──
    enable_project_context: bool = True
    enable_multi_round_context: bool = True
    enable_framework_guide: bool = True  # Generate .lean_ai/framework_guide.md

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

    # ── Post-execution validation ──
    enable_post_validation: bool = True  # Master switch
    post_format_command: str = ""     # e.g. "ruff format src/"
    post_lint_fix_command: str = ""   # e.g. "ruff check --fix src/"
    post_lint_command: str = ""       # e.g. "ruff check src/"
    post_test_command: str = ""       # e.g. "pytest tests/ -x -q"
    post_validation_max_retries: int = 2  # Max LLM fix attempts (0 = no retries)
    post_validation_fix_turns: int = 30  # Tool-calling turns per fix attempt

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
    def _derive_from_context_window(self) -> "Settings":
        """Fill in token limits that weren't explicitly set."""
        if self.ollama_max_tokens is None:
            self.ollama_max_tokens = self.ollama_context_window // 4
        if self.openai_max_tokens is None:
            self.openai_max_tokens = self.openai_context_window // 4
        if self.anthropic_max_tokens is None:
            self.anthropic_max_tokens = self.anthropic_context_window // 4
        if self.ollama_model_expert:
            if self.ollama_expert_context_window is None:
                self.ollama_expert_context_window = self.ollama_context_window
            if self.ollama_expert_max_tokens is None:
                self.ollama_expert_max_tokens = self.ollama_expert_context_window // 4
        if self.inline_context_window is None:
            self.inline_context_window = self.ollama_context_window // 8
        if self.implementation_max_tokens is None:
            # Use the active provider's context window for the derived limit
            ctx = self._active_context_window
            self.implementation_max_tokens = ctx // 4
        return self

    @property
    def _active_context_window(self) -> int:
        """Context window of the currently selected provider."""
        provider = self.llm_provider.lower()
        if provider == "openai":
            return self.openai_context_window
        if provider == "anthropic":
            return self.anthropic_context_window
        return self.ollama_context_window

    @property
    def effective_inline_url(self) -> str:
        return self.inline_ollama_url or self.ollama_url

    @property
    def effective_embedding_url(self) -> str:
        return self.embedding_ollama_url or self.ollama_url

    @property
    def effective_expert_temperature(self) -> float:
        val = self.ollama_expert_temperature
        return val if val is not None else self.ollama_temperature

    @property
    def effective_expert_top_p(self) -> float:
        val = self.ollama_expert_top_p
        return val if val is not None else self.ollama_top_p

    @property
    def effective_expert_top_k(self) -> int:
        val = self.ollama_expert_top_k
        return val if val is not None else self.ollama_top_k

    @property
    def effective_expert_repeat_penalty(self) -> float:
        val = self.ollama_expert_repeat_penalty
        return val if val is not None else self.ollama_repeat_penalty

    @property
    def effective_expert_max_tokens(self) -> int:
        """Max tokens for expert model — derived from the expert provider's settings."""
        ep = (self.expert_llm_provider or "").lower()
        if ep == "openai":
            return self.openai_max_tokens or (self.openai_context_window // 4)
        elif ep == "anthropic":
            return self.anthropic_max_tokens or (self.anthropic_context_window // 4)
        else:  # ollama (default / backwards-compat)
            return self.ollama_expert_max_tokens or (self.ollama_context_window // 4)

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
