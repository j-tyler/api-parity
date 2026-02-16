"""Tests for OpenAPI link expression resolution in executor.

Tests the _resolve_link_expression, _resolve_link_overrides, and
_apply_link_overrides methods that translate OpenAPI link parameters
(e.g., {"widget_id": "$response.body#/id"}) into concrete values
from prior step responses.

These are unit tests — they test the resolution logic directly
without making HTTP requests or spinning up servers.
"""

from __future__ import annotations

import pytest

from unittest.mock import patch

from api_parity.case_generator import HeaderRef, LinkFields, _MISSING
from api_parity.executor import Executor
from api_parity.models import (
    ChainCase,
    ChainStep,
    RequestCase,
    ResponseCase,
    TargetConfig,
)


@pytest.fixture
def executor():
    """Create an executor for testing (no actual HTTP calls needed)."""
    target = TargetConfig(base_url="http://localhost:9999")
    link_fields = LinkFields(
        body_pointers={"id", "data/nested_id"},
        headers=[
            HeaderRef(name="location", original_name="Location", index=None),
            HeaderRef(name="x-resource-id", original_name="X-Resource-Id", index=0),
        ],
    )
    exc = Executor(target, target, link_fields=link_fields)
    yield exc
    exc.close()


# =============================================================================
# _resolve_link_expression: $response.body#/... expressions
# =============================================================================


class TestResolveResponseBodyExpression:
    """Test $response.body#/path resolution."""

    def test_simple_body_field(self, executor):
        """$response.body#/id resolves to extracted body variable."""
        extracted = {"id": "abc-123"}
        result = Executor._resolve_link_expression(
            "$response.body#/id", extracted, None
        )
        assert result == "abc-123"

    def test_nested_body_field(self, executor):
        """$response.body#/data/nested_id resolves to nested extracted variable."""
        extracted = {"data/nested_id": "nested-456"}
        result = Executor._resolve_link_expression(
            "$response.body#/data/nested_id", extracted, None
        )
        assert result == "nested-456"

    def test_nested_body_field_with_shortcut(self, executor):
        """$response.body#/data/nested_id falls back to last segment alias."""
        # Only the shortcut alias is present (not the full path)
        extracted = {"nested_id": "nested-456"}
        result = Executor._resolve_link_expression(
            "$response.body#/data/nested_id", extracted, None
        )
        assert result == "nested-456"

    def test_body_field_prefers_full_path(self, executor):
        """Full path is preferred over last segment shortcut."""
        extracted = {
            "data/nested_id": "full-path-value",
            "nested_id": "shortcut-value",
        }
        result = Executor._resolve_link_expression(
            "$response.body#/data/nested_id", extracted, None
        )
        assert result == "full-path-value"

    def test_body_field_missing(self, executor):
        """Returns _MISSING when body field not in extracted variables."""
        extracted = {"other_field": "value"}
        result = Executor._resolve_link_expression(
            "$response.body#/id", extracted, None
        )
        assert result is _MISSING

    def test_body_field_numeric_value(self, executor):
        """Numeric body values are returned as-is."""
        extracted = {"count": 42}
        result = Executor._resolve_link_expression(
            "$response.body#/count", extracted, None
        )
        assert result == 42

    def test_body_field_null_value(self, executor):
        """JSON null body value resolves as None (not treated as missing).

        Regression test: null values were previously conflated with "not found"
        because both used None as the return value. Now _MISSING is used for
        "not found", and None represents a legitimate JSON null.
        """
        extracted = {"id": None}
        result = Executor._resolve_link_expression(
            "$response.body#/id", extracted, None
        )
        assert result is None
        assert result is not _MISSING

    def test_simple_field_no_shortcut_fallback(self, executor):
        """Simple field (no slash) doesn't try shortcut when not found."""
        extracted = {}
        result = Executor._resolve_link_expression(
            "$response.body#/id", extracted, None
        )
        assert result is _MISSING


# =============================================================================
# _resolve_link_expression: $response.header.X expressions
# =============================================================================


