"""Tests for URL path sanitization in the Executor.

httpx rejects ASCII control characters (0x00-0x1F, 0x7F) in URLs with
InvalidURL.  Schemathesis may fuzz-generate path parameter values containing
these characters.  The executor must percent-encode them before sending.
"""

from unittest.mock import MagicMock, patch

import pytest

from api_parity.executor import Executor, _percent_encode_control_chars
from api_parity.models import RequestCase, TargetConfig


# --- Unit tests for _percent_encode_control_chars ---


class TestPercentEncodeControlChars:
    """_percent_encode_control_chars encodes only control characters."""

    def test_no_control_chars_unchanged(self) -> None:
        assert _percent_encode_control_chars("/api/widgets/abc") == "/api/widgets/abc"

    def test_null_byte_encoded(self) -> None:
        assert _percent_encode_control_chars("/path/a\x00b") == "/path/a%00b"

    def test_sub_character_encoded(self) -> None:
        """SUB (0x1A) is the character from the bug report."""
        assert _percent_encode_control_chars("/path/a\x1ab") == "/path/a%1Ab"

    def test_tab_encoded(self) -> None:
        assert _percent_encode_control_chars("/path/a\tb") == "/path/a%09b"

    def test_newline_encoded(self) -> None:
        assert _percent_encode_control_chars("/path/a\nb") == "/path/a%0Ab"

    def test_del_encoded(self) -> None:
        assert _percent_encode_control_chars("/path/a\x7fb") == "/path/a%7Fb"

    def test_multiple_control_chars(self) -> None:
        assert _percent_encode_control_chars("/\x00/\x1f") == "/%00/%1F"

    def test_slashes_preserved(self) -> None:
        """Path separators must not be encoded."""
        result = _percent_encode_control_chars("/api/v1/widgets/123")
        assert result == "/api/v1/widgets/123"

    def test_spaces_not_encoded(self) -> None:
        """httpx handles spaces itself — we should leave them alone."""
        result = _percent_encode_control_chars("/path/hello world")
        assert result == "/path/hello world"

    def test_unicode_not_encoded(self) -> None:
        """httpx handles unicode itself — we should leave it alone."""
        result = _percent_encode_control_chars("/path/héllo")
        assert result == "/path/héllo"

    def test_printable_ascii_not_encoded(self) -> None:
        """All printable ASCII (0x20-0x7E) should pass through."""
        # Space (0x20) through tilde (0x7E)
        printable = "".join(chr(i) for i in range(0x20, 0x7F))
        path = f"/path/{printable}"
        assert _percent_encode_control_chars(path) == path


# --- Integration test: executor sends request with sanitized URL ---


@pytest.fixture
def mock_targets() -> tuple[TargetConfig, TargetConfig]:
    target_a = TargetConfig(base_url="http://localhost:8001")
    target_b = TargetConfig(base_url="http://localhost:8002")
    return target_a, target_b


def _make_mock_response(
    status_code: int = 200,
    content: bytes = b"",
    content_type: str = "",
) -> MagicMock:
    mock_response = MagicMock()
    mock_response.status_code = status_code
    mock_response.headers.multi_items.return_value = (
        [("content-type", content_type)] if content_type else []
    )
    mock_response.content = content
    mock_response.headers.get.return_value = content_type
    mock_response.http_version = "1.1"
    return mock_response


class TestExecutorUrlSanitization:
    """Executor percent-encodes control characters in URL paths."""

    def test_control_char_in_path_does_not_crash(
        self, mock_targets: tuple[TargetConfig, TargetConfig]
    ) -> None:
        """Path with control character is percent-encoded, not rejected."""
        target_a, target_b = mock_targets

        request = RequestCase(
            case_id="test-url-ctrl",
            operation_id="getWidget",
            method="GET",
            path_template="/widgets/{widgetId}",
            path_parameters={"widgetId": "abc\x1adef"},
            rendered_path="/widgets/abc\x1adef",
        )

        mock_response = _make_mock_response(
            content_type="application/json", content=b'{"id": "abc"}'
        )

        with patch("api_parity.executor.httpx.Client"):
            executor = Executor(target_a, target_b)
            try:
                mock_client = MagicMock()
                mock_client.request.return_value = mock_response

                executor._execute_single(mock_client, request, 30.0, "Test")

                call_kwargs = mock_client.request.call_args.kwargs
                # The URL passed to httpx must have the control char encoded
                assert call_kwargs["url"] == "/widgets/abc%1Adef"
            finally:
                executor.close()
