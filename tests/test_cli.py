"""Tests for api_parity.cli argument parsing.

Tests cover:
- Explore subcommand with required and optional arguments
- Replay subcommand with required arguments
- Error cases (missing required args, unknown args)
- Argument type validation (Path conversion, int parsing)
"""

from pathlib import Path

import pytest

from api_parity.cli import (
    DEFAULT_TIMEOUT,
    ExploreArgs,
    ListOperationsArgs,
    ReplayArgs,
    build_parser,
    parse_args,
    parse_explore_args,
    parse_list_ops_args,
    parse_operation_timeout,
    parse_replay_args,
    positive_float,
    positive_int,
    run_list_operations,
)


# =============================================================================
# List-Operations Subcommand Tests
# =============================================================================


class TestListOperationsArgs:
    def test_basic_list_operations(self):
        """Test list-operations with required --spec."""
        args = parse_args(["list-operations", "--spec", "openapi.yaml"])
        assert isinstance(args, ListOperationsArgs)
        assert args.spec == Path("openapi.yaml")

    def test_list_operations_missing_spec(self):
        """Test list-operations fails without --spec."""
        with pytest.raises(SystemExit) as exc_info:
            parse_args(["list-operations"])
        assert exc_info.value.code == 2

    def test_parse_list_ops_args_converts_correctly(self):
        """Test parse_list_ops_args converts namespace to dataclass."""
        import argparse
        namespace = argparse.Namespace(
            command="list-operations",
            spec=Path("spec.yaml"),
        )
        args = parse_list_ops_args(namespace)
        assert isinstance(args, ListOperationsArgs)
        assert args.spec == Path("spec.yaml")


# =============================================================================
# Explore Subcommand Tests
# =============================================================================


