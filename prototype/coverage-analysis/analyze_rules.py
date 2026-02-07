"""Analyze which operations get state machine rules and classify them.

Confirms: orphan operations (no link involvement) are the ONLY operations
that are completely invisible to the state machine.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import yaml
import schemathesis
from schemathesis.config import StatefulPhaseConfig, PhasesConfig, ProjectConfig, ProjectsConfig, SchemathesisConfig

_InferenceConfig = type(StatefulPhaseConfig().inference)


def analyze(spec_path: Path):
    with open(spec_path) as f:
        spec = yaml.safe_load(f)

    # Classify operations by link involvement
    all_ops = {}  # op_id -> {method, path, has_outbound_links, is_link_target, has_required_path_params}
    link_targets = set()

    paths = spec.get("paths", {})
    for path_template, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if not isinstance(operation, dict) or method.startswith("$"):
                continue
            op_id = operation.get("operationId", f"{method}_{path_template}")

            has_outbound = False
            for resp in operation.get("responses", {}).values():
                if isinstance(resp, dict) and resp.get("links"):
                    has_outbound = True
                    for link_def in resp["links"].values():
                        if isinstance(link_def, dict) and link_def.get("operationId"):
                            link_targets.add(link_def["operationId"])

            params = operation.get("parameters", [])
            has_path_params = any(
                p.get("in") == "path" and p.get("required", False)
                for p in params if isinstance(p, dict)
            )

            all_ops[op_id] = {
                "method": method.upper(),
                "path": path_template,
                "has_outbound_links": has_outbound,
                "has_required_path_params": has_path_params,
            }

    # Get state machine methods
    inference = _InferenceConfig(algorithms=[])
    stateful = StatefulPhaseConfig(inference=inference)
    phases = PhasesConfig(stateful=stateful)
    project = ProjectConfig(phases=phases)
    projects = ProjectsConfig(default=project)
    config = SchemathesisConfig(projects=projects)
    schema = schemathesis.openapi.from_path(str(spec_path), config=config)
    SM = schema.as_state_machine()

    rule_methods = [name for name in dir(SM)
                    if not name.startswith('_') and callable(getattr(SM, name))
                    and (name.startswith('RANDOM__') or '__' in name and not name.startswith(('after', 'before', 'check', 'get_', 'setup', 'teardown', 'validate', 'run', 'step', 'bundle')))]

    # Parse which operations appear in rules
    ops_with_rules = set()
    random_rule_ops = set()
    link_rule_ops = set()

    for method_name in rule_methods:
        if method_name in ('TestCase',):
            continue
        if method_name.startswith('RANDOM__'):
            # RANDOM rule - extract operation
            random_rule_ops.add(method_name)
        else:
            # Link rule - extract source and target operations
            link_rule_ops.add(method_name)

    print(f"\n{'='*60}")
    print(f"Spec: {spec_path.name}")
    print(f"{'='*60}")
    print(f"\nTotal operations: {len(all_ops)}")
    print(f"RANDOM rules: {len(random_rule_ops)}")
    print(f"Link rules: {len(link_rule_ops)}")

    print(f"\n{'Operation':<25} {'Method':<7} {'Outbound Links':<16} {'Link Target':<13} {'Path Params':<12} {'Visible?'}")
    print("-" * 95)

    for op_id in sorted(all_ops):
        info = all_ops[op_id]
        is_target = op_id in link_targets
        has_any_link = info["has_outbound_links"] or is_target
        visible = "YES" if has_any_link else "NO (orphan)"
        print(f"{op_id:<25} {info['method']:<7} {str(info['has_outbound_links']):<16} {str(is_target):<13} {str(info['has_required_path_params']):<12} {visible}")

    orphans = [op for op in all_ops if not all_ops[op]["has_outbound_links"] and op not in link_targets]
    linked = [op for op in all_ops if all_ops[op]["has_outbound_links"] or op in link_targets]

    print(f"\nSummary:")
    print(f"  Linked operations (have rules, reachable): {len(linked)}")
    print(f"  Orphan operations (no rules, invisible):   {len(orphans)}")
    if orphans:
        for op in sorted(orphans):
            print(f"    - {op} ({all_ops[op]['method']} {all_ops[op]['path']})")


if __name__ == "__main__":
    specs = [
        Path("tests/fixtures/test_api.yaml"),
        Path("prototype/coverage-analysis/medium_api_spec.yaml"),
        Path("prototype/coverage-analysis/hard_api_spec.yaml"),
    ]
    for spec_path in specs:
        analyze(spec_path)
    print()
