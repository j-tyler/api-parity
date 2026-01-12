"""Tests for CLI parser general functionality and validators."""

from pathlib import Path

import pytest

from api_parity.cli import (
    ExploreArgs,
    ReplayArgs,
    build_parser,
    parse_args,
    parse_operation_timeout,
    positive_float,
    positive_int,
)


class TestParserGeneral:
    def test_no_command(self):
        """Test parser fails with no subcommand."""
        with pytest.raises(SystemExit) as exc_info:
            parse_args([])
        assert exc_info.value.code == 2

    def test_unknown_command(self):
        """Test parser fails with unknown subcommand."""
        with pytest.raises(SystemExit) as exc_info:
            parse_args(["unknown"])
        assert exc_info.value.code == 2

    def test_unknown_argument_explore(self):
        """Test explore fails with unknown argument."""
        with pytest.raises(SystemExit) as exc_info:
            parse_args([
                "explore",
                "--spec", "openapi.yaml",
                "--config", "runtime.yaml",
                "--target-a", "prod",
                "--target-b", "stage",
                "--out", "./out",
                "--unknown-arg", "value",
            ])
        assert exc_info.value.code == 2

    def test_unknown_argument_replay(self):
        """Test replay fails with unknown argument."""
        with pytest.raises(SystemExit) as exc_info:
            parse_args([
                "replay",
                "--config", "runtime.yaml",
                "--target-a", "prod",
                "--target-b", "stage",
                "--in", "./in",
                "--out", "./out",
                "--unknown-arg", "value",
            ])
        assert exc_info.value.code == 2

    def test_build_parser_returns_parser(self):
        """Test build_parser returns an ArgumentParser."""
        import argparse
        parser = build_parser()
        assert isinstance(parser, argparse.ArgumentParser)

    def test_parser_prog_name(self):
        """Test parser has correct program name."""
        parser = build_parser()
        assert parser.prog == "api-parity"


class TestEdgeCases:
    def test_negative_seed(self):
        """Test negative seed values are accepted."""
        args = parse_args([
            "explore",
            "--spec", "spec.yaml",
            "--config", "config.yaml",
            "--target-a", "a",
            "--target-b", "b",
            "--out", "./out",
            "--seed", "-1",
        ])
        assert args.seed == -1

    def test_one_max_cases(self):
        """Test max-cases=1 is accepted (minimum valid value)."""
        args = parse_args([
            "explore",
            "--spec", "spec.yaml",
            "--config", "config.yaml",
            "--target-a", "a",
            "--target-b", "b",
            "--out", "./out",
            "--max-cases", "1",
        ])
        assert args.max_cases == 1

    def test_target_names_with_special_chars(self):
        """Test target names can contain special characters."""
        args = parse_args([
            "explore",
            "--spec", "spec.yaml",
            "--config", "config.yaml",
            "--target-a", "prod-us-east-1",
            "--target-b", "staging_v2.0",
            "--out", "./out",
        ])
        assert args.target_a == "prod-us-east-1"
        assert args.target_b == "staging_v2.0"

    def test_same_target_names(self):
        """Test same target names are allowed (parser doesn't validate)."""
        args = parse_args([
            "explore",
            "--spec", "spec.yaml",
            "--config", "config.yaml",
            "--target-a", "same",
            "--target-b", "same",
            "--out", "./out",
        ])
        assert args.target_a == "same"
        assert args.target_b == "same"

    def test_large_max_cases(self):
        """Test large max-cases value is accepted."""
        args = parse_args([
            "explore",
            "--spec", "spec.yaml",
            "--config", "config.yaml",
            "--target-a", "a",
            "--target-b", "b",
            "--out", "./out",
            "--max-cases", "999999999",
        ])
        assert args.max_cases == 999999999


class TestOperationTimeoutParsing:
    def test_parse_valid_operation_timeout(self):
        """Test parsing valid operation timeout string."""
        op_id, timeout = parse_operation_timeout("getUser:60")
        assert op_id == "getUser"
        assert timeout == 60.0

    def test_parse_operation_timeout_float(self):
        """Test parsing operation timeout with float value."""
        op_id, timeout = parse_operation_timeout("slowOp:30.5")
        assert op_id == "slowOp"
        assert timeout == 30.5

    def test_parse_operation_timeout_no_colon(self):
        """Test parsing fails without colon."""
        import argparse
        with pytest.raises(argparse.ArgumentTypeError) as exc_info:
            parse_operation_timeout("getUser60")
        assert "Expected OPERATION_ID:SECONDS" in str(exc_info.value)

    def test_parse_operation_timeout_empty_id(self):
        """Test parsing fails with empty operation ID."""
        import argparse
        with pytest.raises(argparse.ArgumentTypeError) as exc_info:
            parse_operation_timeout(":60")
        assert "Operation ID cannot be empty" in str(exc_info.value)

    def test_parse_operation_timeout_invalid_number(self):
        """Test parsing fails with non-numeric timeout."""
        import argparse
        with pytest.raises(argparse.ArgumentTypeError) as exc_info:
            parse_operation_timeout("getUser:abc")
        assert "Must be a number" in str(exc_info.value)

    def test_parse_operation_timeout_zero(self):
        """Test parsing fails with zero timeout."""
        import argparse
        with pytest.raises(argparse.ArgumentTypeError) as exc_info:
            parse_operation_timeout("getUser:0")
        assert "Must be positive" in str(exc_info.value)

    def test_parse_operation_timeout_negative(self):
        """Test parsing fails with negative timeout."""
        import argparse
        with pytest.raises(argparse.ArgumentTypeError) as exc_info:
            parse_operation_timeout("getUser:-5")
        assert "Must be positive" in str(exc_info.value)


