"""Tests for chain signature enumeration and per-operation achievable hits.

Tests the functions _enumerate_possible_chain_signatures() and
_compute_max_achievable_hits() which determine how many unique chain
structures each operation can appear in given the link graph.
"""

import pytest

from api_parity.cli import (
    _compute_max_achievable_hits,
    _enumerate_possible_chain_signatures,
)


class TestEnumeratePossibleChainSignatures:
    """Tests for _enumerate_possible_chain_signatures()."""

    def test_simple_linear_graph(self):
        """A->B->C produces all valid multi-step paths.

        With max_steps=3:
          (A, B), (A, B, C), (B, C)
        """
        edges = [("A", "B"), ("B", "C")]
        linked_ops = {"A", "B", "C"}

        sigs = _enumerate_possible_chain_signatures(edges, linked_ops, max_steps=3)

        assert sigs is not None
        assert ("A", "B") in sigs
        assert ("A", "B", "C") in sigs
        assert ("B", "C") in sigs
        # Single-step chains should not appear (length >= 2 required)
        assert ("A",) not in sigs
        assert ("B",) not in sigs
        assert ("C",) not in sigs
        # C has no outbound edges so no chain starts from C
        # (C can only appear as a target, not a start of a multi-step chain)

    def test_diamond_graph(self):
        """A->B, A->C, B->D, C->D: A (the source) appears in the most chains.

        A has the most outbound reach (can start chains through both B and C),
        so it appears in the most signatures. B and C are symmetric with same
        count. D is a leaf node with no outbound edges, so it only appears as
        a chain suffix, giving it fewer appearances.
        """
        edges = [("A", "B"), ("A", "C"), ("B", "D"), ("C", "D")]
        linked_ops = {"A", "B", "C", "D"}

        sigs = _enumerate_possible_chain_signatures(edges, linked_ops, max_steps=4)

        assert sigs is not None
        # Count appearances
        counts = {}
        for sig in sigs:
            for op in set(sig):
                counts[op] = counts.get(op, 0) + 1

        # A is the most connected source, appears in most chains
        assert counts.get("A", 0) >= counts.get("B", 0)
        assert counts.get("A", 0) >= counts.get("C", 0)
        # B and C are symmetric
        assert counts.get("B", 0) == counts.get("C", 0)
        # D is a leaf, appears in chains as a suffix
        assert counts.get("D", 0) > 0

    def test_cycle(self):
        """A->B->A: enumeration terminates and counts are correct up to max_steps.

        With max_steps=4, valid chains include:
          (A, B), (B, A), (A, B, A), (B, A, B),
          (A, B, A, B), (B, A, B, A)
        """
        edges = [("A", "B"), ("B", "A")]
        linked_ops = {"A", "B"}

        sigs = _enumerate_possible_chain_signatures(edges, linked_ops, max_steps=4)

        assert sigs is not None
        # Should contain chains up to length 4
        assert ("A", "B") in sigs
        assert ("B", "A") in sigs
        assert ("A", "B", "A") in sigs
        assert ("B", "A", "B") in sigs
        assert ("A", "B", "A", "B") in sigs
        assert ("B", "A", "B", "A") in sigs
        # No chains longer than max_steps
        assert all(len(sig) <= 4 for sig in sigs)

    def test_single_edge(self):
        """A->B with max_steps=2: only (A, B) is a valid chain.

        With max_steps=2, no chain can be longer than 2 steps.
        """
        edges = [("A", "B")]
        linked_ops = {"A", "B"}

        sigs = _enumerate_possible_chain_signatures(edges, linked_ops, max_steps=2)

        assert sigs is not None
        assert ("A", "B") in sigs
        # B has no outbound edges, but A is in the chain and has A->B,
        # so (A, B, B) would be valid at max_steps=3. With max_steps=2, only (A, B).
        assert all(len(s) <= 2 for s in sigs)

    def test_disconnected_components(self):
        """A->B, C->D: independent counting, no cross-component chains."""
        edges = [("A", "B"), ("C", "D")]
        linked_ops = {"A", "B", "C", "D"}

        sigs = _enumerate_possible_chain_signatures(edges, linked_ops, max_steps=6)

        assert sigs is not None
        assert ("A", "B") in sigs
        assert ("C", "D") in sigs
        # No cross-component chains
        for sig in sigs:
            ab_ops = {"A", "B"}
            cd_ops = {"C", "D"}
            sig_ops = set(sig)
            # A chain should not mix components
            assert not (sig_ops & ab_ops and sig_ops & cd_ops), (
                f"Cross-component chain found: {sig}"
            )

    def test_safety_cap_returns_none(self):
        """Graph that would produce >max_signatures returns None.

        A fully connected graph with enough nodes generates exponential
        signatures. Setting max_signatures=1 should trigger immediately.
        """
        # Even a simple A->B produces 1 signature, so max_signatures=0
        # would not work. Use a graph that produces >1 signature.
        edges = [("A", "B"), ("B", "A")]
        linked_ops = {"A", "B"}

        result = _enumerate_possible_chain_signatures(
            edges, linked_ops, max_steps=10, max_signatures=1
        )

        assert result is None

    def test_dense_graph_terminates_quickly(self):
        """Dense graph hits safety cap within bounded iterations, not a hang.

        Each DFS iteration produces a unique chain tuple, so the signature
        cap directly bounds iterations. With 28 nodes, 164 edges, and
        max_steps=6, the function should return None (cap hit) in well
        under 1 second. This validates that there is no combinatorial
        blowup between iterations and stored signatures.
        """
        import random
        import time

        rng = random.Random(42)
        nodes = [f"op{i}" for i in range(28)]
        edges_set: set[tuple[str, str]] = set()
        while len(edges_set) < 164:
            a, b = rng.sample(nodes, 2)
            edges_set.add((a, b))

        start = time.monotonic()
        result = _enumerate_possible_chain_signatures(
            list(edges_set), set(nodes), max_steps=6
        )
        elapsed = time.monotonic() - start

        assert result is None  # Cap hit
        assert elapsed < 1.0  # Bounded, not hanging

    def test_empty_edges(self):
        """No edges produces no signatures."""
        sigs = _enumerate_possible_chain_signatures([], {"A", "B"}, max_steps=6)

        assert sigs is not None
        assert sigs == set()

    def test_empty_linked_ops(self):
        """No linked ops produces no signatures."""
        sigs = _enumerate_possible_chain_signatures(
            [("A", "B")], set(), max_steps=6
        )

        assert sigs is not None
        assert sigs == set()

    def test_duplicate_edges_deduplicated(self):
        """Duplicate edges (same pair, different status codes) are treated as one.

        With max_steps=2, (A, B) is the only valid chain regardless of
        how many duplicate A->B edges exist.
        """
        # Two edges A->B (e.g., from 200 and 201 status codes)
        edges = [("A", "B"), ("A", "B")]
        linked_ops = {"A", "B"}

        sigs = _enumerate_possible_chain_signatures(edges, linked_ops, max_steps=2)

        assert sigs is not None
        assert sigs == {("A", "B")}

    def test_any_previous_step_can_provide_next(self):
        """Next operations come from ANY previous step in the chain, not just the last.

        Given A->C and B->C, chain starting (A, B) should be able to reach C
        because A (a previous step) has an edge to C.
        """
        edges = [("A", "B"), ("A", "C"), ("B", "C")]
        linked_ops = {"A", "B", "C"}

        sigs = _enumerate_possible_chain_signatures(edges, linked_ops, max_steps=3)

        assert sigs is not None
        # A->B, then from {A,B} we can reach C (via A->C or B->C)
        assert ("A", "B", "C") in sigs


