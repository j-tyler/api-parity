#!/usr/bin/env python3
"""
Validates comparison rules config and inlines predefined comparisons to CEL.

This script demonstrates the config loading pipeline:
1. Load config JSON
2. Validate against JSON Schema
3. Inline all predefined comparisons to CEL expressions
4. Output normalized config (runtime only needs CEL)

Usage:
    python validate_and_inline.py example_config.json

Requirements:
    pip install jsonschema
"""

import json
import sys
import copy
from pathlib import Path
from typing import Any

try:
    import jsonschema
except ImportError:
    print("Error: jsonschema not installed. Run: pip install jsonschema")
    sys.exit(1)


def load_json(path: Path) -> dict:
    """Load JSON file."""
    with open(path) as f:
        return json.load(f)


def load_library(script_dir: Path) -> dict:
    """Load the predefined comparison library."""
    library_path = script_dir / "comparison_library.json"
    library = load_json(library_path)
    return library["predefined"]


def load_schema(script_dir: Path) -> dict:
    """Load the JSON Schema."""
    schema_path = script_dir / "comparison_rules.schema.json"
    return load_json(schema_path)


def validate_config(config: dict, schema: dict) -> list[str]:
    """Validate config against schema. Returns list of error messages."""
    errors = []
    validator = jsonschema.Draft202012Validator(schema)
    for error in validator.iter_errors(config):
        path = " -> ".join(str(p) for p in error.absolute_path) or "(root)"
        errors.append(f"{path}: {error.message}")
    return errors


def inline_predefined(comparison: dict, library: dict) -> dict:
    """
    Convert a predefined comparison to a CEL expression.

    Input:  {"predefined": "numeric_tolerance", "tolerance": 0.01}
    Output: {"expr": "(a - b) <= 0.01 && (b - a) <= 0.01"}
    """
    if "expr" in comparison:
        # Already a custom expression, return as-is
        return comparison

    if "predefined" not in comparison:
        # Presence-only rule (no value comparison), return as-is
        if "presence" in comparison:
            return comparison
        raise ValueError(f"Invalid comparison: {comparison}")

    name = comparison["predefined"]
    if name not in library:
        raise ValueError(f"Unknown predefined comparison: {name}")

    definition = library[name]
    expr = definition["expr"]

    # Substitute parameters into expression
    for param in definition["params"]:
        if param not in comparison:
            raise ValueError(f"Missing required parameter '{param}' for predefined '{name}'")
        value = comparison[param]
        # Handle string values (need quoting and escaping) vs numeric
        if isinstance(value, str):
            # Escape backslashes first, then quotes for CEL string literal
            escaped = value.replace("\\", "\\\\").replace('"', '\\"')
            expr = expr.replace(param, f'"{escaped}"')
        else:
            expr = expr.replace(param, str(value))

    return {"expr": expr}


def inline_field_rules(field_rules: dict, library: dict) -> dict:
    """Inline all field rules."""
    return {
        path: inline_predefined(comparison, library)
        for path, comparison in field_rules.items()
    }


def inline_body_rules(body: dict, library: dict) -> dict:
    """Inline body comparison rules."""
    result = copy.deepcopy(body)
    if "field_rules" in result:
        result["field_rules"] = inline_field_rules(result["field_rules"], library)
    return result


def inline_comparison_rules(rules: dict, library: dict) -> dict:
    """Inline a comparison_rules object."""
    result = copy.deepcopy(rules)

    if "status_code" in result:
        result["status_code"] = inline_predefined(result["status_code"], library)

    if "headers" in result:
        result["headers"] = {
            name: inline_predefined(rule, library)
            for name, rule in result["headers"].items()
        }

    if "body" in result:
        result["body"] = inline_body_rules(result["body"], library)

    return result


def inline_config(config: dict, library: dict) -> dict:
    """Inline all predefined comparisons in the config."""
    result = copy.deepcopy(config)

    if "default_rules" in result:
        result["default_rules"] = inline_comparison_rules(result["default_rules"], library)

    if "operation_rules" in result:
        result["operation_rules"] = {
            op_id: inline_comparison_rules(rules, library)
            for op_id, rules in result["operation_rules"].items()
        }

    return result


def main():
    if len(sys.argv) < 2:
        print("Usage: python validate_and_inline.py <config.json>")
        print("\nValidates config and outputs inlined version with CEL expressions.")
        sys.exit(1)

    config_path = Path(sys.argv[1])
    script_dir = Path(__file__).parent

    # Load files
    print(f"Loading config: {config_path}")
    config = load_json(config_path)

    print(f"Loading library: {script_dir / 'comparison_library.json'}")
    library = load_library(script_dir)
    print(f"  Loaded {len(library)} predefined comparisons")

    print(f"Loading schema: {script_dir / 'comparison_rules.schema.json'}")
    schema = load_schema(script_dir)

    # Validate
    print("\nValidating config against schema...")
    errors = validate_config(config, schema)
    if errors:
        print("Validation FAILED:")
        for error in errors:
            print(f"  - {error}")
        sys.exit(1)
    print("Validation PASSED")

    # Inline
    print("\nInlining predefined comparisons...")
    inlined = inline_config(config, library)

    # Output
    print("\n" + "=" * 60)
    print("INLINED CONFIG (what runtime sees):")
    print("=" * 60)
    print(json.dumps(inlined, indent=2))

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)

    cel_exprs = []

    def collect_exprs(obj, path=""):
        if isinstance(obj, dict):
            if "expr" in obj:
                cel_exprs.append((path, obj["expr"]))
            for k, v in obj.items():
                collect_exprs(v, f"{path}.{k}" if path else k)
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                collect_exprs(v, f"{path}[{i}]")

    collect_exprs(inlined)

    print(f"\nTotal CEL expressions: {len(cel_exprs)}")
    print("\nExpressions by path:")
    for path, expr in cel_exprs:
        # Truncate long expressions for display
        display_expr = expr if len(expr) <= 60 else expr[:57] + "..."
        print(f"  {path}:")
        print(f"    {display_expr}")


if __name__ == "__main__":
    main()
