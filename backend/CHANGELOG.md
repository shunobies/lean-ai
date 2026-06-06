# Changelog

All notable changes to the Lean AI backend will be documented in this file.

## [Unreleased]

### Added
- **Semantic Diff Review Gate** — added a verification layer that evaluates planned code changes for semantic drift before execution. Uses rubric-based evaluation grounded on raw diffs with narrative summary generation, and an automatic corrective loop capped at 2 iterations to fix detected issues without user intervention.
