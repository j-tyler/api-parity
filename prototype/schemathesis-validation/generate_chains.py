#!/usr/bin/env python3
"""
Schemathesis Chain Generation

Goal: Generate stateful request chains (sequences) from OpenAPI links
WITHOUT making actual HTTP calls. Validates that Schemathesis can
generate multi-step chains for api-parity's differential testing.
"""

import json
import uuid
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any

from schemathesis.openapi import from_path
from schemathesis.specs.openapi.stateful import OpenAPIStateMachine
from schemathesis.core.transport import Response

SPEC_PATH = Path(__file__).parent / "sample_api.yaml"


@dataclass
class CapturedStep:
    """A single step captured from a chain."""
    step_index: int
    operation_id: str
    method: str
    path_template: str
    path_parameters: dict
    formatted_path: str
    query: dict
    headers: dict
    body: Any
    media_type: str | None
    # Link info if this step came from a link
    from_link: str | None = None
    parent_operation: str | None = None


@dataclass
class CapturedChain:
    """A complete chain of steps."""
    chain_id: str
    steps: list[CapturedStep] = field(default_factory=list)


class ChainCapturingStateMachine(OpenAPIStateMachine):
    """State machine that captures chains without making HTTP calls."""

    # Class-level storage for captured chains
    captured_chains: list[CapturedChain] = []
    current_chain: CapturedChain | None = None
    step_counter: int = 0

    def validate_response(self, response, case, **kwargs):
        """Skip validation - we're just generating chains, not testing."""
        pass

    def setup(self):
        """Called before each test run."""
        # Start a new chain
        self.__class__.current_chain = CapturedChain(
            chain_id=str(uuid.uuid4())[:8]
        )
        self.__class__.step_counter = 0

    def teardown(self):
        """Called after each test run."""
        # Save completed chain if it has steps
        if self.current_chain and self.current_chain.steps:
            self.__class__.captured_chains.append(self.current_chain)
        self.__class__.current_chain = None

    def call(self, case, **kwargs) -> Response:
        """Override call to capture the case instead of making HTTP request."""
        # Extract case info
        op_id = case.operation.definition.raw.get('operationId', 'unknown')

        # Build formatted path
        formatted_path = case.path
        path_params = {}
        if case.path_parameters:
            path_params = dict(case.path_parameters)
            for k, v in path_params.items():
                formatted_path = formatted_path.replace(f"{{{k}}}", str(v))

        # Create captured step
        step = CapturedStep(
            step_index=self.__class__.step_counter,
            operation_id=op_id,
            method=case.method,
            path_template=case.path,
            path_parameters=path_params,
            formatted_path=formatted_path,
            query=dict(case.query) if case.query else {},
            headers=dict(case.headers) if case.headers else {},
            body=case.body if hasattr(case, 'body') and str(type(case.body).__name__) != 'NotSet' else None,
            media_type=case.media_type if hasattr(case, 'media_type') else None,
        )

        # Add to current chain
        if self.__class__.current_chain:
            self.__class__.current_chain.steps.append(step)

        self.__class__.step_counter += 1

        # Return a mock response that allows chain to continue
        # We need to return data that the links can use
        mock_body = self._generate_mock_response(case)

        # Create a mock PreparedRequest
        import requests
        req = requests.Request(
            method=case.method,
            url=f"http://mock{case.path}",
        )
        prepared = req.prepare()

        return Response(
            status_code=200 if case.method != 'POST' else 201,
            headers={'content-type': ['application/json']},
            content=json.dumps(mock_body).encode(),
            request=prepared,
            elapsed=0.1,
            verify=False,
            http_version='1.1',
        )

    def _generate_mock_response(self, case) -> dict:
        """Generate mock response body for link resolution."""
        op_id = case.operation.definition.raw.get('operationId', '')

        # For operations that create/return items, return an id
        if 'create' in op_id.lower() or 'Item' in op_id:
            return {
                'id': str(uuid.uuid4()),
                'name': 'Mock Item',
                'price': 9.99,
                'category': 'electronics',
            }
        elif 'Cart' in op_id:
            return {
                'user_id': str(uuid.uuid4()),
                'items': [],
                'total': 0.0,
            }
        elif 'list' in op_id.lower():
            return {
                'items': [{'id': str(uuid.uuid4())} for _ in range(3)],
                'total': 3,
            }
        else:
            return {'id': str(uuid.uuid4())}


