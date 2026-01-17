"""Tests for Executor rate limiting and TLS configuration behavior.

Tests cover:
- Sleep is called when requests are too fast
- Sleep duration is calculated correctly
- Slow requests don't cause unnecessary waits
- No rate limiting when disabled
- TLS/mTLS configuration is passed to httpx.Client correctly
- Cipher configuration creates proper SSL context
"""

import ssl
from unittest.mock import MagicMock, patch

import pytest

from api_parity.executor import Executor, ExecutorError
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


class TestBuildClientKwargs:
    """Tests for Executor._build_client_kwargs method."""

    def test_basic_kwargs(self) -> None:
        """Test basic kwargs without TLS configuration."""
        target = TargetConfig(
            base_url="http://localhost:8000",
            headers={"Authorization": "Bearer token"},
        )

        # Create executor with mock to avoid actual httpx.Client creation
        with patch("api_parity.executor.httpx.Client"):
            executor = Executor(target, target)
            try:
                kwargs = executor._build_client_kwargs(target, 30.0)

                assert kwargs["base_url"] == "http://localhost:8000"
                assert kwargs["headers"] == {"Authorization": "Bearer token"}
                assert kwargs["timeout"] == 30.0
                assert "cert" not in kwargs
                assert "verify" not in kwargs
            finally:
                executor.close()

    def test_with_cert_and_key(self) -> None:
        """Test that cert and key are passed as a tuple."""
        target = TargetConfig(
            base_url="https://secure.example.com",
            cert="/path/to/client.crt",
            key="/path/to/client.key",
        )

        with patch("api_parity.executor.httpx.Client"):
            executor = Executor(target, target)
            try:
                kwargs = executor._build_client_kwargs(target, 30.0)

                assert kwargs["cert"] == ("/path/to/client.crt", "/path/to/client.key")
                assert "verify" not in kwargs  # Default verify not explicitly set
            finally:
                executor.close()

    def test_with_ca_bundle(self) -> None:
        """Test that ca_bundle is passed as verify."""
        target = TargetConfig(
            base_url="https://internal.example.com",
            ca_bundle="/path/to/ca-bundle.crt",
        )

        with patch("api_parity.executor.httpx.Client"):
            executor = Executor(target, target)
            try:
                kwargs = executor._build_client_kwargs(target, 30.0)

                assert kwargs["verify"] == "/path/to/ca-bundle.crt"
                assert "cert" not in kwargs
            finally:
                executor.close()

    def test_with_verify_ssl_false(self) -> None:
        """Test that verify_ssl=False sets verify=False."""
        target = TargetConfig(
            base_url="https://staging.example.com",
            verify_ssl=False,
        )

        with patch("api_parity.executor.httpx.Client"):
            executor = Executor(target, target)
            try:
                kwargs = executor._build_client_kwargs(target, 30.0)

                assert kwargs["verify"] is False
                assert "cert" not in kwargs
            finally:
                executor.close()

    def test_ca_bundle_takes_precedence_over_verify_ssl(self) -> None:
        """Test that ca_bundle is used even when verify_ssl is False."""
        target = TargetConfig(
            base_url="https://secure.example.com",
            ca_bundle="/path/to/ca-bundle.crt",
            verify_ssl=False,  # Should be ignored
        )

        with patch("api_parity.executor.httpx.Client"):
            executor = Executor(target, target)
            try:
                kwargs = executor._build_client_kwargs(target, 30.0)

                # ca_bundle should take precedence
                assert kwargs["verify"] == "/path/to/ca-bundle.crt"
            finally:
                executor.close()

    def test_full_tls_config(self) -> None:
        """Test with all TLS options configured."""
        target = TargetConfig(
            base_url="https://secure.example.com",
            headers={"X-Custom": "value"},
            cert="/path/to/client.crt",
            key="/path/to/client.key",
            ca_bundle="/path/to/ca-bundle.crt",
        )

        with patch("api_parity.executor.httpx.Client"):
            executor = Executor(target, target)
            try:
                kwargs = executor._build_client_kwargs(target, 60.0)

                assert kwargs["base_url"] == "https://secure.example.com"
                assert kwargs["headers"] == {"X-Custom": "value"}
                assert kwargs["timeout"] == 60.0
                assert kwargs["cert"] == ("/path/to/client.crt", "/path/to/client.key")
                assert kwargs["verify"] == "/path/to/ca-bundle.crt"
            finally:
                executor.close()

    def test_default_verify_not_set_when_true(self) -> None:
        """Test that verify is not explicitly set when using defaults."""
        target = TargetConfig(
            base_url="http://localhost:8000",
            verify_ssl=True,  # Default value
        )

        with patch("api_parity.executor.httpx.Client"):
            executor = Executor(target, target)
            try:
                kwargs = executor._build_client_kwargs(target, 30.0)

                # verify should not be in kwargs, letting httpx use its default
                assert "verify" not in kwargs
            finally:
                executor.close()

    def test_with_key_password(self) -> None:
        """Test that cert, key, and key_password are passed as a 3-tuple."""
        target = TargetConfig(
            base_url="https://secure.example.com",
            cert="/path/to/client.crt",
            key="/path/to/client.key",
            key_password="secret123",
        )

        with patch("api_parity.executor.httpx.Client"):
            executor = Executor(target, target)
            try:
                kwargs = executor._build_client_kwargs(target, 30.0)

                assert kwargs["cert"] == (
                    "/path/to/client.crt",
                    "/path/to/client.key",
                    "secret123",
                )
            finally:
                executor.close()

    def test_key_password_without_cert_key_ignored(self) -> None:
        """Test that key_password alone does nothing (requires cert and key)."""
        target = TargetConfig(
            base_url="https://secure.example.com",
            key_password="secret123",  # No cert/key provided
        )

        with patch("api_parity.executor.httpx.Client"):
            executor = Executor(target, target)
            try:
                kwargs = executor._build_client_kwargs(target, 30.0)

                # cert should not be in kwargs since cert/key were not provided
                assert "cert" not in kwargs
            finally:
                executor.close()


