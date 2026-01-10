#!/usr/bin/env python3
"""
Schemathesis Case Generation Exploration

Goal: Understand how to generate HTTP request cases from an OpenAPI spec
WITHOUT making actual HTTP calls. This validates whether Schemathesis
can serve as our request generator for api-parity.
"""

import json
import schemathesis
from schemathesis.openapi import from_path
from pathlib import Path

SPEC_PATH = Path(__file__).parent / "sample_api.yaml"


def unwrap_result(result):
    """Schemathesis wraps operations in Ok/Err results."""
    if hasattr(result, 'ok'):
        return result.ok()
    return result


def get_operation_id(operation):
    """Get operationId from operation definition."""
    raw = operation.definition.raw
    return raw.get('operationId', f"{operation.method}_{operation.path}")


def explore_schema_structure():
    """Understand what a loaded schema looks like."""
    print("=" * 60)
    print("LOADING SCHEMA")
    print("=" * 60)

    schema = from_path(SPEC_PATH)

    print(f"Schema type: {type(schema).__name__}")
    print(f"Key methods: as_state_machine, as_strategy, get_all_operations")
    print()

    print("=" * 60)
    print("API OPERATIONS")
    print("=" * 60)

    for result in schema.get_all_operations():
        operation = unwrap_result(result)
        raw = operation.definition.raw
        op_id = get_operation_id(operation)

        print(f"\n{operation.method.upper()} {operation.path}")
        print(f"  operationId: {op_id}")

        # Check for links in responses
        responses = raw.get('responses', {})
        for status, response in responses.items():
            if isinstance(response, dict) and 'links' in response:
                print(f"  Links from {status}:")
                for link_name, link_def in response['links'].items():
                    print(f"    -> {link_name}: {link_def.get('operationId')}")


def generate_cases_for_operation(operation, max_cases=10):
    """Generate test cases for a single operation using Hypothesis."""
    from hypothesis import given, settings, Phase

    strategy = operation.as_strategy()
    collected = []

    @given(case=strategy)
    @settings(max_examples=max_cases, database=None, phases=[Phase.generate])
    def collect_cases(case):
        collected.append(case)

    try:
        collect_cases()
    except Exception:
        pass  # Hypothesis exits when max_examples reached

    return collected


def case_to_dict(case):
    """Convert a Schemathesis Case to a dictionary for inspection."""
    result = {
        "operation_id": get_operation_id(case.operation),
        "method": case.method,
        "path_template": case.path,
    }

    # Path parameters
    if case.path_parameters:
        result["path_parameters"] = dict(case.path_parameters)
        # Compute formatted path
        formatted = case.path
        for k, v in case.path_parameters.items():
            formatted = formatted.replace(f"{{{k}}}", str(v))
        result["formatted_path"] = formatted
    else:
        result["path_parameters"] = {}
        result["formatted_path"] = case.path

    # Query parameters
    if case.query:
        result["query"] = dict(case.query)
    else:
        result["query"] = {}

    # Headers
    if case.headers:
        result["headers"] = dict(case.headers)
    else:
        result["headers"] = {}

    # Cookies
    if case.cookies:
        result["cookies"] = dict(case.cookies)
    else:
        result["cookies"] = {}

    # Body
    if hasattr(case, 'body') and case.body is not None:
        # Check if it's the NotSet sentinel
        if not str(type(case.body).__name__) == 'NotSet':
            result["body"] = case.body
            result["media_type"] = case.media_type

    return result


def generate_all_cases(max_per_operation=50):
    """Generate cases for all operations in the schema."""
    print("\n" + "=" * 60)
    print("GENERATING CASES (NO HTTP CALLS)")
    print("=" * 60)

    schema = from_path(SPEC_PATH)
    all_cases = []

    for result in schema.get_all_operations():
        operation = unwrap_result(result)
        op_id = get_operation_id(operation)
        print(f"\nGenerating cases for: {op_id}")

        cases = generate_cases_for_operation(operation, max_per_operation)
        print(f"  Generated {len(cases)} cases")

        for case in cases:
            case_dict = case_to_dict(case)
            all_cases.append(case_dict)

    return all_cases


def explore_stateful_testing():
    """Explore Schemathesis stateful testing / link traversal."""
    print("\n" + "=" * 60)
    print("STATEFUL TESTING EXPLORATION")
    print("=" * 60)

    schema = from_path(SPEC_PATH)

    # Check state machine support
    print("\nChecking as_state_machine()...")
    try:
        state_machine = schema.as_state_machine()
        print(f"State machine type: {type(state_machine).__name__}")
        print(f"Attributes: {[a for a in dir(state_machine) if not a.startswith('_')][:15]}")
    except Exception as e:
        print(f"Error creating state machine: {e}")

    # Stateful module exploration
    import schemathesis.stateful as stateful
    print(f"\nStateful module: {[a for a in dir(stateful) if not a.startswith('_')]}")


def explore_case_methods():
    """Explore what methods are available on Case objects."""
    print("\n" + "=" * 60)
    print("CASE OBJECT EXPLORATION")
    print("=" * 60)

    schema = from_path(SPEC_PATH)

    for result in schema.get_all_operations():
        operation = unwrap_result(result)
        cases = generate_cases_for_operation(operation, 1)
        if cases:
            case = cases[0]
            print(f"\nCase type: {type(case).__name__}")
            print(f"Case attributes: {[a for a in dir(case) if not a.startswith('_')]}")

            # Check for as_transport_kwargs - this is how requests are made
            if hasattr(case, 'as_transport_kwargs'):
                kwargs = case.as_transport_kwargs()
                print(f"\nas_transport_kwargs() returns:")
                print(json.dumps(kwargs, indent=2, default=str))
            break


def main():
    print("Schemathesis Case Generation Validation")
    print("=" * 60)

    # 1. Explore schema structure
    explore_schema_structure()

    # 2. Explore case object methods
    explore_case_methods()

    # 3. Generate cases without HTTP calls
    cases = generate_all_cases(max_per_operation=50)

    # 4. Save cases to file for review
    output_path = Path(__file__).parent / "generated_cases.json"
    with open(output_path, 'w') as f:
        json.dump(cases, f, indent=2, default=str)
    print(f"\n\nSaved {len(cases)} cases to {output_path}")

    # 5. Print sample cases for each operation type
    print("\n" + "=" * 60)
    print("SAMPLE CASES BY OPERATION")
    print("=" * 60)

    seen_ops = set()
    for case in cases:
        op_id = case['operation_id']
        if op_id not in seen_ops:
            seen_ops.add(op_id)
            print(f"\n--- {op_id} ---")
            print(json.dumps(case, indent=2, default=str))

    # 6. Explore stateful testing
    explore_stateful_testing()


if __name__ == "__main__":
    main()
