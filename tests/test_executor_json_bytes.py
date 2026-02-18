"""Tests for JSON branch handling of bytes body in Executor._execute_single.

The JSON body encoding branch must handle request.body being bytes, matching
the defensive type checking already present in the XML and plain branches.
When body is bytes that are valid JSON, they should be parsed and sent via
httpx's json= parameter. When not valid JSON, fall back to raw content=.
"""

from unittest.mock import MagicMock, patch

import pytest

from api_parity.executor import Executor
from api_parity.models import RequestCase, TargetConfig


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
    """Create a mock httpx response with the given properties."""
    mock_response = MagicMock()
    mock_response.status_code = status_code
    mock_response.headers.multi_items.return_value = (
        [("content-type", content_type)] if content_type else []
    )
    mock_response.content = content
    mock_response.headers.get.return_value = content_type
    mock_response.http_version = "1.1"
    return mock_response


class TestExecutorJsonBytesBody:
    """JSON branch handles bytes body without crashing."""

    def test_json_body_bytes_valid_json_parsed(
        self, mock_targets: tuple[TargetConfig, TargetConfig]
    ) -> None:
        """When body is bytes containing valid JSON, parse and send via json=."""
        target_a, target_b = mock_targets

        request = RequestCase(
            case_id="test-json-bytes",
            operation_id="testOp",
            method="POST",
            path_template="/test",
            rendered_path="/test",
            body=b'{"key": "value"}',
            media_type="application/json",
        )

        mock_response = _make_mock_response(
            content_type="application/json", content=b'{"ok": true}'
        )

        with patch("api_parity.executor.httpx.Client"):
            executor = Executor(target_a, target_b)
            try:
                mock_client = MagicMock()
                mock_client.request.return_value = mock_response

                executor._execute_single(mock_client, request, 30.0, "Test")

                call_kwargs = mock_client.request.call_args.kwargs
                # Should be parsed dict, not raw bytes
                assert call_kwargs["json"] == {"key": "value"}
                assert call_kwargs["content"] is None
            finally:
                executor.close()

    def test_json_body_bytes_invalid_json_falls_back_to_content(
        self, mock_targets: tuple[TargetConfig, TargetConfig]
    ) -> None:
        """When body is bytes that aren't valid JSON, send as raw content=."""
        target_a, target_b = mock_targets

        request = RequestCase(
            case_id="test-json-bytes-invalid",
            operation_id="testOp",
            method="POST",
            path_template="/test",
            rendered_path="/test",
            body=b"\x00\x01\x02\x03",
            media_type="application/json",
        )

        mock_response = _make_mock_response(
            content_type="application/json", content=b'{"ok": true}'
        )

        with patch("api_parity.executor.httpx.Client"):
            executor = Executor(target_a, target_b)
            try:
                mock_client = MagicMock()
                mock_client.request.return_value = mock_response

                executor._execute_single(mock_client, request, 30.0, "Test")

                call_kwargs = mock_client.request.call_args.kwargs
                # Should fall back to raw content, not json
                assert call_kwargs["json"] is None
                assert call_kwargs["content"] == b"\x00\x01\x02\x03"
            finally:
                executor.close()

    def test_json_body_dict_still_works(
        self, mock_targets: tuple[TargetConfig, TargetConfig]
    ) -> None:
        """Normal dict body for JSON media type still works as before."""
        target_a, target_b = mock_targets

        request = RequestCase(
            case_id="test-json-dict",
            operation_id="testOp",
            method="POST",
            path_template="/test",
            rendered_path="/test",
            body={"key": "value"},
            media_type="application/json",
        )

        mock_response = _make_mock_response(
            content_type="application/json", content=b'{"ok": true}'
        )

        with patch("api_parity.executor.httpx.Client"):
            executor = Executor(target_a, target_b)
            try:
                mock_client = MagicMock()
                mock_client.request.return_value = mock_response

                executor._execute_single(mock_client, request, 30.0, "Test")

                call_kwargs = mock_client.request.call_args.kwargs
                assert call_kwargs["json"] == {"key": "value"}
                assert call_kwargs["content"] is None
            finally:
                executor.close()