class TestResolveResponseHeaderExpression:
    """Test $response.header.X resolution."""

    def test_header_value(self, executor):
        """$response.header.Location resolves to header value."""
        extracted = {"header/location": ["/resources/abc-123"]}
        result = Executor._resolve_link_expression(
            "$response.header.Location", extracted, None
        )
        # Returns first element of the list
        assert result == "/resources/abc-123"

    def test_header_case_insensitive(self, executor):
        """Header name matching is case-insensitive."""
        extracted = {"header/location": ["/resources/abc-123"]}
        result = Executor._resolve_link_expression(
            "$response.header.LOCATION", extracted, None
        )
        assert result == "/resources/abc-123"

    def test_header_with_index(self, executor):
        """$response.header.Set-Cookie[0] resolves to indexed header value."""
        extracted = {"header/set-cookie/0": "session=abc"}
        result = Executor._resolve_link_expression(
            "$response.header.Set-Cookie[0]", extracted, None
        )
        assert result == "session=abc"

    def test_header_with_index_fallback_to_list(self, executor):
        """When indexed key missing, falls back to full header list at correct index."""
        # Only the full list key exists, not the indexed key
        extracted = {"header/set-cookie": ["session=abc", "theme=dark"]}
        result = Executor._resolve_link_expression(
            "$response.header.Set-Cookie[0]", extracted, None
        )
        assert result == "session=abc"

    def test_header_with_nonzero_index_fallback_to_list(self, executor):
        """Fallback to full header list uses the requested index, not always [0].

        Regression test: previously the fallback always returned value[0]
        regardless of the requested index, so $response.header.Set-Cookie[1]
        would incorrectly return the first cookie instead of the second.
        """
        extracted = {"header/set-cookie": ["session=abc", "theme=dark", "lang=en"]}
        # Request index 1 — should get "theme=dark", not "session=abc"
        result = Executor._resolve_link_expression(
            "$response.header.Set-Cookie[1]", extracted, None
        )
        assert result == "theme=dark"

        # Request index 2 — should get "lang=en"
        result = Executor._resolve_link_expression(
            "$response.header.Set-Cookie[2]", extracted, None
        )
        assert result == "lang=en"

    def test_header_with_out_of_bounds_index_fallback(self, executor):
        """Out-of-bounds index on fallback returns _MISSING."""
        extracted = {"header/set-cookie": ["session=abc"]}
        result = Executor._resolve_link_expression(
            "$response.header.Set-Cookie[5]", extracted, None
        )
        assert result is _MISSING

    def test_header_missing(self, executor):
        """Returns _MISSING when header not in extracted variables."""
        extracted = {"header/other": ["value"]}
        result = Executor._resolve_link_expression(
            "$response.header.Location", extracted, None
        )
        assert result is _MISSING

    def test_header_single_string_value(self, executor):
        """Non-list header value is returned as-is."""
        extracted = {"header/location": "/resources/abc-123"}
        result = Executor._resolve_link_expression(
            "$response.header.Location", extracted, None
        )
        assert result == "/resources/abc-123"


# =============================================================================
# _resolve_link_expression: $request.path.X expressions
# =============================================================================


class TestResolveRequestPathExpression:
    """Test $request.path.X resolution."""

    def test_request_path_param(self, executor):
        """$request.path.widget_id resolves from prior request's path parameters."""
        prev_request = RequestCase(
            case_id="prev-1",
            operation_id="createWidget",
            method="POST",
            path_template="/widgets",
            rendered_path="/widgets",
            path_parameters={"widget_id": "abc-123"},
        )
        result = Executor._resolve_link_expression(
            "$request.path.widget_id", {}, prev_request
        )
        assert result == "abc-123"

    def test_request_path_param_missing(self, executor):
        """Returns _MISSING when path parameter not in prior request."""
        prev_request = RequestCase(
            case_id="prev-1",
            operation_id="createWidget",
            method="POST",
            path_template="/widgets",
            rendered_path="/widgets",
            path_parameters={},
        )
        result = Executor._resolve_link_expression(
            "$request.path.widget_id", {}, prev_request
        )
        assert result is _MISSING

    def test_request_path_no_prev_request(self, executor):
        """Returns _MISSING when no prior request available."""
        result = Executor._resolve_link_expression(
            "$request.path.widget_id", {}, None
        )
        assert result is _MISSING


