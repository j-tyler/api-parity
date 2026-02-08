"""Tests for XML request serialization and response parsing in the Executor.

Tests cover:
- XML request body serialization (dict → XML bytes via dict_to_xml)
- XML response body parsing (XML bytes → dict via xml_to_dict)
- Content-type routing: application/xml, text/xml, and edge cases
- Fallback to base64 when XML parsing fails
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


class TestExecutorXmlRequestSerialization:
    """Executor sends dict body as valid XML bytes for application/xml."""

    def test_xml_request_body_sent_as_xml_bytes(
        self, mock_targets: tuple[TargetConfig, TargetConfig]
    ) -> None:
        """When media_type is application/xml and body is a dict,
        the executor converts it to XML bytes via dict_to_xml."""
        target_a, target_b = mock_targets

        request = RequestCase(
            case_id="test-xml-req",
            operation_id="deleteObjects",
            method="POST",
            path_template="/bucket",
            rendered_path="/bucket",
            body={"Delete": {"Object": [{"Key": "a.txt"}, {"Key": "b.txt"}]}},
            media_type="application/xml",
        )

        mock_response = _make_mock_response(content_type="application/xml",
                                            content=b"<Result/>")

        with patch("api_parity.executor.httpx.Client"):
            executor = Executor(target_a, target_b)
            try:
                mock_client = MagicMock()
                mock_client.request.return_value = mock_response

                executor._execute_single(mock_client, request, 30.0, "Test")

                call_kwargs = mock_client.request.call_args.kwargs
                sent_content = call_kwargs["content"]
                assert isinstance(sent_content, bytes)
                assert b"<Delete>" in sent_content
                assert b"<Key>a.txt</Key>" in sent_content
                assert b"<Key>b.txt</Key>" in sent_content
                assert b"<?xml" in sent_content
            finally:
                executor.close()

    def test_xml_request_string_body_encoded(
        self, mock_targets: tuple[TargetConfig, TargetConfig]
    ) -> None:
        """When media_type is XML but body is already a string (pre-serialized),
        it is encoded to UTF-8 bytes directly."""
        target_a, target_b = mock_targets

        request = RequestCase(
            case_id="test-xml-str",
            operation_id="testOp",
            method="POST",
            path_template="/test",
            rendered_path="/test",
            body="<Root><Key>test</Key></Root>",
            media_type="application/xml",
        )

        mock_response = _make_mock_response(content_type="application/xml",
                                            content=b"<OK/>")

        with patch("api_parity.executor.httpx.Client"):
            executor = Executor(target_a, target_b)
            try:
                mock_client = MagicMock()
                mock_client.request.return_value = mock_response

                executor._execute_single(mock_client, request, 30.0, "Test")

                call_kwargs = mock_client.request.call_args.kwargs
                assert call_kwargs["content"] == b"<Root><Key>test</Key></Root>"
            finally:
                executor.close()

    def test_text_xml_media_type_triggers_xml_branch(
        self, mock_targets: tuple[TargetConfig, TargetConfig]
    ) -> None:
        """text/xml is also recognized as XML for request serialization."""
        target_a, target_b = mock_targets

        request = RequestCase(
            case_id="test-text-xml",
            operation_id="testOp",
            method="POST",
            path_template="/test",
            rendered_path="/test",
            body={"Root": {"Key": "value"}},
            media_type="text/xml",
        )

        mock_response = _make_mock_response(content_type="text/xml",
                                            content=b"<OK/>")

        with patch("api_parity.executor.httpx.Client"):
            executor = Executor(target_a, target_b)
            try:
                mock_client = MagicMock()
                mock_client.request.return_value = mock_response

                executor._execute_single(mock_client, request, 30.0, "Test")

                call_kwargs = mock_client.request.call_args.kwargs
                sent_content = call_kwargs["content"]
                assert isinstance(sent_content, bytes)
                assert b"<Root>" in sent_content
                assert b"<Key>value</Key>" in sent_content
            finally:
                executor.close()


class TestExecutorXmlResponseParsing:
    """Executor parses XML response bodies into dicts."""

    def test_application_xml_response_parsed_to_dict(
        self, mock_targets: tuple[TargetConfig, TargetConfig]
    ) -> None:
        """XML response with application/xml content-type is parsed to a dict."""
        target_a, target_b = mock_targets

        request = RequestCase(
            case_id="test-xml-resp",
            operation_id="listObjects",
            method="GET",
            path_template="/bucket",
            rendered_path="/bucket",
        )

        xml_body = (
            b'<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
            b"<Name>my-bucket</Name>"
            b"<Contents><Key>file.txt</Key></Contents>"
            b"</ListBucketResult>"
        )
        mock_response = _make_mock_response(
            content_type="application/xml", content=xml_body
        )

        with patch("api_parity.executor.httpx.Client"):
            executor = Executor(target_a, target_b)
            try:
                mock_client = MagicMock()
                mock_client.request.return_value = mock_response

                response_case = executor._execute_single(
                    mock_client, request, 30.0, "Test"
                )

                assert response_case.body is not None
                assert response_case.body_base64 is None
                assert isinstance(response_case.body, dict)
                root = response_case.body["ListBucketResult"]
                assert root["Name"] == "my-bucket"
                assert root["Contents"]["Key"] == "file.txt"
            finally:
                executor.close()

    def test_text_xml_response_parsed_to_dict(
        self, mock_targets: tuple[TargetConfig, TargetConfig]
    ) -> None:
        """text/xml content-type also triggers XML parsing (not raw text)."""
        target_a, target_b = mock_targets

        request = RequestCase(
            case_id="test-text-xml-resp",
            operation_id="testOp",
            method="GET",
            path_template="/test",
            rendered_path="/test",
        )

        mock_response = _make_mock_response(
            content_type="text/xml",
            content=b"<Error><Code>NotFound</Code></Error>",
        )

        with patch("api_parity.executor.httpx.Client"):
            executor = Executor(target_a, target_b)
            try:
                mock_client = MagicMock()
                mock_client.request.return_value = mock_response

                response_case = executor._execute_single(
                    mock_client, request, 30.0, "Test"
                )

                # Should be parsed as dict, NOT as raw text string
                assert isinstance(response_case.body, dict)
                assert response_case.body["Error"]["Code"] == "NotFound"
            finally:
                executor.close()

    def test_malformed_xml_response_falls_back_to_base64(
        self, mock_targets: tuple[TargetConfig, TargetConfig]
    ) -> None:
        """If content-type says XML but body is not valid XML, fall back to base64."""
        target_a, target_b = mock_targets

        request = RequestCase(
            case_id="test-bad-xml",
            operation_id="testOp",
            method="GET",
            path_template="/test",
            rendered_path="/test",
        )

        mock_response = _make_mock_response(
            content_type="application/xml",
            content=b"this is not valid xml at all",
        )

        with patch("api_parity.executor.httpx.Client"):
            executor = Executor(target_a, target_b)
            try:
                mock_client = MagicMock()
                mock_client.request.return_value = mock_response

                response_case = executor._execute_single(
                    mock_client, request, 30.0, "Test"
                )

                # Falls back to base64 since XML parsing failed
                assert response_case.body is None
                assert response_case.body_base64 is not None
            finally:
                executor.close()

    def test_json_response_still_works(
        self, mock_targets: tuple[TargetConfig, TargetConfig]
    ) -> None:
        """JSON responses are unaffected by the XML change."""
        target_a, target_b = mock_targets

        request = RequestCase(
            case_id="test-json",
            operation_id="testOp",
            method="GET",
            path_template="/test",
            rendered_path="/test",
        )

        mock_response = _make_mock_response(
            content_type="application/json",
            content=b'{"key": "value"}',
        )
        mock_response.json.return_value = {"key": "value"}

        with patch("api_parity.executor.httpx.Client"):
            executor = Executor(target_a, target_b)
            try:
                mock_client = MagicMock()
                mock_client.request.return_value = mock_response

                response_case = executor._execute_single(
                    mock_client, request, 30.0, "Test"
                )

                assert response_case.body == {"key": "value"}
                assert response_case.body_base64 is None
            finally:
                executor.close()

    def test_xml_charset_content_type(
        self, mock_targets: tuple[TargetConfig, TargetConfig]
    ) -> None:
        """Content-type with charset parameter: 'application/xml; charset=utf-8'."""
        target_a, target_b = mock_targets

        request = RequestCase(
            case_id="test-xml-charset",
            operation_id="testOp",
            method="GET",
            path_template="/test",
            rendered_path="/test",
        )

        mock_response = _make_mock_response(
            content_type="application/xml; charset=utf-8",
            content=b"<Root><Name>test</Name></Root>",
        )

        with patch("api_parity.executor.httpx.Client"):
            executor = Executor(target_a, target_b)
            try:
                mock_client = MagicMock()
                mock_client.request.return_value = mock_response

                response_case = executor._execute_single(
                    mock_client, request, 30.0, "Test"
                )

                assert isinstance(response_case.body, dict)
                assert response_case.body["Root"]["Name"] == "test"
            finally:
                executor.close()
