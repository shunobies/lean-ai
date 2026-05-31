"""Unit tests for EvaluationRunner and DatasetService.

Covers:
  1. DatasetService: create_dataset, update_dataset, list_datasets,
     add_traces_to_dataset, get_dataset_traces, UUID references
  2. EvaluationRunner: run_evaluation with mock LLMClient, score
     calculation, evaluation_results persistence, frozen context
  3. Fixtures for isolated SQLite database using tmp_path
  4. Tests verify scores are calculated correctly and stored in
     evaluation_results table
  5. Tests verify UUID references to training_traces without duplication
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from lean_ai.training.capture import DatasetService, EvaluationRunner
from lean_ai.training.db import (
    get_training_db,
    insert_training_trace,
)


# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture
async def eval_db(tmp_path):
    """Training DB connection scoped to a temp directory."""
    db = await get_training_db(str(tmp_path))
    yield db
    await db.close()


@pytest.fixture
def ds(tmp_path) -> DatasetService:
    """DatasetService pointing at a temp directory."""
    return DatasetService(repo_root=str(tmp_path))


@pytest.fixture
async def seed_traces(eval_db):
    """Insert a few training traces and return their UUIDs."""
    trace_uuids = [
        "trace-uuid-001",
        "trace-uuid-002",
        "trace-uuid-003",
    ]
    for i, uuid_ in enumerate(trace_uuids):
        messages = [
            {"role": "user", "content": f"User question {i + 1}"},
            {"role": "assistant", "content": f"Assistant answer {i + 1}"},
        ]
        assistant_output = {"content": f"Response {i + 1}"}
        await insert_training_trace(
            eval_db,
            trace_uuid=uuid_,
            session_id="test-session",
            phase="planning",
            model_name="gpt-4",
            provider="openai",
            messages=messages,
            assistant_output=assistant_output,
            outcome="success",
        )
    return trace_uuids


# ── TestDatasetService ──────────────────────────────────────────────


class TestDatasetService:
    """Tests for DatasetService dataset management operations."""

    async def test_create_dataset(self, ds: DatasetService):
        """create_dataset inserts a row and returns the id."""
        dataset_id = await ds.create_dataset("test-dataset", version="1", description="A test dataset")
        assert dataset_id >= 1

        # Verify the row exists
        db = await get_training_db(ds.repo_root)
        try:
            cursor = await db.execute(
                "SELECT name, version, description FROM evaluation_datasets WHERE id = ?",
                (dataset_id,),
            )
            row = await cursor.fetchone()
            assert row is not None
            assert row["name"] == "test-dataset"
            assert row["version"] == "1"
            assert row["description"] == "A test dataset"
        finally:
            await db.close()

    async def test_create_dataset_default_version(self, ds: DatasetService):
        """create_dataset defaults version to '1' when not specified."""
        dataset_id = await ds.create_dataset("minimal-dataset")
        assert dataset_id >= 1

        db = await get_training_db(ds.repo_root)
        try:
            cursor = await db.execute(
                "SELECT version FROM evaluation_datasets WHERE id = ?",
                (dataset_id,),
            )
            row = await cursor.fetchone()
            assert row["version"] == "1"
        finally:
            await db.close()

    async def test_update_dataset_name(self, ds: DatasetService):
        """update_dataset changes the name when provided."""
        dataset_id = await ds.create_dataset("original-name")
        changed = await ds.update_dataset(dataset_id, name="updated-name")
        assert changed is True

        db = await get_training_db(ds.repo_root)
        try:
            cursor = await db.execute(
                "SELECT name FROM evaluation_datasets WHERE id = ?",
                (dataset_id,),
            )
            row = await cursor.fetchone()
            assert row["name"] == "updated-name"
        finally:
            await db.close()

    async def test_update_dataset_version(self, ds: DatasetService):
        """update_dataset changes the version when provided."""
        dataset_id = await ds.create_dataset("versioned-dataset", version="1")
        changed = await ds.update_dataset(dataset_id, version="2")
        assert changed is True

        db = await get_training_db(ds.repo_root)
        try:
            cursor = await db.execute(
                "SELECT version FROM evaluation_datasets WHERE id = ?",
                (dataset_id,),
            )
            row = await cursor.fetchone()
            assert row["version"] == "2"
        finally:
            await db.close()

    async def test_update_dataset_no_fields_returns_false(self, ds: DatasetService):
        """update_dataset returns False when no fields are provided."""
        dataset_id = await ds.create_dataset("no-change-dataset")
        changed = await ds.update_dataset(dataset_id)
        assert changed is False

    async def test_update_dataset_nonexistent_returns_false(self, ds: DatasetService):
        """update_dataset returns False for a dataset id that doesn't exist."""
        changed = await ds.update_dataset(99999, name="ghost")
        assert changed is False

    async def test_list_datasets_empty(self, ds: DatasetService):
        """list_datasets returns empty list when no datasets exist."""
        datasets = await ds.list_datasets()
        assert datasets == []

    async def test_list_datasets_with_members(self, ds: DatasetService, seed_traces):
        """list_datasets returns datasets with correct member counts."""
        dataset_id = await ds.create_dataset("populated-dataset")
        await ds.add_traces_to_dataset(dataset_id, seed_traces[:2])

        datasets = await ds.list_datasets()
        assert len(datasets) == 1
        assert datasets[0]["id"] == dataset_id
        assert datasets[0]["name"] == "populated-dataset"
        assert datasets[0]["member_count"] == 2

    async def test_list_datasets_multiple(self, ds: DatasetService):
        """list_datasets returns all datasets ordered by created_at DESC."""
        id1 = await ds.create_dataset("first-dataset")
        id2 = await ds.create_dataset("second-dataset")

        datasets = await ds.list_datasets()
        assert len(datasets) == 2
        # Most recent first
        assert datasets[0]["id"] == id2
        assert datasets[1]["id"] == id1

    async def test_add_traces_to_dataset(self, ds: DatasetService, seed_traces):
        """add_traces_to_dataset inserts membership rows and returns count."""
        dataset_id = await ds.create_dataset("trace-dataset")
        count = await ds.add_traces_to_dataset(dataset_id, seed_traces)
        assert count == 3

        # Verify membership rows exist
        db = await get_training_db(ds.repo_root)
        try:
            cursor = await db.execute(
                "SELECT COUNT(*) AS cnt FROM evaluation_dataset_members WHERE dataset_id = ?",
                (dataset_id,),
            )
            row = await cursor.fetchone()
            assert row["cnt"] == 3
        finally:
            await db.close()

    async def test_add_traces_idempotent(self, ds: DatasetService, seed_traces):
        """Adding the same trace UUIDs twice does not duplicate rows."""
        dataset_id = await ds.create_dataset("idempotent-dataset")
        await ds.add_traces_to_dataset(dataset_id, seed_traces)
        await ds.add_traces_to_dataset(dataset_id, seed_traces)

        db = await get_training_db(ds.repo_root)
        try:
            cursor = await db.execute(
                "SELECT COUNT(*) AS cnt FROM evaluation_dataset_members WHERE dataset_id = ?",
                (dataset_id,),
            )
            row = await cursor.fetchone()
            assert row["cnt"] == 3
        finally:
            await db.close()

    async def test_get_dataset_traces(self, ds: DatasetService, seed_traces):
        """get_dataset_traces returns trace metadata for dataset members."""
        dataset_id = await ds.create_dataset("query-dataset")
        await ds.add_traces_to_dataset(dataset_id, seed_traces)

        traces = await ds.get_dataset_traces(dataset_id)
        assert len(traces) == 3
        assert traces[0]["trace_uuid"] == "trace-uuid-001"
        assert traces[0]["session_id"] == "test-session"
        assert traces[0]["model_name"] == "gpt-4"

    async def test_get_dataset_traces_empty(self, ds: DatasetService):
        """get_dataset_traces returns empty list for dataset with no members."""
        dataset_id = await ds.create_dataset("empty-dataset")
        traces = await ds.get_dataset_traces(dataset_id)
        assert traces == []

    async def test_uuid_references_no_content_duplication(
        self, ds: DatasetService, seed_traces, eval_db
    ):
        """Dataset members store only trace_uuid references, not content."""
        dataset_id = await ds.create_dataset("ref-dataset")
        await ds.add_traces_to_dataset(dataset_id, seed_traces)

        # Check that evaluation_dataset_members only has UUID references
        db = await get_training_db(ds.repo_root)
        try:
            cursor = await db.execute(
                "SELECT trace_uuid FROM evaluation_dataset_members WHERE dataset_id = ?",
                (dataset_id,),
            )
            rows = await cursor.fetchall()
            stored_uuids = {row["trace_uuid"] for row in rows}
            assert stored_uuids == {"trace-uuid-001", "trace-uuid-002", "trace-uuid-003"}

            # Verify the member table does not contain message content
            cursor = await db.execute("PRAGMA table_info(evaluation_dataset_members)")
            columns = [r["name"] for r in await cursor.fetchall()]
            assert "messages" not in columns
            assert "assistant_output" not in columns
        finally:
            await db.close()


