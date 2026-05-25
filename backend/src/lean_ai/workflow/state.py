"""Unified workflow state management.

Consolidates six fragmented state sources into one validated JSON file:
  - conversation history (from conversation_logs table)
  - scratchpad content (from .lean_ai/scratchpads/)
  - journal entries (from .lean_ai/journals/)
  - observations (from .lean_ai/observations/)
  - current plan (from ExecutionPlan)
  - session metadata

The StateManager class provides load/save with legacy migration,
reading from old file locations and the conversation_logs table,
then writing a single .lean_ai/state/{session_id}.json file.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

# ── Bounds ──────────────────────────────────────────────────────────────────
MAX_CONVERSATION_TURNS = 20
MAX_CONVERSATION_CHARS = 50_000


# ── WorkflowState model ────────────────────────────────────────────────────
class WorkflowState(BaseModel):
    """Consolidated workflow state for a single session.

    Replaces the six separate state sources (conversation_logs, scratchpad,
    journal, observations, plan file, session metadata) with one validated
    JSON document.
    """

    model_config = ConfigDict(extra="forbid")

    session_id: str
    current_phase: str = ""
    conversation_history: list[dict[str, Any]] = Field(default_factory=list)
    scratchpad_content: str = ""
    journal_entries: list[str] = Field(default_factory=list)
    observations: list[dict[str, Any]] = Field(default_factory=list)
    current_plan: dict[str, Any] | None = None
    session_metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    updated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    @classmethod
    def from_scratch(cls, session_id: str) -> "WorkflowState":
        """Create a fresh WorkflowState for a new session."""
        now = datetime.now(timezone.utc).isoformat()
        return cls(
            session_id=session_id,
            created_at=now,
            updated_at=now,
        )

    # ── Bound enforcement helpers ────────────────────────────────────────

    def add_conversation_turn(self, turn: dict[str, Any]) -> None:
        """Append a conversation turn, enforcing turn and char bounds.

        Keeps only the most recent turns when limits are exceeded.
        """
        self.conversation_history.append(turn)
        self._enforce_conversation_bounds()
        self.updated_at = datetime.now(timezone.utc).isoformat()

    def _enforce_conversation_bounds(self) -> None:
        """Trim conversation_history to stay within turn and char limits."""
        # Trim by turn count
        while len(self.conversation_history) > MAX_CONVERSATION_TURNS:
            self.conversation_history.pop(0)

        # Trim by character count
        total_chars = sum(
            len(json.dumps(turn, ensure_ascii=False))
            for turn in self.conversation_history
        )
        while total_chars > MAX_CONVERSATION_CHARS and self.conversation_history:
            self.conversation_history.pop(0)
            total_chars = sum(
                len(json.dumps(turn, ensure_ascii=False))
                for turn in self.conversation_history
            )

    def add_journal_entry(self, entry: str) -> None:
        """Append a journal entry."""
        self.journal_entries.append(entry)
        self.updated_at = datetime.now(timezone.utc).isoformat()

    def set_current_plan(self, plan_dict: dict[str, Any]) -> None:
        """Store a serialized ExecutionPlan as a plain dict."""
        self.current_plan = plan_dict
        self.updated_at = datetime.now(timezone.utc).isoformat()


# ── StateManager ───────────────────────────────────────────────────────────
class StateManager:
    """Manages persistence of WorkflowState for a single session.

    Creates the ``.lean_ai/state/`` directory if missing, handles JSON
    I/O with validation, and implements dual-mode fallback reading from
    legacy scratchpad/journal/observations files and the conversation_logs
    SQLite table. Deletes legacy files after a successful save.
    """

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self._state_dir = Path(".lean_ai") / "state"
        self._state_file = self._state_dir / f"{session_id}.json"
        self._state: WorkflowState | None = None

    def _state_path(self) -> Path:
        """Return the path to the consolidated state file."""
        return self._state_file

    def load(self) -> WorkflowState:
        """Load state from the consolidated JSON file.

        Falls back to legacy sources if the consolidated file does not exist.
        """
        if self._state_file.is_file():
            try:
                data = json.loads(self._state_file.read_text(encoding="utf-8"))
                self._state = WorkflowState(**data)
                logger.info("Loaded consolidated state for session %s", self.session_id)
                return self._state
            except Exception:
                logger.warning(
                    "Failed to load consolidated state for %s, falling back to legacy",
                    self.session_id,
                    exc_info=True,
                )

        # Fall back to legacy sources
        return self._load_legacy()

    def _load_legacy(self) -> WorkflowState:
        """Read state from legacy scratchpad/journal/observations files and DB.

        Returns a WorkflowState populated from whatever legacy sources exist.
        """
        state = WorkflowState.from_scratch(self.session_id)

        # Scratchpad
        scratchpad_file = Path(".lean_ai") / "scratchpads" / f"{self.session_id}.md"
        if scratchpad_file.is_file():
            try:
                state.scratchpad_content = scratchpad_file.read_text(encoding="utf-8")
                logger.info("Loaded legacy scratchpad for %s", self.session_id)
            except Exception:
                logger.warning("Failed to read legacy scratchpad", exc_info=True)

        # Journal
        journal_file = Path(".lean_ai") / "journals" / f"{self.session_id}.md"
        if journal_file.is_file():
            try:
                raw = journal_file.read_text(encoding="utf-8")
                # Parse timestamped lines like "- [HH:MM] content"
                entries = [
                    line.strip()
                    for line in raw.splitlines()
                    if line.strip().startswith("- [")
                ]
                state.journal_entries = entries
                logger.info("Loaded legacy journal for %s", self.session_id)
            except Exception:
                logger.warning("Failed to read legacy journal", exc_info=True)

        # Observations
        obs_file = Path(".lean_ai") / "observations" / f"{self.session_id}.json"
        if obs_file.is_file():
            try:
                data = json.loads(obs_file.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    state.observations = data
                    logger.info("Loaded legacy observations for %s", self.session_id)
            except Exception:
                logger.warning("Failed to read legacy observations", exc_info=True)

        # Conversation history from SQLite conversation_logs table
        try:
            from lean_ai.db import get_conversation_log, get_db

            import asyncio

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                db = loop.run_until_complete(get_db(str(Path(".").resolve())))
                conv_entries = loop.run_until_complete(
                    get_conversation_log(db, self.session_id)
                )
                state.conversation_history = conv_entries
                logger.info(
                    "Loaded %d conversation entries for %s",
                    len(conv_entries),
                    self.session_id,
                )
            finally:
                loop.close()
        except Exception:
            logger.warning(
                "Failed to load conversation log from DB for %s",
                self.session_id,
                exc_info=True,
            )

        self._state = state
        return state

    def save(self) -> None:
        """Persist the current state to the consolidated JSON file.

        Deletes legacy scratchpad/journal/observations files after a
        successful write to complete the migration.
        """
        if self._state is None:
            raise RuntimeError("No state to save — call load() or set state first")

        self._state.updated_at = datetime.now(timezone.utc).isoformat()

        self._state_dir.mkdir(parents=True, exist_ok=True)
        self._state_file.write_text(
            json.dumps(self._state.model_dump(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.info(
            "Saved consolidated state for %s at %s",
            self.session_id,
            self._state_file,
        )

        # Delete legacy files after successful save
        self._cleanup_legacy_files()

    def _cleanup_legacy_files(self) -> None:
        """Remove legacy scratchpad/journal/observations files after migration."""
        legacy_paths = [
            Path(".lean_ai") / "scratchpads" / f"{self.session_id}.md",
            Path(".lean_ai") / "journals" / f"{self.session_id}.md",
            Path(".lean_ai") / "observations" / f"{self.session_id}.json",
        ]
        for path in legacy_paths:
            if path.is_file():
                try:
                    path.unlink()
                    logger.info("Removed legacy file: %s", path)
                except Exception:
                    logger.warning("Failed to remove legacy file: %s", path, exc_info=True)

    def get_state(self) -> WorkflowState:
        """Return the current in-memory state, loading if necessary."""
        if self._state is None:
            return self.load()
        return self._state

    def refresh_state(self) -> WorkflowState:
        """Reload state from disk, overwriting any in-memory changes."""
        self._state = None
        return self.load()
