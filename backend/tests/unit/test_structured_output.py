"""Tests for shared structured-output validation helpers."""

import pytest
from pydantic import BaseModel

from lean_ai.llm.base import (
    StructuredOutputError,
    strip_json_code_fences,
    validate_structured_output,
)


class SampleModel(BaseModel):
    name: str
    count: int


def test_strip_json_code_fences_removes_wrappers():
    raw = '```json\n{"name":"widget","count":2}\n```'

    assert strip_json_code_fences(raw) == '{"name":"widget","count":2}'


def test_validate_structured_output_accepts_unfenced_json():
    model, cleaned = validate_structured_output(
        '{"name":"widget","count":2}',
        SampleModel,
    )

    assert cleaned == '{"name":"widget","count":2}'
    assert model == SampleModel(name="widget", count=2)


def test_validate_structured_output_reports_json_syntax_line_and_column():
    with pytest.raises(StructuredOutputError) as excinfo:
        validate_structured_output(
            '```json\n{"name":"widget","count": }\n```',
            SampleModel,
        )

    err = excinfo.value
    assert err.cleaned_output == '{"name":"widget","count": }'
    assert err.is_json_syntax_error
    assert err.exact_json_error is not None
    assert "line 1 column" in err.exact_json_error
    assert err.summary == err.exact_json_error


def test_validate_structured_output_reports_compact_schema_summary():
    with pytest.raises(StructuredOutputError) as excinfo:
        validate_structured_output(
            '{"name":"widget","count":"many"}',
            SampleModel,
        )

    err = excinfo.value
    assert not err.is_json_syntax_error
    assert err.summary.startswith("Schema validation failed:")
    assert "count:" in err.summary
    assert "valid integer" in err.summary