class TestHttpxClientKwargsIntegration:
    """Tests that verify httpx.Client is actually called with the correct kwargs from _build_client_kwargs."""

    def test_basic_client_receives_correct_kwargs(self) -> None:
        """Test that httpx.Client receives the kwargs from _build_client_kwargs."""
        target_a = TargetConfig(
            base_url="http://localhost:8001",
            headers={"X-Custom-A": "value-a"},
        )
        target_b = TargetConfig(
            base_url="http://localhost:8002",
            headers={"X-Custom-B": "value-b"},
        )

        with patch("api_parity.executor.httpx.Client") as mock_client_cls:
            executor = Executor(target_a, target_b, default_timeout=45.0)
            try:
                # Should have been called twice, once for each target
                assert mock_client_cls.call_count == 2

                # Verify call args for target_a (first call)
                call_a = mock_client_cls.call_args_list[0]
                assert call_a.kwargs["base_url"] == "http://localhost:8001"
                assert call_a.kwargs["headers"] == {"X-Custom-A": "value-a"}
                assert call_a.kwargs["timeout"] == 45.0

                # Verify call args for target_b (second call)
                call_b = mock_client_cls.call_args_list[1]
                assert call_b.kwargs["base_url"] == "http://localhost:8002"
                assert call_b.kwargs["headers"] == {"X-Custom-B": "value-b"}
                assert call_b.kwargs["timeout"] == 45.0
            finally:
                executor.close()

    def test_tls_kwargs_passed_to_client(self) -> None:
        """Test that TLS configuration kwargs are passed to httpx.Client."""
        target = TargetConfig(
            base_url="https://secure.example.com",
            cert="/path/to/client.crt",
            key="/path/to/client.key",
            ca_bundle="/path/to/ca-bundle.crt",
        )

        with patch("api_parity.executor.httpx.Client") as mock_client_cls:
            executor = Executor(target, target)
            try:
                # Both clients should have the same TLS kwargs
                assert mock_client_cls.call_count == 2

                for call in mock_client_cls.call_args_list:
                    assert call.kwargs["cert"] == ("/path/to/client.crt", "/path/to/client.key")
                    assert call.kwargs["verify"] == "/path/to/ca-bundle.crt"
            finally:
                executor.close()

    def test_key_password_passed_to_client(self) -> None:
        """Test that key_password is passed to httpx.Client as 3-tuple cert."""
        target = TargetConfig(
            base_url="https://secure.example.com",
            cert="/path/to/client.crt",
            key="/path/to/client.key",
            key_password="encrypted-key-pass",
        )

        with patch("api_parity.executor.httpx.Client") as mock_client_cls:
            executor = Executor(target, target)
            try:
                assert mock_client_cls.call_count == 2

                for call in mock_client_cls.call_args_list:
                    # Verify the 3-tuple format is used when key_password is provided
                    assert call.kwargs["cert"] == (
                        "/path/to/client.crt",
                        "/path/to/client.key",
                        "encrypted-key-pass",
                    )
            finally:
                executor.close()

    def test_verify_ssl_false_passed_to_client(self) -> None:
        """Test that verify_ssl=False is passed to httpx.Client as verify=False."""
        target = TargetConfig(
            base_url="https://staging.example.com",
            verify_ssl=False,
        )

        with patch("api_parity.executor.httpx.Client") as mock_client_cls:
            executor = Executor(target, target)
            try:
                assert mock_client_cls.call_count == 2

                for call in mock_client_cls.call_args_list:
                    assert call.kwargs["verify"] is False
            finally:
                executor.close()

    def test_rate_limit_does_not_affect_client_kwargs(self) -> None:
        """Test that rate_limit parameter doesn't leak into httpx.Client kwargs."""
        target = TargetConfig(base_url="http://localhost:8000")

        with patch("api_parity.executor.httpx.Client") as mock_client_cls:
            executor = Executor(target, target, requests_per_second=10.0)
            try:
                assert mock_client_cls.call_count == 2

                for call in mock_client_cls.call_args_list:
                    # Only expected kwargs should be present
                    assert set(call.kwargs.keys()) == {"base_url", "headers", "timeout"}
            finally:
                executor.close()


