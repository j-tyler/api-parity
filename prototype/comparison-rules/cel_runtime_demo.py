#!/usr/bin/env python3
"""
Demonstrates what the CEL runtime evaluation would look like.

This is a conceptual demo showing:
1. How the runtime receives inlined CEL expressions
2. How it evaluates them with values from targets A and B
3. How comparison results are produced

In production, this would use cel-python or similar CEL implementation.

Usage:
    python cel_runtime_demo.py
"""

import json
import re
from typing import Any


class MockCELRuntime:
    """
    Mock CEL evaluator for demonstration.

    In production, replace with actual CEL library:
        import celpy
        env = celpy.Environment()
        ast = env.compile(expr)
        prog = env.program(ast)
        result = prog.evaluate({"a": val_a, "b": val_b})
    """

    def evaluate(self, expr: str, a: Any, b: Any) -> bool:
        """
        Evaluate a CEL expression with bindings a and b.

        This mock handles only the expressions in our predefined library.
        A real CEL runtime would handle arbitrary expressions.
        """
        # Simple exact match
        if expr == "a == b":
            return a == b

        # Ignore (always true)
        if expr == "true":
            return True

        # Numeric tolerance: (a - b) <= tolerance && (b - a) <= tolerance
        tolerance_match = re.match(
            r"\(a - b\) <= ([\d.]+) && \(b - a\) <= ([\d.]+)",
            expr
        )
        if tolerance_match:
            tolerance = float(tolerance_match.group(1))
            if isinstance(a, (int, float)) and isinstance(b, (int, float)):
                return abs(a - b) <= tolerance
            return False

        # Size comparison: size(a) == size(b)
        if expr == "size(a) == size(b)":
            return len(a) == len(b)

        # Non-empty string: size(a) > 0 && size(b) > 0
        if expr == "size(a) > 0 && size(b) > 0":
            return len(a) > 0 and len(b) > 0

        # Unordered array: size(a) == size(b) && a.all(x, x in b)
        if expr == "size(a) == size(b) && a.all(x, x in b)":
            if len(a) != len(b):
                return False
            return all(x in b for x in a)

        # Range check: (a >= min && a <= max) && (b >= min && b <= max)
        range_match = re.match(
            r"\(a >= ([\d.]+) && a <= ([\d.]+)\) && \(b >= ([\d.]+) && b <= ([\d.]+)\)",
            expr
        )
        if range_match:
            min_val = float(range_match.group(1))
            max_val = float(range_match.group(2))
            return (min_val <= a <= max_val) and (min_val <= b <= max_val)

        # Custom expression example: a < 5000 && b < 5000
        if expr == "a < 5000 && b < 5000":
            return a < 5000 and b < 5000

        # UUID format check (simplified)
        if "matches('^[0-9a-f]{8}" in expr:
            uuid_pattern = r'^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
            return bool(re.match(uuid_pattern, str(a))) and bool(re.match(uuid_pattern, str(b)))

        raise NotImplementedError(f"Mock runtime doesn't support: {expr}")


def demo():
    """Run demonstration comparisons."""
    runtime = MockCELRuntime()

    test_cases = [
        # (expression, value_a, value_b, expected_result)
        ("a == b", 42, 42, True),
        ("a == b", 42, 43, False),
        ("a == b", "hello", "hello", True),
        ("a == b", "hello", "world", False),

        ("true", "anything", "different", True),

        ("(a - b) <= 0.01 && (b - a) <= 0.01", 1.005, 1.009, True),
        ("(a - b) <= 0.01 && (b - a) <= 0.01", 1.0, 1.02, False),

        ("(a - b) <= 5 && (b - a) <= 5", 1000, 1003, True),
        ("(a - b) <= 5 && (b - a) <= 5", 1000, 1010, False),

        ("size(a) == size(b)", [1, 2, 3], [4, 5, 6], True),
        ("size(a) == size(b)", [1, 2], [1, 2, 3], False),

        ("size(a) > 0 && size(b) > 0", "abc", "xyz", True),
        ("size(a) > 0 && size(b) > 0", "", "xyz", False),

        ("size(a) == size(b) && a.all(x, x in b)", [1, 2, 3], [3, 1, 2], True),
        ("size(a) == size(b) && a.all(x, x in b)", [1, 2, 3], [1, 2, 4], False),

        ("(a >= 0.0 && a <= 1.0) && (b >= 0.0 && b <= 1.0)", 0.5, 0.7, True),
        ("(a >= 0.0 && a <= 1.0) && (b >= 0.0 && b <= 1.0)", 0.5, 1.5, False),

        ("a < 5000 && b < 5000", 150, 200, True),
        ("a < 5000 && b < 5000", 150, 6000, False),
    ]

    print("CEL Runtime Evaluation Demo")
    print("=" * 70)
    print()

    passed = 0
    failed = 0

    for expr, a, b, expected in test_cases:
        result = runtime.evaluate(expr, a, b)
        status = "✓" if result == expected else "✗"
        if result == expected:
            passed += 1
        else:
            failed += 1

        # Truncate long expressions
        display_expr = expr if len(expr) <= 45 else expr[:42] + "..."

        print(f"{status} {display_expr}")
        print(f"    a={repr(a)}, b={repr(b)}")
        print(f"    result={result}, expected={expected}")
        print()

    print("=" * 70)
    print(f"Results: {passed} passed, {failed} failed")

    # Show how this integrates with inlined config
    print()
    print("=" * 70)
    print("Integration Example: Comparing API Responses")
    print("=" * 70)
    print()

    # Simulated inlined config for createUser operation
    inlined_rules = {
        "$.id": {"expr": "a.matches('^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$') && b.matches('^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$')"},
        "$.balance": {"expr": "(a - b) <= 0.01 && (b - a) <= 0.01"},
        "$.tags": {"expr": "size(a) == size(b) && a.all(x, x in b)"},
    }

    # Simulated responses from targets
    response_a = {
        "id": "550e8400-e29b-41d4-a716-446655440000",
        "balance": 100.005,
        "tags": ["premium", "active", "verified"]
    }

    response_b = {
        "id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
        "balance": 100.009,
        "tags": ["verified", "premium", "active"]
    }

    print("Target A response:", json.dumps(response_a, indent=2))
    print()
    print("Target B response:", json.dumps(response_b, indent=2))
    print()
    print("Comparison results:")
    print()

    for path, rule in inlined_rules.items():
        # Extract field name from JSONPath (simplified)
        field = path.replace("$.", "")
        val_a = response_a.get(field)
        val_b = response_b.get(field)

        try:
            result = runtime.evaluate(rule["expr"], val_a, val_b)
            status = "MATCH" if result else "MISMATCH"
            symbol = "✓" if result else "✗"
        except NotImplementedError as e:
            status = "ERROR"
            symbol = "?"

        print(f"  {symbol} {path}: {status}")
        print(f"      A: {repr(val_a)}")
        print(f"      B: {repr(val_b)}")
        print()


if __name__ == "__main__":
    demo()
