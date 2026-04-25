"""Tests for LLM claim verification detection."""

from lean_ai.llm.facade import _detect_unverified_claims


class TestPositiveDetection:
    """Claims about external things that should trigger verification."""

    def test_doesnt_exist(self):
        assert _detect_unverified_claims(
            "The foobar library doesn't exist in the Python ecosystem."
        )

    def test_does_not_exist(self):
        assert _detect_unverified_claims(
            "This API endpoint does not exist in the current version of Flask."
        )

    def test_is_not_available(self):
        assert _detect_unverified_claims(
            "The streaming option is not available in the requests library."
        )

    def test_isnt_supported(self):
        assert _detect_unverified_claims(
            "Async mode isn't supported by this version of SQLAlchemy."
        )

    def test_not_yet_available(self):
        assert _detect_unverified_claims("The v3 API is not yet available for production use.")

    def test_no_longer_supported(self):
        assert _detect_unverified_claims("Python 2 is no longer supported by the Django framework.")

    def test_future_version(self):
        assert _detect_unverified_claims(
            "This feature is planned for a future version of the library."
        )

    def test_upcoming_release(self):
        assert _detect_unverified_claims(
            "The new router API is part of an upcoming release of Next.js."
        )

    def test_has_been_deprecated(self):
        assert _detect_unverified_claims(
            "The old createStore function has been deprecated in Redux."
        )

    def test_will_be_removed(self):
        assert _detect_unverified_claims("This method will be removed in the next major release.")

    def test_knowledge_cutoff(self):
        assert _detect_unverified_claims(
            "As of my knowledge cutoff, this feature was not available."
        )

    def test_training_data(self):
        assert _detect_unverified_claims("My training data doesn't cover releases after 2024.")

    def test_assume_library(self):
        assert _detect_unverified_claims("I assume this library does not support async operations.")

    def test_believe_package(self):
        assert _detect_unverified_claims("I believe that package was discontinued last year.")

    def test_not_a_valid_function(self):
        assert _detect_unverified_claims("That is not a valid function in the numpy API.")

    def test_does_not_provide(self):
        assert _detect_unverified_claims(
            "The pandas library does not provide a built-in caching mechanism."
        )

    def test_not_implemented(self):
        assert _detect_unverified_claims(
            "WebSocket support is not implemented in this framework yet."
        )

    def test_only_in_beta(self):
        assert _detect_unverified_claims("This is only available in the beta preview of the SDK.")


class TestProjectContext:
    """Claims about project files that should NOT trigger verification."""

    def test_create_file_doesnt_exist(self):
        assert not _detect_unverified_claims(
            "This file doesn't exist yet, let me create it with create_file."
        )

    def test_writing_new_test(self):
        assert not _detect_unverified_claims(
            "We need to create this test file since it doesn't exist yet."
        )

    def test_creating_module(self):
        assert not _detect_unverified_claims(
            "I will create a new file for this module — it doesn't exist yet."
        )

    def test_implement_new_function(self):
        assert not _detect_unverified_claims(
            "We should implement this function since it doesn't exist in the codebase."
        )

    def test_adding_test(self):
        assert not _detect_unverified_claims(
            "Adding a new test — this test file doesn't exist yet."
        )


class TestNegativeDetection:
    """Normal text that should NOT trigger verification."""

    def test_normal_code_discussion(self):
        assert not _detect_unverified_claims(
            "The function takes a list and returns the sorted result."
        )

    def test_tool_result(self):
        assert not _detect_unverified_claims("File created successfully at src/utils/helpers.py")

    def test_empty_string(self):
        assert not _detect_unverified_claims("")

    def test_short_string(self):
        assert not _detect_unverified_claims("OK")

    def test_none(self):
        assert not _detect_unverified_claims(None)

    def test_code_snippet(self):
        assert not _detect_unverified_claims(
            "def process_data(items):\n    return [x * 2 for x in items]"
        )

    def test_plan_description(self):
        assert not _detect_unverified_claims(
            "Step 1: Read the configuration file. Step 2: Update the settings."
        )
