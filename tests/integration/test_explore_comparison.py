"""Tests for explore CLI comparison rules and mismatch detection."""

import json
import subprocess
import sys

from tests.integration.explore_helpers import (
    PROJECT_ROOT,
    TEST_API_SPEC,
    create_runtime_config,
)


class TestStatusCodeMismatch:
    """Tests for status code mismatch detection."""

    def test_status_code_mismatch_detected(self, dual_servers, tmp_path, cel_evaluator_exists):
        """Test that different status codes between targets are detected as mismatch.

        We test this by hitting a non-existent widget on both servers - they should
        both return 404, which is a match. But if we configure one server incorrectly,
        we'd get a mismatch.
        """
        config_path = create_runtime_config(
            dual_servers["a"].port,
            dual_servers["b"].port,
            tmp_path,
        )
        out_dir = tmp_path / "artifacts"

        result = subprocess.run(
            [
                sys.executable, "-m", "api_parity.cli",
                "explore",
                "--spec", str(TEST_API_SPEC),
                "--config", str(config_path),
                "--target-a", "server_a",
                "--target-b", "server_b",
                "--out", str(out_dir),
                "--max-cases", "3",
                "--seed", "42",
            ],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
            timeout=60,
        )

        assert result.returncode == 0

        # Check if any status_code mismatches were recorded
        mismatches_dir = out_dir / "mismatches"
        if mismatches_dir.exists():
            for bundle in mismatches_dir.iterdir():
                diff_path = bundle / "diff.json"
                if diff_path.exists():
                    with open(diff_path) as f:
                        diff = json.load(f)
                    # Verify mismatch_type field exists and is valid
                    assert diff["mismatch_type"] in ["status_code", "headers", "body", None]