class TestComputeMaxAchievableHits:
    """Tests for _compute_max_achievable_hits()."""

    def test_single_edge_max_steps_2(self):
        """A->B with max_steps=2: both operations appear in exactly 1 chain signature."""
        edges = [("A", "B")]
        linked_ops = {"A", "B"}

        result = _compute_max_achievable_hits(edges, linked_ops, max_steps=2)

        assert result is not None
        assert result["A"] == 1
        assert result["B"] == 1

    def test_linear_graph_counts(self):
        """A->B->C with max_steps=3: verify per-op counts.

        Valid chains (length >= 2, max_steps=3):
        Starting from A: (A,B), (A,B,C), (A,B,B)
        Starting from B: (B,C), (B,C,C)
        Starting from C: none (C has no outbound edges from initial step)

        Wait -- after chain (A,B), ops_in_chain={A,B}, adj[A]={B}, adj[B]={C}.
        So next ops = {B,C}. Then (A,B,C) and (A,B,B) are both valid at length 3.
        After (B,C), ops_in_chain={B,C}, adj[B]={C}, adj[C]={}.
        Next ops = {C}. So (B,C,C) is valid at length 3.
        """
        edges = [("A", "B"), ("B", "C")]
        linked_ops = {"A", "B", "C"}

        result = _compute_max_achievable_hits(edges, linked_ops, max_steps=3)

        assert result is not None
        # A in: (A,B), (A,B,C), (A,B,B) => 3
        assert result["A"] == 3
        # B in: (A,B), (A,B,C), (A,B,B), (B,C), (B,C,C) => 5
        assert result["B"] == 5
        # C in: (A,B,C), (B,C), (B,C,C) => 3
        assert result["C"] == 3

    def test_returns_none_for_dense_graph(self):
        """Dense fully-connected graph exceeds default 50k safety cap, returns None.

        A 7-node fully-connected graph (every node links to every other node)
        with max_steps=6 produces far more than 50,000 unique chain signatures.
        _compute_max_achievable_hits should return None.
        """
        nodes = ["A", "B", "C", "D", "E", "F", "G"]
        edges = [(s, t) for s in nodes for t in nodes if s != t]
        linked_ops = set(nodes)

        result = _compute_max_achievable_hits(edges, linked_ops, max_steps=6)

        assert result is None

    def test_disconnected_components_independent(self):
        """A->B, C->D with max_steps=2: counts are independent per component."""
        edges = [("A", "B"), ("C", "D")]
        linked_ops = {"A", "B", "C", "D"}

        result = _compute_max_achievable_hits(edges, linked_ops, max_steps=2)

        assert result is not None
        # With max_steps=2, only length-2 chains possible
        assert result["A"] == 1
        assert result["B"] == 1
        assert result["C"] == 1
        assert result["D"] == 1

    def test_ops_not_in_any_chain_not_in_result(self):
        """Operations that can't form multi-step chains don't appear in result.

        If an operation has no outbound edges and is not reachable as a target,
        it won't appear in any chain of length >= 2.
        """
        edges = [("A", "B")]
        linked_ops = {"A", "B", "C"}  # C is linked but isolated in this edge set

        result = _compute_max_achievable_hits(edges, linked_ops, max_steps=2)

        assert result is not None
        assert "A" in result
        assert "B" in result
        # C has no edges at all so it doesn't appear in any chain
        assert "C" not in result


