"""Check which operations get state machine rules in Schemathesis.

Operations without rules are completely invisible to the state machine.
This script reveals the exact boundary between "reachable" and "invisible".
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import yaml
import schemathesis
from hypothesis.stateful import RuleBasedStateMachine
from schemathesis.config import StatefulPhaseConfig, PhasesConfig, ProjectConfig, ProjectsConfig, SchemathesisConfig

_InferenceConfig = type(StatefulPhaseConfig().inference)


def check_rules(spec_path: Path):
    """Show which operations have state machine rules."""
    # Load spec to get all operation IDs
    with open(spec_path) as f:
        spec = yaml.safe_load(f)

    all_ops = set()
    for path_template, path_item in spec.get("paths", {}).items():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if not isinstance(operation, dict) or method.startswith("$"):
                continue
            op_id = operation.get("operationId", f"{method}_{path_template}")
            all_ops.add(op_id)

    # Create schema with inference disabled (same as CaseGenerator)
    inference = _InferenceConfig(algorithms=[])
    stateful = StatefulPhaseConfig(inference=inference)
    phases = PhasesConfig(stateful=stateful)
    project = ProjectConfig(phases=phases)
    projects = ProjectsConfig(default=project)
    config = SchemathesisConfig(projects=projects)

    schema = schemathesis.openapi.from_path(str(spec_path), config=config)

    # Get state machine and inspect its rules
    StateMachineClass = schema.as_state_machine()

    # Inspect all methods and attributes to find rule-related ones
    print(f"Spec: {spec_path}")
    print(f"Total operations in spec: {len(all_ops)}")
    print(f"  Operations: {sorted(all_ops)}")

    # Hypothesis state machines store rules in class attributes
    # Look for rule markers on methods
    rule_methods = []
    all_methods = []
    for name in dir(StateMachineClass):
        if name.startswith('_'):
            continue
        attr = getattr(StateMachineClass, name)
        if callable(attr):
            all_methods.append(name)
            # Check for hypothesis rule marker
            if hasattr(attr, 'hypothesis_rule_data'):
                rule_methods.append(name)

    print(f"\nCallable methods (non-private): {sorted(all_methods)}")
    print(f"Methods with hypothesis_rule_data: {sorted(rule_methods)}")

    # Also look at Hypothesis's internal rule storage
    # RuleBasedStateMachine stores rules in a class-level structure
    try:
        # Try to get rules from Hypothesis's internal registry
        from hypothesis.stateful import _RuleWrapper
    except ImportError:
        pass

    # Inspect the MRO and look for rule definitions
    print(f"\nMRO: {[c.__name__ for c in StateMachineClass.__mro__]}")

    # Check for rules_to_run or similar
    for attr_name in ['_rules_per_class', '_base_rules_per_class', '_initializes_per_class']:
        if hasattr(RuleBasedStateMachine, attr_name):
            val = getattr(RuleBasedStateMachine, attr_name)
            if isinstance(val, dict):
                if StateMachineClass in val:
                    print(f"\n{attr_name}[{StateMachineClass.__name__}]:")
                    rules = val[StateMachineClass]
                    for r in rules:
                        print(f"  {r}")


if __name__ == "__main__":
    specs = [
        Path("tests/fixtures/test_api.yaml"),
        Path("prototype/coverage-analysis/medium_api_spec.yaml"),
        Path("prototype/coverage-analysis/hard_api_spec.yaml"),
    ]

    for spec_path in specs:
        print("=" * 60)
        check_rules(spec_path)
        print()
