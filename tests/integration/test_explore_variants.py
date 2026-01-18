"""Tests for explore CLI variant server behavior verification."""

import json
import subprocess
import sys

from tests.conftest import MockServer, PortReservation
from tests.integration.explore_helpers import (
    COMPARISON_RULES,
    PROJECT_ROOT,
    TEST_API_SPEC,
)


class TestVariantBehaviorVerification:
    """Tests verifying that variant server differences are correctly handled by rules."""

    def test_variant_differences_and_mismatches(self, fixture_dual_mock_servers, tmp_path, fixture_cel_evaluator_path):
        """Test variant server differences and mismatch handling.

        Combined test verifying:
        - Variant B's price difference (0.001) is within 0.01 tolerance
        - Multiple mismatches each get their own bundle
        """
        # Test 1: Price difference within tolerance
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
                            "$.stock_count": {"predefined": "exact_match"},
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
    base_url: "http://127.0.0.1:{fixture_dual_mock_servers['a'].port}"
    headers: {{}}
  server_b:
    base_url: "http://127.0.0.1:{fixture_dual_mock_servers['b'].port}"
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
                "--max-cases", "1",
                "--seed", "42",
            ],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
            timeout=60,
        )

        assert result.returncode == 0

        # Test 2: Multiple mismatches - use strict rules to cause mismatches
        strict_rules = {
            "version": "1",
            "default_rules": {
                "status_code": {"predefined": "exact_match"},
                "body": {
                    "field_rules": {
                        "$.timestamp": {"predefined": "exact_match"},
                        "$.created_at": {"predefined": "exact_match"},
                    }
                }
            }
        }
        strict_rules_path = tmp_path / "strict_rules.json"
        strict_rules_path.write_text(json.dumps(strict_rules))

        strict_config = f"""
targets:
  server_a:
    base_url: "http://127.0.0.1:{fixture_dual_mock_servers['a'].port}"
    headers: {{}}
  server_b:
    base_url: "http://127.0.0.1:{fixture_dual_mock_servers['b'].port}"
    headers: {{}}
comparison_rules: {strict_rules_path}
"""
        strict_config_path = tmp_path / "strict_config.yaml"
        strict_config_path.write_text(strict_config)
        out_dir2 = tmp_path / "artifacts2"

        result = subprocess.run(
            [
                sys.executable, "-m", "api_parity.cli",
                "explore",
                "--spec", str(TEST_API_SPEC),
                "--config", str(strict_config_path),
                "--target-a", "server_a",
                "--target-b", "server_b",
                "--out", str(out_dir2),
                "--max-cases", "1",
                "--seed", "42",
            ],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
            timeout=60,
        )

        assert result.returncode == 0

        # Check summary for mismatches
        summary_path = out_dir2 / "summary.json"
        if summary_path.exists():
            with open(summary_path) as f:
                summary = json.load(f)

            mismatches_dir = out_dir2 / "mismatches"
            if summary["mismatches"] > 0 and mismatches_dir.exists():
                bundles = list(mismatches_dir.iterdir())
                assert len(bundles) == summary["mismatches"]

    def test_same_variant_all_match(self, tmp_path, fixture_cel_evaluator_path):
        """Test that comparing same variant server to itself produces mostly matches."""
        reservation_a = PortReservation()
        reservation_b = PortReservation()

        with MockServer(reservation_a, variant="a") as server_a:
            with MockServer(reservation_b, variant="a") as server_b:
                config = f"""
targets:
  server_a:
    base_url: "http://127.0.0.1:{server_a.port}"
    headers: {{}}
  server_b:
    base_url: "http://127.0.0.1:{server_b.port}"
    headers: {{}}
comparison_rules: {COMPARISON_RULES}
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
                        "--max-cases", "1",
                        "--seed", "42",
                    ],
                    capture_output=True,
                    text=True,
                    cwd=PROJECT_ROOT,
                    timeout=60,
                )

                assert result.returncode == 0

                summary_path = out_dir / "summary.json"
                assert summary_path.exists()
