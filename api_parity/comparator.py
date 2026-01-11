"""Comparator - Compares API responses according to comparison rules.

The Comparator takes two ResponseCase objects and an OperationRules configuration,
compares them according to the rules, and produces a ComparisonResult. It delegates
CEL expression evaluation to the CELEvaluator.

See ARCHITECTURE.md "Comparator Component" for specifications.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from jsonpath_ng import parse as jsonpath_parse
from jsonpath_ng.exceptions import JsonPathParserError

from api_parity.cel_evaluator import CELEvaluator, CELEvaluationError
from api_parity.models import (
    BodyRules,
    ComparisonLibrary,
    ComparisonResult,
    ComponentResult,
    FieldDifference,
    FieldRule,
    MismatchType,
    OperationRules,
    PresenceMode,
    ResponseCase,
)


# =============================================================================
# Exceptions
# =============================================================================


class ComparatorError(Exception):
    """Base class for comparator errors."""


class JSONPathError(ComparatorError):
    """Invalid JSONPath expression."""


class ComparatorConfigError(ComparatorError):
    """Invalid comparison rule configuration."""


# =============================================================================
# Sentinel for Missing Fields
# =============================================================================


class _NotFound:
    """Sentinel for missing fields (distinct from JSON null).

    This is needed because None is a valid JSON value (null), but we need
    to distinguish between a field that exists with value null vs a field
    that doesn't exist at all.
    """

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "<NOT_FOUND>"


NOT_FOUND = _NotFound()


# =============================================================================
# Presence Check Result
# =============================================================================


@dataclass
class PresenceResult:
    """Result of a field presence check.

    Attributes:
        passed: Whether the presence check passed.
        a_present: Whether the field is present in response A.
        b_present: Whether the field is present in response B.
        skip_value_comparison: If True, skip value comparison (field absent in one/both).
    """

    passed: bool
    a_present: bool
    b_present: bool
    skip_value_comparison: bool


# =============================================================================
# Comparator
# =============================================================================


class Comparator:
    """Compares API responses according to comparison rules.

    Usage:
        with CELEvaluator() as cel:
            comparator = Comparator(cel, library)
            result = comparator.compare(response_a, response_b, rules)
            if not result.match:
                print(f"Mismatch: {result.summary}")
    """

    def __init__(
        self,
        cel_evaluator: CELEvaluator,
        comparison_library: ComparisonLibrary,
    ) -> None:
        """Initialize the Comparator.

        Args:
            cel_evaluator: CEL evaluator instance (caller owns lifecycle).
            comparison_library: Library of predefined comparisons.
        """
        self._cel = cel_evaluator
        self._library = comparison_library
        # Cache compiled JSONPath expressions for performance
        self._jsonpath_cache: dict[str, Any] = {}

    def compare(
        self,
        response_a: ResponseCase,
        response_b: ResponseCase,
        rules: OperationRules,
    ) -> ComparisonResult:
        """Compare two responses according to the given rules.

        Args:
            response_a: Response from target A.
            response_b: Response from target B.
            rules: Comparison rules to apply.

        Returns:
            ComparisonResult with match status and details.
        """
        details: dict[str, ComponentResult] = {}

        # Phase 1: Compare status codes
        status_result = self._compare_status_code(
            response_a.status_code,
            response_b.status_code,
            rules.status_code,
        )
        details["status_code"] = status_result

        if not status_result.match:
            return ComparisonResult(
                match=False,
                mismatch_type=MismatchType.STATUS_CODE,
                summary=self._format_status_summary(response_a.status_code, response_b.status_code),
                details=details,
            )

        # Phase 2: Compare headers
        header_result = self._compare_headers(
            response_a.headers,
            response_b.headers,
            rules.headers,
        )
        details["headers"] = header_result

        if not header_result.match:
            return ComparisonResult(
                match=False,
                mismatch_type=MismatchType.HEADERS,
                summary=self._format_header_summary(header_result.differences),
                details=details,
            )

        # Phase 3: Compare body (only for JSON responses)
        body_result = self._compare_body(
            response_a.body,
            response_b.body,
            rules.body,
        )
        details["body"] = body_result

        if not body_result.match:
            return ComparisonResult(
                match=False,
                mismatch_type=MismatchType.BODY,
                summary=self._format_body_summary(body_result.differences),
                details=details,
            )

        # All components match
        return ComparisonResult(
            match=True,
            mismatch_type=None,
            summary="Responses match",
            details=details,
        )

    def _compare_status_code(
        self,
        status_a: int,
        status_b: int,
        rule: FieldRule | None,
    ) -> ComponentResult:
        """Compare status codes.

        Args:
            status_a: Status code from target A.
            status_b: Status code from target B.
            rule: Optional rule; defaults to exact_match.

        Returns:
            ComponentResult for status code comparison.
        """
        if rule is None:
            # Default: exact match
            if status_a == status_b:
                return ComponentResult(match=True, differences=[])
            return ComponentResult(
                match=False,
                differences=[
                    FieldDifference(
                        path="status_code",
                        target_a=status_a,
                        target_b=status_b,
                        rule="exact_match",
                    )
                ],
            )

        # Use the provided rule
        try:
            result = self._evaluate_field_rule(status_a, status_b, rule)
        except (CELEvaluationError, ComparatorConfigError) as e:
            # Treat evaluation errors as mismatches with error info
            return ComponentResult(
                match=False,
                differences=[
                    FieldDifference(
                        path="status_code",
                        target_a=status_a,
                        target_b=status_b,
                        rule=f"error: {e}",
                    )
                ],
            )

        if result:
            return ComponentResult(match=True, differences=[])

        return ComponentResult(
            match=False,
            differences=[
                FieldDifference(
                    path="status_code",
                    target_a=status_a,
                    target_b=status_b,
                    rule=rule.predefined or "custom",
                )
            ],
        )

    def _compare_headers(
        self,
        headers_a: dict[str, list[str]],
        headers_b: dict[str, list[str]],
        header_rules: dict[str, FieldRule],
    ) -> ComponentResult:
        """Compare response headers.

        Args:
            headers_a: Headers from target A (lowercase keys, list values).
            headers_b: Headers from target B (lowercase keys, list values).
            header_rules: Header name -> FieldRule mapping.

        Returns:
            ComponentResult for header comparison.
        """
        differences: list[FieldDifference] = []

        for header_name, rule in header_rules.items():
            value_a = self._get_header_value(headers_a, header_name)
            value_b = self._get_header_value(headers_b, header_name)

            # Check presence
            presence_result = self._check_presence(value_a, value_b, rule.presence)

            if not presence_result.passed:
                differences.append(
                    FieldDifference(
                        path=f"headers.{header_name}",
                        target_a=value_a if value_a is not NOT_FOUND else "<missing>",
                        target_b=value_b if value_b is not NOT_FOUND else "<missing>",
                        rule=f"presence:{rule.presence.value}",
                    )
                )
                continue

            if presence_result.skip_value_comparison:
                continue

            # Both present and rule has a comparison (not just presence-only)
            if rule.predefined is None and rule.expr is None:
                # Presence-only rule, no value comparison needed
                continue

            try:
                result = self._evaluate_field_rule(value_a, value_b, rule)
            except (CELEvaluationError, ComparatorConfigError) as e:
                differences.append(
                    FieldDifference(
                        path=f"headers.{header_name}",
                        target_a=value_a,
                        target_b=value_b,
                        rule=f"error: {e}",
                    )
                )
                continue

            if not result:
                differences.append(
                    FieldDifference(
                        path=f"headers.{header_name}",
                        target_a=value_a,
                        target_b=value_b,
                        rule=rule.predefined or "custom",
                    )
                )

        return ComponentResult(match=len(differences) == 0, differences=differences)

    def _compare_body(
        self,
        body_a: Any,
        body_b: Any,
        body_rules: BodyRules | None,
    ) -> ComponentResult:
        """Compare response bodies.

        Args:
            body_a: Body from target A (parsed JSON or None).
            body_b: Body from target B (parsed JSON or None).
            body_rules: Body comparison rules.

        Returns:
            ComponentResult for body comparison.
        """
        # Handle None bodies (non-JSON responses)
        if body_a is None and body_b is None:
            return ComponentResult(match=True, differences=[])

        if body_a is None or body_b is None:
            # One has body, one doesn't - mismatch
            return ComponentResult(
                match=False,
                differences=[
                    FieldDifference(
                        path="$",
                        target_a="<no body>" if body_a is None else "<has body>",
                        target_b="<no body>" if body_b is None else "<has body>",
                        rule="body_presence",
                    )
                ],
            )

        # No rules specified - treat as match (no fields to compare)
        if body_rules is None or not body_rules.field_rules:
            return ComponentResult(match=True, differences=[])

        differences: list[FieldDifference] = []

        for jsonpath, rule in body_rules.field_rules.items():
            path_differences = self._compare_jsonpath(body_a, body_b, jsonpath, rule)
            differences.extend(path_differences)

        return ComponentResult(match=len(differences) == 0, differences=differences)

    def _compare_jsonpath(
        self,
        body_a: Any,
        body_b: Any,
        jsonpath: str,
        rule: FieldRule,
    ) -> list[FieldDifference]:
        """Compare values at a JSONPath location.

        Handles wildcards by expanding the path and comparing paired values.

        Args:
            body_a: Body from target A.
            body_b: Body from target B.
            jsonpath: JSONPath expression.
            rule: Field comparison rule.

        Returns:
            List of FieldDifference for any mismatches.
        """
        differences: list[FieldDifference] = []

        try:
            matches_a = self._expand_jsonpath(body_a, jsonpath)
            matches_b = self._expand_jsonpath(body_b, jsonpath)
        except JSONPathError as e:
            # Invalid JSONPath - treat as error
            return [
                FieldDifference(
                    path=jsonpath,
                    target_a="<error>",
                    target_b="<error>",
                    rule=f"jsonpath_error: {e}",
                )
            ]

        # Detect multi-match paths by checking actual match counts
        # This handles all wildcards: [*], .., [?()], [0:5], [0,1,2], etc.
        is_multi_match = len(matches_a) > 1 or len(matches_b) > 1

        if not is_multi_match:
            # Single-value path - extract the value (or NOT_FOUND if no match)
            value_a = matches_a[0][1] if matches_a else NOT_FOUND
            value_b = matches_b[0][1] if matches_b else NOT_FOUND

            diff = self._compare_single_field(jsonpath, value_a, value_b, rule)
            if diff:
                differences.append(diff)
        else:
            # Multi-match path - compare matched sets
            # First check match count parity
            if len(matches_a) != len(matches_b):
                differences.append(
                    FieldDifference(
                        path=jsonpath,
                        target_a=f"<{len(matches_a)} matches>",
                        target_b=f"<{len(matches_b)} matches>",
                        rule="wildcard_count_mismatch",
                    )
                )
            else:
                # Compare paired by index
                for (path_a, value_a), (path_b, value_b) in zip(matches_a, matches_b):
                    # Use the concrete path from target A for reporting
                    diff = self._compare_single_field(path_a, value_a, value_b, rule)
                    if diff:
                        differences.append(diff)

        return differences

    def _compare_single_field(
        self,
        path: str,
        value_a: Any,
        value_b: Any,
        rule: FieldRule,
    ) -> FieldDifference | None:
        """Compare a single field value pair.

        Args:
            path: Path for error reporting.
            value_a: Value from target A (may be NOT_FOUND).
            value_b: Value from target B (may be NOT_FOUND).
            rule: Comparison rule.

        Returns:
            FieldDifference if mismatch, None if match.
        """
        # Check presence
        presence_result = self._check_presence(value_a, value_b, rule.presence)

        if not presence_result.passed:
            return FieldDifference(
                path=path,
                target_a=value_a if value_a is not NOT_FOUND else "<missing>",
                target_b=value_b if value_b is not NOT_FOUND else "<missing>",
                rule=f"presence:{rule.presence.value}",
            )

        if presence_result.skip_value_comparison:
            return None

        # Both present - check for value comparison rule
        if rule.predefined is None and rule.expr is None:
            # Presence-only rule
            return None

        try:
            result = self._evaluate_field_rule(value_a, value_b, rule)
        except (CELEvaluationError, ComparatorConfigError) as e:
            return FieldDifference(
                path=path,
                target_a=value_a,
                target_b=value_b,
                rule=f"error: {e}",
            )

        if not result:
            return FieldDifference(
                path=path,
                target_a=value_a,
                target_b=value_b,
                rule=rule.predefined or "custom",
            )

        return None

    def _check_presence(
        self,
        value_a: Any,
        value_b: Any,
        presence: PresenceMode,
    ) -> PresenceResult:
        """Check if field presence satisfies the presence mode.

        Args:
            value_a: Value from target A (NOT_FOUND if missing).
            value_b: Value from target B (NOT_FOUND if missing).
            presence: Required presence mode.

        Returns:
            PresenceResult with check outcome.
        """
        a_present = value_a is not NOT_FOUND
        b_present = value_b is not NOT_FOUND

        if presence == PresenceMode.PARITY:
            # Both present or both absent
            if a_present == b_present:
                return PresenceResult(
                    passed=True,
                    a_present=a_present,
                    b_present=b_present,
                    skip_value_comparison=not (a_present and b_present),
                )
            return PresenceResult(
                passed=False,
                a_present=a_present,
                b_present=b_present,
                skip_value_comparison=True,
            )

        if presence == PresenceMode.REQUIRED:
            # Both must be present
            if a_present and b_present:
                return PresenceResult(
                    passed=True,
                    a_present=True,
                    b_present=True,
                    skip_value_comparison=False,
                )
            return PresenceResult(
                passed=False,
                a_present=a_present,
                b_present=b_present,
                skip_value_comparison=True,
            )

        if presence == PresenceMode.FORBIDDEN:
            # Both must be absent
            if not a_present and not b_present:
                return PresenceResult(
                    passed=True,
                    a_present=False,
                    b_present=False,
                    skip_value_comparison=True,
                )
            return PresenceResult(
                passed=False,
                a_present=a_present,
                b_present=b_present,
                skip_value_comparison=True,
            )

        if presence == PresenceMode.OPTIONAL:
            # Always passes; compare only if both present
            return PresenceResult(
                passed=True,
                a_present=a_present,
                b_present=b_present,
                skip_value_comparison=not (a_present and b_present),
            )

        # Should never reach here, but be defensive
        raise ComparatorConfigError(f"Unknown presence mode: {presence}")

    def _evaluate_field_rule(
        self,
        value_a: Any,
        value_b: Any,
        rule: FieldRule,
    ) -> bool:
        """Evaluate a field comparison rule.

        Args:
            value_a: Value from target A.
            value_b: Value from target B.
            rule: The field rule to evaluate.

        Returns:
            True if comparison passes, False otherwise.

        Raises:
            ComparatorConfigError: If rule configuration is invalid.
            CELEvaluationError: If CEL evaluation fails.
        """
        if rule.expr is not None:
            # Custom CEL expression
            expr = rule.expr
        elif rule.predefined is not None:
            # Expand predefined to CEL expression
            expr = self._expand_predefined(rule)
        else:
            # No comparison specified (presence-only) - treat as pass
            return True

        return self._cel.evaluate(expr, {"a": value_a, "b": value_b})

    def _expand_predefined(self, rule: FieldRule) -> str:
        """Expand a predefined rule to its CEL expression.

        Args:
            rule: FieldRule with predefined set.

        Returns:
            The expanded CEL expression.

        Raises:
            ComparatorConfigError: If predefined not found or params missing.
        """
        if rule.predefined not in self._library.predefined:
            raise ComparatorConfigError(f"Unknown predefined: {rule.predefined}")

        predef = self._library.predefined[rule.predefined]
        expr = predef.expr

        # Substitute each required parameter
        for param in predef.params:
            value = getattr(rule, param, None)
            if value is None:
                raise ComparatorConfigError(
                    f"Missing required parameter '{param}' for predefined '{rule.predefined}'"
                )

            # Handle string parameters - need quoting and escaping
            if isinstance(value, str):
                escaped = value.replace("\\", "\\\\").replace('"', '\\"')
                expr = expr.replace(param, f'"{escaped}"')
            else:
                expr = expr.replace(param, str(value))

        return expr

    def _expand_jsonpath(
        self,
        body: Any,
        path: str,
    ) -> list[tuple[str, Any]]:
        """Expand a JSONPath against a body.

        Args:
            body: The JSON body to search.
            path: JSONPath expression.

        Returns:
            List of (concrete_path, value) tuples for all matches.

        Raises:
            JSONPathError: If path is syntactically invalid.
        """
        # Use cache for compiled paths
        if path not in self._jsonpath_cache:
            try:
                self._jsonpath_cache[path] = jsonpath_parse(path)
            except JsonPathParserError as e:
                raise JSONPathError(f"Invalid JSONPath '{path}': {e}") from e

        compiled = self._jsonpath_cache[path]
        matches = compiled.find(body)

        return [(str(match.full_path), match.value) for match in matches]

    def _get_header_value(
        self,
        headers: dict[str, list[str]],
        name: str,
    ) -> Any:
        """Get a header value (case-insensitive).

        Multi-value headers return only the first value per design.

        Args:
            headers: Response headers dict (lowercase keys, list values).
            name: Header name to find.

        Returns:
            First header value, or NOT_FOUND if not present.
        """
        # Headers are stored with lowercase keys
        name_lower = name.lower()
        for key, values in headers.items():
            if key.lower() == name_lower and values:
                return values[0]
        return NOT_FOUND

    def _format_status_summary(self, status_a: int, status_b: int) -> str:
        """Format a summary for status code mismatch."""
        return f"Status code mismatch: {status_a} vs {status_b}"

    def _format_header_summary(self, differences: list[FieldDifference]) -> str:
        """Format a summary for header mismatches."""
        if len(differences) == 1:
            diff = differences[0]
            return f"Header mismatch: {diff.path}"
        return f"Header mismatches: {len(differences)} differences"

    def _format_body_summary(self, differences: list[FieldDifference]) -> str:
        """Format a summary for body mismatches."""
        if len(differences) == 1:
            diff = differences[0]
            return f"Body mismatch at {diff.path}"
        return f"Body mismatches: {len(differences)} differences"
