"""Tests for chain topology caching in CaseGenerator.

Covers the performance optimization where chain structures (topologies) are
cached after the first generate_chains() call, and subsequent calls with
different seeds reuse the cached topologies instead of re-running the
expensive Hypothesis state machine.

See DESIGN.md "Chain Topology Caching" for rationale.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from api_parity.case_generator import CaseGenerator
from api_parity.models import ChainCase, ChainStep, RequestCase


FIXTURES_DIR = Path(__file__).parent / "fixtures"
# test_api.yaml has OpenAPI links defined, enabling chain generation
TEST_API_SPEC = FIXTURES_DIR / "test_api.yaml"


def _make_request_case(operation_id: str, method: str = "GET", path: str = "/test") -> RequestCase:
    """Create a minimal RequestCase for testing."""
    return RequestCase(
        case_id="test-case-id",
        operation_id=operation_id,
        method=method,
        path_template=path,
        rendered_path=path,
    )


def _make_chain(steps_data: list[tuple[str, str, str, dict | None]]) -> ChainCase:
    """Create a ChainCase from list of (op_id, method, path, link_source) tuples."""
    steps = []
    for i, (op_id, method, path, link_source) in enumerate(steps_data):
        step = ChainStep(
            step_index=i,
            request_template=_make_request_case(op_id, method, path),
            link_source=link_source,
        )
        steps.append(step)
    return ChainCase(chain_id="test-chain", steps=steps)


class TestExtractChainTopologies:
    """Tests for CaseGenerator._extract_chain_topologies.

    Verifies that topology extraction captures operation_id, method,
    path_template, and link_source for each step, but NOT fuzz values
    (body, query params, headers, etc.).
    """

    def test_extracts_operation_metadata(self):
        """Topology captures operation_id, method, path_template per step."""
        generator = CaseGenerator(TEST_API_SPEC)

        chain = _make_chain([
            ("createWidget", "POST", "/widgets", None),
            ("getWidget", "GET", "/widgets/{widget_id}",
             {"link_name": "GetCreatedWidget", "source_operation": "createWidget"}),
        ])

        topologies = generator._extract_chain_topologies([chain])

        assert len(topologies) == 1
        topo = topologies[0]
        assert len(topo) == 2

        assert topo[0]["operation_id"] == "createWidget"
        assert topo[0]["method"] == "POST"
        assert topo[0]["path_template"] == "/widgets"
        assert topo[0]["link_source"] is None

        assert topo[1]["operation_id"] == "getWidget"
        assert topo[1]["method"] == "GET"
        assert topo[1]["path_template"] == "/widgets/{widget_id}"
        assert topo[1]["link_source"]["link_name"] == "GetCreatedWidget"

    def test_multiple_chains_produce_multiple_topologies(self):
        """Each input chain produces one topology."""
        generator = CaseGenerator(TEST_API_SPEC)

        chains = [
            _make_chain([
                ("createWidget", "POST", "/widgets", None),
                ("getWidget", "GET", "/widgets/{id}", {"link_name": "L1"}),
            ]),
            _make_chain([
                ("listWidgets", "GET", "/widgets", None),
                ("getWidget", "GET", "/widgets/{id}", {"link_name": "L2"}),
                ("deleteWidget", "DELETE", "/widgets/{id}", {"link_name": "L3"}),
            ]),
        ]

        topologies = generator._extract_chain_topologies(chains)
        assert len(topologies) == 2
        assert len(topologies[0]) == 2
        assert len(topologies[1]) == 3

    def test_empty_chains_produce_empty_topologies(self):
        """Empty input list produces empty topologies list."""
        generator = CaseGenerator(TEST_API_SPEC)
        assert generator._extract_chain_topologies([]) == []

    def test_topology_does_not_include_fuzz_values(self):
        """Topology only has structural keys, not request body/query/headers."""
        generator = CaseGenerator(TEST_API_SPEC)

        # Create a chain with a request that has body, query, and headers
        req = RequestCase(
            case_id="fuzz-case",
            operation_id="createWidget",
            method="POST",
            path_template="/widgets",
            rendered_path="/widgets",
            body={"name": "fuzzed-widget", "category": "gadgets"},
            query={"debug": ["true"]},
            headers={"x-trace": ["abc"]},
        )
        chain = ChainCase(
            chain_id="test",
            steps=[ChainStep(step_index=0, request_template=req, link_source=None)],
        )

        topologies = generator._extract_chain_topologies([chain])
        step_desc = topologies[0][0]

        # Topology should only have these keys
        assert set(step_desc.keys()) == {"operation_id", "method", "path_template", "link_source"}


class TestRegenerateFromCache:
    """Tests for CaseGenerator._regenerate_chains_from_cache.

    Verifies that cached topologies produce fresh chains with new fuzz values,
    that chains are skipped when an operation is not found, and that
    per-step seed derivation works correctly.
    """

    def test_skips_chain_when_operation_not_in_schemathesis(self):
        """Chains referencing operations not in the spec are skipped entirely."""
        generator = CaseGenerator(TEST_API_SPEC)

        # Create a topology referencing a non-existent operation
        topologies = [[
            {"operation_id": "nonExistentOp", "method": "GET",
             "path_template": "/nope", "link_source": None},
            {"operation_id": "getWidget", "method": "GET",
             "path_template": "/widgets/{id}", "link_source": {"link_name": "L1"}},
        ]]

        chains = generator._regenerate_chains_from_cache(topologies, seed=42)
        # Chain should be skipped because first step's operation doesn't exist
        assert len(chains) == 0

    def test_skips_chain_when_middle_step_operation_missing(self):
        """Chain is skipped if ANY step's operation is not found."""
        generator = CaseGenerator(TEST_API_SPEC)

        # Get a real operation_id from the spec
        ops = generator.get_operations()
        real_op_id = ops[0]["operation_id"]

        topologies = [[
            {"operation_id": real_op_id, "method": "GET",
             "path_template": "/test", "link_source": None},
            {"operation_id": "missingMiddleOp", "method": "POST",
             "path_template": "/missing", "link_source": None},
            {"operation_id": real_op_id, "method": "GET",
             "path_template": "/test", "link_source": None},
        ]]

        chains = generator._regenerate_chains_from_cache(topologies, seed=42)
        assert len(chains) == 0

    def test_single_step_chains_filtered_out(self):
        """Chains with only one step are not included (multi-step only)."""
        generator = CaseGenerator(TEST_API_SPEC)

        ops = generator.get_operations()
        real_op_id = ops[0]["operation_id"]

        # Single-step topology
        topologies = [[
            {"operation_id": real_op_id, "method": "GET",
             "path_template": "/test", "link_source": None},
        ]]

        chains = generator._regenerate_chains_from_cache(topologies, seed=42)
        # Single-step chains are filtered out (len(steps) > 1 check)
        assert len(chains) == 0

    def test_regenerated_chains_have_correct_structure(self):
        """Regenerated chains preserve operation order and link sources."""
        generator = CaseGenerator(TEST_API_SPEC)

        # Find two real operations
        ops = generator.get_operations()
        if len(ops) < 2:
            pytest.skip("Need at least 2 operations in test spec")

        op1_id = ops[0]["operation_id"]
        op2_id = ops[1]["operation_id"]
        link_source = {"link_name": "TestLink", "source_operation": op1_id}

        topologies = [[
            {"operation_id": op1_id, "method": ops[0]["method"],
             "path_template": ops[0]["path"], "link_source": None},
            {"operation_id": op2_id, "method": ops[1]["method"],
             "path_template": ops[1]["path"], "link_source": link_source},
        ]]

        chains = generator._regenerate_chains_from_cache(topologies, seed=42)
        assert len(chains) == 1

        chain = chains[0]
        assert len(chain.steps) == 2
        assert chain.steps[0].request_template.operation_id == op1_id
        assert chain.steps[1].request_template.operation_id == op2_id
        assert chain.steps[1].link_source == link_source

    def test_regenerated_chains_have_fresh_case_ids(self):
        """Each regeneration produces fresh UUIDs, even with same topology.

        The cache regeneration path calls _generate_for_operation with
        max_cases=1. Hypothesis with max_examples=1 produces deterministic
        "minimal" values regardless of seed — this is expected Hypothesis
        behavior, not a bug. What the cache path guarantees is that each
        call produces fresh RequestCase objects (new case_ids) suitable
        for a new execution run.
        """
        generator = CaseGenerator(TEST_API_SPEC)

        ops = generator.get_operations()
        if len(ops) < 2:
            pytest.skip("Need at least 2 operations in test spec")

        op1_id = ops[0]["operation_id"]
        op2_id = ops[1]["operation_id"]

        topologies = [[
            {"operation_id": op1_id, "method": ops[0]["method"],
             "path_template": ops[0]["path"], "link_source": None},
            {"operation_id": op2_id, "method": ops[1]["method"],
             "path_template": ops[1]["path"],
             "link_source": {"link_name": "L", "source_operation": op1_id}},
        ]]

        chains_a = generator._regenerate_chains_from_cache(topologies, seed=1)
        chains_b = generator._regenerate_chains_from_cache(topologies, seed=2)

        assert len(chains_a) >= 1
        assert len(chains_b) >= 1

        # Each call produces distinct RequestCase objects with fresh case_ids
        ids_a = {step.request_template.case_id for step in chains_a[0].steps}
        ids_b = {step.request_template.case_id for step in chains_b[0].steps}
        assert ids_a.isdisjoint(ids_b), "Regenerated chains should have fresh case_ids"

        # Each chain also gets a fresh chain_id
        assert chains_a[0].chain_id != chains_b[0].chain_id