class TestExploreArgs:
    def test_all_required_args(self):
        """Test explore with all required arguments."""
        args = parse_args([
            "explore",
            "--spec", "openapi.yaml",
            "--config", "runtime.yaml",
            "--target-a", "production",
            "--target-b", "staging",
            "--out", "./artifacts",
        ])

        assert isinstance(args, ExploreArgs)
        assert args.spec == Path("openapi.yaml")
        assert args.config == Path("runtime.yaml")
        assert args.target_a == "production"
        assert args.target_b == "staging"
        assert args.out == Path("./artifacts")
        assert args.seed is None
        assert args.max_cases is None
        assert args.validate is False
        assert args.exclude == []
        assert args.timeout == DEFAULT_TIMEOUT
        assert args.operation_timeout == {}

    def test_with_seed(self):
        """Test explore with optional --seed argument."""
        args = parse_args([
            "explore",
            "--spec", "openapi.yaml",
            "--config", "runtime.yaml",
            "--target-a", "prod",
            "--target-b", "stage",
            "--out", "./out",
            "--seed", "42",
        ])

        assert isinstance(args, ExploreArgs)
        assert args.seed == 42

    def test_with_max_cases(self):
        """Test explore with optional --max-cases argument."""
        args = parse_args([
            "explore",
            "--spec", "openapi.yaml",
            "--config", "runtime.yaml",
            "--target-a", "prod",
            "--target-b", "stage",
            "--out", "./out",
            "--max-cases", "1000",
        ])

        assert isinstance(args, ExploreArgs)
        assert args.max_cases == 1000

    def test_with_all_optional_args(self):
        """Test explore with all optional arguments."""
        args = parse_args([
            "explore",
            "--spec", "spec.yaml",
            "--config", "config.yaml",
            "--target-a", "a",
            "--target-b", "b",
            "--out", "./out",
            "--seed", "123",
            "--max-cases", "500",
        ])

        assert isinstance(args, ExploreArgs)
        assert args.seed == 123
        assert args.max_cases == 500

    def test_missing_spec(self):
        """Test explore fails without --spec."""
        with pytest.raises(SystemExit) as exc_info:
            parse_args([
                "explore",
                "--config", "runtime.yaml",
                "--target-a", "prod",
                "--target-b", "stage",
                "--out", "./out",
            ])
        assert exc_info.value.code == 2

    def test_missing_config(self):
        """Test explore fails without --config."""
        with pytest.raises(SystemExit) as exc_info:
            parse_args([
                "explore",
                "--spec", "openapi.yaml",
                "--target-a", "prod",
                "--target-b", "stage",
                "--out", "./out",
            ])
        assert exc_info.value.code == 2

    def test_missing_target_a(self):
        """Test explore fails without --target-a."""
        with pytest.raises(SystemExit) as exc_info:
            parse_args([
                "explore",
                "--spec", "openapi.yaml",
                "--config", "runtime.yaml",
                "--target-b", "stage",
                "--out", "./out",
            ])
        assert exc_info.value.code == 2

    def test_missing_target_b(self):
        """Test explore fails without --target-b."""
        with pytest.raises(SystemExit) as exc_info:
            parse_args([
                "explore",
                "--spec", "openapi.yaml",
                "--config", "runtime.yaml",
                "--target-a", "prod",
                "--out", "./out",
            ])
        assert exc_info.value.code == 2

    def test_missing_out(self):
        """Test explore fails without --out."""
        with pytest.raises(SystemExit) as exc_info:
            parse_args([
                "explore",
                "--spec", "openapi.yaml",
                "--config", "runtime.yaml",
                "--target-a", "prod",
                "--target-b", "stage",
            ])
        assert exc_info.value.code == 2

    def test_invalid_seed_type(self):
        """Test explore fails with non-integer --seed."""
        with pytest.raises(SystemExit) as exc_info:
            parse_args([
                "explore",
                "--spec", "openapi.yaml",
                "--config", "runtime.yaml",
                "--target-a", "prod",
                "--target-b", "stage",
                "--out", "./out",
                "--seed", "not-an-int",
            ])
        assert exc_info.value.code == 2

    def test_invalid_max_cases_type(self):
        """Test explore fails with non-integer --max-cases."""
        with pytest.raises(SystemExit) as exc_info:
            parse_args([
                "explore",
                "--spec", "openapi.yaml",
                "--config", "runtime.yaml",
                "--target-a", "prod",
                "--target-b", "stage",
                "--out", "./out",
                "--max-cases", "abc",
            ])
        assert exc_info.value.code == 2

    def test_path_with_spaces(self):
        """Test paths with spaces are handled correctly."""
        args = parse_args([
            "explore",
            "--spec", "path with spaces/openapi.yaml",
            "--config", "runtime.yaml",
            "--target-a", "prod",
            "--target-b", "stage",
            "--out", "./out dir",
        ])

        assert args.spec == Path("path with spaces/openapi.yaml")
        assert args.out == Path("./out dir")

    def test_absolute_paths(self):
        """Test absolute paths are preserved."""
        args = parse_args([
            "explore",
            "--spec", "/absolute/path/to/spec.yaml",
            "--config", "/etc/api-parity/config.yaml",
            "--target-a", "prod",
            "--target-b", "stage",
            "--out", "/var/artifacts",
        ])

        assert args.spec == Path("/absolute/path/to/spec.yaml")
        assert args.config == Path("/etc/api-parity/config.yaml")
        assert args.out == Path("/var/artifacts")


# =============================================================================
# Replay Subcommand Tests
# =============================================================================


