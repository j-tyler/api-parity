"""Integration tests for the CEL Evaluator (Python to Go subprocess).

These tests verify:
1. Basic CEL expression evaluation
2. Various data types (numbers, strings, arrays, objects)
3. Error handling for invalid expressions
4. Subprocess lifecycle management
"""

import pytest

from api_parity.cel_evaluator import CELEvaluator, CELEvaluationError, CELSubprocessError


class TestCELEvaluatorBasic:
    """Basic CEL evaluation tests."""

    def test_simple_equality_true(self):
        """Test simple equality that returns true."""
        with CELEvaluator() as evaluator:
            result = evaluator.evaluate("a == b", {"a": 1, "b": 1})
            assert result is True

    def test_simple_equality_false(self):
        """Test simple equality that returns false."""
        with CELEvaluator() as evaluator:
            result = evaluator.evaluate("a == b", {"a": 1, "b": 2})
            assert result is False

    def test_string_equality(self):
        """Test string comparison."""
        with CELEvaluator() as evaluator:
            result = evaluator.evaluate("a == b", {"a": "hello", "b": "hello"})
            assert result is True

            result = evaluator.evaluate("a == b", {"a": "hello", "b": "world"})
            assert result is False

    def test_always_true(self):
        """Test ignore expression (always true)."""
        with CELEvaluator() as evaluator:
            result = evaluator.evaluate("true", {"a": 1, "b": 99999})
            assert result is True

    def test_always_false(self):
        """Test always false expression."""
        with CELEvaluator() as evaluator:
            result = evaluator.evaluate("false", {"a": 1, "b": 1})
            assert result is False


class TestCELEvaluatorNumeric:
    """Tests for numeric comparisons."""

    def test_numeric_tolerance_within(self):
        """Test numeric tolerance - values within tolerance."""
        with CELEvaluator() as evaluator:
            # 1.005 and 1.009 differ by 0.004, which is <= 0.01
            result = evaluator.evaluate(
                "(a - b) <= 0.01 && (b - a) <= 0.01",
                {"a": 1.005, "b": 1.009}
            )
            assert result is True

    def test_numeric_tolerance_outside(self):
        """Test numeric tolerance - values outside tolerance."""
        with CELEvaluator() as evaluator:
            # 1.0 and 1.02 differ by 0.02, which is > 0.01
            result = evaluator.evaluate(
                "(a - b) <= 0.01 && (b - a) <= 0.01",
                {"a": 1.0, "b": 1.02}
            )
            assert result is False

    def test_greater_than(self):
        """Test greater than comparison."""
        with CELEvaluator() as evaluator:
            result = evaluator.evaluate("a > b", {"a": 10, "b": 5})
            assert result is True

            result = evaluator.evaluate("a > b", {"a": 5, "b": 10})
            assert result is False

    def test_range_check(self):
        """Test range check expression."""
        with CELEvaluator() as evaluator:
            result = evaluator.evaluate(
                "(a >= 0.0 && a <= 1.0) && (b >= 0.0 && b <= 1.0)",
                {"a": 0.5, "b": 0.7}
            )
            assert result is True

            result = evaluator.evaluate(
                "(a >= 0.0 && a <= 1.0) && (b >= 0.0 && b <= 1.0)",
                {"a": 0.5, "b": 1.5}
            )
            assert result is False


class TestCELEvaluatorArrays:
    """Tests for array operations."""

    def test_array_size_equal(self):
        """Test array size comparison."""
        with CELEvaluator() as evaluator:
            result = evaluator.evaluate("size(a) == size(b)", {"a": [1, 2, 3], "b": [4, 5, 6]})
            assert result is True

    def test_array_size_different(self):
        """Test array size comparison with different sizes."""
        with CELEvaluator() as evaluator:
            result = evaluator.evaluate("size(a) == size(b)", {"a": [1, 2], "b": [1, 2, 3]})
            assert result is False

    def test_unordered_array_match(self):
        """Test unordered array comparison - same elements different order."""
        with CELEvaluator() as evaluator:
            result = evaluator.evaluate(
                "size(a) == size(b) && a.all(x, x in b)",
                {"a": [1, 2, 3], "b": [3, 1, 2]}
            )
            assert result is True

    def test_unordered_array_mismatch(self):
        """Test unordered array comparison - different elements."""
        with CELEvaluator() as evaluator:
            result = evaluator.evaluate(
                "size(a) == size(b) && a.all(x, x in b)",
                {"a": [1, 2, 3], "b": [1, 2, 4]}
            )
            assert result is False


