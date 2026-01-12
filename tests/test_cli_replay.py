"""Tests for replay subcommand argument parsing."""

from pathlib import Path

import pytest

from api_parity.cli import DEFAULT_TIMEOUT, ReplayArgs, parse_args, parse_replay_args


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
