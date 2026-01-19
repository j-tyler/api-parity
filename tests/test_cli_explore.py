"""Tests for explore subcommand argument parsing."""

from pathlib import Path

import pytest

from api_parity.cli import DEFAULT_TIMEOUT, ExploreArgs, parse_args, parse_explore_args


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
            log_chains=False,
            ensure_coverage=False,
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
        assert args.log_chains is False

    def test_parse_explore_args_stateful_mode(self):
        """Test parse_explore_args handles stateful mode flags."""
        import argparse
        namespace = argparse.Namespace(
            command="explore",
            spec=Path("spec.yaml"),
            config=Path("config.yaml"),
            target_a="a",
            target_b="b",
            out=Path("./out"),
            seed=None,
            max_cases=None,
            validate=False,
            exclude=[],
            timeout=30.0,
            operation_timeout=[],
            stateful=True,
            max_chains=50,
            max_steps=10,
            log_chains=True,
            ensure_coverage=False,
        )
        args = parse_explore_args(namespace)
        assert args.stateful is True
        assert args.max_chains == 50
        assert args.max_steps == 10
        assert args.log_chains is True
        assert args.ensure_coverage is False

    def test_parse_explore_args_ensure_coverage_flag(self):
        """Test parse_explore_args handles ensure_coverage flag."""
        import argparse
        namespace = argparse.Namespace(
            command="explore",
            spec=Path("spec.yaml"),
            config=Path("config.yaml"),
            target_a="a",
            target_b="b",
            out=Path("./out"),
            seed=None,
            max_cases=None,
            validate=False,
            exclude=[],
            timeout=30.0,
            operation_timeout=[],
            stateful=True,
            max_chains=20,
            max_steps=6,
            log_chains=False,
            ensure_coverage=True,
        )
        args = parse_explore_args(namespace)
        assert args.stateful is True
        assert args.ensure_coverage is True

    def test_parse_explore_args_exclude_with_ensure_coverage(self):
        """Test that exclude and ensure_coverage can be used together.

        Verifies that the args are correctly parsed when both --exclude
        and --ensure-coverage are specified. The actual behavior (excluded
        operations not appearing in coverage warnings) is tested by the
        exclude parameter being passed to _run_stateful_explore.
        """
        import argparse
        namespace = argparse.Namespace(
            command="explore",
            spec=Path("spec.yaml"),
            config=Path("config.yaml"),
            target_a="a",
            target_b="b",
            out=Path("./out"),
            seed=None,
            max_cases=None,
            validate=False,
            exclude=["excludedOp1", "excludedOp2"],
            timeout=30.0,
            operation_timeout=[],
            stateful=True,
            max_chains=20,
            max_steps=6,
            log_chains=False,
            ensure_coverage=True,
        )
        args = parse_explore_args(namespace)
        assert args.stateful is True
        assert args.ensure_coverage is True
        assert args.exclude == ["excludedOp1", "excludedOp2"]


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
