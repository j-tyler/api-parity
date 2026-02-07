"""Measure how many rounds of chain generation it takes to cover all linked operations.

This prototype answers two questions:
1. How many seeds does it take to reach full coverage of linked operations?
2. Which operations are never reached, and why?

"Coverage" here means: every operation that participates in at least one link
(as source or target) appears in at least one generated chain. Orphan operations
(no links at all) are excluded — they're a separate problem handled by --ensure-coverage.

Usage:
    python prototype/coverage-analysis/measure_coverage.py <spec_path> [--max-seeds 50] [--max-chains 20] [--max-steps 6] [--trials 5]
"""

import argparse
import sys
import time
from pathlib import Path

# Add project root to path so we can import api_parity
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api_parity.case_generator import CaseGenerator


def get_linked_operations(spec_path: Path) -> tuple[set[str], set[str], set[str]]:
    """Classify operations by their link involvement.

    Returns:
        (all_ops, linked_ops, orphan_ops) where:
        - all_ops: every operationId in the spec
        - linked_ops: operations that are source or target of at least one link
        - orphan_ops: operations with no link involvement at all
    """
    import yaml

    with open(spec_path) as f:
        spec = yaml.safe_load(f)

    all_ops: set[str] = set()
    link_sources: set[str] = set()  # ops that HAVE outbound links
    link_targets: set[str] = set()  # ops that ARE targeted by links

    paths = spec.get("paths", {})
    for path_template, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if not isinstance(operation, dict) or method.startswith("$"):
                continue
            op_id = operation.get("operationId", f"{method}_{path_template}")
            all_ops.add(op_id)

            # Check responses for links
            for resp in operation.get("responses", {}).values():
                if not isinstance(resp, dict):
                    continue
                links = resp.get("links", {})
                if links:
                    link_sources.add(op_id)
                    for link_def in links.values():
                        if isinstance(link_def, dict):
                            target = link_def.get("operationId")
                            if target:
                                link_targets.add(target)

    linked_ops = link_sources | link_targets
    orphan_ops = all_ops - linked_ops

    return all_ops, linked_ops, orphan_ops


def measure_single_trial(
    spec_path: Path,
    linked_ops: set[str],
    max_seeds: int,
    max_chains: int,
    max_steps: int,
) -> dict:
    """Run chain generation with incrementing seeds until full linked coverage.

    Returns dict with:
        - seeds_to_full_coverage: number of seeds needed (or None if not reached)
        - coverage_by_seed: list of (seed, cumulative_coverage_count, new_ops)
        - never_reached: set of operations never covered
        - chains_per_seed: list of chain counts per seed
    """
    gen = CaseGenerator(spec_path)

    cumulative_covered: set[str] = set()
    coverage_by_seed: list[tuple[int, int, set[str]]] = []
    chains_per_seed: list[int] = []
    seeds_to_full_coverage = None

    for seed in range(max_seeds):
        chains = gen.generate_chains(
            max_chains=max_chains,
            max_steps=max_steps,
            seed=seed,
        )

        # Extract which operations appeared in this seed's chains
        ops_this_seed: set[str] = set()
        for chain in chains:
            for step in chain.steps:
                ops_this_seed.add(step.request_template.operation_id)

        new_ops = ops_this_seed - cumulative_covered
        cumulative_covered |= ops_this_seed
        chains_per_seed.append(len(chains))

        # Only record coverage of linked operations (not orphans that sneak in via free transitions)
        linked_covered = cumulative_covered & linked_ops
        coverage_by_seed.append((seed, len(linked_covered), new_ops))

        if linked_covered >= linked_ops:
            seeds_to_full_coverage = seed + 1  # 1-indexed count
            break

    never_reached = linked_ops - cumulative_covered
    return {
        "seeds_to_full_coverage": seeds_to_full_coverage,
        "coverage_by_seed": coverage_by_seed,
        "never_reached": never_reached,
        "chains_per_seed": chains_per_seed,
        "total_seeds_run": len(chains_per_seed),
    }


def analyze_unreachable(spec_path: Path, never_reached: set[str]) -> dict[str, list[str]]:
    """For each unreachable operation, diagnose WHY it's unreachable.

    Categories:
    - "no_inbound_links": Has no links pointing TO it (can only be reached by free transition)
    - "deep_only": Only reachable at depth 3+ (low probability)
    - "requires_specific_status": Only linked from a non-default status code
    - "terminal_only": Only appears as a link target, never as a source (dead-end)
    """
    import yaml

    with open(spec_path) as f:
        spec = yaml.safe_load(f)

    # Build link graph
    paths = spec.get("paths", {})
    inbound_links: dict[str, list[str]] = {}  # target -> [sources]
    outbound_links: dict[str, list[str]] = {}  # source -> [targets]
    link_status_codes: dict[str, list[str]] = {}  # target -> [status_codes from which linked]

    for path_template, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if not isinstance(operation, dict) or method.startswith("$"):
                continue
            op_id = operation.get("operationId", f"{method}_{path_template}")

            for resp_code, resp in operation.get("responses", {}).items():
                if not isinstance(resp, dict):
                    continue
                for link_def in resp.get("links", {}).values():
                    if not isinstance(link_def, dict):
                        continue
                    target = link_def.get("operationId")
                    if target:
                        inbound_links.setdefault(target, []).append(op_id)
                        outbound_links.setdefault(op_id, []).append(target)
                        link_status_codes.setdefault(target, []).append(str(resp_code))

    # Find entry points (operations with no required path params)
    entry_points: set[str] = set()
    for path_template, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if not isinstance(operation, dict) or method.startswith("$"):
                continue
            op_id = operation.get("operationId", f"{method}_{path_template}")
            params = operation.get("parameters", [])
            has_required_path = any(
                p.get("in") == "path" and p.get("required", False)
                for p in params if isinstance(p, dict)
            )
            if not has_required_path:
                entry_points.add(op_id)

    # BFS from entry points to compute depths
    depths: dict[str, int] = {}
    queue = list(entry_points)
    for ep in entry_points:
        depths[ep] = 0
    while queue:
        current = queue.pop(0)
        current_depth = depths[current]
        for target in outbound_links.get(current, []):
            if target not in depths:
                depths[target] = current_depth + 1
                queue.append(target)

    # Diagnose each unreachable operation
    reasons: dict[str, list[str]] = {}
    for op in never_reached:
        op_reasons = []
        if op not in inbound_links:
            op_reasons.append("no_inbound_links: No operations link TO this operation")
        if op in depths and depths[op] >= 3:
            op_reasons.append(f"deep_only: Minimum depth {depths[op]} from entry point")
        if op not in depths:
            op_reasons.append("unreachable_via_links: Not reachable from any entry point via links")
        if op not in outbound_links:
            op_reasons.append("terminal: Has no outbound links (dead-end in chain)")
        # Check for unusual status codes
        for code in link_status_codes.get(op, []):
            if code not in ("200", "201"):
                op_reasons.append(f"non_standard_status: Linked from status code {code}")

        if not op_reasons:
            op_reasons.append("unknown: Linked and reachable but still not covered (probabilistic)")

        reasons[op] = op_reasons

    return reasons


