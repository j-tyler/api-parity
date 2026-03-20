"""Unit tests for mismatch_classifier module.

Tests mismatch_dedup_key(), is_same_mismatch(), and is_same_chain_mismatch().
These are pure functions operating on dicts and ComparisonResult objects.
"""

import pytest

from api_parity.mismatch_classifier import (
    is_same_chain_mismatch,
    is_same_mismatch,
    mismatch_dedup_key,
)
from api_parity.models import ComparisonResult, ComponentResult, FieldDifference, MismatchType


# =============================================================================
# Helpers
# =============================================================================


def _make_comparison_result(
    *,
    match: bool = False,
    mismatch_type: MismatchType | None = None,
    body_differences: list[FieldDifference] | None = None,
    header_differences: list[FieldDifference] | None = None,
) -> ComparisonResult:
    """Build a ComparisonResult for testing.

    Only populates the details relevant to the mismatch_type being tested.
    """
    body_result = ComponentResult(
        match=body_differences is None,
        differences=body_differences or [],
    )
    header_result = ComponentResult(
        match=header_differences is None,
        differences=header_differences or [],
    )
    status_result = ComponentResult(
        match=mismatch_type != MismatchType.STATUS_CODE,
        differences=(
            [FieldDifference(path="status_code", target_a=200, target_b=500, rule="equals")]
            if mismatch_type == MismatchType.STATUS_CODE
            else []
        ),
    )
    return ComparisonResult(
        match=match,
        mismatch_type=mismatch_type,
        summary="test",
        details={
            "status_code": status_result,
            "headers": header_result,
            "body": body_result,
        },
    )


def _field_diff(path: str) -> FieldDifference:
    """Shorthand for a FieldDifference with only the path relevant."""
    return FieldDifference(path=path, target_a="a", target_b="b", rule="exact_match")


# =============================================================================
# Tests: mismatch_dedup_key
# =============================================================================


class TestMismatchDedupKey:
    def test_body_mismatch_same_paths_same_key(self):
        """Two body mismatches at same JSONPath paths produce identical keys."""
        diff1 = {
            "mismatch_type": "body",
            "details": {"body": {"differences": [{"path": "$.id"}, {"path": "$.name"}]}},
        }
        diff2 = {
            "mismatch_type": "body",
            "details": {"body": {"differences": [{"path": "$.name"}, {"path": "$.id"}]}},
        }
        assert mismatch_dedup_key("createWidget", diff1) == mismatch_dedup_key("createWidget", diff2)

    def test_body_mismatch_different_paths_different_key(self):
        """Body mismatches at different JSONPath paths produce different keys."""
        diff1 = {"mismatch_type": "body", "details": {"body": {"differences": [{"path": "$.id"}]}}}
        diff2 = {"mismatch_type": "body", "details": {"body": {"differences": [{"path": "$.name"}]}}}
        assert mismatch_dedup_key("createWidget", diff1) != mismatch_dedup_key("createWidget", diff2)

    def test_different_operation_id_different_key(self):
        """Same mismatch type but different operation_id produces different keys."""
        diff = {"mismatch_type": "status_code", "details": {}}
        assert mismatch_dedup_key("createWidget", diff) != mismatch_dedup_key("getWidget", diff)

    def test_status_code_key_stable(self):
        """Status code key is deterministic across calls."""
        diff = {"mismatch_type": "status_code", "details": {}}
        assert mismatch_dedup_key("op1", diff) == mismatch_dedup_key("op1", diff)

    def test_header_mismatch_same_names_same_key(self):
        """Header mismatches with same names (different order) produce identical keys."""
        diff1 = {
            "mismatch_type": "headers",
            "details": {
                "headers": {"differences": [{"path": "content-type"}, {"path": "x-request-id"}]}
            },
        }
        diff2 = {
            "mismatch_type": "headers",
            "details": {
                "headers": {"differences": [{"path": "x-request-id"}, {"path": "content-type"}]}
            },
        }
        assert mismatch_dedup_key("op1", diff1) == mismatch_dedup_key("op1", diff2)

    def test_header_mismatch_different_names_different_key(self):
        """Header mismatches with different names produce different keys."""
        diff1 = {
            "mismatch_type": "headers",
            "details": {"headers": {"differences": [{"path": "content-type"}]}},
        }
        diff2 = {
            "mismatch_type": "headers",
            "details": {"headers": {"differences": [{"path": "x-custom"}]}},
        }
        assert mismatch_dedup_key("op1", diff1) != mismatch_dedup_key("op1", diff2)

    def test_schema_violation_key(self):
        """Schema violation key is (operation_id, 'schema_violation')."""
        diff = {"mismatch_type": "schema_violation", "details": {}}
        key = mismatch_dedup_key("op1", diff)
        assert key == ("op1", "schema_violation")

    def test_chain_same_step_same_pattern_same_key(self):
        """Chain diffs at same step with same pattern produce identical keys."""
        diff1 = {
            "type": "chain",
            "mismatch_step": 1,
            "steps": [
                {"mismatch_type": None, "match": True, "details": {}},
                {"mismatch_type": "body", "details": {"body": {"differences": [{"path": "$.id"}]}}},
            ],
        }
        diff2 = {
            "type": "chain",
            "mismatch_step": 1,
            "steps": [
                {"mismatch_type": None, "match": True, "details": {}},
                {"mismatch_type": "body", "details": {"body": {"differences": [{"path": "$.id"}]}}},
            ],
        }
        assert mismatch_dedup_key("createWidget", diff1) == mismatch_dedup_key("createWidget", diff2)

    def test_chain_different_step_different_key(self):
        """Chain diffs at different steps produce different keys."""
        diff1 = {
            "type": "chain",
            "mismatch_step": 0,
            "steps": [
                {"mismatch_type": "body", "details": {"body": {"differences": [{"path": "$.id"}]}}},
            ],
        }
        diff2 = {
            "type": "chain",
            "mismatch_step": 1,
            "steps": [
                {"mismatch_type": None, "match": True, "details": {}},
                {"mismatch_type": "body", "details": {"body": {"differences": [{"path": "$.id"}]}}},
            ],
        }
        assert mismatch_dedup_key("createWidget", diff1) != mismatch_dedup_key("createWidget", diff2)


