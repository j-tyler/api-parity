"""Tests for api_parity.models.

Tests cover:
- Serialization round-trips for all models
- Validation logic for mutual exclusion and format constraints
- Edge cases (empty collections, None vs missing, unicode)
"""

import json

import pytest

from api_parity.models import (
    BodyRules,
    CELRequest,
    CELResponse,
    ChainCase,
    ChainExecution,
    ChainStep,
    ChainStepExecution,
    ComparisonLibrary,
    ComparisonResult,
    ComparisonRulesFile,
    ComponentResult,
    FieldDifference,
    FieldRule,
    MismatchMetadata,
    MismatchType,
    OperationRules,
    PredefinedComparison,
    PresenceMode,
    RateLimitConfig,
    RequestCase,
    ResponseCase,
    RuntimeConfig,
    SecretsConfig,
    StatelessExecution,
    TargetConfig,
    TargetInfo,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def sample_request_case() -> RequestCase:
    return RequestCase(
        case_id="test-123",
        operation_id="createItem",
        method="POST",
        path_template="/items/{id}",
        path_parameters={"id": "abc"},
        rendered_path="/items/abc",
        query={"limit": ["10"]},
        headers={"Content-Type": ["application/json"]},
        body={"name": "Test"},
    )


@pytest.fixture
def sample_response_case() -> ResponseCase:
    return ResponseCase(
        status_code=200,
        headers={"content-type": ["application/json"]},
        body={"id": "abc", "name": "Test"},
        elapsed_ms=42.5,
    )


# =============================================================================
# RequestCase Tests
# =============================================================================


class TestRequestCase:
    def test_serialization_roundtrip(self, sample_request_case: RequestCase):
        json_str = sample_request_case.model_dump_json()
        restored = RequestCase.model_validate_json(json_str)
        assert restored == sample_request_case

    def test_body_only(self):
        req = RequestCase(
            case_id="t",
            operation_id="op",
            method="GET",
            path_template="/",
            rendered_path="/",
            body={"key": "value"},
        )
        assert req.body == {"key": "value"}
        assert req.body_base64 is None

    def test_body_base64_only(self):
        req = RequestCase(
            case_id="t",
            operation_id="op",
            method="GET",
            path_template="/",
            rendered_path="/",
            body_base64="SGVsbG8gV29ybGQ=",
        )
        assert req.body is None
        assert req.body_base64 == "SGVsbG8gV29ybGQ="

    def test_body_and_body_base64_mutually_exclusive(self):
        with pytest.raises(ValueError, match="mutually exclusive"):
            RequestCase(
                case_id="t",
                operation_id="op",
                method="GET",
                path_template="/",
                rendered_path="/",
                body={"key": "value"},
                body_base64="SGVsbG8=",
            )

    def test_empty_collections(self):
        req = RequestCase(
            case_id="t",
            operation_id="op",
            method="GET",
            path_template="/",
            rendered_path="/",
        )
        assert req.path_parameters == {}
        assert req.query == {}
        assert req.headers == {}
        assert req.cookies == {}

    def test_unicode_in_body(self):
        req = RequestCase(
            case_id="t",
            operation_id="op",
            method="POST",
            path_template="/",
            rendered_path="/",
            body={"name": "日本語", "emoji": "🎉"},
        )
        json_str = req.model_dump_json()
        restored = RequestCase.model_validate_json(json_str)
        assert restored.body["name"] == "日本語"
        assert restored.body["emoji"] == "🎉"

    def test_extra_fields_forbidden(self):
        with pytest.raises(ValueError):
            RequestCase(
                case_id="t",
                operation_id="op",
                method="GET",
                path_template="/",
                rendered_path="/",
                unknown_field="value",
            )


# =============================================================================
# ResponseCase Tests
# =============================================================================


class TestResponseCase:
    def test_serialization_roundtrip(self, sample_response_case: ResponseCase):
        json_str = sample_response_case.model_dump_json()
        restored = ResponseCase.model_validate_json(json_str)
        assert restored == sample_response_case

    def test_body_and_body_base64_mutually_exclusive(self):
        with pytest.raises(ValueError, match="mutually exclusive"):
            ResponseCase(
                status_code=200,
                elapsed_ms=10,
                body={"key": "value"},
                body_base64="SGVsbG8=",
            )

    def test_default_http_version(self):
        resp = ResponseCase(status_code=200, elapsed_ms=10)
        assert resp.http_version == "1.1"


# =============================================================================
# FieldRule Tests
# =============================================================================


class TestFieldRule:
    def test_predefined_only(self):
        rule = FieldRule(predefined="exact_match")
        assert rule.predefined == "exact_match"
        assert rule.expr is None

    def test_expr_only(self):
        rule = FieldRule(expr="a == b")
        assert rule.expr == "a == b"
        assert rule.predefined is None

    def test_predefined_and_expr_mutually_exclusive(self):
        with pytest.raises(ValueError, match="cannot specify both"):
            FieldRule(predefined="exact_match", expr="a == b")

    def test_presence_only_valid(self):
        rule = FieldRule(presence=PresenceMode.REQUIRED)
        assert rule.presence == PresenceMode.REQUIRED
        assert rule.predefined is None
        assert rule.expr is None

    def test_forbidden_without_comparison(self):
        rule = FieldRule(presence=PresenceMode.FORBIDDEN)
        assert rule.presence == PresenceMode.FORBIDDEN

    def test_forbidden_with_predefined_rejected(self):
        with pytest.raises(ValueError, match="forbidden field"):
            FieldRule(presence=PresenceMode.FORBIDDEN, predefined="exact_match")

    def test_forbidden_with_expr_rejected(self):
        with pytest.raises(ValueError, match="forbidden field"):
            FieldRule(presence=PresenceMode.FORBIDDEN, expr="a == b")

    def test_seconds_accepts_float(self):
        rule = FieldRule(predefined="epoch_seconds_tolerance", seconds=0.5)
        assert rule.seconds == 0.5

    def test_seconds_accepts_int(self):
        rule = FieldRule(predefined="epoch_seconds_tolerance", seconds=5)
        assert rule.seconds == 5.0

    def test_millis_accepts_float(self):
        rule = FieldRule(predefined="epoch_millis_tolerance", millis=500.5)
        assert rule.millis == 500.5

    def test_with_tolerance_param(self):
        rule = FieldRule(predefined="numeric_tolerance", tolerance=0.01)
        assert rule.tolerance == 0.01

    def test_with_range_params(self):
        rule = FieldRule(predefined="both_in_range", min=0.0, max=100.0)
        assert rule.min == 0.0
        assert rule.max == 100.0

    def test_with_pattern_param(self):
        rule = FieldRule(predefined="both_match_regex", pattern=r"^\d+$")
        assert rule.pattern == r"^\d+$"

    def test_serialization_roundtrip(self):
        rule = FieldRule(
            presence=PresenceMode.REQUIRED,
            predefined="numeric_tolerance",
            tolerance=0.01,
        )
        json_str = rule.model_dump_json()
        restored = FieldRule.model_validate_json(json_str)
        assert restored == rule


# =============================================================================
# MismatchMetadata Tests
# =============================================================================


class TestMismatchMetadata:
    def test_valid_timestamp_basic(self):
        meta = MismatchMetadata(
            tool_version="0.1.0",
            timestamp="2026-01-11T12:00:00",
            target_a=TargetInfo(name="a", base_url="http://a"),
            target_b=TargetInfo(name="b", base_url="http://b"),
            comparison_rules_applied="default",
        )
        assert meta.timestamp == "2026-01-11T12:00:00"

    def test_valid_timestamp_with_fractional_seconds(self):
        meta = MismatchMetadata(
            tool_version="0.1.0",
            timestamp="2026-01-11T12:00:00.123456",
            target_a=TargetInfo(name="a", base_url="http://a"),
            target_b=TargetInfo(name="b", base_url="http://b"),
            comparison_rules_applied="default",
        )
        assert meta.timestamp == "2026-01-11T12:00:00.123456"

    def test_valid_timestamp_with_z_timezone(self):
        meta = MismatchMetadata(
            tool_version="0.1.0",
            timestamp="2026-01-11T12:00:00Z",
            target_a=TargetInfo(name="a", base_url="http://a"),
            target_b=TargetInfo(name="b", base_url="http://b"),
            comparison_rules_applied="default",
        )
        assert meta.timestamp == "2026-01-11T12:00:00Z"

    def test_valid_timestamp_with_offset_timezone(self):
        meta = MismatchMetadata(
            tool_version="0.1.0",
            timestamp="2026-01-11T12:00:00+05:30",
            target_a=TargetInfo(name="a", base_url="http://a"),
            target_b=TargetInfo(name="b", base_url="http://b"),
            comparison_rules_applied="default",
        )
        assert meta.timestamp == "2026-01-11T12:00:00+05:30"

    def test_invalid_timestamp_rejected(self):
        with pytest.raises(ValueError, match="ISO 8601"):
            MismatchMetadata(
                tool_version="0.1.0",
                timestamp="not-a-timestamp",
                target_a=TargetInfo(name="a", base_url="http://a"),
                target_b=TargetInfo(name="b", base_url="http://b"),
                comparison_rules_applied="default",
            )

    def test_date_only_rejected(self):
        with pytest.raises(ValueError, match="ISO 8601"):
            MismatchMetadata(
                tool_version="0.1.0",
                timestamp="2026-01-11",
                target_a=TargetInfo(name="a", base_url="http://a"),
                target_b=TargetInfo(name="b", base_url="http://b"),
                comparison_rules_applied="default",
            )

    def test_serialization_roundtrip(self):
        meta = MismatchMetadata(
            tool_version="0.1.0",
            timestamp="2026-01-11T12:00:00.123Z",
            seed=42,
            target_a=TargetInfo(name="prod", base_url="http://prod"),
            target_b=TargetInfo(name="staging", base_url="http://staging"),
            comparison_rules_applied="createUser",
        )
        json_str = meta.model_dump_json()
        restored = MismatchMetadata.model_validate_json(json_str)
        assert restored == meta


# =============================================================================
# Chain Models Tests
# =============================================================================


class TestChainModels:
    def test_chain_case_serialization(self, sample_request_case: RequestCase):
        chain = ChainCase(
            chain_id="chain-1",
            steps=[
                ChainStep(step_index=0, request_template=sample_request_case),
                ChainStep(
                    step_index=1,
                    request_template=sample_request_case,
                    link_source={"step": 0, "field": "$.id"},
                ),
            ],
        )
        json_str = chain.model_dump_json()
        restored = ChainCase.model_validate_json(json_str)
        assert len(restored.steps) == 2
        assert restored.steps[1].link_source == {"step": 0, "field": "$.id"}

    def test_chain_execution_serialization(
        self, sample_request_case: RequestCase, sample_response_case: ResponseCase
    ):
        execution = ChainExecution(
            steps=[
                ChainStepExecution(
                    step_index=0,
                    request=sample_request_case,
                    response=sample_response_case,
                    extracted={"id": "abc123"},
                ),
            ]
        )
        json_str = execution.model_dump_json()
        restored = ChainExecution.model_validate_json(json_str)
        assert restored.steps[0].extracted == {"id": "abc123"}


# =============================================================================
# Comparison Rules Tests
# =============================================================================


class TestComparisonRules:
    def test_comparison_rules_file_serialization(self):
        rules = ComparisonRulesFile(
            version="1",
            default_rules=OperationRules(
                status_code=FieldRule(predefined="exact_match"),
                headers={"content-type": FieldRule(predefined="exact_match")},
                body=BodyRules(
                    field_rules={
                        "$.id": FieldRule(
                            presence=PresenceMode.REQUIRED, predefined="uuid_format"
                        ),
                    }
                ),
            ),
            operation_rules={
                "createUser": OperationRules(
                    body=BodyRules(
                        field_rules={
                            "$.timestamp": FieldRule(predefined="ignore"),
                        }
                    )
                )
            },
        )
        json_str = rules.model_dump_json()
        restored = ComparisonRulesFile.model_validate_json(json_str)
        assert restored.version == "1"
        assert "createUser" in restored.operation_rules

    def test_comparison_library_serialization(self):
        library = ComparisonLibrary(
            library_version="1",
            description="Test library",
            predefined={
                "exact_match": PredefinedComparison(
                    description="Values must be exactly equal",
                    params=[],
                    expr="a == b",
                ),
                "numeric_tolerance": PredefinedComparison(
                    description="Within tolerance",
                    params=["tolerance"],
                    expr="(a - b) <= tolerance && (b - a) <= tolerance",
                ),
            },
        )
        json_str = library.model_dump_json()
        restored = ComparisonLibrary.model_validate_json(json_str)
        assert "exact_match" in restored.predefined
        assert restored.predefined["numeric_tolerance"].params == ["tolerance"]


# =============================================================================
# Comparison Result Tests
# =============================================================================


class TestComparisonResult:
    def test_match_result(self):
        result = ComparisonResult(
            match=True,
            summary="Responses match",
            details={
                "status_code": ComponentResult(match=True),
                "headers": ComponentResult(match=True),
                "body": ComponentResult(match=True),
            },
        )
        assert result.match is True
        assert result.mismatch_type is None

    def test_mismatch_result(self):
        result = ComparisonResult(
            match=False,
            mismatch_type=MismatchType.BODY,
            summary="Body field $.status differs",
            details={
                "status_code": ComponentResult(match=True),
                "headers": ComponentResult(match=True),
                "body": ComponentResult(
                    match=False,
                    differences=[
                        FieldDifference(
                            path="$.status",
                            target_a="active",
                            target_b="pending",
                            rule="exact_match",
                        )
                    ],
                ),
            },
        )
        assert result.match is False
        assert result.mismatch_type == MismatchType.BODY
        assert len(result.details["body"].differences) == 1

    def test_serialization_roundtrip(self):
        result = ComparisonResult(
            match=False,
            mismatch_type=MismatchType.HEADERS,
            summary="Header differs",
            details={
                "status_code": ComponentResult(match=True),
                "headers": ComponentResult(
                    match=False,
                    differences=[
                        FieldDifference(
                            path="x-request-id",
                            target_a="abc",
                            target_b="xyz",
                            rule="exact_match",
                        )
                    ],
                ),
                "body": ComponentResult(match=True),
            },
        )
        json_str = result.model_dump_json()
        restored = ComparisonResult.model_validate_json(json_str)
        assert restored == result


# =============================================================================
# Runtime Config Tests
# =============================================================================


class TestRuntimeConfig:
    def test_full_config_serialization(self):
        config = RuntimeConfig(
            targets={
                "prod": TargetConfig(
                    base_url="https://api.example.com",
                    headers={"Authorization": "Bearer ${TOKEN}"},
                ),
                "staging": TargetConfig(
                    base_url="https://staging.example.com",
                ),
            },
            comparison_rules="./rules.json",
            rate_limit=RateLimitConfig(requests_per_second=10.0),
            secrets=SecretsConfig(redact_fields=["$.password", "$.api_key"]),
        )
        json_str = config.model_dump_json()
        restored = RuntimeConfig.model_validate_json(json_str)
        assert len(restored.targets) == 2
        assert restored.rate_limit.requests_per_second == 10.0
        assert "$.password" in restored.secrets.redact_fields

    def test_minimal_config(self):
        config = RuntimeConfig(
            targets={"prod": TargetConfig(base_url="http://localhost")},
            comparison_rules="rules.json",
        )
        assert config.rate_limit is None
        assert config.secrets is None


# =============================================================================
# CEL IPC Models Tests
# =============================================================================


class TestCELModels:
    def test_cel_request_serialization(self):
        req = CELRequest(
            id="req-123",
            expr="a == b",
            data={"a": 1, "b": 1},
        )
        json_str = req.model_dump_json()
        restored = CELRequest.model_validate_json(json_str)
        assert restored == req

    def test_cel_response_success(self):
        resp = CELResponse(id="req-123", ok=True, result=True)
        assert resp.ok is True
        assert resp.result is True
        assert resp.error is None

    def test_cel_response_error(self):
        resp = CELResponse(id="req-123", ok=False, error="undefined variable")
        assert resp.ok is False
        assert resp.result is None
        assert resp.error == "undefined variable"


# =============================================================================
# StatelessExecution Tests
# =============================================================================


class TestStatelessExecution:
    def test_serialization_roundtrip(
        self, sample_request_case: RequestCase, sample_response_case: ResponseCase
    ):
        execution = StatelessExecution(
            request=sample_request_case,
            response=sample_response_case,
        )
        json_str = execution.model_dump_json()
        restored = StatelessExecution.model_validate_json(json_str)
        assert restored == execution