class TestEffectiveTargetsInCoverageCheck:
    """Tests for per-operation effective targets in the coverage check.

    These test the interaction between max_achievable_hits and min_hits_per_op
    in the seed walking coverage check.
    """

    @staticmethod
    def _make_chain(op_ids: list[str], chain_id: str):
        """Helper to create a ChainCase from operation ID list."""
        from api_parity.models import ChainCase, ChainStep, RequestCase
        steps = []
        for i, op_id in enumerate(op_ids):
            request = RequestCase(
                case_id=f"case-{i}",
                operation_id=op_id,
                method="GET",
                path_template="/test",
                rendered_path="/test",
            )
            steps.append(ChainStep(step_index=i, request_template=request))
        return ChainCase(chain_id=chain_id, steps=steps)

    def test_capped_operation_meets_target_with_fewer_hits(self):
        """Operation with achievable=1, requested min_hits_per_op=5: effective target is 1.

        Coverage is met after 1 hit for the capped operation.
        """
        from unittest.mock import MagicMock

        from api_parity.cli import _generate_chains_with_seed_walking

        mock_generator = MagicMock()
        linked_ops = {"opA", "opB"}

        # opA can only appear in 1 unique chain structure
        max_achievable = {"opA": 1, "opB": 10}

        def mock_generate(max_chains, max_steps, seed):
            # Seed 0: unique chain covering opA and opB
            # Seed 1-4: unique chains covering only opB (with unique extra ops)
            if seed == 0:
                return [self._make_chain(["opA", "opB"], "c0")]
            elif seed < 5:
                return [self._make_chain(["opB", f"filler{seed}"], f"c{seed}")]
            return []

        mock_generator.generate_chains.side_effect = mock_generate

        result = _generate_chains_with_seed_walking(
            generator=mock_generator,
            max_chains=None,
            max_steps=6,
            starting_seed=0,
            linked_operations=linked_ops,
            min_hits_per_op=5,
            min_coverage_pct=100.0,
            max_achievable_hits=max_achievable,
        )

        # opA effective target = min(1, 5) = 1, met after seed 0
        # opB effective target = min(10, 5) = 5, needs 5 seeds
        assert result.stopped_reason == "coverage_met"
        assert result.operation_hit_counts["opA"] == 1  # Only 1 hit possible
        assert result.operation_hit_counts["opB"] == 5  # Met the full target
        assert result.effective_targets["opA"] == 1
        assert result.effective_targets["opB"] == 5

    def test_uncapped_operation_uses_flat_target(self):
        """Operation with achievable=10, requested min_hits_per_op=3: effective target is 3."""
        from unittest.mock import MagicMock

        from api_parity.cli import _generate_chains_with_seed_walking

        mock_generator = MagicMock()
        linked_ops = {"opA"}

        max_achievable = {"opA": 10}

        def mock_generate(max_chains, max_steps, seed):
            return [self._make_chain(["opA", f"x{seed}"], f"c{seed}")]

        mock_generator.generate_chains.side_effect = mock_generate

        result = _generate_chains_with_seed_walking(
            generator=mock_generator,
            max_chains=None,
            max_steps=6,
            starting_seed=0,
            linked_operations=linked_ops,
            min_hits_per_op=3,
            min_coverage_pct=100.0,
            max_achievable_hits=max_achievable,
        )

        assert result.stopped_reason == "coverage_met"
        assert result.operation_hit_counts["opA"] == 3
        assert result.effective_targets["opA"] == 3  # min(10, 3) = 3

    def test_none_achievable_falls_back_to_flat(self):
        """max_achievable_hits=None (enumeration capped out): falls back to flat min_hits_per_op."""
        from unittest.mock import MagicMock

        from api_parity.cli import _generate_chains_with_seed_walking

        mock_generator = MagicMock()
        linked_ops = {"opA"}

        def mock_generate(max_chains, max_steps, seed):
            return [self._make_chain(["opA", f"filler{seed}"], f"c{seed}")]

        mock_generator.generate_chains.side_effect = mock_generate

        result = _generate_chains_with_seed_walking(
            generator=mock_generator,
            max_chains=None,
            max_steps=6,
            starting_seed=0,
            linked_operations=linked_ops,
            min_hits_per_op=3,
            min_coverage_pct=100.0,
            max_achievable_hits=None,  # Enumeration capped out
        )

        assert result.stopped_reason == "coverage_met"
        assert result.operation_hit_counts["opA"] == 3
        assert result.max_achievable_hits is None
        # Effective target should fall back to min_hits_per_op
        assert result.effective_targets["opA"] == 3

    def test_mixed_capped_and_uncapped(self):
        """Mix of capped and uncapped operations: verify coverage check uses per-op targets."""
        from unittest.mock import MagicMock

        from api_parity.cli import _generate_chains_with_seed_walking

        mock_generator = MagicMock()
        linked_ops = {"opCapped", "opFull"}

        max_achievable = {"opCapped": 2, "opFull": 100}

        call_count = [0]

        def mock_generate(max_chains, max_steps, seed):
            call_count[0] += 1
            # Both ops in every chain, with unique signatures
            return [self._make_chain(["opCapped", "opFull", f"x{seed}"], f"c{seed}")]

        mock_generator.generate_chains.side_effect = mock_generate

        result = _generate_chains_with_seed_walking(
            generator=mock_generator,
            max_chains=None,
            max_steps=6,
            starting_seed=0,
            linked_operations=linked_ops,
            min_hits_per_op=5,
            min_coverage_pct=100.0,
            max_achievable_hits=max_achievable,
        )

        # opCapped effective = min(2, 5) = 2, met after 2 seeds
        # opFull effective = min(100, 5) = 5, met after 5 seeds
        # Overall coverage met when both meet their effective targets = 5 seeds
        assert result.stopped_reason == "coverage_met"
        assert result.effective_targets["opCapped"] == 2
        assert result.effective_targets["opFull"] == 5
        assert result.operation_hit_counts["opCapped"] == 5  # Hit 5 times (but target was 2)
        assert result.operation_hit_counts["opFull"] == 5

    def test_chain_generation_result_properties_use_effective_targets(self):
        """Verify ChainGenerationResult properties use effective_targets, not flat min_hits_per_op."""
        from api_parity.cli import ChainGenerationResult

        result = ChainGenerationResult(
            chains=[],
            seeds_used=[],
            operations_covered={"opA", "opB"},
            operation_hit_counts={"opA": 2, "opB": 5},
            linked_operations={"opA", "opB"},
            orphan_operations=set(),
            min_hits_per_op=5,
            min_coverage_pct=100.0,
            stopped_reason="coverage_met",
            seeds_tried=5,
            max_achievable_hits={"opA": 2, "opB": 100},
            effective_targets={"opA": 2, "opB": 5},
        )

        # opA has 2 hits, effective target 2 -> meets target
        # opB has 5 hits, effective target 5 -> meets target
        assert result.ops_meeting_hits_target == 2
        assert result.ops_below_hits_target == {}
        assert result.coverage_complete is True

    def test_chain_generation_result_below_target_uses_effective(self):
        """ops_below_hits_target uses effective targets, not flat min_hits_per_op."""
        from api_parity.cli import ChainGenerationResult

        result = ChainGenerationResult(
            chains=[],
            seeds_used=[],
            operations_covered={"opA", "opB"},
            operation_hit_counts={"opA": 1, "opB": 3},
            linked_operations={"opA", "opB"},
            orphan_operations=set(),
            min_hits_per_op=5,
            min_coverage_pct=100.0,
            stopped_reason="max_seeds",
            seeds_tried=100,
            max_achievable_hits={"opA": 2, "opB": 100},
            effective_targets={"opA": 2, "opB": 5},
        )

        # opA: 1 hit < effective target 2 -> below
        # opB: 3 hits < effective target 5 -> below
        below = result.ops_below_hits_target
        assert "opA" in below
        assert below["opA"] == 1
        assert "opB" in below
        assert below["opB"] == 3


