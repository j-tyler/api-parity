"""Shared helpers for explore CLI integration tests."""

from pathlib import Path

# Directory structure: tests/integration/explore_helpers.py
# Paths are relative to this file's location in the test tree.
FIXTURES_DIR: Path = Path(__file__).parent.parent / "fixtures"
TEST_API_SPEC: Path = FIXTURES_DIR / "test_api.yaml"
COMPARISON_RULES: Path = FIXTURES_DIR / "comparison_rules.json"
# All operations in test_api.yaml. Used to generate --exclude args so that
# tests only run the operations they actually need (without --max-cases, the
# generator produces 100 cases per operation, which is far too slow for tests).
ALL_OPERATIONS: list[str] = [
    "listWidgets", "createWidget", "getWidget", "updateWidget",
    "deleteWidget", "getUserProfile", "createOrder", "getOrder", "healthCheck",
]


def exclude_ops_except(*keep: str) -> list[str]:
    """Generate --exclude arguments for all operations except the ones to keep.

    Usage:
        run_cli("explore", *exclude_ops_except("healthCheck", "createWidget"), ...)
    """
    args: list[str] = []
    for op in ALL_OPERATIONS:
        if op not in keep:
            args.extend(["--exclude", op])
    return args


def create_runtime_config(port_a: int, port_b: int, tmp_path: Path) -> Path:
    """Create a runtime config pointing to the test servers."""
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
    config_path = tmp_path / "runtime_config.yaml"
    config_path.write_text(config)
    return config_path
