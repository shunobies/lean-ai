"""Application configuration via pydantic-settings.

Token limits are derived from ``ollama_context_window`` so that changing
a single value (or upgrading a GPU) automatically scales all limits.
"""

from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LEAN_AI_", env_file=".env")

    # ── Ollama — primary model ──
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "qwen3-coder:30b"
    ollama_temperature: float = 0.7  # Qwen3 warns against greedy decoding (0.0)
    ollama_top_p: float = 0.8
    ollama_top_k: int = 20
    ollama_repeat_penalty: float = 1.05
    ollama_context_window: int = 131072  # Single source of truth
    ollama_max_tokens: int | None = None  # Derived: 25% of context window

    # ── Ollama — inline prediction model ──
    inline_model: str = ""
    inline_max_tokens: int = 256
    inline_context_window: int | None = None  # Derived: 12.5% of context window
    inline_ollama_url: str | None = None

    # ── Embedding model ──
    embedding_model: str = "qwen3-embedding:0.6b"
    enable_embeddings: bool = True
    embedding_ollama_url: str | None = None

    # ── Indexer ──
    index_dir: str = ".lean_ai_index"
    chunk_max_lines: int = 50
    chunk_overlap_lines: int = 10

    # ── Internet / Search ──
    search_provider: str = "duckduckgo"  # "duckduckgo" or "searxng"
    search_api_url: str = ""
    search_api_key: str = ""
    internet_timeout_seconds: int = 30

    # ── Project context ──
    enable_project_context: bool = True
    enable_multi_round_context: bool = True

    # ── Knowledge base ──
    knowledge_dir: str = ".lean_ai/knowledge"
    knowledge_index_dir: str = ".lean_ai_knowledge_index"

    # ── Implementation ──
    implementation_max_tokens: int | None = None  # Derived: 25% of context window
    implementation_max_turns: int = 0  # 0 = unlimited
    reminder_interval: int = 10  # Re-inject task every N tool-calling turns

    # ── Tool execution ──
    tool_timeout_seconds: int = 60

    # ── LLM retry ──
    llm_retry_max: int = 3
    llm_retry_base_delay: float = 2.0

    # ── Server ──
    host: str = "127.0.0.1"
    port: int = 8422

    @model_validator(mode="after")
    def _derive_from_context_window(self) -> "Settings":
        """Fill in token limits that weren't explicitly set."""
        if self.ollama_max_tokens is None:
            self.ollama_max_tokens = self.ollama_context_window // 4
        if self.inline_context_window is None:
            self.inline_context_window = self.ollama_context_window // 8
        if self.implementation_max_tokens is None:
            self.implementation_max_tokens = self.ollama_context_window // 4
        return self

    @property
    def effective_inline_url(self) -> str:
        return self.inline_ollama_url or self.ollama_url

    @property
    def effective_embedding_url(self) -> str:
        return self.embedding_ollama_url or self.ollama_url

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