# =============================================================================
# _resolve_link_expression: $request.header.X expressions
# =============================================================================


class TestResolveRequestHeaderExpression:
    """Test $request.header.X resolution."""

    def test_request_header(self, executor):
        """$request.header.Authorization resolves from prior request's headers."""
        prev_request = RequestCase(
            case_id="prev-1",
            operation_id="createWidget",
            method="POST",
            path_template="/widgets",
            rendered_path="/widgets",
            headers={"authorization": ["Bearer token123"]},
        )
        result = Executor._resolve_link_expression(
            "$request.header.Authorization", {}, prev_request
        )
        assert result == "Bearer token123"

    def test_request_header_no_prev_request(self, executor):
        """Returns _MISSING when no prior request available."""
        result = Executor._resolve_link_expression(
            "$request.header.Authorization", {}, None
        )
        assert result is _MISSING


# =============================================================================
# _resolve_link_expression: Unrecognized expressions
# =============================================================================


class TestResolveUnrecognizedExpression:
    """Test handling of unknown expression patterns."""

    def test_unknown_expression(self, executor):
        """Unrecognized expressions return _MISSING."""
        result = Executor._resolve_link_expression(
            "$url", {"id": "abc"}, None
        )
        assert result is _MISSING

    def test_malformed_body_expression(self, executor):
        """Malformed body expression (no #/) returns _MISSING."""
        result = Executor._resolve_link_expression(
            "$response.body/id", {"id": "abc"}, None
        )
        assert result is _MISSING


# =============================================================================
# _resolve_link_overrides
# =============================================================================


class TestResolveLinkOverrides:
    """Test resolving all parameters from a link_source."""

    def test_single_body_parameter(self, executor):
        """Resolves a single body parameter from link_source."""
        link_source = {
            "parameters": {"widget_id": "$response.body#/id"},
        }
        extracted = {"id": "real-widget-id"}
        result = executor._resolve_link_overrides(link_source, extracted, None)
        assert result == {"widget_id": "real-widget-id"}

    def test_multiple_parameters(self, executor):
        """Resolves multiple parameters from link_source."""
        link_source = {
            "parameters": {
                "widget_id": "$response.body#/id",
                "resource_path": "$response.header.Location",
            },
        }
        extracted = {
            "id": "widget-123",
            "header/location": ["/widgets/widget-123"],
        }
        result = executor._resolve_link_overrides(link_source, extracted, None)
        assert result == {
            "widget_id": "widget-123",
            "resource_path": "/widgets/widget-123",
        }

    def test_partial_resolution(self, executor):
        """Only resolved parameters are included in result."""
        link_source = {
            "parameters": {
                "widget_id": "$response.body#/id",
                "missing_param": "$response.body#/nonexistent",
            },
        }
        extracted = {"id": "widget-123"}
        result = executor._resolve_link_overrides(link_source, extracted, None)
        # Only widget_id resolves, missing_param is excluded
        assert result == {"widget_id": "widget-123"}

    def test_no_parameters_key(self, executor):
        """Returns empty dict when link_source has no parameters."""
        link_source = {"field": "$response.body#/id"}  # Old format
        extracted = {"id": "widget-123"}
        result = executor._resolve_link_overrides(link_source, extracted, None)
        assert result == {}

    def test_all_fail_returns_empty(self, executor):
        """Returns empty dict when no parameters can be resolved."""
        link_source = {
            "parameters": {"widget_id": "$response.body#/id"},
        }
        extracted = {}  # No variables extracted (source step failed)
        result = executor._resolve_link_overrides(link_source, extracted, None)
        assert result == {}

    def test_null_value_included_in_overrides(self, executor):
        """JSON null values are included in overrides (not treated as unresolved).

        Regression test: null values were previously conflated with "not found"
        because _resolve_link_expression returned None for both cases. Now
        _MISSING is used for "not found", and None represents legitimate null.
        """
        link_source = {
            "parameters": {"widget_id": "$response.body#/id"},
        }
        extracted = {"id": None}  # API returned {"id": null}
        result = executor._resolve_link_overrides(link_source, extracted, None)
        assert result == {"widget_id": None}


