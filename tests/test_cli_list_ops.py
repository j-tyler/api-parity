"""Tests for list-operations subcommand argument parsing."""

from pathlib import Path

import pytest

from api_parity.cli import ListOperationsArgs, parse_args, parse_list_ops_args


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