class TestCELEvaluatorStrings:
    """Tests for string operations."""

    def test_string_non_empty(self):
        """Test non-empty string check."""
        with CELEvaluator() as evaluator:
            result = evaluator.evaluate("size(a) > 0 && size(b) > 0", {"a": "abc", "b": "xyz"})
            assert result is True

            result = evaluator.evaluate("size(a) > 0 && size(b) > 0", {"a": "", "b": "xyz"})
            assert result is False

    def test_string_contains(self):
        """Test string contains check."""
        with CELEvaluator() as evaluator:
            result = evaluator.evaluate('a.contains("world")', {"a": "hello world"})
            assert result is True

            result = evaluator.evaluate('a.contains("foo")', {"a": "hello world"})
            assert result is False

    def test_string_starts_with(self):
        """Test string startsWith check."""
        with CELEvaluator() as evaluator:
            result = evaluator.evaluate('a.startsWith("hello")', {"a": "hello world"})
            assert result is True


class TestCELEvaluatorErrors:
    """Tests for error handling."""

    def test_undefined_variable(self):
        """Test error on undefined variable."""
        with CELEvaluator() as evaluator:
            with pytest.raises(CELEvaluationError) as exc_info:
                evaluator.evaluate("undefined_var == 1", {"a": 1})
            assert "undefined_var" in str(exc_info.value).lower() or "undeclared" in str(exc_info.value).lower()

    def test_syntax_error(self):
        """Test error on syntax error."""
        with CELEvaluator() as evaluator:
            with pytest.raises(CELEvaluationError) as exc_info:
                evaluator.evaluate("a ==", {"a": 1})
            assert "error" in str(exc_info.value).lower() or "syntax" in str(exc_info.value).lower()

    def test_type_mismatch(self):
        """Test error on type mismatch in expression."""
        with CELEvaluator() as evaluator:
            with pytest.raises(CELEvaluationError):
                # size() on an integer should fail
                evaluator.evaluate("size(a) > 0", {"a": 123})

    def test_non_boolean_result(self):
        """Test error when expression doesn't return boolean."""
        with CELEvaluator() as evaluator:
            with pytest.raises(CELEvaluationError) as exc_info:
                evaluator.evaluate("a + b", {"a": 1, "b": 2})
            assert "not boolean" in str(exc_info.value).lower() or "bool" in str(exc_info.value).lower()


class TestCELEvaluatorLifecycle:
    """Tests for subprocess lifecycle."""

    def test_context_manager(self):
        """Test using CELEvaluator as context manager."""
        with CELEvaluator() as evaluator:
            assert evaluator.is_running
            result = evaluator.evaluate("a == b", {"a": 1, "b": 1})
            assert result is True
        # After exiting context, should be closed
        assert not evaluator.is_running

    def test_explicit_close(self):
        """Test explicit close()."""
        evaluator = CELEvaluator()
        assert evaluator.is_running
        evaluator.close()
        assert not evaluator.is_running

    def test_multiple_evaluations(self):
        """Test multiple evaluations on same instance."""
        with CELEvaluator() as evaluator:
            for i in range(10):
                result = evaluator.evaluate("a == b", {"a": i, "b": i})
                assert result is True

    def test_invalid_binary_path(self):
        """Test error on invalid binary path."""
        with pytest.raises(CELSubprocessError) as exc_info:
            CELEvaluator(binary_path="/nonexistent/path/to/binary")
        assert "not found" in str(exc_info.value).lower()

    def test_subprocess_crash_recovery(self):
        """Test automatic recovery when subprocess is killed."""
        import signal

        evaluator = CELEvaluator()
        try:
            # Verify initial operation works
            result = evaluator.evaluate("a == b", {"a": 1, "b": 1})
            assert result is True

            # Kill the subprocess
            evaluator._process.send_signal(signal.SIGKILL)
            evaluator._process.wait()

            # Next evaluation should trigger restart and succeed
            result = evaluator.evaluate("a == b", {"a": 2, "b": 2})
            assert result is True
            assert evaluator.is_running
        finally:
            evaluator.close()

    def test_max_restarts_exceeded(self):
        """Test that MAX_RESTARTS limit is enforced."""
        import signal

        evaluator = CELEvaluator()
        try:
            # Kill subprocess MAX_RESTARTS times
            for i in range(CELEvaluator.MAX_RESTARTS):
                evaluator._process.send_signal(signal.SIGKILL)
                evaluator._process.wait()
                # This should trigger a restart
                evaluator.evaluate("a == b", {"a": 1, "b": 1})

            # Kill one more time - should exceed limit
            evaluator._process.send_signal(signal.SIGKILL)
            evaluator._process.wait()

            with pytest.raises(CELSubprocessError) as exc_info:
                evaluator.evaluate("a == b", {"a": 1, "b": 1})
            assert "crashed" in str(exc_info.value).lower()
        finally:
            evaluator.close()


