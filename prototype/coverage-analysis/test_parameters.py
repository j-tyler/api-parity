"""Test how max_chains and max_steps affect coverage of the hard pattern (depth 5).

Focuses on the hard_api_spec.yaml where chainE5 at depth 5 is the only
consistently hard-to-reach operation.

Tests:
1. Does increasing max_chains help? (Hypothesis seems to generate ~150-180 regardless)
2. Does increasing max_steps help? (Allows longer chains, more chances to reach depth 5)
3. How many actual chains does Hypothesis generate vs what we request?
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api_parity.case_generator import CaseGenerator

SPEC = Path("prototype/coverage-analysis/hard_api_spec.yaml")
TARGET_OP = "chainE5"


def test_config(max_chains: int, max_steps: int, seeds_to_try: int = 10) -> dict:
    """Run chain generation with given config, report when chainE5 is first seen."""
    gen = CaseGenerator(SPEC)

    for seed in range(seeds_to_try):
        chains = gen.generate_chains(max_chains=max_chains, max_steps=max_steps, seed=seed)

        ops_covered = set()
        max_chain_len = 0
        for chain in chains:
            max_chain_len = max(max_chain_len, len(chain.steps))
            for step in chain.steps:
                ops_covered.add(step.request_template.operation_id)

        if TARGET_OP in ops_covered:
            return {
                "found_at_seed": seed,
                "chains_generated": len(chains),
                "max_chain_length": max_chain_len,
                "ops_covered": len(ops_covered),
            }

    return {
        "found_at_seed": None,
        "total_seeds_tried": seeds_to_try,
    }


def main():
    print("Testing how parameters affect depth-5 chain coverage")
    print(f"Target operation: {TARGET_OP}")
    print()

    configs = [
        # (max_chains, max_steps, description)
        (5, 6, "Low chains, normal steps"),
        (20, 6, "Default config"),
        (50, 6, "High chains, normal steps"),
        (100, 6, "Very high chains, normal steps"),
        (20, 4, "Default chains, SHORT steps (too short for depth 5)"),
        (20, 5, "Default chains, EXACT steps (just barely enough)"),
        (20, 8, "Default chains, long steps"),
        (20, 10, "Default chains, very long steps"),
        (50, 8, "High chains + long steps"),
    ]

    results = []
    for max_chains, max_steps, desc in configs:
        print(f"Testing: {desc} (max_chains={max_chains}, max_steps={max_steps})")
        start = time.time()
        result = test_config(max_chains, max_steps, seeds_to_try=15)
        elapsed = time.time() - start

        if result.get("found_at_seed") is not None:
            seed = result["found_at_seed"]
            print(f"  FOUND at seed {seed} ({result['chains_generated']} chains, "
                  f"max_len={result['max_chain_length']}, "
                  f"{result['ops_covered']} ops) [{elapsed:.1f}s]")
        else:
            print(f"  NOT FOUND in {result['total_seeds_tried']} seeds [{elapsed:.1f}s]")

        results.append((desc, max_chains, max_steps, result, elapsed))
        print()

    # Also measure: how many chains does Hypothesis actually generate?
    print("=" * 60)
    print("CHAINS ACTUALLY GENERATED vs REQUESTED")
    print("=" * 60)
    gen = CaseGenerator(SPEC)
    for requested in [5, 10, 20, 50, 100]:
        chains = gen.generate_chains(max_chains=requested, max_steps=6, seed=42)
        print(f"  Requested: {requested:4d}  |  Generated: {len(chains):4d}")

    # Summary
    print()
    print("=" * 60)
    print("KEY FINDINGS")
    print("=" * 60)

    # Group by max_steps
    for max_steps_val in [4, 5, 6, 8, 10]:
        step_results = [(d, mc, ms, r, e) for d, mc, ms, r, e in results if ms == max_steps_val]
        if step_results:
            print(f"\nmax_steps={max_steps_val}:")
            for desc, mc, ms, r, e in step_results:
                if r.get("found_at_seed") is not None:
                    print(f"  max_chains={mc:3d}: Found at seed {r['found_at_seed']}")
                else:
                    print(f"  max_chains={mc:3d}: Not found in {r['total_seeds_tried']} seeds")


if __name__ == "__main__":
    main()