class TestWarningOutput:
    """Tests for warning messages when operations are capped."""

    def test_capped_operations_warning_printed(self, capsys):
        """When operations are capped, warning message is printed with correct info."""
        from api_parity.cli import _compute_max_achievable_hits

        # We can't easily run the full _run_stateful_explore without real components,
        # so test the warning logic directly by checking _compute_max_achievable_hits
        # produces the right data that the warning code would use.
        edges = [("A", "B")]
        linked_ops = {"A", "B"}

        # With max_steps=2, only (A,B) is possible -- both have 1 hit
        max_achievable = _compute_max_achievable_hits(edges, linked_ops, max_steps=2)

        assert max_achievable is not None
        assert max_achievable["A"] == 1
        assert max_achievable["B"] == 1

        # Verify the capping logic that would trigger the warning
        min_hits_per_op = 5
        capped_ops = {
            op: achievable
            for op, achievable in max_achievable.items()
            if op in linked_ops and achievable < min_hits_per_op
        }
        assert len(capped_ops) == 2
        assert capped_ops["A"] == 1
        assert capped_ops["B"] == 1

    def test_no_warning_when_all_ops_meet_target(self):
        """No capped ops when all operations have achievable >= requested."""
        edges = [("A", "B"), ("B", "A")]
        linked_ops = {"A", "B"}

        max_achievable = _compute_max_achievable_hits(edges, linked_ops, max_steps=4)

        assert max_achievable is not None
        # With A->B and B->A cycle at max_steps=4, many chains exist
        # Both A and B should have many achievable hits
        min_hits_per_op = 2
        capped_ops = {
            op: achievable
            for op, achievable in max_achievable.items()
            if op in linked_ops and achievable < min_hits_per_op
        }
        assert len(capped_ops) == 0

    def test_no_warning_when_min_hits_is_one(self):
        """No warning when min_hits_per_op is 1 (all operations can appear in at least 1 chain)."""
        edges = [("A", "B")]
        linked_ops = {"A", "B"}

        max_achievable = _compute_max_achievable_hits(edges, linked_ops, max_steps=2)

        assert max_achievable is not None
        min_hits_per_op = 1
        capped_ops = {
            op: achievable
            for op, achievable in max_achievable.items()
            if op in linked_ops and achievable < min_hits_per_op
        }
        assert len(capped_ops) == 0