class TestCELEvaluatorPredefinedExpressions:
    """Tests for predefined comparison expressions from the library."""

    def test_exact_match(self):
        """Test exact_match predefined."""
        with CELEvaluator() as evaluator:
            # exact_match expands to "a == b"
            assert evaluator.evaluate("a == b", {"a": 42, "b": 42}) is True
            assert evaluator.evaluate("a == b", {"a": 42, "b": 43}) is False

    def test_ignore(self):
        """Test ignore predefined."""
        with CELEvaluator() as evaluator:
            # ignore expands to "true"
            assert evaluator.evaluate("true", {"a": "anything", "b": "different"}) is True

    def test_numeric_tolerance_expression(self):
        """Test numeric_tolerance predefined expression."""
        with CELEvaluator() as evaluator:
            # numeric_tolerance with tolerance=0.01 expands to:
            # (a - b) <= 0.01 && (b - a) <= 0.01
            expr = "(a - b) <= 0.01 && (b - a) <= 0.01"
            assert evaluator.evaluate(expr, {"a": 1.005, "b": 1.009}) is True
            assert evaluator.evaluate(expr, {"a": 1.0, "b": 1.02}) is False

    def test_epoch_seconds_tolerance(self):
        """Test epoch timestamp comparison logic."""
        with CELEvaluator() as evaluator:
            # epoch_seconds_tolerance with seconds=5 expands to:
            # (a - b) <= 5 && (b - a) <= 5
            expr = "(a - b) <= 5 && (b - a) <= 5"
            assert evaluator.evaluate(expr, {"a": 1000, "b": 1003}) is True
            assert evaluator.evaluate(expr, {"a": 1000, "b": 1010}) is False


class TestCELEvaluatorComplexData:
    """Tests with complex nested data structures."""

    def test_nested_object_access(self):
        """Test accessing nested object fields."""
        with CELEvaluator() as evaluator:
            data = {
                "a": {"name": "Alice", "age": 30},
                "b": {"name": "Alice", "age": 30}
            }
            result = evaluator.evaluate("a.name == b.name && a.age == b.age", data)
            assert result is True

    def test_array_of_objects(self):
        """Test with array of objects."""
        with CELEvaluator() as evaluator:
            data = {
                "items": [{"id": 1}, {"id": 2}, {"id": 3}]
            }
            result = evaluator.evaluate("size(items) == 3", data)
            assert result is True

    def test_mixed_types(self):
        """Test with various data types."""
        with CELEvaluator() as evaluator:
            data = {
                "str_val": "hello",
                "int_val": 42,
                "float_val": 3.14,
                "bool_val": True,
                "null_val": None,
                "list_val": [1, 2, 3]
            }
            result = evaluator.evaluate(
                'str_val == "hello" && int_val == 42 && bool_val == true',
                data
            )
            assert result is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
