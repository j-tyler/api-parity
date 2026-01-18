"""Tests for lint-spec subcommand."""

from pathlib import Path

import pytest
import yaml

from api_parity.cli import (
    LintSpecArgs,
    parse_args,
    parse_lint_spec_args,
    run_lint_spec,
)


class TestLintSpecArgs:
    """Tests for lint-spec argument parsing."""

    def test_basic_lint_spec(self):
        """Test lint-spec with required --spec."""
        args = parse_args(["lint-spec", "--spec", "openapi.yaml"])
        assert isinstance(args, LintSpecArgs)
        assert args.spec == Path("openapi.yaml")
        assert args.output == "text"  # default

    def test_lint_spec_with_json_output(self):
        """Test lint-spec with --output json."""
        args = parse_args(["lint-spec", "--spec", "openapi.yaml", "--output", "json"])
        assert isinstance(args, LintSpecArgs)
        assert args.output == "json"

    def test_lint_spec_with_text_output(self):
        """Test lint-spec with explicit --output text."""
        args = parse_args(["lint-spec", "--spec", "openapi.yaml", "--output", "text"])
        assert isinstance(args, LintSpecArgs)
        assert args.output == "text"

    def test_lint_spec_missing_spec(self):
        """Test lint-spec fails without --spec."""
        with pytest.raises(SystemExit) as exc_info:
            parse_args(["lint-spec"])
        assert exc_info.value.code == 2

    def test_lint_spec_invalid_output(self):
        """Test lint-spec fails with invalid --output value."""
        with pytest.raises(SystemExit) as exc_info:
            parse_args(["lint-spec", "--spec", "openapi.yaml", "--output", "xml"])
        assert exc_info.value.code == 2

    def test_parse_lint_spec_args_converts_correctly(self):
        """Test parse_lint_spec_args converts namespace to dataclass."""
        import argparse
        namespace = argparse.Namespace(
            command="lint-spec",
            spec=Path("spec.yaml"),
            output="json",
        )
        args = parse_lint_spec_args(namespace)
        assert isinstance(args, LintSpecArgs)
        assert args.spec == Path("spec.yaml")
        assert args.output == "json"


class TestRunLintSpec:
    """Tests for run_lint_spec function."""

    def test_run_with_valid_spec(self, tmp_path, capsys):
        """Test run_lint_spec with a valid spec."""
        spec = {
            "openapi": "3.0.3",
            "info": {"title": "Test", "version": "1.0"},
            "paths": {
                "/items": {
                    "get": {
                        "operationId": "listItems",
                        "responses": {
                            "200": {
                                "description": "OK",
                                "content": {
                                    "application/json": {
                                        "schema": {"type": "array"}
                                    }
                                },
                            }
                        },
                    }
                },
            },
        }

        spec_file = tmp_path / "valid.yaml"
        with open(spec_file, "w") as f:
            yaml.dump(spec, f)

        args = LintSpecArgs(spec=spec_file, output="text")
        result = run_lint_spec(args)

        assert result == 0  # No errors
        captured = capsys.readouterr()
        assert "Total operations:" in captured.out

    def test_run_with_invalid_spec_path(self, tmp_path, capsys):
        """Test run_lint_spec with non-existent spec."""
        args = LintSpecArgs(spec=tmp_path / "nonexistent.yaml", output="text")
        result = run_lint_spec(args)

        assert result == 1
        captured = capsys.readouterr()
        assert "Error" in captured.err

    def test_run_with_json_output(self, tmp_path, capsys):
        """Test run_lint_spec with JSON output."""
        import json

        spec = {
            "openapi": "3.0.3",
            "info": {"title": "Test", "version": "1.0"},
            "paths": {
                "/items": {
                    "get": {
                        "operationId": "listItems",
                        "responses": {"200": {"description": "OK"}},
                    }
                },
            },
        }

        spec_file = tmp_path / "test.yaml"
        with open(spec_file, "w") as f:
            yaml.dump(spec, f)

        args = LintSpecArgs(spec=spec_file, output="json")
        result = run_lint_spec(args)

        assert result == 0
        captured = capsys.readouterr()
        # Should be valid JSON
        output = json.loads(captured.out)
        assert "errors" in output
        assert "warnings" in output
        assert "summary" in output

    def test_run_with_errors_returns_nonzero(self, tmp_path, capsys):
        """Test run_lint_spec returns non-zero when spec has errors."""
        spec = {
            "openapi": "3.0.3",
            "info": {"title": "Test", "version": "1.0"},
            "paths": {
                "/items": {
                    "post": {
                        "operationId": "createItem",
                        "responses": {
                            "201": {
                                "description": "Created",
                                "links": {
                                    "GetItem": {
                                        "operationId": "nonExistentOperation",
                                    }
                                },
                            }
                        },
                    }
                },
            },
        }

        spec_file = tmp_path / "errors.yaml"
        with open(spec_file, "w") as f:
            yaml.dump(spec, f)

        args = LintSpecArgs(spec=spec_file, output="text")
        result = run_lint_spec(args)

        assert result == 1
        captured = capsys.readouterr()
        assert "ERRORS" in captured.out or "FAIL" in captured.out

    def test_run_with_test_fixture(self):
        """Test run_lint_spec with actual test fixture."""
        spec_path = Path(__file__).parent / "fixtures" / "test_api.yaml"
        if not spec_path.exists():
            pytest.skip("Test fixture not found")

        args = LintSpecArgs(spec=spec_path, output="text")
        result = run_lint_spec(args)

        # test_api.yaml should be well-formed (no errors)
        assert result == 0
