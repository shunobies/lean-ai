"""Self-improvement training data pipeline.

Layer 1 of the curated-memory + raw-archive split described in
`docs/training.md`. Everything in this package is append-only capture
of LLM traces and workflow events for future SFT/DPO/KTO export.

Modules:

- :mod:`lean_ai.training.db` — SQLite schema for `.lean_ai/training.db`
  (training_traces, plan_decisions, validation_attempts, workflow_events,
  redaction_audit).
- :mod:`lean_ai.training.scrubber` — PII/secrets scrubber that runs
  before any trace enters the archive.
- :mod:`lean_ai.training.capture` — high-level capture functions called
  from workflow hooks.
- :mod:`lean_ai.training.export_formats` — raw/SFT/DPO/KTO JSONL
  converters used by the export API.
- :mod:`lean_ai.training.memory_anonymizer` — workspace-identifier
  stripping for cross-workspace memory export.
"""
