"""Integration tests for evaluation framework API endpoints and database persistence.

Verifies the complete flow from API endpoint calls through DatasetService and
EvaluationRunner to database persistence in evaluation_datasets,
evaluation_dataset_members, evaluation_runs, and evaluation_results tables.

Covers:
  1. TestCreateDataset: POST /eval/datasets creates dataset in DB
  2. TestListDatasets: GET /eval/datasets returns dataset list
  3. TestRunEvaluation: POST /eval/run triggers evaluation and stores results
  4. TestGetResults: GET /eval/results/{run_id} returns detailed quality report
  5. Tests verify valid result records in evaluation_runs and evaluation_results
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from lean_ai.routers.sessions import (
    create_or_update_dataset,
    get_evaluation_results,
    list_datasets,
    trigger_evaluation,
)
from lean_ai.routers.sessions import (
    CreateDatasetRequest,
    CreateEvalRunRequest,
)
from lean_ai.training.capture import DatasetService, EvaluationRunner
from lean_ai.training.db import (
    get_training_db,
    insert_training_trace,
)


def _make_criteria(repo_root=None) -> dict:
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


def _build_mock_runner_factory(mock_client, criteria_fn=_make_criteria):
    """Return a factory that produces an EvaluationRunner with mocked deps.

    The factory is used to patch EvaluationRunner in the sessions module so
    that trigger_evaluation() gets a runner with a controlled LLM client and
    fixed criteria, while still writing real rows to the database.
    """

    def factory(repo_root):
        runner = object.__new__(EvaluationRunner)
        runner.repo_root = repo_root
        runner.llm_client = mock_client
        runner._load_criteria = criteria_fn
        return runner

    return factory


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
async def eval_db(tmp_path):
    """Training DB connection scoped to a temp directory."""
    db = await get_training_db(str(tmp_path))
    yield db
    await db.close()


@pytest.fixture
async def seed_traces(eval_db):
    """Insert training traces and return their UUIDs."""
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


# ── TestCreateDataset ───────────────────────────────────────────────────────


class TestCreateDataset:
    """Tests for POST /eval/datasets endpoint creating datasets in DB."""

    async def test_create_dataset_endpoint_creates_db_record(self, tmp_path):
        """POST /eval/datasets creates a dataset row in evaluation_datasets."""
        request = CreateDatasetRequest(
            name="integration-test-dataset",
            repo_root=str(tmp_path),
            version="1",
            description="Created via API endpoint",
        )
        response = await create_or_update_dataset(request)

        assert response.dataset_id >= 1
        assert response.name == "integration-test-dataset"
        assert response.version == "1"
        assert response.description == "Created via API endpoint"

        # Verify the row exists in the database
        db = await get_training_db(str(tmp_path))
        try:
            cursor = await db.execute(
                "SELECT name, version, description FROM evaluation_datasets WHERE id = ?",
                (response.dataset_id,),
            )
            row = await cursor.fetchone()
            assert row is not None
            assert row["name"] == "integration-test-dataset"
            assert row["version"] == "1"
            assert row["description"] == "Created via API endpoint"
        finally:
            await db.close()

    async def test_create_dataset_default_version(self, tmp_path):
        """POST /eval/datasets defaults version to '1' when not specified."""
        request = CreateDatasetRequest(
            name="minimal-dataset",
            repo_root=str(tmp_path),
        )
        response = await create_or_update_dataset(request)

        assert response.version == "1"

        db = await get_training_db(str(tmp_path))
        try:
            cursor = await db.execute(
                "SELECT version FROM evaluation_datasets WHERE id = ?",
                (response.dataset_id,),
            )
            row = await cursor.fetchone()
            assert row["version"] == "1"
        finally:
            await db.close()

    async def test_update_existing_dataset(self, tmp_path):
        """POST /eval/datasets with dataset_id updates an existing dataset."""
        # First create a dataset
        create_req = CreateDatasetRequest(
            name="original-name",
            repo_root=str(tmp_path),
            version="1",
        )
        create_response = await create_or_update_dataset(create_req)

        # Now update it
        update_req = CreateDatasetRequest(
            name="updated-name",
            repo_root=str(tmp_path),
            version="2",
            description="Updated description",
            dataset_id=create_response.dataset_id,
        )
        update_response = await create_or_update_dataset(update_req)

        assert update_response.dataset_id == create_response.dataset_id
        assert update_response.name == "updated-name"
        assert update_response.version == "2"

        # Verify in DB
        db = await get_training_db(str(tmp_path))
        try:
            cursor = await db.execute(
                "SELECT name, version, description FROM evaluation_datasets WHERE id = ?",
                (create_response.dataset_id,),
            )
            row = await cursor.fetchone()
            assert row["name"] == "updated-name"
            assert row["version"] == "2"
            assert row["description"] == "Updated description"
        finally:
            await db.close()

    async def test_update_nonexistent_dataset_raises_404(self, tmp_path):
        """POST /eval/datasets with nonexistent dataset_id returns 404."""
        from fastapi import HTTPException

        request = CreateDatasetRequest(
            name="ghost",
            repo_root=str(tmp_path),
            dataset_id=99999,
        )
        with pytest.raises(HTTPException) as exc_info:
            await create_or_update_dataset(request)
        assert exc_info.value.status_code == 404


# ── TestListDatasets ────────────────────────────────────────────────────────


class TestListDatasets:
    """Tests for GET /eval/datasets endpoint returning dataset lists."""

    async def test_list_datasets_empty(self, tmp_path):
        """GET /eval/datasets returns empty list when no datasets exist."""
        datasets = await list_datasets(str(tmp_path))
        assert datasets == []

    async def test_list_datasets_returns_created_datasets(self, tmp_path):
        """GET /eval/datasets returns datasets created via the API."""
        # Create two datasets
        await create_or_update_dataset(
            CreateDatasetRequest(name="first", repo_root=str(tmp_path))
        )
        await create_or_update_dataset(
            CreateDatasetRequest(name="second", repo_root=str(tmp_path))
        )

        datasets = await list_datasets(str(tmp_path))
        assert len(datasets) == 2
        # Most recent first
        assert datasets[0]["name"] == "second"
        assert datasets[1]["name"] == "first"

    async def test_list_datasets_includes_member_count(self, tmp_path, seed_traces):
        """GET /eval/datasets returns correct member_count for datasets."""
        ds = DatasetService(repo_root=str(tmp_path))
        dataset_id = await ds.create_dataset("with-members")
        await ds.add_traces_to_dataset(dataset_id, seed_traces[:2])

        datasets = await list_datasets(str(tmp_path))
        assert len(datasets) == 1
        assert datasets[0]["member_count"] == 2


# ── TestRunEvaluation ───────────────────────────────────────────────────────


class TestRunEvaluation:
    """Tests for POST /eval/run endpoint triggering evaluation and storing results."""

    async def test_trigger_evaluation_creates_run_and_results(
        self, tmp_path, seed_traces
    ):
        """POST /eval/run creates evaluation_run and evaluation_results records."""
        # Create dataset and add traces
        ds = DatasetService(repo_root=str(tmp_path))
        dataset_id = await ds.create_dataset("eval-integration-dataset")
        await ds.add_traces_to_dataset(dataset_id, seed_traces)

        # Mock the LLM client used by EvaluationRunner
        mock_client = AsyncMock()
        mock_client.chat_raw = AsyncMock(
            return_value="Correctness: 0.9\nClarity: 0.8\nGood response overall."
        )

        with patch(
            "lean_ai.routers.sessions.EvaluationRunner",
            side_effect=_build_mock_runner_factory(mock_client),
        ):
            request = CreateEvalRunRequest(
                dataset_id=dataset_id,
                repo_root=str(tmp_path),
                prompt_version="v1",
            )
            response = await trigger_evaluation(request)

        assert response.run_id >= 1
        assert response.dataset_id == dataset_id
        assert response.status == "completed"

        # Verify evaluation_runs record
        db = await get_training_db(str(tmp_path))
        try:
            cursor = await db.execute(
                "SELECT dataset_id, prompt_version, status FROM evaluation_runs WHERE id = ?",
                (response.run_id,),
            )
            row = await cursor.fetchone()
            assert row is not None
            assert row["dataset_id"] == dataset_id
            assert row["prompt_version"] == "v1"
            assert row["status"] == "completed"

            # Verify evaluation_results records (one per trace)
            res_cursor = await db.execute(
                "SELECT COUNT(*) AS cnt FROM evaluation_results WHERE run_id = ?",
                (response.run_id,),
            )
            res_row = await res_cursor.fetchone()
            assert res_row["cnt"] == 3
        finally:
            await db.close()

    async def test_trigger_evaluation_stores_scores_and_reasoning(
        self, tmp_path, seed_traces
    ):
        """POST /eval/run stores score values and judge reasoning in results."""
        ds = DatasetService(repo_root=str(tmp_path))
        dataset_id = await ds.create_dataset("score-integration-dataset")
        await ds.add_traces_to_dataset(dataset_id, seed_traces[:1])

        mock_client = AsyncMock()
        mock_client.chat_raw = AsyncMock(
            return_value="Correctness: 0.9\nClarity: 0.8\nReasoning: well done."
        )

        with patch(
            "lean_ai.routers.sessions.EvaluationRunner",
            side_effect=_build_mock_runner_factory(mock_client),
        ):
            request = CreateEvalRunRequest(
                dataset_id=dataset_id,
                repo_root=str(tmp_path),
            )
            response = await trigger_evaluation(request)

        # Verify score and reasoning in evaluation_results
        db = await get_training_db(str(tmp_path))
        try:
            cursor = await db.execute(
                "SELECT score, judge_reasoning FROM evaluation_results WHERE run_id = ?",
                (response.run_id,),
            )
            row = await cursor.fetchone()
            assert row is not None
            # Score should be weighted average: (0.9*0.5 + 0.8*0.5) / (0.5+0.5) = 0.85
            assert abs(row["score"] - 0.85) < 0.001
            assert "well done" in row["judge_reasoning"]
        finally:
            await db.close()

    async def test_trigger_evaluation_empty_dataset(self, tmp_path):
        """POST /eval/run on empty dataset creates run with no results."""
        ds = DatasetService(repo_root=str(tmp_path))
        dataset_id = await ds.create_dataset("empty-integration-dataset")

        mock_client = AsyncMock()
        mock_client.chat_raw = AsyncMock(return_value="Correctness: 1.0")

        with patch(
            "lean_ai.routers.sessions.EvaluationRunner",
            side_effect=_build_mock_runner_factory(mock_client),
        ):
            request = CreateEvalRunRequest(
                dataset_id=dataset_id,
                repo_root=str(tmp_path),
            )
            response = await trigger_evaluation(request)

        assert response.status == "completed"

        db = await get_training_db(str(tmp_path))
        try:
            cursor = await db.execute(
                "SELECT status FROM evaluation_runs WHERE id = ?",
                (response.run_id,),
            )
            row = await cursor.fetchone()
            assert row["status"] == "completed"

            res_cursor = await db.execute(
                "SELECT COUNT(*) AS cnt FROM evaluation_results WHERE run_id = ?",
                (response.run_id,),
            )
            res_row = await res_cursor.fetchone()
            assert res_row["cnt"] == 0
        finally:
            await db.close()


# ── TestGetResults ──────────────────────────────────────────────────────────


class TestGetResults:
    """Tests for GET /eval/results/{run_id} returning detailed quality reports."""

    async def test_get_results_returns_run_and_results(self, tmp_path, seed_traces):
        """GET /eval/results/{run_id} returns run metadata and all result items."""
        # Create dataset, add traces, run evaluation
        ds = DatasetService(repo_root=str(tmp_path))
        dataset_id = await ds.create_dataset("results-integration-dataset")
        await ds.add_traces_to_dataset(dataset_id, seed_traces)

        mock_client = AsyncMock()
        mock_client.chat_raw = AsyncMock(
            return_value="Correctness: 0.9\nClarity: 0.8\nGood."
        )

        with patch(
            "lean_ai.routers.sessions.EvaluationRunner",
            side_effect=_build_mock_runner_factory(mock_client),
        ):
            request = CreateEvalRunRequest(
                dataset_id=dataset_id,
                repo_root=str(tmp_path),
                prompt_version="v1",
            )
            run_response = await trigger_evaluation(request)

        # Now fetch results via the endpoint
        results_response = await get_evaluation_results(
            run_id=run_response.run_id,
            repo_root=str(tmp_path),
        )

        assert results_response.run_id == run_response.run_id
        assert results_response.dataset_id == dataset_id
        assert results_response.status == "completed"
        assert len(results_response.results) == 3

        # Verify each result has the expected fields
        for result in results_response.results:
            assert result.run_id == run_response.run_id
            assert result.trace_uuid in seed_traces
            assert isinstance(result.score, float)
            assert isinstance(result.judge_reasoning, str)

    async def test_get_results_nonexistent_run_raises_404(self, tmp_path):
        """GET /eval/results/{run_id} returns 404 for nonexistent run."""
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await get_evaluation_results(run_id=99999, repo_root=str(tmp_path))
        assert exc_info.value.status_code == 404

    async def test_get_results_includes_created_at(self, tmp_path, seed_traces):
        """GET /eval/results/{run_id} includes created_at timestamps."""
        ds = DatasetService(repo_root=str(tmp_path))
        dataset_id = await ds.create_dataset("timestamp-dataset")
        await ds.add_traces_to_dataset(dataset_id, seed_traces[:1])

        mock_client = AsyncMock()
        mock_client.chat_raw = AsyncMock(
            return_value="Correctness: 0.7\nClarity: 0.6\nOk."
        )

        with patch(
            "lean_ai.routers.sessions.EvaluationRunner",
            side_effect=_build_mock_runner_factory(mock_client),
        ):
            request = CreateEvalRunRequest(
                dataset_id=dataset_id,
                repo_root=str(tmp_path),
            )
            run_response = await trigger_evaluation(request)

        results_response = await get_evaluation_results(
            run_id=run_response.run_id,
            repo_root=str(tmp_path),
        )

        assert len(results_response.results) == 1
        assert results_response.results[0].created_at is not None
