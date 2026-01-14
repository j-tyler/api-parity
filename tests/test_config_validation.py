"""Tests for configuration cross-validation functions.

Tests cover:
- validate_comparison_rules: operationIds and predefined validation
- validate_cli_operation_ids: --exclude and --operation-timeout validation
"""

import pytest

from api_parity.config_loader import (
    ValidationResult,
    ValidationWarning,
    ValidationError,
    validate_comparison_rules,
    validate_cli_operation_ids,
)
from api_parity.models import (
    BodyRules,
    ComparisonLibrary,
    ComparisonRulesFile,
    FieldRule,
    OperationRules,
    PredefinedComparison,
)


@pytest.fixture
def sample_library() -> ComparisonLibrary:
    """Create a sample comparison library for testing."""
    return ComparisonLibrary(
        library_version="1.0",
        description="Test library",
        predefined={
            "exact_match": PredefinedComparison(
                description="Exact match",
                params=[],
                expr="a == b",
            ),
            "ignore": PredefinedComparison(
                description="Ignore field",
                params=[],
                expr="true",
            ),
            "numeric_tolerance": PredefinedComparison(
                description="Numeric tolerance",
                params=["tolerance"],
                expr="(a - b) <= tolerance && (b - a) <= tolerance",
            ),
            "uuid_format": PredefinedComparison(
                description="UUID format",
                params=[],
                expr="a.matches('...')",
            ),
            "string_contains": PredefinedComparison(
                description="Both strings contain substring",
                params=["substring"],
                expr="a.contains(substring) && b.contains(substring)",
            ),
        },
    )


@pytest.fixture
def spec_operation_ids() -> set[str]:
    """Sample operationIds from a spec."""
    return {"createWidget", "getWidget", "updateWidget", "deleteWidget", "listWidgets"}


class TestValidateComparisonRules:
    """Tests for validate_comparison_rules function."""

    def test_valid_rules_no_warnings(
        self, sample_library: ComparisonLibrary, spec_operation_ids: set[str]
    ) -> None:
        """Valid rules with known operationIds and predefined produce no warnings."""
        rules = ComparisonRulesFile(
            version="1",
            default_rules=OperationRules(
                status_code=FieldRule(predefined="exact_match"),
            ),
            operation_rules={
                "createWidget": OperationRules(
                    body=BodyRules(
                        field_rules={
                            "$.id": FieldRule(predefined="uuid_format"),
                        }
                    )
                ),
            },
        )

        result = validate_comparison_rules(rules, sample_library, spec_operation_ids)

        assert result.is_valid
        assert len(result.warnings) == 0
        assert len(result.errors) == 0

    def test_unknown_operation_id_warning(
        self, sample_library: ComparisonLibrary, spec_operation_ids: set[str]
    ) -> None:
        """Unknown operationId in operation_rules produces warning."""
        rules = ComparisonRulesFile(
            version="1",
            default_rules=OperationRules(),
            operation_rules={
                "nonExistentOperation": OperationRules(
                    status_code=FieldRule(predefined="exact_match"),
                ),
            },
        )

        result = validate_comparison_rules(rules, sample_library, spec_operation_ids)

        assert result.is_valid  # Warnings don't make it invalid
        assert len(result.warnings) == 1
        assert "nonExistentOperation" in result.warnings[0].message
        assert "not found in spec" in result.warnings[0].message

    def test_typo_in_operation_id_warning(
        self, sample_library: ComparisonLibrary, spec_operation_ids: set[str]
    ) -> None:
        """Typo in operationId produces warning (common LLM error)."""
        rules = ComparisonRulesFile(
            version="1",
            default_rules=OperationRules(),
            operation_rules={
                "createWidgett": OperationRules(  # typo: extra 't'
                    body=BodyRules(field_rules={"$.id": FieldRule(predefined="ignore")}),
                ),
            },
        )

        result = validate_comparison_rules(rules, sample_library, spec_operation_ids)

        assert result.is_valid
        assert len(result.warnings) == 1
        assert "createWidgett" in result.warnings[0].message

    def test_unknown_predefined_error(
        self, sample_library: ComparisonLibrary, spec_operation_ids: set[str]
    ) -> None:
        """Unknown predefined name produces error."""
        rules = ComparisonRulesFile(
            version="1",
            default_rules=OperationRules(
                status_code=FieldRule(predefined="exact"),  # typo: should be exact_match
            ),
            operation_rules={},
        )

        result = validate_comparison_rules(rules, sample_library, spec_operation_ids)

        assert not result.is_valid
        assert len(result.errors) == 1
        assert "exact" in result.errors[0].message
        assert "Unknown predefined" in result.errors[0].message

    def test_missing_required_parameter_error(
        self, sample_library: ComparisonLibrary, spec_operation_ids: set[str]
    ) -> None:
        """Missing required parameter for predefined produces error."""
        rules = ComparisonRulesFile(
            version="1",
            default_rules=OperationRules(
                body=BodyRules(
                    field_rules={
                        "$.price": FieldRule(predefined="numeric_tolerance"),  # missing tolerance param
                    }
                )
            ),
            operation_rules={},
        )

        result = validate_comparison_rules(rules, sample_library, spec_operation_ids)

        assert not result.is_valid
        assert len(result.errors) == 1
        assert "tolerance" in result.errors[0].message
        assert "numeric_tolerance" in result.errors[0].message

    def test_valid_predefined_with_parameter(
        self, sample_library: ComparisonLibrary, spec_operation_ids: set[str]
    ) -> None:
        """Predefined with required parameter provided is valid."""
        rules = ComparisonRulesFile(
            version="1",
            default_rules=OperationRules(
                body=BodyRules(
                    field_rules={
                        "$.price": FieldRule(predefined="numeric_tolerance", tolerance=0.01),
                    }
                )
            ),
            operation_rules={},
        )

        result = validate_comparison_rules(rules, sample_library, spec_operation_ids)

        assert result.is_valid
        assert len(result.errors) == 0

    def test_multiple_errors_reported(
        self, sample_library: ComparisonLibrary, spec_operation_ids: set[str]
    ) -> None:
        """Multiple errors are all reported."""
        rules = ComparisonRulesFile(
            version="1",
            default_rules=OperationRules(
                status_code=FieldRule(predefined="bad_predefined_1"),
            ),
            operation_rules={
                "createWidget": OperationRules(
                    body=BodyRules(
                        field_rules={
                            "$.id": FieldRule(predefined="bad_predefined_2"),
                        }
                    )
                ),
            },
        )

        result = validate_comparison_rules(rules, sample_library, spec_operation_ids)

        assert not result.is_valid
        assert len(result.errors) == 2

    def test_header_rules_validated(
        self, sample_library: ComparisonLibrary, spec_operation_ids: set[str]
    ) -> None:
        """Header rules are also validated."""
        rules = ComparisonRulesFile(
            version="1",
            default_rules=OperationRules(
                headers={
                    "content-type": FieldRule(predefined="unknown_header_rule"),
                }
            ),
            operation_rules={},
        )

        result = validate_comparison_rules(rules, sample_library, spec_operation_ids)

        assert not result.is_valid
        assert len(result.errors) == 1
        assert "headers.content-type" in result.errors[0].message