# =============================================================================
# _apply_link_overrides
# =============================================================================


class TestApplyLinkOverrides:
    """Test applying resolved link values to request templates."""

    def test_override_path_parameter(self, executor):
        """Path parameter value is overridden with resolved link value."""
        template = RequestCase(
            case_id="test-1",
            operation_id="getWidget",
            method="GET",
            path_template="/widgets/{widget_id}",
            path_parameters={"widget_id": "fuzz-generated-uuid"},
            rendered_path="/widgets/fuzz-generated-uuid",
        )
        overrides = {"widget_id": "real-widget-id"}
        result = executor._apply_link_overrides(template, overrides)

        assert result.path_parameters["widget_id"] == "real-widget-id"
        assert result.rendered_path == "/widgets/real-widget-id"

    def test_strip_leading_slash_from_path_param(self, executor):
        """Leading slashes are stripped from path parameter values.

        Location headers often return paths like "/resourceId" — the leading
        slash is the URL separator, not part of the value. Without stripping,
        the rendered path would be "/widgets//resourceId" (double slash).
        """
        template = RequestCase(
            case_id="test-1",
            operation_id="getWidget",
            method="GET",
            path_template="/widgets/{widget_id}",
            path_parameters={"widget_id": "fuzz-generated-uuid"},
            rendered_path="/widgets/fuzz-generated-uuid",
        )
        overrides = {"widget_id": "/real-widget-id"}
        result = executor._apply_link_overrides(template, overrides)

        assert result.path_parameters["widget_id"] == "real-widget-id"
        assert result.rendered_path == "/widgets/real-widget-id"

    def test_override_query_parameter(self, executor):
        """Query parameter value is overridden with resolved link value."""
        template = RequestCase(
            case_id="test-1",
            operation_id="searchWidgets",
            method="GET",
            path_template="/widgets",
            rendered_path="/widgets",
            query={"status_url": ["fuzz-value"]},
        )
        overrides = {"status_url": "http://example.com/status/123"}
        result = executor._apply_link_overrides(template, overrides)

        assert result.query["status_url"] == ["http://example.com/status/123"]

    def test_no_overrides_returns_template(self, executor):
        """Empty overrides returns the original template unchanged."""
        template = RequestCase(
            case_id="test-1",
            operation_id="getWidget",
            method="GET",
            path_template="/widgets/{widget_id}",
            path_parameters={"widget_id": "fuzz-uuid"},
            rendered_path="/widgets/fuzz-uuid",
        )
        result = executor._apply_link_overrides(template, {})
        # Should return the same template object
        assert result is template

    def test_override_does_not_mutate_original(self, executor):
        """Override creates a new RequestCase, does not mutate the template."""
        template = RequestCase(
            case_id="test-1",
            operation_id="getWidget",
            method="GET",
            path_template="/widgets/{widget_id}",
            path_parameters={"widget_id": "fuzz-uuid"},
            rendered_path="/widgets/fuzz-uuid",
        )
        overrides = {"widget_id": "new-value"}
        result = executor._apply_link_overrides(template, overrides)

        # Original unchanged
        assert template.path_parameters["widget_id"] == "fuzz-uuid"
        # New value applied
        assert result.path_parameters["widget_id"] == "new-value"

    def test_list_value_uses_first_element(self, executor):
        """List values (from headers) use first element as string."""
        template = RequestCase(
            case_id="test-1",
            operation_id="getWidget",
            method="GET",
            path_template="/widgets/{widget_id}",
            path_parameters={"widget_id": "fuzz-uuid"},
            rendered_path="/widgets/fuzz-uuid",
        )
        overrides = {"widget_id": ["/widgets/abc-123"]}
        result = executor._apply_link_overrides(template, overrides)

        # First element, with leading slash stripped
        assert result.path_parameters["widget_id"] == "widgets/abc-123"