class TestReplayArgs:
    def test_all_required_args(self):
        """Test replay with all required arguments."""
        args = parse_args([
            "replay",
            "--config", "runtime.yaml",
            "--target-a", "production",
            "--target-b", "staging",
            "--in", "./artifacts/mismatches",
            "--out", "./artifacts/replay",
        ])

        assert isinstance(args, ReplayArgs)
        assert args.config == Path("runtime.yaml")
        assert args.target_a == "production"
        assert args.target_b == "staging"
        assert args.input_dir == Path("./artifacts/mismatches")
        assert args.out == Path("./artifacts/replay")
        assert args.validate is False
        assert args.timeout == DEFAULT_TIMEOUT
        assert args.operation_timeout == {}

    def test_missing_config(self):
        """Test replay fails without --config."""
        with pytest.raises(SystemExit) as exc_info:
            parse_args([
                "replay",
                "--target-a", "prod",
                "--target-b", "stage",
                "--in", "./in",
                "--out", "./out",
            ])
        assert exc_info.value.code == 2

    def test_missing_target_a(self):
        """Test replay fails without --target-a."""
        with pytest.raises(SystemExit) as exc_info:
            parse_args([
                "replay",
                "--config", "runtime.yaml",
                "--target-b", "stage",
                "--in", "./in",
                "--out", "./out",
            ])
        assert exc_info.value.code == 2

    def test_missing_target_b(self):
        """Test replay fails without --target-b."""
        with pytest.raises(SystemExit) as exc_info:
            parse_args([
                "replay",
                "--config", "runtime.yaml",
                "--target-a", "prod",
                "--in", "./in",
                "--out", "./out",
            ])
        assert exc_info.value.code == 2

    def test_missing_in(self):
        """Test replay fails without --in."""
        with pytest.raises(SystemExit) as exc_info:
            parse_args([
                "replay",
                "--config", "runtime.yaml",
                "--target-a", "prod",
                "--target-b", "stage",
                "--out", "./out",
            ])
        assert exc_info.value.code == 2

    def test_missing_out(self):
        """Test replay fails without --out."""
        with pytest.raises(SystemExit) as exc_info:
            parse_args([
                "replay",
                "--config", "runtime.yaml",
                "--target-a", "prod",
                "--target-b", "stage",
                "--in", "./in",
            ])
        assert exc_info.value.code == 2


# =============================================================================
# General Parser Tests
# =============================================================================


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


# =============================================================================
# Dataclass Conversion Tests
# =============================================================================


class TestDataclassConversion:
    def test_parse_explore_args_converts_correctly(self):
        """Test parse_explore_args converts namespace to dataclass."""
        import argparse
        namespace = argparse.Namespace(
            command="explore",
            spec=Path("spec.yaml"),
            config=Path("config.yaml"),
            target_a="a",
            target_b="b",
            out=Path("./out"),
            seed=42,
            max_cases=100,
            validate=False,
            exclude=["getUser"],
            timeout=60.0,
            operation_timeout=[("slowOp", 120.0)],
            stateful=False,
            max_chains=None,
            max_steps=6,
        )
        args = parse_explore_args(namespace)
        assert isinstance(args, ExploreArgs)
        assert args.spec == Path("spec.yaml")
        assert args.seed == 42
        assert args.exclude == ["getUser"]
        assert args.timeout == 60.0
        assert args.operation_timeout == {"slowOp": 120.0}
        assert args.stateful is False
        assert args.max_chains is None
        assert args.max_steps == 6

    def test_parse_replay_args_converts_correctly(self):
        """Test parse_replay_args converts namespace to dataclass."""
        import argparse
        namespace = argparse.Namespace(
            command="replay",
            config=Path("config.yaml"),
            target_a="a",
            target_b="b",
            input_dir=Path("./in"),
            out=Path("./out"),
            validate=False,
            timeout=45.0,
            operation_timeout=[],
        )
        args = parse_replay_args(namespace)
        assert isinstance(args, ReplayArgs)
        assert args.input_dir == Path("./in")
        assert args.timeout == 45.0
        assert args.operation_timeout == {}


# =============================================================================
# Argument Order Tests
# =============================================================================


class TestArgumentOrder:
    def test_explore_args_any_order(self):
        """Test explore arguments can be in any order."""
        args = parse_args([
            "explore",
            "--out", "./artifacts",
            "--target-b", "staging",
            "--seed", "99",
            "--target-a", "production",
            "--config", "runtime.yaml",
            "--spec", "openapi.yaml",
            "--max-cases", "50",
        ])

        assert isinstance(args, ExploreArgs)
        assert args.spec == Path("openapi.yaml")
        assert args.config == Path("runtime.yaml")
        assert args.target_a == "production"
        assert args.target_b == "staging"
        assert args.out == Path("./artifacts")
        assert args.seed == 99
        assert args.max_cases == 50

    def test_replay_args_any_order(self):
        """Test replay arguments can be in any order."""
        args = parse_args([
            "replay",
            "--out", "./replay",
            "--in", "./mismatches",
            "--target-b", "staging",
            "--target-a", "production",
            "--config", "runtime.yaml",
        ])

        assert isinstance(args, ReplayArgs)
        assert args.config == Path("runtime.yaml")
        assert args.target_a == "production"
        assert args.target_b == "staging"
        assert args.input_dir == Path("./mismatches")
        assert args.out == Path("./replay")


