"""Shared fixtures for Comparator unit tests.

These tests mock the CELEvaluator to test the Comparator logic in isolation.
Integration tests with the real CEL runtime are in tests/integration/test_comparator_cel.py.
"""

import pytest
from unittest.mock import MagicMock

from api_parity.comparator import Comparator
from api_parity.models import ComparisonLibrary, PredefinedComparison


@pytest.fixture
def mock_cel() -> MagicMock:
    """Create a mock CEL evaluator that returns True for all expressions.

    The mock returns True by default so tests can focus on Comparator logic
    (rule selection, path matching, mismatch reporting) without CEL evaluation.
    Override mock_cel.evaluate.return_value in individual tests to simulate
    CEL failures or specific return values.
    """
    cel = MagicMock()
    cel.evaluate = MagicMock(return_value=True)
    return cel


@pytest.fixture
def comparison_library() -> ComparisonLibrary:
    """Create a minimal comparison library for testing.

    Includes a representative subset of comparison types: exact match, ignore,
    numeric tolerance, regex, string prefix, and binary comparisons. This covers
    the main patterns (parameterless, single-param, type-specific) without
    duplicating the full production library.
    """
    return ComparisonLibrary(
        library_version="1",
        description="Test library",
        predefined={
            "exact_match": PredefinedComparison(
                description="Exact equality",
                params=[],
                expr="a == b",
            ),
            "ignore": PredefinedComparison(
                description="Always passes",
                params=[],
                expr="true",
            ),
            "numeric_tolerance": PredefinedComparison(
                description="Within tolerance",
                params=["tolerance"],
                expr="(a - b) <= tolerance && (b - a) <= tolerance",
            ),
            "both_match_regex": PredefinedComparison(
                description="Both match pattern",
                params=["pattern"],
                expr="a.matches(pattern) && b.matches(pattern)",
            ),
            "string_prefix": PredefinedComparison(
                description="First N chars match",
                params=["length"],
                expr="a.substring(0, length) == b.substring(0, length)",
            ),
            "string_length_match": PredefinedComparison(
                description="Strings have same length",
                params=[],
                expr="size(a) == size(b)",
            ),
            "binary_exact_match": PredefinedComparison(
                description="Binary content must be identical",
                params=[],
                expr="a == b",
            ),
            "binary_length_match": PredefinedComparison(
                description="Binary content has same length",
                params=[],
                expr="size(a) == size(b)",
            ),
            "binary_nonempty": PredefinedComparison(
                description="Both have non-empty binary content",
                params=[],
                expr="size(a) > 0 && size(b) > 0",
            ),
        },
    )


@pytest.fixture
def comparator(mock_cel: MagicMock, comparison_library: ComparisonLibrary) -> Comparator:
    """Create a Comparator with mocked CEL evaluator.

    Uses mock_cel (returns True by default) so tests verify Comparator behavior
    without real CEL evaluation. For tests that need actual CEL evaluation,
    use integration tests in tests/integration/test_comparator_cel.py instead.
    """
    return Comparator(mock_cel, comparison_library)