def main():
    parser = argparse.ArgumentParser(description="Measure chain coverage over multiple seeds")
    parser.add_argument("spec_path", type=Path, help="Path to OpenAPI spec")
    parser.add_argument("--max-seeds", type=int, default=50, help="Max seeds to try (default: 50)")
    parser.add_argument("--max-chains", type=int, default=20, help="Chains per seed (default: 20)")
    parser.add_argument("--max-steps", type=int, default=6, help="Steps per chain (default: 6)")
    parser.add_argument("--trials", type=int, default=3, help="Number of trials with different starting seeds (default: 3)")
    args = parser.parse_args()

    print(f"Spec: {args.spec_path}")
    print(f"Config: max_seeds={args.max_seeds}, max_chains={args.max_chains}, max_steps={args.max_steps}, trials={args.trials}")
    print()

    # Classify operations
    all_ops, linked_ops, orphan_ops = get_linked_operations(args.spec_path)
    print(f"Total operations: {len(all_ops)}")
    print(f"  Linked (in at least one link): {len(linked_ops)}")
    print(f"  Orphans (no link involvement): {len(orphan_ops)}")
    if orphan_ops:
        for op in sorted(orphan_ops):
            print(f"    - {op}")
    print()

    # Run multiple trials
    all_results = []
    overall_never_reached: set[str] = set(linked_ops)  # start with all, intersect

    for trial in range(args.trials):
        print(f"--- Trial {trial + 1}/{args.trials} ---")
        start = time.time()

        result = measure_single_trial(
            args.spec_path, linked_ops,
            max_seeds=args.max_seeds,
            max_chains=args.max_chains,
            max_steps=args.max_steps,
        )
        elapsed = time.time() - start
        all_results.append(result)

        if result["seeds_to_full_coverage"]:
            print(f"  Full linked coverage after {result['seeds_to_full_coverage']} seed(s)")
        else:
            covered = linked_ops - result["never_reached"]
            print(f"  NOT fully covered after {result['total_seeds_run']} seeds "
                  f"({len(covered)}/{len(linked_ops)} linked ops)")
            print(f"  Never reached: {sorted(result['never_reached'])}")

        # Show coverage progression
        prev_count = 0
        for seed, count, new_ops in result["coverage_by_seed"]:
            if count > prev_count:
                print(f"    Seed {seed}: {count}/{len(linked_ops)} linked ops "
                      f"(+{count - prev_count}: {sorted(new_ops & linked_ops) if new_ops & linked_ops else 'orphans only'})")
                prev_count = count

        avg_chains = sum(result["chains_per_seed"]) / len(result["chains_per_seed"])
        print(f"  Avg chains/seed: {avg_chains:.1f}")
        print(f"  Time: {elapsed:.1f}s")

        # Intersect never_reached across trials
        overall_never_reached &= result["never_reached"]
        print()

    # Summary
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)

    seeds_list = [r["seeds_to_full_coverage"] for r in all_results if r["seeds_to_full_coverage"]]
    if seeds_list:
        avg_seeds = sum(seeds_list) / len(seeds_list)
        print(f"Seeds to full linked coverage: avg={avg_seeds:.1f}, min={min(seeds_list)}, max={max(seeds_list)}")
        print(f"  ({len(seeds_list)}/{args.trials} trials achieved full coverage)")
    else:
        print(f"No trial achieved full linked coverage within {args.max_seeds} seeds")

    if overall_never_reached:
        print(f"\nPERMANENTLY UNREACHABLE (never covered in ANY trial):")
        reasons = analyze_unreachable(args.spec_path, overall_never_reached)
        for op in sorted(overall_never_reached):
            print(f"  {op}:")
            for reason in reasons.get(op, ["unknown"]):
                print(f"    - {reason}")
    else:
        print(f"\nAll linked operations were eventually reached.")

    # Show orphan reminder
    if orphan_ops:
        print(f"\nReminder: {len(orphan_ops)} orphan operations need --ensure-coverage:")
        for op in sorted(orphan_ops):
            print(f"  - {op}")


if __name__ == "__main__":
    main()