# =============================================================================
# Edge Cases
# =============================================================================


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


# =============================================================================
# Validate Mode Tests
# =============================================================================


class TestValidateMode:
    def test_explore_validate_flag(self):
        """Test explore with --validate flag."""
        args = parse_args([
            "explore",
            "--spec", "spec.yaml",
            "--config", "config.yaml",
            "--target-a", "a",
            "--target-b", "b",
            "--out", "./out",
            "--validate",
        ])
        assert isinstance(args, ExploreArgs)
        assert args.validate is True

    def test_explore_validate_with_other_options(self):
        """Test explore --validate works with other optional args."""
        args = parse_args([
            "explore",
            "--spec", "spec.yaml",
            "--config", "config.yaml",
            "--target-a", "a",
            "--target-b", "b",
            "--out", "./out",
            "--seed", "42",
            "--max-cases", "100",
            "--validate",
        ])
        assert args.validate is True
        assert args.seed == 42
        assert args.max_cases == 100

    def test_replay_validate_flag(self):
        """Test replay with --validate flag."""
        args = parse_args([
            "replay",
            "--config", "config.yaml",
            "--target-a", "a",
            "--target-b", "b",
            "--in", "./in",
            "--out", "./out",
            "--validate",
        ])
        assert isinstance(args, ReplayArgs)
        assert args.validate is True

    def test_validate_flag_position_independent(self):
        """Test --validate can appear anywhere in args."""
        args = parse_args([
            "explore",
            "--validate",
            "--spec", "spec.yaml",
            "--config", "config.yaml",
            "--target-a", "a",
            "--target-b", "b",
            "--out", "./out",
        ])
        assert args.validate is True


# =============================================================================
# Exclude Option Tests
# =============================================================================


class TestExcludeOption:
    def test_single_exclude(self):
        """Test explore with single --exclude."""
        args = parse_args([
            "explore",
            "--spec", "spec.yaml",
            "--config", "config.yaml",
            "--target-a", "a",
            "--target-b", "b",
            "--out", "./out",
            "--exclude", "getUser",
        ])
        assert isinstance(args, ExploreArgs)
        assert args.exclude == ["getUser"]

    def test_multiple_excludes(self):
        """Test explore with multiple --exclude options."""
        args = parse_args([
            "explore",
            "--spec", "spec.yaml",
            "--config", "config.yaml",
            "--target-a", "a",
            "--target-b", "b",
            "--out", "./out",
            "--exclude", "getUser",
            "--exclude", "createItem",
            "--exclude", "deleteOrder",
        ])
        assert args.exclude == ["getUser", "createItem", "deleteOrder"]

    def test_exclude_with_other_options(self):
        """Test --exclude works with other optional args."""
        args = parse_args([
            "explore",
            "--spec", "spec.yaml",
            "--config", "config.yaml",
            "--target-a", "a",
            "--target-b", "b",
            "--out", "./out",
            "--seed", "42",
            "--exclude", "getUser",
            "--validate",
        ])
        assert args.exclude == ["getUser"]
        assert args.seed == 42
        assert args.validate is True

    def test_exclude_empty_by_default(self):
        """Test exclude is empty list when not specified."""
        args = parse_args([
            "explore",
            "--spec", "spec.yaml",
            "--config", "config.yaml",
            "--target-a", "a",
            "--target-b", "b",
            "--out", "./out",
        ])
        assert args.exclude == []

    def test_exclude_position_independent(self):
        """Test --exclude can appear anywhere in args."""
        args = parse_args([
            "explore",
            "--exclude", "firstOp",
            "--spec", "spec.yaml",
            "--exclude", "secondOp",
            "--config", "config.yaml",
            "--target-a", "a",
            "--target-b", "b",
            "--out", "./out",
        ])
        assert args.exclude == ["firstOp", "secondOp"]


# =============================================================================
# Timeout Option Tests
# =============================================================================