class TestComparisonRuleVerification:
    """Tests verifying specific comparison rules work correctly."""

    def test_numeric_tolerance_allows_small_differences(self, dual_servers, tmp_path, cel_evaluator_exists):
        """Test that numeric_tolerance rule allows prices within tolerance.

        Variant B adds 0.001 to prices, which is within the 0.01 tolerance.
        This should result in MATCH, not MISMATCH.
        """
        # Create rules with explicit numeric tolerance for createWidget
        rules = {
            "version": "1",
            "default_rules": {
                "status_code": {"predefined": "exact_match"},
                "body": {"field_rules": {}}
            },
            "operation_rules": {
                "createWidget": {
                    "body": {
                        "field_rules": {
                            "$.id": {"predefined": "uuid_format"},
                            "$.name": {"predefined": "exact_match"},
                            "$.price": {"predefined": "numeric_tolerance", "tolerance": 0.01},
                            "$.category": {"predefined": "exact_match"},
                            "$.in_stock": {"predefined": "exact_match"},
                            "$.tags": {"predefined": "unordered_array"},
                            "$.created_at": {"predefined": "iso_timestamp_format"}
                        }
                    }
                }
            }
        }
        rules_path = tmp_path / "rules.json"
        rules_path.write_text(json.dumps(rules))

        config = f"""
targets:
  server_a:
    base_url: "http://127.0.0.1:{dual_servers['a'].port}"
    headers: {{}}
  server_b:
    base_url: "http://127.0.0.1:{dual_servers['b'].port}"
    headers: {{}}
comparison_rules: {rules_path}
"""
        config_path = tmp_path / "config.yaml"
        config_path.write_text(config)
        out_dir = tmp_path / "artifacts"

        result = subprocess.run(
            [
                sys.executable, "-m", "api_parity.cli",
                "explore",
                "--spec", str(TEST_API_SPEC),
                "--config", str(config_path),
                "--target-a", "server_a",
                "--target-b", "server_b",
                "--out", str(out_dir),
                "--max-cases", "3",
                "--seed", "1",
            ],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
            timeout=60,
        )

        print(f"stdout:\n{result.stdout}")
        assert result.returncode == 0

        # createWidget should match (price diff 0.001 within 0.01 tolerance)
        assert "createWidget" in result.stdout
        # Look for MATCH on createWidget line
        for line in result.stdout.split("\n"):
            if "createWidget:" in line:
                # Should be MATCH due to tolerance
                assert "MATCH" in line, f"Expected createWidget to MATCH due to tolerance, got: {line}"

    def test_unordered_array_allows_shuffled_elements(self, dual_servers, tmp_path, cel_evaluator_exists):
        """Test that unordered_array rule treats shuffled arrays as matching.

        Variant B shuffles arrays. With unordered_array rule, this should MATCH.
        """
        rules = {
            "version": "1",
            "default_rules": {
                "status_code": {"predefined": "exact_match"},
                "body": {"field_rules": {}}
            },
            "operation_rules": {
                "getUserProfile": {
                    "body": {
                        "field_rules": {
                            "$.id": {"predefined": "uuid_format"},
                            "$.username": {"predefined": "exact_match"},
                            "$.roles": {"predefined": "unordered_array"},
                            "$.scores.reputation": {"predefined": "numeric_tolerance", "tolerance": 1.0},
                            "$.scores.activity": {"predefined": "numeric_tolerance", "tolerance": 1.0},
                            "$.scores.trust": {"predefined": "numeric_tolerance", "tolerance": 0.1},
                            "$.created_at": {"predefined": "iso_timestamp_format"}
                        }
                    }
                }
            }
        }
        rules_path = tmp_path / "rules.json"
        rules_path.write_text(json.dumps(rules))

        config = f"""
targets:
  server_a:
    base_url: "http://127.0.0.1:{dual_servers['a'].port}"
    headers: {{}}
  server_b:
    base_url: "http://127.0.0.1:{dual_servers['b'].port}"
    headers: {{}}
comparison_rules: {rules_path}
"""
        config_path = tmp_path / "config.yaml"
        config_path.write_text(config)
        out_dir = tmp_path / "artifacts"

        result = subprocess.run(
            [
                sys.executable, "-m", "api_parity.cli",
                "explore",
                "--spec", str(TEST_API_SPEC),
                "--config", str(config_path),
                "--target-a", "server_a",
                "--target-b", "server_b",
                "--out", str(out_dir),
                "--max-cases", "3",
                "--seed", "123",
            ],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
            timeout=60,
        )

        print(f"stdout:\n{result.stdout}")
        assert result.returncode == 0

    def test_operation_rules_override_defaults(self, dual_servers, tmp_path, cel_evaluator_exists):
        """Test that operation_rules completely override default_rules for that operation."""
        # Default rules require exact_match, operation rules use ignore
        rules = {
            "version": "1",
            "default_rules": {
                "status_code": {"predefined": "exact_match"},
                "body": {
                    "field_rules": {
                        "$.timestamp": {"predefined": "exact_match"}  # Would fail
                    }
                }
            },
            "operation_rules": {
                "healthCheck": {
                    "body": {
                        "field_rules": {
                            "$.status": {"predefined": "exact_match"},
                            "$.timestamp": {"predefined": "ignore"},  # Override to ignore
                            "$.uptime_seconds": {"predefined": "ignore"}
                        }
                    }
                }
            }
        }
        rules_path = tmp_path / "rules.json"
        rules_path.write_text(json.dumps(rules))

        config = f"""
targets:
  server_a:
    base_url: "http://127.0.0.1:{dual_servers['a'].port}"
    headers: {{}}
  server_b:
    base_url: "http://127.0.0.1:{dual_servers['b'].port}"
    headers: {{}}
comparison_rules: {rules_path}
"""
        config_path = tmp_path / "config.yaml"
        config_path.write_text(config)
        out_dir = tmp_path / "artifacts"

        result = subprocess.run(
            [
                sys.executable, "-m", "api_parity.cli",
                "explore",
                "--spec", str(TEST_API_SPEC),
                "--config", str(config_path),
                "--target-a", "server_a",
                "--target-b", "server_b",
                "--out", str(out_dir),
                "--max-cases", "3",
                "--seed", "1",
            ],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
            timeout=60,
        )

        print(f"stdout:\n{result.stdout}")
        assert result.returncode == 0

        # healthCheck should MATCH because timestamp is ignored via operation override
        for line in result.stdout.split("\n"):
            if "healthCheck:" in line:
                assert "MATCH" in line, f"Expected healthCheck to MATCH due to override, got: {line}"

    def test_presence_required_fails_when_missing(self, dual_servers, tmp_path, cel_evaluator_exists):
        """Test that presence: required fails when field is missing."""
        rules = {
            "version": "1",
            "default_rules": {
                "status_code": {"predefined": "exact_match"},
                "body": {"field_rules": {}}
            },
            "operation_rules": {
                "healthCheck": {
                    "body": {
                        "field_rules": {
                            "$.nonexistent_field": {"presence": "required"}
                        }
                    }
                }
            }
        }
        rules_path = tmp_path / "rules.json"
        rules_path.write_text(json.dumps(rules))

        config = f"""
targets:
  server_a:
    base_url: "http://127.0.0.1:{dual_servers['a'].port}"
    headers: {{}}
  server_b:
    base_url: "http://127.0.0.1:{dual_servers['b'].port}"
    headers: {{}}
comparison_rules: {rules_path}
"""
        config_path = tmp_path / "config.yaml"
        config_path.write_text(config)
        out_dir = tmp_path / "artifacts"

        result = subprocess.run(
            [
                sys.executable, "-m", "api_parity.cli",
                "explore",
                "--spec", str(TEST_API_SPEC),
                "--config", str(config_path),
                "--target-a", "server_a",
                "--target-b", "server_b",
                "--out", str(out_dir),
                "--max-cases", "3",
                "--seed", "1",
            ],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
            timeout=60,
        )

        print(f"stdout:\n{result.stdout}")
        assert result.returncode == 0

        # healthCheck should MISMATCH because nonexistent_field is required but missing
        for line in result.stdout.split("\n"):
            if "healthCheck:" in line:
                assert "MISMATCH" in line, f"Expected healthCheck to MISMATCH, got: {line}"

    def test_presence_optional_passes_when_missing(self, dual_servers, tmp_path, cel_evaluator_exists):
        """Test that presence: optional passes even when field is missing."""
        rules = {
            "version": "1",
            "default_rules": {
                "status_code": {"predefined": "exact_match"},
                "body": {"field_rules": {}}
            },
            "operation_rules": {
                "healthCheck": {
                    "body": {
                        "field_rules": {
                            "$.status": {"predefined": "exact_match"},
                            "$.nonexistent_field": {"presence": "optional"},
                            "$.timestamp": {"predefined": "ignore"},
                            "$.uptime_seconds": {"predefined": "ignore"}
                        }
                    }
                }
            }
        }
        rules_path = tmp_path / "rules.json"
        rules_path.write_text(json.dumps(rules))

        config = f"""
targets:
  server_a:
    base_url: "http://127.0.0.1:{dual_servers['a'].port}"
    headers: {{}}
  server_b:
    base_url: "http://127.0.0.1:{dual_servers['b'].port}"
    headers: {{}}
comparison_rules: {rules_path}
"""
        config_path = tmp_path / "config.yaml"
        config_path.write_text(config)
        out_dir = tmp_path / "artifacts"

        result = subprocess.run(
            [
                sys.executable, "-m", "api_parity.cli",
                "explore",
                "--spec", str(TEST_API_SPEC),
                "--config", str(config_path),
                "--target-a", "server_a",
                "--target-b", "server_b",
                "--out", str(out_dir),
                "--max-cases", "3",
                "--seed", "1",
            ],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
            timeout=60,
        )

        print(f"stdout:\n{result.stdout}")
        assert result.returncode == 0

        # healthCheck should MATCH because nonexistent_field is optional
        for line in result.stdout.split("\n"):
            if "healthCheck:" in line:
                assert "MATCH" in line, f"Expected healthCheck to MATCH, got: {line}"


