"""Mismatch classification and deduplication.

Provides functions to determine if two mismatches represent the same failure
pattern, and to generate hashable dedup keys for grouping duplicate mismatches.

Pattern matching compares mismatch_type and failing paths, not values.
Values change between runs (timestamps, IDs), but the structural pattern
of "which fields fail" is what matters for deduplication.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from api_parity.models import ComparisonResult


def _extract_paths_from_step_diff(step_diff: dict) -> frozenset[str]:
    """Extract failing paths from a single step's diff data.

    Used for both stateless diffs and individual chain steps.
    Returns frozenset of path strings from body, binary_body, extra_fields,
    or header differences.

    The Comparator returns mismatch_type=BODY for three distinct sub-cases:
    - JSON body differences → details["body"]
    - Binary body differences → details["binary_body"]
    - Extra field differences → details["extra_fields"]
    Only one is populated per mismatch (early return in comparator), so we
    check all three to avoid collapsing distinct failures to frozenset().
    """
    mismatch_type = step_diff.get("mismatch_type")
    details = step_diff.get("details", {})

    if mismatch_type == "body":
        # Check all three body sub-cases. Comparator early-returns so only
        # one will have differences, but we check all for robustness.
        body = details.get("body", {})
        differences = body.get("differences", [])
        if differences:
            return frozenset(d.get("path", "") for d in differences)

        binary_body = details.get("binary_body", {})
        binary_differences = binary_body.get("differences", [])
        if binary_differences:
            # Prefix with "binary:" so binary paths never collide with JSON paths
            return frozenset(f"binary:{d.get('path', '')}" for d in binary_differences)

        extra_fields = details.get("extra_fields", {})
        extra_differences = extra_fields.get("differences", [])
        if extra_differences:
            # Prefix with "extra:" so extra-field paths never collide with JSON paths
            return frozenset(f"extra:{d.get('path', '')}" for d in extra_differences)

        return frozenset()

    if mismatch_type == "headers":
        headers = details.get("headers", {})
        differences = headers.get("differences", [])
        return frozenset(d.get("path", "") for d in differences)

    return frozenset()


def mismatch_dedup_key(operation_id: str, diff_data: dict) -> tuple:
    """Return a hashable key from diff.json data for grouping duplicates.

    Dedup key structure by mismatch type:
    - status_code: (operation_id, "status_code")
    - schema_violation: (operation_id, "schema_violation", frozenset(violation_paths))
    - headers: (operation_id, "headers", frozenset(header_names))
    - body: (operation_id, "body", frozenset(jsonpath_paths))
    - chain: ("chain", mismatch_step, operation_id, step_mismatch_type, frozenset(paths))
    """
    # Chain diffs have "type": "chain"
    if diff_data.get("type") == "chain":
        mismatch_step = diff_data.get("mismatch_step")
        steps = diff_data.get("steps", [])
        if mismatch_step is not None and mismatch_step < len(steps):
            step_diff = steps[mismatch_step]
            step_mismatch_type = step_diff.get("mismatch_type", "")
            paths = _extract_paths_from_step_diff(step_diff)
            return ("chain", mismatch_step, operation_id, step_mismatch_type, paths)
        return ("chain", mismatch_step, operation_id, "", frozenset())

    mismatch_type = diff_data.get("mismatch_type", "")

    if mismatch_type == "status_code":
        return (operation_id, "status_code")

    if mismatch_type == "schema_violation":
        # Extract per-field paths from schema differences so that distinct
        # schema violations on the same operation (e.g. missing $.id vs
        # unexpected $.debug) produce different dedup keys.
        details = diff_data.get("details", {})
        schema = details.get("schema", {})
        differences = schema.get("differences", [])
        if differences:
            paths = frozenset(d.get("path", "") for d in differences)
            return (operation_id, "schema_violation", paths)
        return (operation_id, "schema_violation", frozenset())

    if mismatch_type == "headers":
        paths = _extract_paths_from_step_diff(diff_data)
        return (operation_id, "headers", paths)

    if mismatch_type == "body":
        paths = _extract_paths_from_step_diff(diff_data)
        return (operation_id, "body", paths)

    # Fallback for unknown mismatch types
    return (operation_id, mismatch_type)


def is_same_mismatch(original_diff: dict, new_result: ComparisonResult) -> bool:
    """Determine if two mismatches are essentially the same failure pattern.

    Why pattern matching instead of exact value comparison:
    - Values change between runs (timestamps, IDs, etc.)
    - We care about "still failing at the same place" not "exact same failure"
    - DIFFERENT MISMATCH after rule changes is expected and useful to track

    Comparison strategy by mismatch_type:
    - status_code: Type match is sufficient (specific codes may vary legitimately)
    - headers: Same header names must fail (values ignored)
    - body: Same JSONPath fields must fail (values ignored)
    """
    # Get original mismatch type
    original_type = original_diff.get("mismatch_type")
    new_type = new_result.mismatch_type.value if new_result.mismatch_type else None

    if original_type != new_type:
        return False

    # For body mismatches, check if same paths failed
    if original_type == "body":
        original_details = original_diff.get("details", {})
        original_body = original_details.get("body", {})
        original_differences = original_body.get("differences", [])
        original_paths = {d.get("path") for d in original_differences}

        new_body = new_result.details.get("body")
        new_paths = {d.path for d in new_body.differences} if new_body else set()

        return original_paths == new_paths

    # For header mismatches, check if same header names failed
    if original_type == "headers":
        original_details = original_diff.get("details", {})
        original_headers = original_details.get("headers", {})
        original_differences = original_headers.get("differences", [])
        original_names = {d.get("path") for d in original_differences}

        new_headers = new_result.details.get("headers")
        new_names = {d.path for d in new_headers.differences} if new_headers else set()

        return original_names == new_names

    # For status_code mismatches, mismatch_type being the same is sufficient
    return True


def is_same_chain_mismatch(
    original_diff: dict, new_step_diffs: list[ComparisonResult]
) -> bool:
    """Determine if chain mismatches are essentially the same.

    Compares:
    - Same step number failed
    - Same mismatch type at that step
    """
    original_mismatch_step = original_diff.get("mismatch_step")
    new_mismatch_step = len(new_step_diffs) - 1 if new_step_diffs else None

    if original_mismatch_step != new_mismatch_step:
        return False

    # Get original step diff
    original_steps = original_diff.get("steps", [])
    if original_mismatch_step is None or original_mismatch_step >= len(original_steps):
        return False

    original_step_diff = original_steps[original_mismatch_step]

    # Compare the mismatch at that step
    if not new_step_diffs:
        return False

    new_step_diff = new_step_diffs[new_mismatch_step]
    return is_same_mismatch(original_step_diff, new_step_diff)