class TestCipherConfiguration:
    """Tests for cipher configuration in Executor._build_client_kwargs."""

    def test_ciphers_creates_ssl_context(self) -> None:
        """Test that specifying ciphers creates an SSL context with restricted ciphers."""
        target = TargetConfig(
            base_url="https://secure.example.com",
            ciphers="ECDHE+AESGCM",
        )

        with patch("api_parity.executor.httpx.Client"):
            executor = Executor(target, target)
            try:
                kwargs = executor._build_client_kwargs(target, 30.0)

                ssl_context = kwargs["verify"]
                assert isinstance(ssl_context, ssl.SSLContext)
                # Verify ciphers were actually restricted (default context has ~17,
                # ECDHE+AESGCM restricts to ~7 depending on OpenSSL version)
                enabled_ciphers = ssl_context.get_ciphers()
                assert len(enabled_ciphers) < 15, "Ciphers should be restricted"
                # Verify all enabled ciphers match the pattern (contain ECDHE and GCM)
                for cipher in enabled_ciphers:
                    name = cipher["name"]
                    # TLS 1.3 ciphers don't follow the same naming, skip them
                    if not name.startswith("TLS_"):
                        assert "ECDHE" in name or "GCM" in name, f"Unexpected cipher: {name}"
            finally:
                executor.close()

    def test_ciphers_with_ca_bundle(self) -> None:
        """Test that ciphers work together with ca_bundle."""
        target = TargetConfig(
            base_url="https://secure.example.com",
            ciphers="ECDHE+AESGCM",
            ca_bundle="/path/to/ca-bundle.crt",
        )

        with patch("api_parity.executor.httpx.Client"), \
             patch("ssl.SSLContext.load_verify_locations") as mock_load:
            executor = Executor(target, target)
            try:
                kwargs = executor._build_client_kwargs(target, 30.0)

                assert isinstance(kwargs["verify"], ssl.SSLContext)
                # load_verify_locations should be called with the ca_bundle
                mock_load.assert_called_with("/path/to/ca-bundle.crt")
            finally:
                executor.close()

    def test_ciphers_with_verify_ssl_false(self) -> None:
        """Test that ciphers work with verify_ssl=False."""
        target = TargetConfig(
            base_url="https://staging.example.com",
            ciphers="ECDHE+AESGCM",
            verify_ssl=False,
        )

        with patch("api_parity.executor.httpx.Client"):
            executor = Executor(target, target)
            try:
                kwargs = executor._build_client_kwargs(target, 30.0)

                ssl_context = kwargs["verify"]
                assert isinstance(ssl_context, ssl.SSLContext)
                # Verification should be disabled
                assert ssl_context.check_hostname is False
                assert ssl_context.verify_mode == ssl.CERT_NONE
            finally:
                executor.close()

    def test_ciphers_passed_to_httpx_client(self) -> None:
        """Test that SSL context with ciphers is passed to httpx.Client."""
        target = TargetConfig(
            base_url="https://secure.example.com",
            ciphers="ECDHE+AESGCM:DHE+AESGCM",
        )

        with patch("api_parity.executor.httpx.Client") as mock_client_cls:
            executor = Executor(target, target)
            try:
                assert mock_client_cls.call_count == 2

                for call in mock_client_cls.call_args_list:
                    assert isinstance(call.kwargs["verify"], ssl.SSLContext)
            finally:
                executor.close()

    def test_different_ciphers_per_target(self) -> None:
        """Test that different cipher configurations work for each target."""
        target_a = TargetConfig(
            base_url="https://server-a.example.com",
            ciphers="ECDHE+AESGCM",
        )
        target_b = TargetConfig(
            base_url="https://server-b.example.com",
            # No ciphers, uses default
        )

        with patch("api_parity.executor.httpx.Client") as mock_client_cls:
            executor = Executor(target_a, target_b)
            try:
                assert mock_client_cls.call_count == 2

                # First call (target_a) should have SSL context
                call_a = mock_client_cls.call_args_list[0]
                assert isinstance(call_a.kwargs["verify"], ssl.SSLContext)

                # Second call (target_b) should not have verify set (uses default)
                call_b = mock_client_cls.call_args_list[1]
                assert "verify" not in call_b.kwargs
            finally:
                executor.close()

    def test_invalid_cipher_string_raises_executor_error(self) -> None:
        """Test that invalid cipher string raises ExecutorError with clear message."""
        target = TargetConfig(
            base_url="https://secure.example.com",
            ciphers="INVALID_CIPHER_THAT_DOES_NOT_EXIST",
        )

        with pytest.raises(ExecutorError, match="Invalid cipher string"):
            Executor(target, target)


