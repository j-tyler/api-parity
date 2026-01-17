"""Shared fixtures for Comparator unit tests.

These tests mock the CELEvaluator to test the Comparator logic in isolation.
Integration tests with the real CEL runtime are in tests/integration/test_comparator_cel.py.
"""

import pytest
from unittest.mock import MagicMock

from api_parity.comparator import Comparator
from api_parity.models import ComparisonLibrary, PredefinedComparison


@pytest.fixture
def mock_cel():
    """Create a mock CEL evaluator."""
    cel = MagicMock()
    cel.evaluate = MagicMock(return_value=True)
    return cel


@pytest.fixture
def comparison_library():
    """Create a minimal comparison library for testing."""
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
def comparator(mock_cel, comparison_library):
    """Create a Comparator with mocked CEL evaluator."""
    return Comparator(mock_cel, comparison_library)
