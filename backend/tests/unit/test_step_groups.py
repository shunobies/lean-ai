"""Tests for _build_step_groups dependency detection."""

from lean_ai.llm.plan_schema import PlanStep
from lean_ai.workflow.executor import _build_step_groups, _path_mentioned_in


def _step(
    num: int, tool: str = "edit_file", file_path: str = "", instruction: str = ""
) -> PlanStep:
    return PlanStep(
        step_number=num,
        tool=tool,
        file_path=file_path,
        instruction=instruction,
        context="",
    )


class TestPathMentionedIn:
    def test_exact_match(self):
        assert _path_mentioned_in("src/config.py", "edit src/config.py here")

    def test_no_false_positive_on_substring(self):
        """'a.py' should NOT match 'baa.py'."""
        assert not _path_mentioned_in("a.py", "open baa.py")

    def test_path_at_start_of_string(self):
        assert _path_mentioned_in("src/main.py", "src/main.py is the entry")

    def test_path_at_end_of_string(self):
        assert _path_mentioned_in("src/main.py", "modify src/main.py")

    def test_path_in_backticks(self):
        assert _path_mentioned_in("src/utils.py", "see `src/utils.py`")

    def test_path_in_quotes(self):
        assert _path_mentioned_in("src/utils.py", 'open "src/utils.py"')

    def test_no_match_in_longer_path(self):
        """'config.py' should NOT match 'src/config.py.bak' via substring."""
        assert not _path_mentioned_in("config.py", "check config.py.bak")

    def test_normalized_path(self):
        assert _path_mentioned_in("./src/a.py", "update src/a.py now")

    def test_empty_inputs(self):
        assert not _path_mentioned_in("", "some text")
        assert not _path_mentioned_in("file.py", "")


class TestBuildStepGroups:
    def test_empty(self):
        assert _build_step_groups([]) == []

    def test_independent_files_parallel(self):
        """Steps on different files with no cross-references -> one group."""
        steps = [
            _step(1, file_path="a.py", instruction="create a"),
            _step(2, file_path="b.py", instruction="create b"),
        ]
        groups = _build_step_groups(steps)
        assert len(groups) == 1
        assert len(groups[0]) == 2

    def test_same_file_sequential(self):
        """Two steps on the same file -> two groups."""
        steps = [
            _step(1, file_path="a.py", instruction="first edit"),
            _step(2, file_path="a.py", instruction="second edit"),
        ]
        groups = _build_step_groups(steps)
        assert len(groups) == 2

    def test_cross_file_reference_creates_dependency(self):
        """Step B mentioning step A's file_path -> sequential."""
        steps = [
            _step(1, file_path="src/models.py", instruction="add model"),
            _step(2, file_path="src/views.py", instruction="import from src/models.py"),
        ]
        groups = _build_step_groups(steps)
        assert len(groups) == 2

    def test_context_reference_creates_dependency(self):
        """Planner-supplied context mentioning another file must serialize the steps."""
        steps = [
            PlanStep(
                step_number=1,
                tool="edit_file",
                file_path="src/models.py",
                instruction="add model",
                reason="",
                context="",
            ),
            PlanStep(
                step_number=2,
                tool="edit_file",
                file_path="src/views.py",
                instruction="wire view",
                reason="",
                context="Follow the serializer pattern from src/models.py",
            ),
        ]
        groups = _build_step_groups(steps)
        assert len(groups) == 2

    def test_no_false_positive_from_substring(self):
        """Step A edits 'a.py', Step B mentions 'baa.py' -> parallel."""
        steps = [
            _step(1, file_path="a.py", instruction="edit a"),
            _step(2, file_path="b.py", instruction="reference baa.py here"),
        ]
        groups = _build_step_groups(steps)
        assert len(groups) == 1

    def test_barrier_tool_separates_groups(self):
        """run_tests acts as a barrier between groups."""
        steps = [
            _step(1, file_path="a.py", instruction="edit a"),
            _step(2, tool="run_tests", instruction="pytest"),
            _step(3, file_path="b.py", instruction="edit b"),
        ]
        groups = _build_step_groups(steps)
        assert len(groups) == 3

    def test_normalized_paths_match(self):
        """./src/a.py and src/a.py should be treated as the same file."""
        steps = [
            _step(1, file_path="./src/a.py", instruction="first edit"),
            _step(2, file_path="src/a.py", instruction="second edit"),
        ]
        groups = _build_step_groups(steps)
        assert len(groups) == 2
