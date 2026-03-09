"""Tests for universal search rate limiting in internet.py."""

import time
from unittest.mock import AsyncMock, patch

import pytest


class TestEnforceSearchDelay:
    @pytest.fixture(autouse=True)
    def _reset_state(self):
        """Reset module-level rate-limit state between tests."""
        import lean_ai.tools.internet as mod

        mod._last_search_time = 0.0

    @pytest.mark.asyncio
    async def test_first_call_no_delay(self):
        """First search ever should not sleep — enough time has 'elapsed'."""
        from lean_ai.tools.internet import _enforce_search_delay

        with patch(
            "lean_ai.tools.internet.asyncio.sleep",
            new_callable=AsyncMock,
        ) as mock_sleep:
            await _enforce_search_delay()
            mock_sleep.assert_not_called()

    @pytest.mark.asyncio
    async def test_rapid_second_call_delays(self):
        """Back-to-back call should trigger an async sleep."""
        import lean_ai.tools.internet as mod
        from lean_ai.tools.internet import _enforce_search_delay

        mod._last_search_time = time.monotonic()

        with patch(
            "lean_ai.tools.internet.asyncio.sleep",
            new_callable=AsyncMock,
        ) as mock_sleep:
            await _enforce_search_delay()
            mock_sleep.assert_called_once()
            delay = mock_sleep.call_args[0][0]
            assert delay > 0

    @pytest.mark.asyncio
    async def test_zero_delay_config_skips(self):
        """search_delay=0 disables rate limiting entirely."""
        import lean_ai.tools.internet as mod
        from lean_ai.tools.internet import _enforce_search_delay

        mod._last_search_time = time.monotonic()

        with (
            patch.object(mod.settings, "search_delay", 0),
            patch(
                "lean_ai.tools.internet.asyncio.sleep",
                new_callable=AsyncMock,
            ) as mock_sleep,
        ):
            await _enforce_search_delay()
            mock_sleep.assert_not_called()