class TestTimeoutOptions:
    def test_explore_default_timeout(self):
        """Test explore uses default timeout when not specified."""
        args = parse_args([
            "explore",
            "--spec", "spec.yaml",
            "--config", "config.yaml",
            "--target-a", "a",
            "--target-b", "b",
            "--out", "./out",
        ])
        assert args.timeout == DEFAULT_TIMEOUT

    def test_explore_custom_timeout(self):
        """Test explore with custom --timeout."""
        args = parse_args([
            "explore",
            "--spec", "spec.yaml",
            "--config", "config.yaml",
            "--target-a", "a",
            "--target-b", "b",
            "--out", "./out",
            "--timeout", "60",
        ])
        assert args.timeout == 60.0

    def test_explore_timeout_float(self):
        """Test explore timeout accepts float values."""
        args = parse_args([
            "explore",
            "--spec", "spec.yaml",
            "--config", "config.yaml",
            "--target-a", "a",
            "--target-b", "b",
            "--out", "./out",
            "--timeout", "5.5",
        ])
        assert args.timeout == 5.5

    def test_explore_single_operation_timeout(self):
        """Test explore with single --operation-timeout."""
        args = parse_args([
            "explore",
            "--spec", "spec.yaml",
            "--config", "config.yaml",
            "--target-a", "a",
            "--target-b", "b",
            "--out", "./out",
            "--operation-timeout", "getUser:60",
        ])
        assert args.operation_timeout == {"getUser": 60.0}

    def test_explore_multiple_operation_timeouts(self):
        """Test explore with multiple --operation-timeout options."""
        args = parse_args([
            "explore",
            "--spec", "spec.yaml",
            "--config", "config.yaml",
            "--target-a", "a",
            "--target-b", "b",
            "--out", "./out",
            "--operation-timeout", "getUser:60",
            "--operation-timeout", "createItem:120",
            "--operation-timeout", "slowReport:300",
        ])
        assert args.operation_timeout == {
            "getUser": 60.0,
            "createItem": 120.0,
            "slowReport": 300.0,
        }

    def test_replay_custom_timeout(self):
        """Test replay with custom --timeout."""
        args = parse_args([
            "replay",
            "--config", "config.yaml",
            "--target-a", "a",
            "--target-b", "b",
            "--in", "./in",
            "--out", "./out",
            "--timeout", "90",
        ])
        assert args.timeout == 90.0

    def test_replay_operation_timeout(self):
        """Test replay with --operation-timeout."""
        args = parse_args([
            "replay",
            "--config", "config.yaml",
            "--target-a", "a",
            "--target-b", "b",
            "--in", "./in",
            "--out", "./out",
            "--operation-timeout", "slowOp:180",
        ])
        assert args.operation_timeout == {"slowOp": 180.0}

    def test_operation_timeout_with_colon_in_id(self):
        """Test operation timeout handles operationId with colon (uses rsplit)."""
        args = parse_args([
            "explore",
            "--spec", "spec.yaml",
            "--config", "config.yaml",
            "--target-a", "a",
            "--target-b", "b",
            "--out", "./out",
            "--operation-timeout", "namespace:getUser:60",
        ])
        assert args.operation_timeout == {"namespace:getUser": 60.0}


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


# =============================================================================
# Positive Value Validation Tests
# =============================================================================


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


# =============================================================================
# List Operations Integration Tests
# =============================================================================


class TestListOperationsIntegration:
    def test_list_operations_with_fixture(self, capsys):
        """Test list-operations with actual OpenAPI fixture."""
        args = ListOperationsArgs(spec=Path("tests/fixtures/test_api.yaml"))
        exit_code = run_list_operations(args)

        assert exit_code == 0
        captured = capsys.readouterr()

        # Check expected operations are listed
        assert "createWidget" in captured.out
        assert "getWidget" in captured.out
        assert "listWidgets" in captured.out
        assert "POST /widgets" in captured.out
        assert "GET /widgets/{widget_id}" in captured.out

        # Check links are shown
        assert "Links:" in captured.out
        assert "→" in captured.out

        # Check total count
        assert "Total:" in captured.out
        assert "operations" in captured.out

    def test_list_operations_nonexistent_file(self, capsys):
        """Test list-operations with nonexistent file returns error."""
        args = ListOperationsArgs(spec=Path("nonexistent.yaml"))
        exit_code = run_list_operations(args)

        assert exit_code == 1
        captured = capsys.readouterr()
        assert "Error loading spec" in captured.err
