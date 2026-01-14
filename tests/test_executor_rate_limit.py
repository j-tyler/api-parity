"""Tests for Executor rate limiting behavior.

Tests cover:
- Sleep is called when requests are too fast
- Sleep duration is calculated correctly
- Slow requests don't cause unnecessary waits
- No rate limiting when disabled
"""

from unittest.mock import MagicMock, patch

import pytest

from api_parity.executor import Executor
from api_parity.models import TargetConfig


@pytest.fixture
def mock_targets() -> tuple[TargetConfig, TargetConfig]:
    """Create mock target configs for testing."""
    target_a = TargetConfig(base_url="http://localhost:8001")
    target_b = TargetConfig(base_url="http://localhost:8002")
    return target_a, target_b


class TestWaitForRateLimit:
    """Tests for Executor._wait_for_rate_limit method."""

    def test_no_rate_limit_when_disabled(self, mock_targets: tuple[TargetConfig, TargetConfig]) -> None:
        """When requests_per_second is None, no waiting occurs."""
        target_a, target_b = mock_targets

        with patch("api_parity.executor.time.sleep") as mock_sleep:
            executor = Executor(target_a, target_b, requests_per_second=None)
            try:
                # Call rate limit check multiple times
                executor._wait_for_rate_limit()
                executor._wait_for_rate_limit()
                executor._wait_for_rate_limit()

                # Should never sleep
                mock_sleep.assert_not_called()
            finally:
                executor.close()

    def test_sleep_called_when_requests_too_fast(
        self, mock_targets: tuple[TargetConfig, TargetConfig]
    ) -> None:
        """When requests come faster than rate limit, sleep is called."""
        target_a, target_b = mock_targets

        # 10 requests/second = 0.1s minimum interval
        with patch("api_parity.executor.time.sleep") as mock_sleep, \
             patch("api_parity.executor.time.monotonic") as mock_monotonic:
            # Use realistic monotonic values (large base time like system uptime)
            # _last_request_time starts at 0.0, so first call sees huge elapsed time
            base_time = 10000.0
            mock_monotonic.side_effect = [
                base_time,        # First call: now (elapsed = 10000 - 0 >> 0.1, no sleep)
                base_time,        # First call: update last_request_time to 10000
                base_time + 0.05, # Second call: now (elapsed = 0.05 < 0.1, needs sleep)
                base_time + 0.1,  # Second call: update last_request_time after sleep
            ]

            executor = Executor(target_a, target_b, requests_per_second=10.0)
            try:
                # First request - large elapsed time from init, should not sleep
                executor._wait_for_rate_limit()

                # Second request - only 0.05s elapsed, should sleep for 0.05s more
                executor._wait_for_rate_limit()

                # Should have slept exactly once for the second request
                mock_sleep.assert_called_once()
                sleep_duration = mock_sleep.call_args[0][0]
                assert abs(sleep_duration - 0.05) < 0.001
            finally:
                executor.close()

    def test_no_sleep_when_enough_time_elapsed(
        self, mock_targets: tuple[TargetConfig, TargetConfig]
    ) -> None:
        """When enough time has passed between requests, no sleep needed."""
        target_a, target_b = mock_targets

        # 10 requests/second = 0.1s minimum interval
        with patch("api_parity.executor.time.sleep") as mock_sleep, \
             patch("api_parity.executor.time.monotonic") as mock_monotonic:
            # Use realistic monotonic values
            base_time = 10000.0
            mock_monotonic.side_effect = [
                base_time,        # First call: now
                base_time,        # First call: update last_request_time
                base_time + 0.2,  # Second call: now (0.2s elapsed, only need 0.1s)
                base_time + 0.2,  # Second call: update last_request_time
            ]

            executor = Executor(target_a, target_b, requests_per_second=10.0)
            try:
                executor._wait_for_rate_limit()
                executor._wait_for_rate_limit()

                # Should not have slept - enough time passed
                mock_sleep.assert_not_called()
            finally:
                executor.close()

    def test_sleep_duration_calculation(
        self, mock_targets: tuple[TargetConfig, TargetConfig]
    ) -> None:
        """Sleep duration is calculated as (min_interval - elapsed)."""
        target_a, target_b = mock_targets

        # 5 requests/second = 0.2s minimum interval
        with patch("api_parity.executor.time.sleep") as mock_sleep, \
             patch("api_parity.executor.time.monotonic") as mock_monotonic:
            # Use realistic monotonic values
            base_time = 10000.0
            mock_monotonic.side_effect = [
                base_time,         # First call: now
                base_time,         # First call: update last_request_time
                base_time + 0.12,  # Second call: now (need to wait 0.08s more)
                base_time + 0.2,   # Second call: update last_request_time after sleep
            ]

            executor = Executor(target_a, target_b, requests_per_second=5.0)
            try:
                executor._wait_for_rate_limit()
                executor._wait_for_rate_limit()

                mock_sleep.assert_called_once()
                sleep_duration = mock_sleep.call_args[0][0]
                # Should sleep for 0.2 - 0.12 = 0.08 seconds
                assert abs(sleep_duration - 0.08) < 0.001
            finally:
                executor.close()

    def test_first_request_never_waits(
        self, mock_targets: tuple[TargetConfig, TargetConfig]
    ) -> None:
        """The first request should never wait (large elapsed time from init)."""
        target_a, target_b = mock_targets

        with patch("api_parity.executor.time.sleep") as mock_sleep, \
             patch("api_parity.executor.time.monotonic") as mock_monotonic:
            # _last_request_time starts at 0.0, but monotonic() returns large value
            # so elapsed = large_value - 0.0 >> min_interval, no sleep needed
            base_time = 10000.0
            mock_monotonic.side_effect = [
                base_time,  # First call: now (elapsed = 10000 - 0 >> 0.1)
                base_time,  # First call: update last_request_time
            ]

            executor = Executor(target_a, target_b, requests_per_second=10.0)
            try:
                executor._wait_for_rate_limit()

                # First request should not sleep - huge elapsed time
                mock_sleep.assert_not_called()
            finally:
                executor.close()
