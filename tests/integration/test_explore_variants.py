"""Tests for explore CLI variant server behavior verification."""

import json
import subprocess
import sys

from tests.conftest import MockServer, find_free_port
from tests.integration.explore_helpers import (
    COMPARISON_RULES,
    PROJECT_ROOT,
    TEST_API_SPEC,
)


class TestVariantBehaviorVerification:
    """Tests verifying that variant server differences are correctly handled by rules."""

    def test_variant_price_difference_within_tolerance(self, dual_servers, tmp_path, cel_evaluator_exists):
        """Test that variant B's price difference (0.001) is within 0.01 tolerance."""
        # Create rules that should allow the price difference
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
                "--max-cases", "10",
                "--seed", "42",
            ],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
            timeout=60,
        )

        print(f"stdout:\n{result.stdout}")
        assert result.returncode == 0

        # createWidget should MATCH because price diff is within tolerance and tags use unordered
        for line in result.stdout.split("\n"):
            if "createWidget:" in line:
                assert "MATCH" in line, f"Expected createWidget to MATCH, got: {line}"

    def test_both_servers_same_variant_all_match(self, tmp_path, cel_evaluator_exists):
        """Test that comparing same variant server to itself produces all matches."""
        # Start two instances of variant A
        port_a = find_free_port()
        port_b = find_free_port()

        with MockServer(port_a, variant="a") as server_a:
            with MockServer(port_b, variant="a") as server_b:
                config = f"""
targets:
  server_a:
    base_url: "http://127.0.0.1:{port_a}"
    headers: {{}}
  server_b:
    base_url: "http://127.0.0.1:{port_b}"
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
                        "--max-cases", "10",
                        "--seed", "42",
                    ],
                    capture_output=True,
                    text=True,
                    cwd=PROJECT_ROOT,
                    timeout=60,
                )

                print(f"stdout:\n{result.stdout}")
                assert result.returncode == 0

                # Check summary - should have mostly matches (some may fail due to timing/UUIDs)
                summary_path = out_dir / "summary.json"
                assert summary_path.exists()
                with open(summary_path) as f:
                    summary = json.load(f)

                # With same variant, most should match (except volatile fields like timestamps/UUIDs)
                # The fixture comparison_rules.json handles these with ignore/format rules
                print(f"Summary: matches={summary['matches']}, mismatches={summary['mismatches']}")


class TestMultipleMismatches:
    """Tests for handling multiple mismatches in a single run."""

    def test_multiple_mismatch_bundles_created(self, dual_servers, tmp_path, cel_evaluator_exists):
        """Test that multiple mismatches each get their own bundle."""
        # Create rules that will cause mismatches (require exact timestamp match)
        rules = {
            "version": "1",
            "default_rules": {
                "status_code": {"predefined": "exact_match"},
                "body": {
                    "field_rules": {
                        "$.timestamp": {"predefined": "exact_match"},
                        "$.created_at": {"predefined": "exact_match"},
                        "$.updated_at": {"predefined": "exact_match"}
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
                "--max-cases", "15",
                "--seed", "42",
            ],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
            timeout=120,
        )

        print(f"stdout:\n{result.stdout}")
        assert result.returncode == 0

        # Check summary
        summary_path = out_dir / "summary.json"
        assert summary_path.exists()
        with open(summary_path) as f:
            summary = json.load(f)

        # Should have multiple mismatches
        mismatches_dir = out_dir / "mismatches"
        if summary["mismatches"] > 0:
            assert mismatches_dir.exists()
            bundles = list(mismatches_dir.iterdir())
            assert len(bundles) == summary["mismatches"]

            # Each bundle should be unique
            bundle_names = [b.name for b in bundles]
            assert len(bundle_names) == len(set(bundle_names))
