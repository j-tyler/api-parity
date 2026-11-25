"""Comparator - Compares API responses according to comparison rules.

The Comparator takes two ResponseCase objects and an OperationRules configuration,
compares them according to the rules, and produces a ComparisonResult. It delegates
CEL expression evaluation to the CELEvaluator.

Optionally validates responses against OpenAPI schemas before comparison
(see "OpenAPI Spec as Field Authority" in ARCHITECTURE.md).

See ARCHITECTURE.md "Comparator Component" for specifications.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from jsonpath_ng import parse as jsonpath_parse
from jsonpath_ng.jsonpath import Child, DatumInContext, Fields, Index, JSONPath, Slice
from jsonpath_ng.exceptions import JsonPathLexerError, JsonPathParserError

from api_parity.cel_evaluator import CELEvaluator, CELEvaluationError, CELSubprocessError
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

from api_parity.schema_validator import FieldLocation, SchemaValidator, field_path


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


_EXACT_RULES = {
    "exact_match": "a == b",
    "both_null_or_equal": "(a == null && b == null) || (a != null && b != null && a == b)",
}


@dataclass
class _RuledLocations:
    selected: set[FieldLocation] = field(default_factory=set)
    ignored_containers: set[FieldLocation] = field(default_factory=set)


def _match_location(match: DatumInContext) -> FieldLocation | None:
    """Recover literal object keys and absolute array indices from a rule match."""
    segments = []
    while match.context is not None:
        selector, parent = match.path, match.context
        if isinstance(selector, Fields) and len(selector.fields) == 1:
            segments.append(selector.fields[0])
        elif isinstance(selector, Index) and isinstance(parent.value, list):
            index = selector.index
            segments.append(index if index >= 0 else len(parent.value) + index)
        else:
            return None
        match = parent
    return tuple(reversed(segments))


def _value_at(body: Any, location: FieldLocation) -> Any:
    for part in location:
        if isinstance(part, str) and isinstance(body, dict):
            body = body.get(part, NOT_FOUND)
        elif isinstance(part, int) and isinstance(body, list) and part < len(body):
            body = body[part]
        else:
            return NOT_FOUND
    return body


def _uncovered_equal(a: Any, b: Any, path: FieldLocation,
                     covered: set[FieldLocation], ancestors: set[FieldLocation]) -> bool:
    """Compare siblings of explicitly ruled fields inside an undeclared container."""
    if path in covered:
        return True
    if path in ancestors:
        if isinstance(a, dict) and isinstance(b, dict):
            return all(_uncovered_equal(a.get(k, NOT_FOUND), b.get(k, NOT_FOUND), (*path, k), covered, ancestors)
                       for k in a.keys() | b.keys())
        if isinstance(a, list) and isinstance(b, list):
            return len(a) == len(b) and all(
                _uncovered_equal(x, y, (*path, i), covered, ancestors) for i, (x, y) in enumerate(zip(a, b))
            )
    return _json_equal(a, b, mixed_as_double=False)


def _json_equal(left: Any, right: Any, *, mixed_as_double: bool) -> bool:
    """Compare JSON with one iterator per open container, independent of Python stack depth."""
    pending = [iter(((left, right),))]
    while pending:
        pair = next(pending[-1], None)
        if pair is None:
            pending.pop()
            continue
        a, b = pair
        if type(a) is not type(b):
            if {type(a), type(b)} != {int, float}:
                return False
            if mixed_as_double:
                try:
                    a, b = float(a), float(b)
                except OverflowError:
                    return False
        if isinstance(a, dict):
            if a.keys() != b.keys():
                return False
            pending.append(zip(a.values(), map(b.__getitem__, a)))
        elif isinstance(a, list):
            if len(a) != len(b):
                return False
            pending.append(zip(a, b))
        elif a != b:
            return False
    return True


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
            result = comparator.compare(response_a, response_b, rules, "createWidget")
            if not result.match:
                print(f"Mismatch: {result.summary}")

    With schema validation (OpenAPI Spec as Field Authority):
        from api_parity.schema_validator import SchemaValidator
        validator = SchemaValidator(spec_path)
        comparator = Comparator(cel, library, schema_validator=validator)
        result = comparator.compare(response_a, response_b, rules, "createWidget")
    """

    def __init__(
        self,
        cel_evaluator: CELEvaluator,
        comparison_library: ComparisonLibrary,
        schema_validator: "SchemaValidator | None" = None,
    ) -> None:
        """Initialize the Comparator.

        Args:
            cel_evaluator: CEL evaluator instance (caller owns lifecycle).
            comparison_library: Library of predefined comparisons.
            schema_validator: Optional schema validator for OpenAPI Spec as Field Authority.
        """
        self._cel = cel_evaluator
        self._library = comparison_library
        self._schema_validator = schema_validator
        # Cache compiled JSONPath expressions for performance
        self._jsonpath_cache: dict[str, Any] = {}

    def compare(
        self,
        response_a: ResponseCase,
        response_b: ResponseCase,
        rules: OperationRules,
        operation_id: str | None = None,
    ) -> ComparisonResult:
        """Compare two responses according to the given rules.

        Args:
            response_a: Response from target A.
            response_b: Response from target B.
            rules: Comparison rules to apply.
            operation_id: Optional operationId for schema validation lookup.

        Returns:
            ComparisonResult with match status and details.
        """
        details: dict[str, ComponentResult] = {}

        # Phase 0: Schema validation (if schema_validator is configured)
        # Validates both responses against OpenAPI spec before comparison
        extra_fields_a: list[FieldLocation] = []
        extra_fields_b: list[FieldLocation] = []

        if self._schema_validator is not None and operation_id is not None:
            schema_result, extra_fields_a, extra_fields_b = self._validate_schemas(
                response_a, response_b, operation_id
            )
            details["schema"] = schema_result

            if not schema_result.match:
                return ComparisonResult(
                    match=False,
                    mismatch_type=MismatchType.SCHEMA_VIOLATION,
                    summary=self._format_schema_summary(schema_result.differences),
                    details=details,
                )

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
        covered = _RuledLocations() if (extra_fields_a or extra_fields_b) and rules.body and rules.body.field_rules else None
        body_result = self._compare_body(
            response_a, response_b, rules.body, covered,
        )
        details["body"] = body_result

        if not body_result.match:
            return ComparisonResult(
                match=False,
                mismatch_type=MismatchType.BODY,
                summary=self._format_body_summary(body_result.differences),
                details=details,
            )

        # Phase 3b: Compare binary body (non-JSON responses)
        binary_result = self._compare_binary_body(
            response_a.body_base64,
            response_b.body_base64,
            rules.body.binary_rule if rules.body else None,
        )
        details["binary_body"] = binary_result

        if not binary_result.match:
            return ComparisonResult(
                match=False,
                mismatch_type=MismatchType.BODY,
                summary=self._format_binary_summary(binary_result.differences),
                details=details,
            )

        # Phase 4: Compare extra fields (fields not in schema but allowed)
        # These fields exist in the response but aren't defined in the OpenAPI spec.
        # When additionalProperties is true/unspecified, we still need to compare them.
        if extra_fields_a or extra_fields_b:
            extra_result = self._compare_extra_fields(
                response_a.body, response_b.body, extra_fields_a, extra_fields_b, covered
            )
            details["extra_fields"] = extra_result

            if not extra_result.match:
                return ComparisonResult(
                    match=False,
                    mismatch_type=MismatchType.BODY,
                    summary=self._format_extra_fields_summary(extra_result.differences),
                    details=details,
                )

        # All components match
        return ComparisonResult(
            match=True,
            mismatch_type=None,
            summary="Responses match",
            details=details,
        )

    def _validate_schemas(
        self,
        response_a: ResponseCase,
        response_b: ResponseCase,
        operation_id: str,
    ) -> tuple[ComponentResult, list[FieldLocation], list[FieldLocation]]:
        """Validate both responses against OpenAPI schema.

        Args:
            response_a: Response from target A.
            response_b: Response from target B.
            operation_id: The operationId for schema lookup.

        Returns:
            Tuple of (ComponentResult, extra_fields_a, extra_fields_b).
        """
        def validate(response: ResponseCase):
            media = self._get_header_value(response.headers, "content-type")
            return self._schema_validator.validate_response_locations(
                response.body, operation_id, response.status_code, media if isinstance(media, str) else None,
                json_body_state=response.json_body_state,
            )

        differences: list[FieldDifference] = []
        # Validate response A
        violations_a, extra_fields_a = validate(response_a)
        for violation in violations_a:
            differences.append(
                FieldDifference(
                    path=violation.path,
                    target_a=f"<violation: {violation.violation_type}>",
                    target_b="<not checked>",
                    rule=f"schema_violation: {violation.message}",
                )
            )

        # Validate response B
        violations_b, extra_fields_b = validate(response_b)
        for violation in violations_b:
            differences.append(
                FieldDifference(
                    path=violation.path,
                    target_a="<not checked>",
                    target_b=f"<violation: {violation.violation_type}>",
                    rule=f"schema_violation: {violation.message}",
                )
            )

        return (
            ComponentResult(match=len(differences) == 0, differences=differences),
            extra_fields_a,
            extra_fields_b,
        )

    def _compare_extra_fields(
        self,
        body_a: Any,
        body_b: Any,
        extra_fields_a: list[FieldLocation],
        extra_fields_b: list[FieldLocation],
        covered: _RuledLocations | None,
    ) -> ComponentResult:
        """Apply default equality to extra fields left over after configured body rules."""
        selected = covered.selected if covered else set()
        ignored = covered.ignored_containers if covered else set()
        ancestors = {path[:i] for path in selected for i in range(len(path))}
        groups: dict[tuple[str, str], tuple[int, FieldLocation]] = {}
        for location in set(extra_fields_a).union(extra_fields_b):
            if location in selected or (ignored and any(location[:i] in ignored for i in range(len(location)))):
                continue
            a, b = _value_at(body_a, location), _value_at(body_b, location)
            if _uncovered_equal(a, b, location, selected, ancestors):
                continue
            rule = "extra_field_presence" if a is NOT_FOUND or b is NOT_FOUND else "extra_field_equality"
            key = field_path(location, wildcard_indices=True), rule
            count, first = groups.get(key, (0, location))
            groups[key] = count + 1, min(first, location)

        differences = []
        for (pattern, rule), (count, first) in sorted(groups.items()):
            grouped = any(isinstance(segment, int) for segment in first)
            a, b = _value_at(body_a, first), _value_at(body_b, first)
            differences.append(FieldDifference(
                path=field_path(first), rule=rule,
                target_a="<missing>" if a is NOT_FOUND else a,
                target_b="<missing>" if b is NOT_FOUND else b,
                path_pattern=pattern if grouped else None, occurrences=count if grouped else None,
            ))
        return ComponentResult(match=not differences, differences=differences)

    def _format_schema_summary(self, differences: list[FieldDifference]) -> str:
        """Format a summary for schema violations."""
        if len(differences) == 1:
            return f"Schema violation at {differences[0].path}"
        return f"Schema violations: {len(differences)} violations"

    def _format_extra_fields_summary(self, differences: list[FieldDifference]) -> str:
        """Format a summary for extra fields mismatches."""
        if len(differences) == 1:
            diff = differences[0]
            if diff.occurrences and diff.occurrences > 1:
                return f"Extra field mismatch at {diff.path_pattern} ({diff.occurrences} occurrences)"
            return f"Extra field mismatch at {diff.path}"
        total = sum(diff.occurrences or 1 for diff in differences)
        if total > len(differences):
            return f"Extra field mismatches: {total} differences in {len(differences)} groups"
        return f"Extra field mismatches: {total} differences"

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
        response_a: ResponseCase,
        response_b: ResponseCase,
        body_rules: BodyRules | None,
        covered: _RuledLocations | None = None,
    ) -> ComponentResult:
        """Compare parsed values, including a present null, using the configured rules."""
        body_a, body_b = response_a.body, response_b.body
        invalid_a = response_a.json_body_state == "invalid"
        invalid_b = response_b.json_body_state == "invalid"
        if (invalid_a or invalid_b) and body_rules and body_rules.field_rules:
            return ComponentResult(match=False, differences=[FieldDifference(
                path="$", target_a="<invalid JSON>" if invalid_a else "<not checked>",
                target_b="<invalid JSON>" if invalid_b else "<not checked>", rule="invalid_json",
            )])

        present_a = body_a is not None or response_a.json_body_state == "null"
        present_b = body_b is not None or response_b.json_body_state == "null"
        if not present_a and not present_b:
            return ComponentResult(match=True, differences=[])
        if present_a != present_b:
            return ComponentResult(match=False, differences=[FieldDifference(
                path="$", target_a="<has body>" if present_a else "<no body>",
                target_b="<has body>" if present_b else "<no body>", rule="body_presence",
            )])

        field_rules = body_rules.field_rules if body_rules else {}
        different_nullness = (body_a is None) != (body_b is None)
        # Use the actual JSONPath matches so any rule selecting the root can decide.
        if different_nullness and field_rules and covered is None:
            covered = _RuledLocations()
        differences: list[FieldDifference] = []
        for jsonpath, rule in field_rules.items():
            path_differences = self._compare_jsonpath(body_a, body_b, jsonpath, rule, covered)
            differences.extend(path_differences)

        if different_nullness and (covered is None or () not in covered.selected):
            differences.insert(0, FieldDifference(
                path="$", target_a="<JSON null>" if body_a is None else "<non-null body>",
                target_b="<JSON null>" if body_b is None else "<non-null body>", rule="body_null_parity",
            ))
        return ComponentResult(match=len(differences) == 0, differences=differences)

    def _compare_binary_body(
        self,
        body_a: str | None,
        body_b: str | None,
        binary_rule: FieldRule | None,
    ) -> ComponentResult:
        """Compare binary response bodies (base64-encoded).

        Args:
            body_a: Base64-encoded body from target A (None if not binary).
            body_b: Base64-encoded body from target B (None if not binary).
            binary_rule: Comparison rule for binary bodies.

        Returns:
            ComponentResult for binary body comparison.
        """
        # If no rule specified, skip all binary comparison (including presence check)
        if binary_rule is None:
            return ComponentResult(match=True, differences=[])

        # Neither response has binary body - nothing to compare
        if body_a is None and body_b is None:
            return ComponentResult(match=True, differences=[])

        # One has binary body, one doesn't - mismatch
        if body_a is None or body_b is None:
            return ComponentResult(
                match=False,
                differences=[
                    FieldDifference(
                        path="body_base64",
                        target_a="<no binary body>" if body_a is None else "<has binary body>",
                        target_b="<no binary body>" if body_b is None else "<has binary body>",
                        rule="binary_presence",
                    )
                ],
            )

        # Evaluate the rule using CEL with base64 strings as values
        try:
            result = self._evaluate_field_rule(body_a, body_b, binary_rule)
        except (CELEvaluationError, ComparatorConfigError) as e:
            return ComponentResult(
                match=False,
                differences=[
                    FieldDifference(
                        path="body_base64",
                        target_a=f"<{len(body_a)} chars>",
                        target_b=f"<{len(body_b)} chars>",
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
                    path="body_base64",
                    target_a=f"<{len(body_a)} chars>",
                    target_b=f"<{len(body_b)} chars>",
                    rule=binary_rule.predefined or "custom",
                )
            ],
        )

    def _format_binary_summary(self, differences: list[FieldDifference]) -> str:
        """Format a summary for binary body mismatches."""
        if len(differences) == 1:
            diff = differences[0]
            return f"Binary body mismatch: {diff.rule}"
        return f"Binary body mismatches: {len(differences)} differences"

    @staticmethod
    def _optional_array_length_change(
        query: JSONPath, body_a: Any, body_b: Any,
    ) -> tuple[int, int] | None:
        """An optional leaf can be absent even when its containing array has changed size."""
        while isinstance(query, Child):
            if query.right == Slice():
                lengths = {m.full_path: len(m.value) for m in query.left.find(body_a)
                           if isinstance(m.value, list)}
                for match in query.left.find(body_b):
                    if isinstance(match.value, list):
                        size_a = lengths.get(match.full_path)
                        if size_a is not None and size_a != len(match.value):
                            return size_a, len(match.value)
            query = query.left
        if query == Slice() and isinstance(body_a, list) and isinstance(body_b, list):
            if len(body_a) != len(body_b):
                return len(body_a), len(body_b)
        return None

    @staticmethod
    def _outside_array(match: DatumInContext, other: Any) -> bool:
        """Is the first missing part of this location an out-of-range array index?"""
        selectors = []
        while match.context is not None:
            selectors.append(match.path)
            match = match.context
        for selector in reversed(selectors):
            if isinstance(selector, Index) and isinstance(other, list):
                if not -len(other) <= selector.index < len(other):
                    return True
                other = other[selector.index]
            elif isinstance(selector, Fields) and isinstance(other, dict):
                other = other.get(selector.fields[0], NOT_FOUND)
            else:
                return False
        return False

    def _compare_jsonpath(
        self, body_a: Any, body_b: Any, jsonpath: str, rule: FieldRule,
        covered: _RuledLocations | None = None,
    ) -> list[FieldDifference]:
        try:
            query = self._compile_jsonpath(jsonpath)
        except JSONPathError as e:
            return [FieldDifference(path=jsonpath, target_a="<error>", target_b="<error>",
                                    rule=f"jsonpath_error: {e}")]

        a = {match.full_path: match for match in query.find(body_a)}
        b = {match.full_path: match for match in query.find(body_b)}
        if covered is not None:
            for matches in (a, b):
                for match in matches.values():
                    location = _match_location(match)
                    if location is not None:
                        covered.selected.add(location)
                        if rule.predefined == "ignore" and isinstance(match.value, (list, dict)):
                            covered.ignored_containers.add(location)
        counts = None
        if rule.presence == PresenceMode.OPTIONAL:
            counts = self._optional_array_length_change(query, body_a, body_b)
        elif max(len(a), len(b)) > 1 and len(a) != len(b):
            counts = len(a), len(b)

        differences = []
        if counts is not None:
            differences.append(FieldDifference(
                path=jsonpath, target_a=f"<{counts[0]} matches>", target_b=f"<{counts[1]} matches>",
                rule="wildcard_count_mismatch",
            ))

        # Single-location failures keep the configured query as their diagnostic path.
        # Two singleton results at different locations still need separate presence checks.
        if max(len(a), len(b)) <= 1 and (not a or not b or a.keys() == b.keys()) and counts is None:
            value_a = next(iter(a.values())).value if a else NOT_FOUND
            value_b = next(iter(b.values())).value if b else NOT_FOUND
            diff = self._compare_single_field(jsonpath, value_a, value_b, rule)
            return [diff] if diff else []

        for path, match in a.items():
            other = b.pop(path, None)
            if other is None and counts is not None and self._outside_array(match, body_b):
                continue
            value_b = NOT_FOUND if other is None else other.value
            diff = self._compare_single_field(str(path), match.value, value_b, rule)
            if diff:
                differences.append(diff)
        for path, match in b.items():
            if counts is not None and self._outside_array(match, body_a):
                continue
            diff = self._compare_single_field(str(path), NOT_FOUND, match.value, rule)
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

        Presence modes:
            PARITY: Both present or both absent. Value comparison only if both present.
            REQUIRED: Both must be present. Fails if either missing.
            FORBIDDEN: Both must be absent. Fails if either present.
            OPTIONAL: Always passes. Value comparison only if both present.
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
            if _EXACT_RULES.get(rule.predefined) == expr:
                return _json_equal(value_a, value_b, mixed_as_double=True)
        else:
            # No comparison specified (presence-only) - treat as pass
            return True

        try:
            return self._cel.evaluate(expr, {"a": value_a, "b": value_b})
        except CELEvaluationError as exc:
            # Older binaries accept the protocol but cannot compile the shipped substring rules.
            if (rule.predefined in {"string_prefix", "string_suffix"}
                    and "undeclared reference to 'substring'" in str(exc)):
                raise CELSubprocessError(
                    f"{rule.predefined} requires CEL substring support. Rebuild cel-evaluator with ./scripts/build.sh."
                ) from exc
            raise

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

    def _compile_jsonpath(self, path: str) -> JSONPath:
        # Use cache for compiled paths
        if path not in self._jsonpath_cache:
            try:
                self._jsonpath_cache[path] = jsonpath_parse(path)
            except (JsonPathParserError, JsonPathLexerError) as e:
                # JsonPathParserError: invalid JSONPath syntax
                # JsonPathLexerError: non-printable or unexpected characters in path
                # (can occur when schema validator reports extra fields with
                # fuzz-generated names containing non-ASCII bytes)
                raise JSONPathError(f"Invalid JSONPath '{path}': {e}") from e

        return self._jsonpath_cache[path]

    def _get_header_value(
        self,
        headers: dict[str, list[str]],
        name: str,
    ) -> str | _NotFound:
        """Get a header value (case-insensitive).

        Multi-value headers return only the first value per design.

        Args:
            headers: Response headers dict (lowercase keys, list values).
            name: Header name to find.

        Returns:
            First header value, or NOT_FOUND if not present.
        """
        # Headers should already be lowercase (per ResponseCase normalization), but we
        # compare case-insensitively as a defensive measure against upstream changes.
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