# =============================================================================
# Tests: is_same_mismatch
# =============================================================================


class TestIsSameMismatch:
    def test_same_body_paths_returns_true(self):
        """Body mismatches with same failing paths are the same."""
        original_diff = {
            "mismatch_type": "body",
            "details": {"body": {"differences": [{"path": "$.id"}, {"path": "$.name"}]}},
        }
        new_result = _make_comparison_result(
            mismatch_type=MismatchType.BODY,
            body_differences=[_field_diff("$.id"), _field_diff("$.name")],
        )
        assert is_same_mismatch(original_diff, new_result) is True

    def test_different_body_paths_returns_false(self):
        """Body mismatches with different failing paths are not the same."""
        original_diff = {
            "mismatch_type": "body",
            "details": {"body": {"differences": [{"path": "$.id"}]}},
        }
        new_result = _make_comparison_result(
            mismatch_type=MismatchType.BODY,
            body_differences=[_field_diff("$.name")],
        )
        assert is_same_mismatch(original_diff, new_result) is False

    def test_same_header_names_returns_true(self):
        """Header mismatches with same failing header names are the same."""
        original_diff = {
            "mismatch_type": "headers",
            "details": {"headers": {"differences": [{"path": "content-type"}]}},
        }
        new_result = _make_comparison_result(
            mismatch_type=MismatchType.HEADERS,
            header_differences=[_field_diff("content-type")],
        )
        assert is_same_mismatch(original_diff, new_result) is True

    def test_different_header_names_returns_false(self):
        """Header mismatches with different failing header names are not the same."""
        original_diff = {
            "mismatch_type": "headers",
            "details": {"headers": {"differences": [{"path": "content-type"}]}},
        }
        new_result = _make_comparison_result(
            mismatch_type=MismatchType.HEADERS,
            header_differences=[_field_diff("x-custom")],
        )
        assert is_same_mismatch(original_diff, new_result) is False

    def test_same_status_code_type_returns_true(self):
        """Status code mismatches with same type are the same (values ignored)."""
        original_diff = {"mismatch_type": "status_code", "details": {}}
        new_result = _make_comparison_result(mismatch_type=MismatchType.STATUS_CODE)
        assert is_same_mismatch(original_diff, new_result) is True

    def test_different_mismatch_type_returns_false(self):
        """Different mismatch types are never the same."""
        original_diff = {"mismatch_type": "status_code", "details": {}}
        new_result = _make_comparison_result(
            mismatch_type=MismatchType.BODY,
            body_differences=[_field_diff("$.id")],
        )
        assert is_same_mismatch(original_diff, new_result) is False


# =============================================================================
# Tests: is_same_chain_mismatch
# =============================================================================


class TestIsSameChainMismatch:
    def test_same_step_same_pattern_returns_true(self):
        """Chain mismatches at same step with same pattern are the same."""
        original_diff = {
            "mismatch_step": 1,
            "steps": [
                {"mismatch_type": None, "details": {}},
                {
                    "mismatch_type": "body",
                    "details": {"body": {"differences": [{"path": "$.id"}]}},
                },
            ],
        }
        # new_step_diffs has 2 entries: step 0 matched, step 1 mismatched
        step0 = _make_comparison_result(match=True)
        step1 = _make_comparison_result(
            mismatch_type=MismatchType.BODY,
            body_differences=[_field_diff("$.id")],
        )
        assert is_same_chain_mismatch(original_diff, [step0, step1]) is True

    def test_different_step_returns_false(self):
        """Chain mismatches at different steps are not the same."""
        original_diff = {
            "mismatch_step": 0,
            "steps": [
                {
                    "mismatch_type": "body",
                    "details": {"body": {"differences": [{"path": "$.id"}]}},
                },
            ],
        }
        # New mismatch at step 1 (list has 2 entries, mismatch_step = len-1 = 1)
        step0 = _make_comparison_result(match=True)
        step1 = _make_comparison_result(
            mismatch_type=MismatchType.BODY,
            body_differences=[_field_diff("$.id")],
        )
        assert is_same_chain_mismatch(original_diff, [step0, step1]) is False

    def test_original_has_more_steps_returns_false(self):
        """If original has more steps than new, they differ."""
        original_diff = {
            "mismatch_step": 2,
            "steps": [
                {"mismatch_type": None, "details": {}},
                {"mismatch_type": None, "details": {}},
                {
                    "mismatch_type": "body",
                    "details": {"body": {"differences": [{"path": "$.id"}]}},
                },
            ],
        }
        # New result only has 1 step (mismatch at step 0)
        step0 = _make_comparison_result(
            mismatch_type=MismatchType.BODY,
            body_differences=[_field_diff("$.id")],
        )
        assert is_same_chain_mismatch(original_diff, [step0]) is False
