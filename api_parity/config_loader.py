"""Config Loader - Loads runtime configuration and comparison rules.

Handles loading YAML config files with environment variable substitution,
loading JSON comparison rules, and loading the comparison library.

See ARCHITECTURE.md "Runtime Configuration" for specifications.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import yaml

from api_parity.models import (
    ComparisonLibrary,
    ComparisonRulesFile,
    OperationRules,
    RuntimeConfig,
    TargetConfig,
)


class ConfigError(Exception):
    """Raised when configuration loading fails."""


# Default comparison library location (relative to package)
DEFAULT_LIBRARY_PATH = Path(__file__).parent.parent / "prototype" / "comparison-rules" / "comparison_library.json"


def load_runtime_config(config_path: Path) -> RuntimeConfig:
    """Load runtime configuration from a YAML file.

    Supports ${ENV_VAR} substitution in string values.

    Args:
        config_path: Path to the YAML config file.

    Returns:
        RuntimeConfig model instance.

    Raises:
        ConfigError: If loading or validation fails.
    """
    if not config_path.exists():
        raise ConfigError(f"Config file not found: {config_path}")

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            raw_config = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ConfigError(f"Invalid YAML in config file: {e}") from e

    if not isinstance(raw_config, dict):
        raise ConfigError("Config file must be a YAML mapping")

    # Substitute environment variables
    raw_config = _substitute_env_vars(raw_config)

    try:
        return RuntimeConfig.model_validate(raw_config)
    except Exception as e:
        raise ConfigError(f"Invalid config structure: {e}") from e


def load_comparison_rules(rules_path: Path) -> ComparisonRulesFile:
    """Load comparison rules from a JSON file.

    Args:
        rules_path: Path to the JSON rules file.

    Returns:
        ComparisonRulesFile model instance.

    Raises:
        ConfigError: If loading or validation fails.
    """
    if not rules_path.exists():
        raise ConfigError(f"Comparison rules file not found: {rules_path}")

    try:
        with open(rules_path, "r", encoding="utf-8") as f:
            raw_rules = json.load(f)
    except json.JSONDecodeError as e:
        raise ConfigError(f"Invalid JSON in rules file: {e}") from e

    try:
        return ComparisonRulesFile.model_validate(raw_rules)
    except Exception as e:
        raise ConfigError(f"Invalid rules structure: {e}") from e


def load_comparison_library(library_path: Path | None = None) -> ComparisonLibrary:
    """Load the comparison library (predefined comparisons).

    Args:
        library_path: Path to the library JSON file. Uses default if None.

    Returns:
        ComparisonLibrary model instance.

    Raises:
        ConfigError: If loading or validation fails.
    """
    path = library_path or DEFAULT_LIBRARY_PATH

    if not path.exists():
        raise ConfigError(f"Comparison library not found: {path}")

    try:
        with open(path, "r", encoding="utf-8") as f:
            raw_library = json.load(f)
    except json.JSONDecodeError as e:
        raise ConfigError(f"Invalid JSON in library file: {e}") from e

    try:
        return ComparisonLibrary.model_validate(raw_library)
    except Exception as e:
        raise ConfigError(f"Invalid library structure: {e}") from e


def get_operation_rules(
    rules_file: ComparisonRulesFile,
    operation_id: str,
) -> OperationRules:
    """Get effective rules for an operation.

    Operation-specific rules override defaults for any key they define.
    There is no deep merging - if an operation specifies body rules,
    the entire body section is replaced.

    Args:
        rules_file: Loaded comparison rules file.
        operation_id: The operation ID to get rules for.

    Returns:
        OperationRules to use for comparison.
    """
    default = rules_file.default_rules
    override = rules_file.operation_rules.get(operation_id)

    if override is None:
        return default

    # Start with defaults, override specified fields
    return OperationRules(
        status_code=override.status_code if override.status_code is not None else default.status_code,
        headers=override.headers if override.headers else default.headers,
        body=override.body if override.body is not None else default.body,
    )


def resolve_comparison_rules_path(
    config_path: Path,
    rules_ref: str,
) -> Path:
    """Resolve comparison rules path relative to config file.

    Args:
        config_path: Path to the config file (for relative resolution).
        rules_ref: The comparison_rules value from config.

    Returns:
        Resolved absolute path to the rules file.
    """
    rules_path = Path(rules_ref)
    if rules_path.is_absolute():
        return rules_path
    return (config_path.parent / rules_path).resolve()


def validate_targets(
    config: RuntimeConfig,
    target_a_name: str,
    target_b_name: str,
) -> tuple[TargetConfig, TargetConfig]:
    """Validate and extract target configurations.

    Args:
        config: Runtime configuration.
        target_a_name: Name of target A.
        target_b_name: Name of target B.

    Returns:
        Tuple of (target_a_config, target_b_config).

    Raises:
        ConfigError: If targets are not found or invalid.
    """
    if target_a_name not in config.targets:
        available = ", ".join(config.targets.keys())
        raise ConfigError(
            f"Target '{target_a_name}' not found in config. Available: {available}"
        )

    if target_b_name not in config.targets:
        available = ", ".join(config.targets.keys())
        raise ConfigError(
            f"Target '{target_b_name}' not found in config. Available: {available}"
        )

    if target_a_name == target_b_name:
        raise ConfigError("Target A and Target B must be different")

    return config.targets[target_a_name], config.targets[target_b_name]


def _substitute_env_vars(data: Any) -> Any:
    """Recursively substitute ${ENV_VAR} patterns in data.

    Args:
        data: Data structure to process.

    Returns:
        Data with environment variables substituted.
    """
    if isinstance(data, str):
        return _substitute_string(data)
    elif isinstance(data, dict):
        return {k: _substitute_env_vars(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [_substitute_env_vars(item) for item in data]
    return data


def _substitute_string(s: str) -> str:
    """Substitute ${ENV_VAR} patterns in a string.

    Args:
        s: String to process.

    Returns:
        String with environment variables substituted.

    Raises:
        ConfigError: If referenced environment variable is not set.
    """
    pattern = re.compile(r"\$\{([^}]+)\}")

    def replacer(match: re.Match) -> str:
        var_name = match.group(1)
        value = os.environ.get(var_name)
        if value is None:
            raise ConfigError(f"Environment variable '{var_name}' is not set")
        return value

    return pattern.sub(replacer, s)