class TestValidateCliOperationIds:
    """Tests for validate_cli_operation_ids function."""

    def test_valid_exclude_no_warning(self, spec_operation_ids: set[str]) -> None:
        """Valid --exclude operationIds produce no warnings."""
        result = validate_cli_operation_ids(
            exclude_ops=["createWidget", "deleteWidget"],
            operation_timeouts={},
            spec_operation_ids=spec_operation_ids,
        )

        assert result.is_valid
        assert len(result.warnings) == 0

    def test_invalid_exclude_warning(self, spec_operation_ids: set[str]) -> None:
        """Invalid --exclude operationId produces warning."""
        result = validate_cli_operation_ids(
            exclude_ops=["nonExistent"],
            operation_timeouts={},
            spec_operation_ids=spec_operation_ids,
        )

        assert result.is_valid  # Warnings don't make it invalid
        assert len(result.warnings) == 1
        assert "nonExistent" in result.warnings[0].message
        assert "--exclude" in result.warnings[0].message

    def test_valid_operation_timeout_no_warning(self, spec_operation_ids: set[str]) -> None:
        """Valid --operation-timeout operationIds produce no warnings."""
        result = validate_cli_operation_ids(
            exclude_ops=[],
            operation_timeouts={"createWidget": 60.0, "updateWidget": 120.0},
            spec_operation_ids=spec_operation_ids,
        )

        assert result.is_valid
        assert len(result.warnings) == 0

    def test_invalid_operation_timeout_warning(self, spec_operation_ids: set[str]) -> None:
        """Invalid --operation-timeout operationId produces warning."""
        result = validate_cli_operation_ids(
            exclude_ops=[],
            operation_timeouts={"slowOperation": 120.0},
            spec_operation_ids=spec_operation_ids,
        )

        assert result.is_valid
        assert len(result.warnings) == 1
        assert "slowOperation" in result.warnings[0].message
        assert "--operation-timeout" in result.warnings[0].message

    def test_multiple_cli_warnings(self, spec_operation_ids: set[str]) -> None:
        """Multiple invalid CLI args produce multiple warnings."""
        result = validate_cli_operation_ids(
            exclude_ops=["badExclude1", "badExclude2"],
            operation_timeouts={"badTimeout": 60.0},
            spec_operation_ids=spec_operation_ids,
        )

        assert result.is_valid
        assert len(result.warnings) == 3


class TestValidationResult:
    """Tests for ValidationResult class."""

    def test_empty_result_is_valid(self) -> None:
        """Empty result is valid."""
        result = ValidationResult()
        assert result.is_valid

    def test_warnings_dont_invalidate(self) -> None:
        """Warnings alone don't make result invalid."""
        result = ValidationResult()
        result.add_warning("test", "This is a warning")
        assert result.is_valid

    def test_errors_invalidate(self) -> None:
        """Errors make result invalid."""
        result = ValidationResult()
        result.add_error("test", "This is an error")
        assert not result.is_valid

    def test_merge_combines_results(self) -> None:
        """Merge combines warnings and errors from both results."""
        result1 = ValidationResult()
        result1.add_warning("cat1", "warning1")
        result1.add_error("cat2", "error1")

        result2 = ValidationResult()
        result2.add_warning("cat3", "warning2")
        result2.add_error("cat4", "error2")

        result1.merge(result2)

        assert len(result1.warnings) == 2
        assert len(result1.errors) == 2
        assert not result1.is_valid