# ── TestEvaluationRunner ───────────────────────────────────────────


class TestEvaluationRunner:
    """Tests for EvaluationRunner evaluation execution and scoring."""

    def _make_criteria(self) -> dict:
        """Return a fixed criteria dict for tests."""
        return {
            "criteria": {
                "correctness": {
                    "label": "Correctness",
                    "checkbox": "Does the response correctly answer the question?",
                    "weight": 0.5,
                    "threshold": 0.7,
                },
                "clarity": {
                    "label": "Clarity",
                    "checkbox": "Is the response clearly written?",
                    "weight": 0.5,
                    "threshold": 0.6,
                },
            }
        }

    async def test_run_evaluation_creates_run_record(
        self, ds: DatasetService, seed_traces
    ):
        """run_evaluation creates an evaluation_run with status completed."""
        mock_client = AsyncMock()
        mock_client.chat_raw = AsyncMock(return_value="Correctness: 0.9, Clarity: 0.8")

        runner = EvaluationRunner(
            repo_root=ds.repo_root,
            llm_client=mock_client,
            load_criteria=lambda _: self._make_criteria(),
        )

        dataset_id = await ds.create_dataset("eval-dataset")
        await ds.add_traces_to_dataset(dataset_id, seed_traces)

        run_id = await runner.run_evaluation(dataset_id, prompt_version="v1")
        assert run_id >= 1

        # Verify run record
        db = await get_training_db(ds.repo_root)
        try:
            cursor = await db.execute(
                "SELECT dataset_id, prompt_version, status FROM evaluation_runs WHERE id = ?",
                (run_id,),
            )
            row = await cursor.fetchone()
            assert row is not None
            assert row["dataset_id"] == dataset_id
            assert row["prompt_version"] == "v1"
            assert row["status"] == "completed"
        finally:
            await db.close()

    async def test_run_evaluation_stores_results(
        self, ds: DatasetService, seed_traces
    ):
        """run_evaluation stores one evaluation_result per trace."""
        mock_client = AsyncMock()
        mock_client.chat_raw = AsyncMock(return_value="Correctness: 0.9, Clarity: 0.8")

        runner = EvaluationRunner(
            repo_root=ds.repo_root,
            llm_client=mock_client,
            load_criteria=lambda _: self._make_criteria(),
        )

        dataset_id = await ds.create_dataset("results-dataset")
        await ds.add_traces_to_dataset(dataset_id, seed_traces)

        run_id = await runner.run_evaluation(dataset_id)

        # Verify results were stored
        db = await get_training_db(ds.repo_root)
        try:
            cursor = await db.execute(
                "SELECT COUNT(*) AS cnt FROM evaluation_results WHERE run_id = ?",
                (run_id,),
            )
            row = await cursor.fetchone()
            assert row["cnt"] == 3
        finally:
            await db.close()

    async def test_run_evaluation_scores_calculated_correctly(
        self, ds: DatasetService, seed_traces
    ):
        """Scores are computed as weighted average of criterion scores."""
        mock_client = AsyncMock()
        # Correctness weight 0.5, score 0.9; Clarity weight 0.5, score 0.8
        # Expected: (0.9 * 0.5 + 0.8 * 0.5) / (0.5 + 0.5) = 0.85
        mock_client.chat_raw = AsyncMock(
            return_value="Correctness: 0.9\nClarity: 0.8\nReasoning: good response."
        )

        runner = EvaluationRunner(
            repo_root=ds.repo_root,
            llm_client=mock_client,
            load_criteria=lambda _: self._make_criteria(),
        )

        dataset_id = await ds.create_dataset("score-dataset")
        await ds.add_traces_to_dataset(dataset_id, seed_traces[:1])

        run_id = await runner.run_evaluation(dataset_id)

        db = await get_training_db(ds.repo_root)
        try:
            cursor = await db.execute(
                "SELECT score FROM evaluation_results WHERE run_id = ?",
                (run_id,),
            )
            row = await cursor.fetchone()
            assert row is not None
            assert abs(row["score"] - 0.85) < 0.001
        finally:
            await db.close()

    async def test_run_evaluation_stores_judge_reasoning(
        self, ds: DatasetService, seed_traces
    ):
        """Judge reasoning text is persisted in evaluation_results."""
        reasoning_text = "The response was accurate and well-structured."
        mock_client = AsyncMock()
        mock_client.chat_raw = AsyncMock(
            return_value=f"Correctness: 0.7\nClarity: 0.6\n{reasoning_text}"
        )

        runner = EvaluationRunner(
            repo_root=ds.repo_root,
            llm_client=mock_client,
            load_criteria=lambda _: self._make_criteria(),
        )

        dataset_id = await ds.create_dataset("reasoning-dataset")
        await ds.add_traces_to_dataset(dataset_id, seed_traces[:1])
        run_id = await runner.run_evaluation(dataset_id)

        db = await get_training_db(ds.repo_root)
        try:
            cursor = await db.execute(
                "SELECT judge_reasoning FROM evaluation_results WHERE run_id = ?",
                (run_id,),
            )
            row = await cursor.fetchone()
            assert row is not None
            assert reasoning_text in row["judge_reasoning"]
        finally:
            await db.close()

    async def test_run_evaluation_empty_dataset(
        self, ds: DatasetService
    ):
        """run_evaluation on empty dataset creates run with no results."""
        mock_client = AsyncMock()
        mock_client.chat_raw = AsyncMock(return_value="Correctness: 1.0")

        runner = EvaluationRunner(
            repo_root=ds.repo_root,
            llm_client=mock_client,
            load_criteria=lambda _: self._make_criteria(),
        )

        dataset_id = await ds.create_dataset("empty-eval-dataset")
        run_id = await runner.run_evaluation(dataset_id)

        db = await get_training_db(ds.repo_root)
        try:
            cursor = await db.execute(
                "SELECT status FROM evaluation_runs WHERE id = ?",
                (run_id,),
            )
            row = await cursor.fetchone()
            assert row["status"] == "completed"

            cursor = await db.execute(
                "SELECT COUNT(*) AS cnt FROM evaluation_results WHERE run_id = ?",
                (run_id,),
            )
            row = await cursor.fetchone()
            assert row["cnt"] == 0
        finally:
            await db.close()

    async def test_run_evaluation_llm_failure_stores_zero_score(
        self, ds: DatasetService, seed_traces
    ):
        """When LLM client raises, score defaults to 0.0 with failure reasoning."""
        mock_client = AsyncMock()
        mock_client.chat_raw = AsyncMock(side_effect=RuntimeError("LLM unavailable"))

        runner = EvaluationRunner(
            repo_root=ds.repo_root,
            llm_client=mock_client,
            load_criteria=lambda _: self._make_criteria(),
        )

        dataset_id = await ds.create_dataset("failure-dataset")
        await ds.add_traces_to_dataset(dataset_id, seed_traces[:1])
        run_id = await runner.run_evaluation(dataset_id)

        db = await get_training_db(ds.repo_root)
        try:
            cursor = await db.execute(
                "SELECT score, judge_reasoning FROM evaluation_results WHERE run_id = ?",
                (run_id,),
            )
            row = await cursor.fetchone()
            assert row is not None
            assert row["score"] == 0.0
            assert "Evaluation failed" in row["judge_reasoning"]
        finally:
            await db.close()

    async def test_run_evaluation_frozen_context_from_traces(
        self, ds: DatasetService, seed_traces
    ):
        """Evaluation uses frozen context from training_traces, not live data."""
        captured_prompts = []

        async def capture_prompt(messages, **kwargs):
            captured_prompts.append(messages)
            return "Correctness: 0.5\nClarity: 0.5"

        mock_client = AsyncMock()
        mock_client.chat_raw = AsyncMock(side_effect=capture_prompt)

        runner = EvaluationRunner(
            repo_root=ds.repo_root,
            llm_client=mock_client,
            load_criteria=lambda _: self._make_criteria(),
        )

        dataset_id = await ds.create_dataset("context-dataset")
        await ds.add_traces_to_dataset(dataset_id, seed_traces[:2])
        await runner.run_evaluation(dataset_id)

        # Verify the judge received evaluation prompts with user/assistant content
        assert len(captured_prompts) == 2
        for prompt_msgs in captured_prompts:
            assert len(prompt_msgs) == 1
            assert prompt_msgs[0]["role"] == "user"
            content = prompt_msgs[0]["content"]
            assert "User request:" in content
            assert "Assistant response:" in content
            assert "Criteria to evaluate:" in content

    async def test_extract_score_weighted_average(self):
        """_extract_score computes correct weighted average from response text."""
        criteria = {
            "correctness": {"label": "Correctness", "weight": 0.6, "checkbox": ""},
            "clarity": {"label": "Clarity", "weight": 0.4, "checkbox": ""},
        }
        response = "Correctness: 0.8\nClarity: 0.6\nSome reasoning here."
        score = EvaluationRunner._extract_score(response, criteria)
        # (0.8 * 0.6 + 0.6 * 0.4) / (0.6 + 0.4) = 0.72
        assert abs(score - 0.72) < 0.001

    async def test_extract_score_clamps_values(self):
        """_extract_score clamps scores to [0, 1] range."""
        criteria = {
            "correctness": {"label": "Correctness", "weight": 1.0, "checkbox": ""},
        }
        response = "Correctness: 1.5"
        score = EvaluationRunner._extract_score(response, criteria)
        assert score == 1.0

        response = "Correctness: -0.3"
        score = EvaluationRunner._extract_score(response, criteria)
        assert score == 0.0

    async def test_extract_score_empty_criteria(self):
        """_extract_score returns 0.0 when criteria is empty."""
        score = EvaluationRunner._extract_score("some response", {})
        assert score == 0.0

    async def test_extract_score_missing_labels(self):
        """_extract_score returns 0.0 when response has no matching labels."""
        criteria = {
            "correctness": {"label": "Correctness", "weight": 1.0, "checkbox": ""},
        }
        response = "No criterion labels here at all."
        score = EvaluationRunner._extract_score(response, criteria)
        assert score == 0.0

    async def test_run_evaluation_trace_uuid_references(
        self, ds: DatasetService, seed_traces
    ):
        """evaluation_results stores trace_uuid references, not duplicated content."""
        mock_client = AsyncMock()
        mock_client.chat_raw = AsyncMock(return_value="Correctness: 0.7")

        runner = EvaluationRunner(
            repo_root=ds.repo_root,
            llm_client=mock_client,
            load_criteria=lambda _: self._make_criteria(),
        )

        dataset_id = await ds.create_dataset("uuid-ref-dataset")
        await ds.add_traces_to_dataset(dataset_id, seed_traces)
        run_id = await runner.run_evaluation(dataset_id)

        db = await get_training_db(ds.repo_root)
        try:
            cursor = await db.execute(
                "SELECT trace_uuid FROM evaluation_results WHERE run_id = ?",
                (run_id,),
            )
            rows = await cursor.fetchall()
            stored_uuids = {row["trace_uuid"] for row in rows}
            assert stored_uuids == {"trace-uuid-001", "trace-uuid-002", "trace-uuid-003"}

            # Verify evaluation_results table schema has no content columns
            cursor = await db.execute("PRAGMA table_info(evaluation_results)")
            columns = [r["name"] for r in await cursor.fetchall()]
            assert "messages" not in columns
            assert "assistant_output" not in columns
        finally:
            await db.close()