class TestPositiveFloat:
    def test_valid_positive_float(self):
        """Test valid positive float."""
        assert positive_float("30.5") == 30.5

    def test_valid_integer_as_float(self):
        """Test integer string parsed as float."""
        assert positive_float("60") == 60.0

    def test_zero_rejected(self):
        """Test zero is rejected."""
        import argparse
        with pytest.raises(argparse.ArgumentTypeError) as exc_info:
            positive_float("0")
        assert "must be positive" in str(exc_info.value)

    def test_negative_rejected(self):
        """Test negative value is rejected."""
        import argparse
        with pytest.raises(argparse.ArgumentTypeError) as exc_info:
            positive_float("-5.5")
        assert "must be positive" in str(exc_info.value)

    def test_non_numeric_rejected(self):
        """Test non-numeric value is rejected."""
        import argparse
        with pytest.raises(argparse.ArgumentTypeError) as exc_info:
            positive_float("abc")
        assert "Invalid number" in str(exc_info.value)


class TestPositiveInt:
    def test_valid_positive_int(self):
        """Test valid positive integer."""
        assert positive_int("100") == 100

    def test_zero_rejected(self):
        """Test zero is rejected."""
        import argparse
        with pytest.raises(argparse.ArgumentTypeError) as exc_info:
            positive_int("0")
        assert "must be positive" in str(exc_info.value)

    def test_negative_rejected(self):
        """Test negative value is rejected."""
        import argparse
        with pytest.raises(argparse.ArgumentTypeError) as exc_info:
            positive_int("-5")
        assert "must be positive" in str(exc_info.value)

    def test_non_integer_rejected(self):
        """Test non-integer value is rejected."""
        import argparse
        with pytest.raises(argparse.ArgumentTypeError) as exc_info:
            positive_int("abc")
        assert "Invalid integer" in str(exc_info.value)


class TestTimeoutValidation:
    def test_explore_timeout_zero_rejected(self):
        """Test --timeout 0 is rejected for explore."""
        with pytest.raises(SystemExit) as exc_info:
            parse_args([
                "explore",
                "--spec", "spec.yaml",
                "--config", "config.yaml",
                "--target-a", "a",
                "--target-b", "b",
                "--out", "./out",
                "--timeout", "0",
            ])
        assert exc_info.value.code == 2

    def test_explore_timeout_negative_rejected(self):
        """Test --timeout -5 is rejected for explore."""
        with pytest.raises(SystemExit) as exc_info:
            parse_args([
                "explore",
                "--spec", "spec.yaml",
                "--config", "config.yaml",
                "--target-a", "a",
                "--target-b", "b",
                "--out", "./out",
                "--timeout", "-5",
            ])
        assert exc_info.value.code == 2

    def test_replay_timeout_zero_rejected(self):
        """Test --timeout 0 is rejected for replay."""
        with pytest.raises(SystemExit) as exc_info:
            parse_args([
                "replay",
                "--config", "config.yaml",
                "--target-a", "a",
                "--target-b", "b",
                "--in", "./in",
                "--out", "./out",
                "--timeout", "0",
            ])
        assert exc_info.value.code == 2


class TestMaxCasesValidation:
    def test_max_cases_zero_rejected(self):
        """Test --max-cases 0 is rejected."""
        with pytest.raises(SystemExit) as exc_info:
            parse_args([
                "explore",
                "--spec", "spec.yaml",
                "--config", "config.yaml",
                "--target-a", "a",
                "--target-b", "b",
                "--out", "./out",
                "--max-cases", "0",
            ])
        assert exc_info.value.code == 2

    def test_max_cases_negative_rejected(self):
        """Test --max-cases -5 is rejected."""
        with pytest.raises(SystemExit) as exc_info:
            parse_args([
                "explore",
                "--spec", "spec.yaml",
                "--config", "config.yaml",
                "--target-a", "a",
                "--target-b", "b",
                "--out", "./out",
                "--max-cases", "-5",
            ])
        assert exc_info.value.code == 2


class TestDuplicateOperationTimeout:
    def test_duplicate_operation_timeout_uses_last_value(self, capsys):
        """Test duplicate --operation-timeout uses last value and warns."""
        args = parse_args([
            "explore",
            "--spec", "spec.yaml",
            "--config", "config.yaml",
            "--target-a", "a",
            "--target-b", "b",
            "--out", "./out",
            "--operation-timeout", "getUser:60",
            "--operation-timeout", "getUser:120",
        ])
        assert args.operation_timeout == {"getUser": 120.0}
        captured = capsys.readouterr()
        assert "Warning" in captured.err
        assert "getUser" in captured.err
        assert "120" in captured.err

    def test_no_warning_for_unique_operation_timeouts(self, capsys):
        """Test no warning when operation timeouts are unique."""
        args = parse_args([
            "explore",
            "--spec", "spec.yaml",
            "--config", "config.yaml",
            "--target-a", "a",
            "--target-b", "b",
            "--out", "./out",
            "--operation-timeout", "getUser:60",
            "--operation-timeout", "createItem:120",
        ])
        assert args.operation_timeout == {"getUser": 60.0, "createItem": 120.0}
        captured = capsys.readouterr()
        assert "Warning" not in captured.err
