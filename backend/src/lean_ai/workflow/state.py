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
import asyncio
import uuid
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

    def __init__(
        self, session_id: str, checkpoints_dir: str | Path | None = None
    ) -> None:
        self.session_id = session_id
        self._state_dir = Path(".lean_ai") / "state"
        self._state_file = self._state_dir / f"{session_id}.json"
        self._state: WorkflowState | None = None
        self._checkpoints_dir_base = Path(
            checkpoints_dir if checkpoints_dir is not None else Path(".lean_ai") / "checkpoints"
        )

    def _state_path(self) -> Path:
        """Return the path to the consolidated state file."""
        return self._state_file

    def load(self) -> WorkflowState:
        """Load state from sync code.

        Raises a clear error when called from an active event loop.
        """
        return self._run_async_helper(self.load_async(), "load_async()")

    async def load_async(self) -> WorkflowState:
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
        return await self._load_legacy_async()

    def _load_legacy(self) -> WorkflowState:
        """Read state from legacy sources from sync code."""
        return self._run_async_helper(self._load_legacy_async(), "_load_legacy_async()")

    async def _load_legacy_async(self) -> WorkflowState:
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
            from lean_ai.db import get_conversation_log, get_db  # noqa: I001

            db = await get_db(str(Path(".").resolve()))
            try:
                conv_entries = await get_conversation_log(db, self.session_id)
                state.conversation_history = conv_entries
                logger.info(
                    "Loaded %d conversation entries for %s",
                    len(conv_entries),
                    self.session_id,
                )
            finally:
                await db.close()
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

    async def get_state_async(self) -> WorkflowState:
        """Return the current in-memory state, loading if necessary."""
        if self._state is None:
            return await self.load_async()
        return self._state

    def get_cached_state(self) -> WorkflowState:
        """Return the current in-memory state without loading from disk."""
        if self._state is None:
            raise RuntimeError(
                "State is not loaded — call load_async() before using cached state"
            )
        return self._state

    def refresh_state(self) -> WorkflowState:
        """Reload state from disk, overwriting any in-memory changes."""
        self._state = None
        return self.load()

    # ── Checkpoint helpers ────────────────────────────────────────

    def _checkpoints_dir(self, session_id: str) -> Path:
        """Return the checkpoint directory for a given session."""
        return self._checkpoints_dir_base / session_id

    @property
    def _checkpoints_dir_base(self) -> Path:
        """Return the base checkpoints directory."""
        return self._checkpoints_dir_base_prop

    @_checkpoints_dir_base.setter
    def _checkpoints_dir_base(self, value: Path) -> None:
        self._checkpoints_dir_base_prop = value

    def save_checkpoint(
        self,
        state: WorkflowState,
        phase: str,
        summary: str,
        parent_id: str | None = None,
    ) -> str:
        """Save a checkpoint from sync code."""
        return self._run_async_helper(
            self.save_checkpoint_async(
                state=state,
                phase=phase,
                summary=summary,
                parent_id=parent_id,
            ),
            "save_checkpoint_async()",
        )

    async def save_checkpoint_async(
        self,
        state: WorkflowState,
        phase: str,
        summary: str,
        parent_id: str | None = None,
    ) -> str:
        """Save a checkpoint of the given state.

        Serialises *state* to JSON, writes the JSON file to the
        per-session checkpoint directory, and persists the full
        serialised state into the SQLite ``checkpoints`` table.

        Returns the checkpoint ID (a hex string).
        """
        checkpoint_id = uuid.uuid4().hex[:12]
        now = datetime.now(timezone.utc).isoformat()

        # Ensure checkpoint directory exists
        cp_dir = self._checkpoints_dir_base / self.session_id
        cp_dir.mkdir(parents=True, exist_ok=True)

        # Write JSON cache file
        cp_file = cp_dir / f"{checkpoint_id}.json"
        cp_file.write_text(
            json.dumps(state.model_dump(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        # Persist state_json into SQLite
        try:
            from lean_ai.db import get_db

            db = await get_db(str(Path(".").resolve()))
            try:
                await db.execute(
                    "INSERT INTO checkpoints "
                    "(id, session_id, parent_id, phase, state_json, timestamp, summary) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        checkpoint_id,
                        self.session_id,
                        parent_id,
                        phase,
                        json.dumps(state.model_dump(), ensure_ascii=False),
                        now,
                        summary,
                    ),
                )
                await db.commit()
            finally:
                await db.close()
        except Exception:
            logger.warning(
                "Failed to persist checkpoint %s to SQLite for %s",
                checkpoint_id,
                self.session_id,
                exc_info=True,
            )

        logger.info(
            "Saved checkpoint %s for session %s (phase=%s)",
            checkpoint_id,
            self.session_id,
            phase,
        )

        # Enforce cache limit
        self._cleanup_checkpoint_cache()

        return checkpoint_id

    def get_checkpoint(self, checkpoint_id: str) -> WorkflowState:
        """Load a checkpoint from sync code."""
        return self._run_async_helper(
            self.get_checkpoint_async(checkpoint_id),
            "get_checkpoint_async()",
        )

    async def get_checkpoint_async(self, checkpoint_id: str) -> WorkflowState:
        """Load a checkpoint by ID.

        Attempts to read the JSON cache file first; on ``FileNotFoundError``
        falls back to reconstructing the state from the SQLite ``state_json``
        blob.
        """
        cp_dir = self._checkpoints_dir_base / self.session_id
        cp_file = cp_dir / f"{checkpoint_id}.json"

        # Try JSON cache first
        if cp_file.is_file():
            try:
                data = json.loads(cp_file.read_text(encoding="utf-8"))
                return WorkflowState(**data)
            except Exception:
                logger.warning(
                    "Failed to load checkpoint %s from JSON cache, falling back to SQLite",
                    checkpoint_id,
                    exc_info=True,
                )

        # Fallback: reconstruct from SQLite
        try:
            from lean_ai.db import get_db

            db = await get_db(str(Path(".").resolve()))
            try:
                cursor = await db.execute(
                    "SELECT state_json FROM checkpoints WHERE id = ?",
                    (checkpoint_id,),
                )
                row = await cursor.fetchone()
                if row is None:
                    raise FileNotFoundError(
                        f"Checkpoint {checkpoint_id} not found"
                    )
                state_json = row["state_json"] or row[0]
                data = json.loads(state_json)
                return WorkflowState(**data)
            finally:
                await db.close()
        except Exception:
            logger.warning(
                "Failed to load checkpoint %s from SQLite",
                checkpoint_id,
                exc_info=True,
            )
            raise FileNotFoundError(
                f"Checkpoint {checkpoint_id} not found in cache or database"
            ) from None

    def list_checkpoints(self, session_id: str) -> list[dict]:
        """List checkpoints from sync code."""
        return self._run_async_helper(
            self.list_checkpoints_async(session_id),
            "list_checkpoints_async()",
        )

    async def list_checkpoints_async(self, session_id: str) -> list[dict]:
        """Return metadata-only checkpoint tree for *session_id*.

        Queries only ``id, session_id, parent_id, phase, summary, timestamp``
        columns from SQLite (never loads ``state_json`` blobs).
        """
        try:
            from lean_ai.db import get_db

            db = await get_db(str(Path(".").resolve()))
            try:
                cursor = await db.execute(
                    "SELECT id, session_id, parent_id, phase, summary, "
                    "timestamp FROM checkpoints "
                    "WHERE session_id = ? ORDER BY timestamp ASC",
                    (session_id,),
                )
                rows = await cursor.fetchall()
            finally:
                await db.close()
        except Exception:
            logger.warning(
                "Failed to list checkpoints for session %s", session_id, exc_info=True
            )
            return []

        # Determine which checkpoint is the head (latest by timestamp)
        if rows:
            head_id = rows[-1]["id"] if isinstance(rows[-1], dict) else rows[-1][0]
        else:
            head_id = None

        result: list[dict] = []
        for idx, row in enumerate(rows):
            rid = row["id"] if isinstance(row, dict) else row[0]
            phase = row["phase"] if isinstance(row, dict) else row[3]
            summary = row["summary"] if isinstance(row, dict) else row[4]
            created_at = row["timestamp"] if isinstance(row, dict) else row[5]
            is_head = rid == head_id
            result.append(
                {
                    "id": rid,
                    "session_id": row["session_id"] if isinstance(row, dict) else row[1],
                    "parent_id": row["parent_id"] if isinstance(row, dict) else row[2],
                    "phase": phase,
                    "summary": summary,
                    "label": phase or summary or f"Checkpoint {idx + 1}",
                    "created_at": created_at,
                    "timestamp": created_at,
                    "status": "active" if is_head else "completed",
                    "is_head": is_head,
                }
            )
        return result

    def _cleanup_checkpoint_cache(self) -> None:
        """Delete oldest JSON checkpoint files when count exceeds 50."""
        cp_dir = self._checkpoints_dir_base / self.session_id
        if not cp_dir.is_dir():
            return

        cp_files = sorted(
            cp_dir.glob("*.json"),
            key=lambda p: p.stat().st_mtime,
        )

        max_cache = 50
        if len(cp_files) > max_cache:
            to_delete = cp_files[: len(cp_files) - max_cache]
            for f in to_delete:
                try:
                    f.unlink()
                    logger.info("Removed stale checkpoint file: %s", f)
                except Exception:
                    logger.warning("Failed to remove checkpoint file: %s", f, exc_info=True)

    def _run_async_helper(self, coro: Any, async_name: str) -> Any:
        """Run an async state helper from sync code when no loop is active."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)

        coro.close()
        raise RuntimeError(
            f"StateManager.{async_name} must be awaited when called from async code"
        )