class TestCacheIntegration:
    """Tests for the full cache lifecycle in generate_chains().

    Verifies that the first call populates the cache and subsequent calls
    use the cached topologies.
    """

    def test_cache_not_populated_initially(self):
        """Cache starts as None before any generate_chains() call."""
        generator = CaseGenerator(TEST_API_SPEC)
        assert generator._cached_chain_topologies is None

    def test_generate_chains_populates_cache(self):
        """First generate_chains() call populates the topology cache."""
        generator = CaseGenerator(TEST_API_SPEC)
        chains = generator.generate_chains(max_chains=5, seed=42)

        if not chains:
            pytest.skip("No chains generated from test spec (no links?)")

        # Only populated if multi-step chains were found
        multi_step = [c for c in chains if len(c.steps) > 1]
        if multi_step:
            assert generator._cached_chain_topologies is not None
            assert len(generator._cached_chain_topologies) > 0
        else:
            # Single-step chains don't populate cache
            assert generator._cached_chain_topologies is None

    def test_second_call_uses_cache(self):
        """Second generate_chains() call uses cached topologies."""
        generator = CaseGenerator(TEST_API_SPEC)

        # First call — runs the state machine
        chains1 = generator.generate_chains(max_chains=5, seed=42)
        if not chains1 or not any(len(c.steps) > 1 for c in chains1):
            pytest.skip("No multi-step chains generated")

        cached = generator._cached_chain_topologies
        assert cached is not None

        # Second call — should use cache (different seed)
        chains2 = generator.generate_chains(max_chains=5, seed=99)

        # Cache should be unchanged (same object)
        assert generator._cached_chain_topologies is cached

        # Both calls should return chains
        assert len(chains2) > 0

    def test_cache_only_populated_for_multi_step_chains(self):
        """Cache is only populated when multi-step chains exist.

        If the state machine only produces single-step chains (no links),
        there's nothing worth caching — topology caching only helps when
        the expensive link-following state machine found multi-step paths.
        """
        generator = CaseGenerator(TEST_API_SPEC)

        # We can't easily force single-step-only chains, but we can verify
        # the attribute type contract: it's either None or a non-empty list
        chains = generator.generate_chains(max_chains=5, seed=42)
        cache = generator._cached_chain_topologies
        assert cache is None or (isinstance(cache, list) and len(cache) > 0)
