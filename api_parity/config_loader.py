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
    FieldRule,
    OperationRules,
    RuntimeConfig,
    TargetConfig,
)


class ConfigError(Exception):
    """Raised when configuration loading fails."""


# Default comparison library location (relative to package)
DEFAULT_LIBRARY_PATH = Path(__file__).parent.parent / "prototype" / "comparison-rules" / "comparison_library.json"


def load_runtime_config(config_path: Path) -> RuntimeConfig:
    """Load runtime configuration from YAML with ${ENV_VAR} substitution."""
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
    """Load comparison rules from JSON."""
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
    """Load predefined comparisons from library JSON. Uses DEFAULT_LIBRARY_PATH if None."""
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
    """Get effective rules for an operation, merging defaults with any override.

    Override semantics: operation rules replace defaults at the field level
    (status_code, headers, body). No deep merging within fields.
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
    """Resolve rules_ref relative to config_path's directory. Absolute paths pass through."""
    rules_path = Path(rules_ref)
    if rules_path.is_absolute():
        return rules_path
    return (config_path.parent / rules_path).resolve()


def validate_targets(
    config: RuntimeConfig,
    target_a_name: str,
    target_b_name: str,
) -> tuple[TargetConfig, TargetConfig]:
    """Validate that both targets exist and are different, then return their configs."""
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
    """Recursively substitute ${ENV_VAR} patterns in strings within data."""
    if isinstance(data, str):
        return _substitute_string(data)
    elif isinstance(data, dict):
        return {k: _substitute_env_vars(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [_substitute_env_vars(item) for item in data]
    return data


def _substitute_string(s: str) -> str:
    """Substitute ${ENV_VAR} patterns. Raises ConfigError if env var is not set."""
    pattern = re.compile(r"\$\{([^}]+)\}")

    def replacer(match: re.Match) -> str:
        var_name = match.group(1)
        value = os.environ.get(var_name)
        if value is None:
            raise ConfigError(f"Environment variable '{var_name}' is not set")
        return value

    return pattern.sub(replacer, s)


class ValidationWarning:
    """A non-fatal validation warning."""

    def __init__(self, category: str, message: str) -> None:
        self.category = category
        self.message = message

    def __str__(self) -> str:
        return f"[{self.category}] {self.message}"


class ValidationError:
    """A fatal validation error."""

    def __init__(self, category: str, message: str) -> None:
        self.category = category
        self.message = message

    def __str__(self) -> str:
        return f"[{self.category}] {self.message}"


class ValidationResult:
    """Result of cross-validation checks."""

    def __init__(self) -> None:
        self.warnings: list[ValidationWarning] = []
        self.errors: list[ValidationError] = []

    def add_warning(self, category: str, message: str) -> None:
        self.warnings.append(ValidationWarning(category, message))

    def add_error(self, category: str, message: str) -> None:
        self.errors.append(ValidationError(category, message))

    @property
    def is_valid(self) -> bool:
        """True if no errors (warnings are OK)."""
        return len(self.errors) == 0

    def merge(self, other: "ValidationResult") -> None:
        """Merge another result into this one."""
        self.warnings.extend(other.warnings)
        self.errors.extend(other.errors)


def validate_comparison_rules(
    rules: ComparisonRulesFile,
    library: ComparisonLibrary,
    spec_operation_ids: set[str],
) -> ValidationResult:
    """Validate rules against spec and library.

    Checks: (1) operationIds exist in spec, (2) predefined names exist in library,
    (3) required parameters for predefined rules are present.
    """
    result = ValidationResult()
    valid_predefined = set(library.predefined.keys())

    # Check operation_rules operationIds
    for op_id in rules.operation_rules.keys():
        if op_id not in spec_operation_ids:
            result.add_warning(
                "operation_rules",
                f"operationId '{op_id}' not found in spec. "
                f"Rules for this operation will be ignored."
            )

    # Validate predefined names and parameters in default_rules
    _validate_operation_rules(
        rules.default_rules, valid_predefined, library, "default_rules", result
    )

    # Validate predefined names and parameters in each operation override
    for op_id, op_rules in rules.operation_rules.items():
        _validate_operation_rules(
            op_rules, valid_predefined, library, f"operation_rules.{op_id}", result
        )

    return result


def _validate_operation_rules(
    rules: OperationRules,
    valid_predefined: set[str],
    library: ComparisonLibrary,
    context: str,
    result: ValidationResult,
) -> None:
    """Validate predefined names and parameters in rules. Adds issues to result."""
    # Check status_code rule
    if rules.status_code and rules.status_code.predefined:
        _validate_field_rule(
            rules.status_code, valid_predefined, library,
            f"{context}.status_code", result
        )

    # Check header rules
    for header_name, header_rule in rules.headers.items():
        if header_rule.predefined:
            _validate_field_rule(
                header_rule, valid_predefined, library,
                f"{context}.headers.{header_name}", result
            )

    # Check body field rules
    if rules.body and rules.body.field_rules:
        for jsonpath, field_rule in rules.body.field_rules.items():
            if field_rule.predefined:
                _validate_field_rule(
                    field_rule, valid_predefined, library,
                    f"{context}.body.field_rules[{jsonpath}]", result
                )


def _validate_field_rule(
    rule: FieldRule,
    valid_predefined: set[str],
    library: ComparisonLibrary,
    context: str,
    result: ValidationResult,
) -> None:
    """Validate a field rule's predefined name exists and has required params."""
    predefined_name = rule.predefined
    if predefined_name not in valid_predefined:
        result.add_error(
            "predefined",
            f"{context}: Unknown predefined '{predefined_name}'. "
            f"Valid options: {', '.join(sorted(valid_predefined))}"
        )
        return

    # Check required parameters
    predefined_def = library.predefined[predefined_name]
    for param in predefined_def.params:
        param_value = getattr(rule, param, None)
        if param_value is None:
            result.add_error(
                "predefined",
                f"{context}: Predefined '{predefined_name}' requires parameter "
                f"'{param}' but it was not provided."
            )


def validate_cli_operation_ids(
    exclude_ops: list[str],
    operation_timeouts: dict[str, float],
    spec_operation_ids: set[str],
) -> ValidationResult:
    """Warn if --exclude or --operation-timeout operationIds don't exist in spec."""
    result = ValidationResult()

    # Check --exclude operationIds
    for op_id in exclude_ops:
        if op_id not in spec_operation_ids:
            result.add_warning(
                "exclude",
                f"--exclude '{op_id}' not found in spec. Flag has no effect."
            )

    # Check --operation-timeout operationIds
    for op_id in operation_timeouts.keys():
        if op_id not in spec_operation_ids:
            result.add_warning(
                "operation-timeout",
                f"--operation-timeout '{op_id}' not found in spec. "
                f"Timeout will never be used."
            )

    return result