class TestHeaderSanitization:
    """Tests for ASCII header sanitization.

    HTTP headers must be ASCII per RFC 7230. Schemathesis/Hypothesis may generate
    non-ASCII characters during fuzzing. The executor sanitizes header values to
    prevent UnicodeEncodeError when httpx encodes headers.
    """

    def test_sanitize_header_value_ascii_passthrough(self) -> None:
        """ASCII values pass through unchanged."""
        from api_parity.executor import _sanitize_header_value

        assert _sanitize_header_value("Bearer token123") == "Bearer token123"
        assert _sanitize_header_value("application/json") == "application/json"
        assert _sanitize_header_value("") == ""
        assert _sanitize_header_value("x-y-z_123") == "x-y-z_123"

    def test_sanitize_header_value_non_ascii_replaced(self) -> None:
        """Non-ASCII characters are replaced with '?'."""
        from api_parity.executor import _sanitize_header_value

        # \xaf is the character that caused the original issue
        assert _sanitize_header_value("\xaf") == "?"
        assert _sanitize_header_value("test\xafvalue") == "test?value"

        # Unicode characters
        assert _sanitize_header_value("héllo") == "h?llo"
        assert _sanitize_header_value("日本語") == "???"
        assert _sanitize_header_value("café") == "caf?"

        # Mixed ASCII and non-ASCII
        assert _sanitize_header_value("a\xafb\xafc") == "a?b?c"

    def test_sanitize_header_value_preserves_structure(self) -> None:
        """Sanitization preserves overall string structure and length."""
        from api_parity.executor import _sanitize_header_value

        original = "test\xafvalue"
        sanitized = _sanitize_header_value(original)
        # Non-ASCII becomes single '?' so length is preserved
        assert len(sanitized) == len(original)

    def test_execute_single_sanitizes_headers(
        self, mock_targets: tuple[TargetConfig, TargetConfig]
    ) -> None:
        """Executor._execute_single sanitizes non-ASCII header values."""
        from api_parity.models import RequestCase

        target_a, target_b = mock_targets

        # Create request with non-ASCII header value
        request = RequestCase(
            case_id="test-case",
            operation_id="testOp",
            method="GET",
            path_template="/test",
            rendered_path="/test",
            headers={"X-Custom": ["test\xafvalue"]},  # Non-ASCII \xaf
        )

        with patch("api_parity.executor.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.headers.multi_items.return_value = []
            mock_response.content = b""
            mock_response.headers.get.return_value = ""
            mock_response.http_version = "1.1"
            mock_client.request.return_value = mock_response
            mock_client_cls.return_value = mock_client

            executor = Executor(target_a, target_b)
            try:
                executor._execute_single(mock_client, request, 30.0, "Test")

                # Verify the header value was sanitized before being sent
                call_kwargs = mock_client.request.call_args.kwargs
                assert call_kwargs["headers"]["X-Custom"] == "test?value"
            finally:
                executor.close()

    def test_execute_does_not_crash_on_non_ascii_headers(
        self, mock_targets: tuple[TargetConfig, TargetConfig]
    ) -> None:
        """Executor.execute completes without UnicodeEncodeError for non-ASCII headers."""
        from api_parity.models import RequestCase

        target_a, target_b = mock_targets

        # Create request with various non-ASCII header values
        request = RequestCase(
            case_id="test-case",
            operation_id="testOp",
            method="GET",
            path_template="/test",
            rendered_path="/test",
            headers={
                "X-Latin": ["\xaf\xb0\xb1"],  # Latin supplement chars
                "X-Unicode": ["日本語"],  # Japanese
                "X-Mixed": ["hello\xafworld"],
            },
        )

        with patch("api_parity.executor.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.headers.multi_items.return_value = []
            mock_response.content = b""
            mock_response.headers.get.return_value = ""
            mock_response.http_version = "1.1"
            mock_client.request.return_value = mock_response
            mock_client_cls.return_value = mock_client

            executor = Executor(target_a, target_b)
            try:
                # Should not raise UnicodeEncodeError
                response_a, response_b = executor.execute(request)

                # Should have completed successfully
                assert response_a.status_code == 200
                assert response_b.status_code == 200

                # Both targets should have received sanitized headers
                for call in mock_client.request.call_args_list:
                    headers = call.kwargs["headers"]
                    assert headers["X-Latin"] == "???"
                    assert headers["X-Unicode"] == "???"
                    assert headers["X-Mixed"] == "hello?world"
            finally:
                executor.close()

    def test_unicode_encode_error_caught_as_request_error(
        self, mock_targets: tuple[TargetConfig, TargetConfig]
    ) -> None:
        """UnicodeEncodeError from httpx is caught and wrapped as RequestError.

        This tests the fallback error handler for edge cases not caught by
        header value sanitization (e.g., non-ASCII in header keys from a
        malformed OpenAPI spec).
        """
        from api_parity.executor import RequestError
        from api_parity.models import RequestCase

        target_a, target_b = mock_targets

        request = RequestCase(
            case_id="test-case",
            operation_id="testOp",
            method="GET",
            path_template="/test",
            rendered_path="/test",
        )

        with patch("api_parity.executor.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            # Simulate httpx raising UnicodeEncodeError (e.g., from non-ASCII header key)
            mock_client.request.side_effect = UnicodeEncodeError(
                'ascii', 'tëst', 1, 2, 'ordinal not in range(128)'
            )
            mock_client_cls.return_value = mock_client

            executor = Executor(target_a, target_b)
            try:
                with pytest.raises(RequestError, match="encoding error"):
                    executor._execute_single(mock_client, request, 30.0, "Test")
            finally:
                executor.close()
