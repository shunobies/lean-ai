"""Tests for mechanical section reorganization."""

from lean_ai.context.dedup import reorganize_sections


class TestReorganizeSections:
    def test_merges_duplicate_headings(self):
        text = (
            "## Architecture\n"
            "MVC pattern with controllers.\n\n"
            "## Data Flow\n"
            "Request flows through middleware.\n\n"
            "## Architecture\n"
            "The system uses MVC architecture."
        )
        result = reorganize_sections(text)
        # Only one Architecture heading
        assert result.count("## Architecture") == 1
        assert "MVC pattern with controllers." in result
        assert "The system uses MVC architecture." in result
        assert "## Data Flow" in result

    def test_drops_exact_duplicate_lines(self):
        text = (
            "## Architecture\n"
            "MVC pattern.\n"
            "Uses controllers.\n\n"
            "## Architecture\n"
            "MVC pattern.\n"
            "Has views too."
        )
        result = reorganize_sections(text)
        # "MVC pattern." appears only once
        assert result.count("MVC pattern.") == 1
        assert "Uses controllers." in result
        assert "Has views too." in result

    def test_preserves_preamble(self):
        text = "# Project Context\nSome intro text.\n\n## Section A\nContent A"
        result = reorganize_sections(text)
        assert result.startswith("# Project Context\n")
        assert "Some intro text." in result
        assert "## Section A" in result

    def test_preserves_first_occurrence_order(self):
        text = "## Bravo\nB content\n\n## Alpha\nA content\n\n## Bravo\nMore B content"
        result = reorganize_sections(text)
        bravo_pos = result.index("## Bravo")
        alpha_pos = result.index("## Alpha")
        assert bravo_pos < alpha_pos

    def test_single_section_unchanged(self):
        text = "## Only Section\nContent here."
        result = reorganize_sections(text)
        assert "## Only Section" in result
        assert "Content here." in result

    def test_no_headings_unchanged(self):
        text = "Just some text\nwith no headings."
        result = reorganize_sections(text)
        assert result == text

    def test_collapses_blank_lines(self):
        text = "## Keep\nContent\n\n\n\n## Also Keep\nMore content"
        result = reorganize_sections(text)
        assert "\n\n\n\n" not in result
        assert "## Keep" in result
        assert "## Also Keep" in result

    def test_custom_log_prefix(self):
        """Ensure log_prefix parameter is accepted without error."""
        text = "## A\nContent A\n\n## B\nContent B"
        result = reorganize_sections(text, log_prefix="Project context")
        assert result == text

    def test_three_occurrences_merged(self):
        text = "## X\nFirst.\n\n## Y\nMiddle.\n\n## X\nSecond.\n\n## X\nThird."
        result = reorganize_sections(text)
        assert result.count("## X") == 1
        assert "First." in result
        assert "Second." in result
        assert "Third." in result
        assert "## Y" in result