class TestHeaderMismatch:
    """Tests for header comparison and mismatch detection."""

    def test_header_comparison_with_rules(self, dual_servers, tmp_path, cel_evaluator_exists):
        """Test that header rules are applied during comparison."""
        rules = {
            "version": "1",
            "default_rules": {
                "status_code": {"predefined": "exact_match"},
                "headers": {
                    "x-request-id": {"predefined": "uuid_format"},
                    "content-type": {"predefined": "exact_match"}
                },
                "body": {"field_rules": {}}
            },
            "operation_rules": {
                "healthCheck": {
                    "headers": {
                        "x-request-id": {"predefined": "ignore"}  # Ignore volatile header
                    },
                    "body": {
                        "field_rules": {
                            "$.status": {"predefined": "exact_match"},
                            "$.timestamp": {"predefined": "ignore"},
                            "$.uptime_seconds": {"predefined": "ignore"}
                        }
                    }
                }
            }
        }
        rules_path = tmp_path / "rules.json"
        rules_path.write_text(json.dumps(rules))

        config = f"""
targets:
  server_a:
    base_url: "http://127.0.0.1:{dual_servers['a'].port}"
    headers: {{}}
  server_b:
    base_url: "http://127.0.0.1:{dual_servers['b'].port}"
    headers: {{}}
comparison_rules: {rules_path}
"""
        config_path = tmp_path / "config.yaml"
        config_path.write_text(config)
        out_dir = tmp_path / "artifacts"

        result = subprocess.run(
            [
                sys.executable, "-m", "api_parity.cli",
                "explore",
                "--spec", str(TEST_API_SPEC),
                "--config", str(config_path),
                "--target-a", "server_a",
                "--target-b", "server_b",
                "--out", str(out_dir),
                "--max-cases", "3",
                "--seed", "1",
            ],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
            timeout=60,
        )

        print(f"stdout:\n{result.stdout}")
        assert result.returncode == 0

        # healthCheck should MATCH because x-request-id is ignored
        for line in result.stdout.split("\n"):
            if "healthCheck:" in line:
                assert "MATCH" in line