def generate_chains(max_chains=20, max_steps_per_chain=10):
    """Generate chains using the state machine."""
    print("=" * 60)
    print("GENERATING CHAINS")
    print("=" * 60)

    schema = from_path(SPEC_PATH)

    # Create our capturing state machine
    OriginalStateMachine = schema.as_state_machine()

    # Create a subclass that inherits from our capturing class
    class CapturingMachine(ChainCapturingStateMachine, OriginalStateMachine):
        pass

    # Reset captured chains
    ChainCapturingStateMachine.captured_chains = []

    # Run the state machine using Hypothesis
    from hypothesis import settings, Phase, given
    from hypothesis.stateful import run_state_machine_as_test

    # Configure to generate multiple chains
    @settings(
        max_examples=max_chains,
        stateful_step_count=max_steps_per_chain,
        database=None,
        phases=[Phase.generate],
        deadline=None,
    )
    def run_test():
        run_state_machine_as_test(CapturingMachine)

    try:
        run_test()
    except Exception as e:
        # Hypothesis may raise when done, that's ok
        print(f"Note: {type(e).__name__}: {e}")

    return ChainCapturingStateMachine.captured_chains


def chain_to_dict(chain: CapturedChain) -> dict:
    """Convert chain to dictionary for JSON serialization."""
    return {
        'chain_id': chain.chain_id,
        'step_count': len(chain.steps),
        'steps': [
            {
                'step_index': s.step_index,
                'operation_id': s.operation_id,
                'method': s.method,
                'path_template': s.path_template,
                'path_parameters': s.path_parameters,
                'formatted_path': s.formatted_path,
                'query': s.query,
                'headers': s.headers,
                'body': s.body,
                'media_type': s.media_type,
            }
            for s in chain.steps
        ]
    }


def main():
    print("Schemathesis Chain Generation Validation")
    print("=" * 60)

    # Generate chains
    chains = generate_chains(max_chains=30, max_steps_per_chain=8)

    print(f"\nGenerated {len(chains)} chains")

    # Save to file
    chains_data = [chain_to_dict(c) for c in chains]
    output_path = Path(__file__).parent / "generated_chains.json"
    with open(output_path, 'w') as f:
        json.dump(chains_data, f, indent=2, default=str)
    print(f"Saved to {output_path}")

    # Analyze chains
    print("\n" + "=" * 60)
    print("CHAIN ANALYSIS")
    print("=" * 60)

    # Chain length distribution
    lengths = [len(c.steps) for c in chains]
    print(f"\nChain lengths: min={min(lengths)}, max={max(lengths)}, avg={sum(lengths)/len(lengths):.1f}")

    # Show sample chains
    print("\n" + "=" * 60)
    print("SAMPLE CHAINS")
    print("=" * 60)

    # Show a few interesting chains (longer ones)
    sorted_chains = sorted(chains, key=lambda c: len(c.steps), reverse=True)

    for i, chain in enumerate(sorted_chains[:5]):
        print(f"\n--- Chain {chain.chain_id} ({len(chain.steps)} steps) ---")
        for step in chain.steps:
            body_preview = ""
            if step.body:
                body_str = json.dumps(step.body, default=str)
                body_preview = f" body={body_str[:50]}..." if len(body_str) > 50 else f" body={body_str}"
            print(f"  [{step.step_index}] {step.method} {step.formatted_path}{body_preview}")

    # Check for Create -> Get -> Update -> Delete patterns
    print("\n" + "=" * 60)
    print("CRUD PATTERN ANALYSIS")
    print("=" * 60)

    crud_patterns = []
    for chain in chains:
        ops = [s.operation_id for s in chain.steps]
        ops_str = " -> ".join(ops)

        # Check for CRUD-like sequences
        has_create = any('create' in op.lower() for op in ops)
        has_get = any('get' in op.lower() for op in ops)
        has_update = any('update' in op.lower() for op in ops)
        has_delete = any('delete' in op.lower() for op in ops)

        if has_create and (has_get or has_update or has_delete):
            crud_patterns.append((chain.chain_id, ops_str))

    print(f"\nChains with CRUD-like patterns: {len(crud_patterns)}")
    for chain_id, pattern in crud_patterns[:10]:
        print(f"  {chain_id}: {pattern}")


if __name__ == "__main__":
    main()
