"""Regression tests for prompt formatting and validation."""

from lean_ai.llm.prompt_registry import registry


def _phase4_prompt_kwargs() -> dict[str, str]:
    """Dummy Phase 4 substitutions that exercise prompt rendering."""
    return {
        "task": "Implement the feature.",
        "design_and_risks": "Design output.",
        "file_summary": "File summary.",
        "project_context": "",
        "scope": "Scope details.",
        "missing_files": "",
        "test_command": "pytest -q",
        "testing_inventory": "Testing inventory.",
        "verification_targets": "src/app.py",
        "security_concerns": "None.",
        "core_functionality": "Core paths.",
    }


def _broken_phase4_prompt() -> str:
    """Return a Phase 4 prompt with one literal JSON object unescaped."""
    text = registry.get("planning.assembly_user")
    return text.replace(
        '{{"source": "src/config/handlers.ext"',
        '{"source": "src/config/handlers.ext"',
        1,
    )


def test_phase4_assembly_prompt_formats_and_preserves_json_example() -> None:
    rendered = registry.format("planning.assembly_user", **_phase4_prompt_kwargs())

    assert (
        '{"source": "src/config/handlers.ext", "details": '
        '"Existing registry around line 34 and import block around line 8."}'
        in rendered
    )
    assert '"command": "pytest -q"' in rendered
    assert '{{"source": "src/config/handlers.ext"' not in rendered


def test_all_templated_prompts_format_with_dummy_values() -> None:
    failures: list[str] = []

    for prompt in registry.get_all():
        key = prompt["key"]
        template_vars = prompt["template_vars"]
        if not template_vars:
            continue
        kwargs = {var: f"<{var}>" for var in template_vars}
        try:
            registry.format(key, **kwargs)
        except Exception as exc:  # pragma: no cover - exercised on failure only
            failures.append(f"{key}: {type(exc).__name__}: {exc}")

    assert failures == []


def test_validate_reports_invalid_format_syntax() -> None:
    errors = registry.validate("planning.assembly_user", _broken_phase4_prompt())

    assert any(error.startswith("Invalid format syntax:") for error in errors)
