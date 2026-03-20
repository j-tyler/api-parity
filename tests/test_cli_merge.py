"""Tests for merge subcommand argument parsing."""

from pathlib import Path

import pytest

from api_parity.cli import MergeArgs, parse_args


class TestMergeArgParsing:
    def test_valid_args_multiple_inputs(self):
        """Test merge with multiple --in directories."""
        args = parse_args(["merge", "--in", "dir1", "dir2", "--out", "outdir"])
        assert isinstance(args, MergeArgs)
        assert len(args.input_dirs) == 2
        assert args.input_dirs[0] == Path("dir1")
        assert args.input_dirs[1] == Path("dir2")
        assert args.out == Path("outdir")

    def test_single_input_dir(self):
        """Test merge with a single --in directory."""
        args = parse_args(["merge", "--in", "dir1", "--out", "outdir"])
        assert isinstance(args, MergeArgs)
        assert len(args.input_dirs) == 1
        assert args.input_dirs[0] == Path("dir1")

    def test_missing_in_fails(self):
        """Test merge fails without --in."""
        with pytest.raises(SystemExit) as exc_info:
            parse_args(["merge", "--out", "outdir"])
        assert exc_info.value.code == 2

    def test_missing_out_fails(self):
        """Test merge fails without --out."""
        with pytest.raises(SystemExit) as exc_info:
            parse_args(["merge", "--in", "dir1"])
        assert exc_info.value.code == 2