# =============================================================================
# End-to-end: link resolution in execute_chain
# =============================================================================


def _make_two_step_chain() -> ChainCase:
    """Helper: create a 2-step chain (createWidget → getWidget via body#/id)."""
    step0_template = RequestCase(
        case_id="step-0",
        operation_id="createWidget",
        method="POST",
        path_template="/widgets",
        rendered_path="/widgets",
    )
    step1_template = RequestCase(
        case_id="step-1",
        operation_id="getWidget",
        method="GET",
        path_template="/widgets/{widget_id}",
        path_parameters={"widget_id": "fuzz-uuid"},
        rendered_path="/widgets/fuzz-uuid",
    )
    return ChainCase(
        chain_id="test-chain",
        steps=[
            ChainStep(step_index=0, request_template=step0_template),
            ChainStep(
                step_index=1,
                request_template=step1_template,
                link_source={
                    "parameters": {"widget_id": "$response.body#/id"},
                    "source_operation": "createWidget",
                },
            ),
        ],
    )


class TestChainLinkResolution:
    """Test that execute_chain correctly resolves links.

    Uses a controlled setup to verify the full resolution path:
    link_source.parameters → _resolve_link_overrides → _apply_link_overrides.
    """

    def test_chain_breaks_when_both_targets_fail_resolution(self, executor):
        """Chain stops (via break) when link resolution fails for both targets.

        Mocks _execute_single so both targets return error responses with no
        body. With no extracted variables, overrides are empty for both targets,
        triggering the break at step 1. Only step 0 should be in the execution.
        """
        chain = _make_two_step_chain()

        error_response = ResponseCase(
            status_code=500,
            headers={},
            body=None,
            elapsed_ms=10.0,
        )

        with patch.object(executor, '_execute_single', return_value=error_response):
            exec_a, exec_b = executor.execute_chain(chain)

        # Step 0 executed, step 1 skipped by break
        assert len(exec_a.steps) == 1, (
            f"Expected 1 step (break should skip step 1), got {len(exec_a.steps)}"
        )
        assert len(exec_b.steps) == 1
        assert exec_a.steps[0].step_index == 0
        assert exec_b.steps[0].step_index == 0

    def test_asymmetric_resolution_one_target_resolves(self, executor):
        """Chain continues when one target resolves and the other doesn't.

        Target A returns 201 with {"id": "real-123"}, target B returns 500
        with no body. The chain should NOT break (overrides_a is non-empty).
        Step 1 should use "real-123" for target A and keep "fuzz-uuid" for
        target B.
        """
        chain = _make_two_step_chain()

        success_response = ResponseCase(
            status_code=201,
            headers={},
            body={"id": "real-123"},
            elapsed_ms=10.0,
        )
        error_response = ResponseCase(
            status_code=500,
            headers={},
            body=None,
            elapsed_ms=10.0,
        )

        def mock_execute_single(client, request, timeout, target_name):
            if client is executor._client_a:
                return success_response
            return error_response

        with patch.object(executor, '_execute_single', side_effect=mock_execute_single):
            exec_a, exec_b = executor.execute_chain(chain)

        # Chain continues — both targets get step 0 and step 1
        assert len(exec_a.steps) == 2, (
            f"Expected 2 steps (chain should continue), got {len(exec_a.steps)}"
        )
        assert len(exec_b.steps) == 2

        # Target A's step 1: widget_id resolved to "real-123"
        assert "real-123" in exec_a.steps[1].request.rendered_path, (
            f"Expected 'real-123' in target A step 1 path, "
            f"got {exec_a.steps[1].request.rendered_path!r}"
        )

        # Target B's step 1: widget_id kept as fuzz value (no overrides)
        assert "fuzz-uuid" in exec_b.steps[1].request.rendered_path, (
            f"Expected 'fuzz-uuid' in target B step 1 path, "
            f"got {exec_b.steps[1].request.rendered_path!r}"
        )
