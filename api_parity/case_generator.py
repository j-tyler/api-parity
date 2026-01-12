"""Case Generator - Wraps Schemathesis for test case generation.

Generates RequestCase objects from an OpenAPI specification using Schemathesis
as the underlying fuzzer. Supports both stateless (single request) generation
and stateful chain generation via OpenAPI links.

See ARCHITECTURE.md "Case Generator Component" for specifications.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Iterator

import requests
import schemathesis
from hypothesis import Phase, settings
from hypothesis.errors import HypothesisException
from hypothesis.stateful import run_state_machine_as_test
from schemathesis.core.transport import Response as SchemathesisResponse
from schemathesis.specs.openapi.stateful import OpenAPIStateMachine

from api_parity.models import ChainCase, ChainStep, RequestCase


class CaseGeneratorError(Exception):
    """Raised when case generation fails."""


class CaseGenerator:
    """Generates test cases from an OpenAPI specification.

    Usage:
        generator = CaseGenerator(Path("api.yaml"))
        for case in generator.generate(max_cases=100, seed=42):
            print(case)
    """

    def __init__(
        self,
        spec_path: Path,
        exclude_operations: list[str] | None = None,
    ) -> None:
        """Initialize the case generator.

        Args:
            spec_path: Path to OpenAPI specification file (YAML or JSON).
            exclude_operations: List of operationIds to skip.

        Raises:
            CaseGeneratorError: If spec cannot be loaded.
        """
        self._spec_path = spec_path
        self._exclude = set(exclude_operations or [])
        self._operations_cache: list[dict[str, Any]] | None = None

        try:
            self._schema = schemathesis.openapi.from_path(str(spec_path))
        except Exception as e:
            raise CaseGeneratorError(f"Failed to load OpenAPI spec: {e}") from e

    def get_operations(self) -> list[dict[str, Any]]:
        """Get all operations from the spec.

        Returns:
            List of operation info dicts with operationId, method, path.
        """
        if self._operations_cache is not None:
            return self._operations_cache

        operations = []
        for result in self._schema.get_all_operations():
            op = result.ok()
            if op is None:
                continue
            raw = op.definition.raw
            operation_id = raw.get("operationId", f"{op.method}_{op.path}")
            if operation_id not in self._exclude:
                operations.append({
                    "operation_id": operation_id,
                    "method": op.method.upper(),
                    "path": op.path,
                })

        self._operations_cache = operations
        return operations

    def generate(
        self,
        max_cases: int | None = None,
        seed: int | None = None,
    ) -> Iterator[RequestCase]:
        """Generate test cases for all operations.

        Args:
            max_cases: Maximum total cases to generate (None for no limit).
            seed: Random seed for reproducibility.

        Yields:
            RequestCase objects ready for execution.
        """
        cases_per_operation = max_cases or 100
        if max_cases:
            # Distribute cases across operations
            ops = self.get_operations()
            if ops:
                cases_per_operation = max(1, max_cases // len(ops))

        total_generated = 0

        for result in self._schema.get_all_operations():
            op = result.ok()
            if op is None:
                continue

            raw = op.definition.raw
            operation_id = raw.get("operationId", f"{op.method}_{op.path}")

            if operation_id in self._exclude:
                continue

            if max_cases and total_generated >= max_cases:
                break

            # Calculate how many cases to generate for this operation
            remaining = (max_cases - total_generated) if max_cases else cases_per_operation
            op_cases = min(cases_per_operation, remaining)

            for case in self._generate_for_operation(op, operation_id, op_cases, seed):
                yield case
                total_generated += 1
                if max_cases and total_generated >= max_cases:
                    break

    def _generate_for_operation(
        self,
        operation: Any,
        operation_id: str,
        max_cases: int,
        seed: int | None,
    ) -> Iterator[RequestCase]:
        """Generate cases for a single operation.

        Args:
            operation: Schemathesis operation object.
            operation_id: The operation's ID.
            max_cases: Maximum cases for this operation.
            seed: Random seed.

        Yields:
            RequestCase objects.
        """
        from hypothesis import given

        strategy = operation.as_strategy()
        collected: list[Any] = []

        @given(case=strategy)
        @settings(
            max_examples=max_cases,
            database=None,
            phases=[Phase.generate],
            derandomize=seed is not None,
        )
        def collect_cases(case):
            collected.append(case)

        collect_cases()

        for schemathesis_case in collected:
            yield self._convert_case(schemathesis_case, operation_id)

    def _convert_case(self, case: Any, operation_id: str) -> RequestCase:
        """Convert a Schemathesis case to our RequestCase model.

        Args:
            case: Schemathesis Case object.
            operation_id: The operation ID.

        Returns:
            RequestCase model instance.
        """
        # Path parameters
        path_params = dict(case.path_parameters) if case.path_parameters else {}

        # Compute rendered path
        rendered_path = case.path
        for key, value in path_params.items():
            rendered_path = rendered_path.replace(f"{{{key}}}", str(value))

        # Query parameters - normalize to lists
        query: dict[str, list[str]] = {}
        if case.query:
            for key, value in case.query.items():
                if isinstance(value, list):
                    query[key] = [str(v) for v in value]
                else:
                    query[key] = [str(value)]

        # Headers - normalize to lists
        headers: dict[str, list[str]] = {}
        if case.headers:
            for key, value in case.headers.items():
                if isinstance(value, list):
                    headers[key] = [str(v) for v in value]
                else:
                    headers[key] = [str(value)]

        # Cookies
        cookies: dict[str, str] = {}
        if case.cookies:
            cookies = {str(k): str(v) for k, v in case.cookies.items()}

        # Body - check for NotSet sentinel
        body: Any = None
        media_type: str | None = None
        if case.body is not None and str(type(case.body).__name__) != "NotSet":
            body = case.body
            media_type = case.media_type

        return RequestCase(
            case_id=str(uuid.uuid4()),
            operation_id=operation_id,
            method=case.method.upper(),
            path_template=case.path,
            path_parameters=path_params,
            rendered_path=rendered_path,
            query=query,
            headers=headers,
            cookies=cookies,
            body=body,
            media_type=media_type,
        )

    def generate_chains(
        self,
        max_chains: int | None = None,
        max_steps: int = 6,
        seed: int | None = None,
    ) -> list[ChainCase]:
        """Generate stateful request chains following OpenAPI links.

        Uses Schemathesis state machine to generate multi-step sequences.
        Chains are generated without making HTTP calls - the executor handles
        actual execution.

        Args:
            max_chains: Maximum number of chains to generate (default 20).
            max_steps: Maximum steps per chain (default 6).
            seed: Random seed for reproducibility.

        Returns:
            List of ChainCase objects ready for execution.
        """
        max_chains = max_chains or 20

        # Create capturing state machine
        captured_chains: list[ChainCase] = []
        current_steps: list[ChainStep] = []
        step_counter = 0

        generator_self = self

        class ChainCapturingStateMachine(OpenAPIStateMachine):
            """State machine that captures chains without making HTTP calls."""

            def validate_response(self, response, case, **kwargs):
                """Skip validation - we're just generating chains."""
                pass

            def setup(self):
                """Called before each test run - start a new chain."""
                nonlocal current_steps, step_counter
                current_steps = []
                step_counter = 0

            def teardown(self):
                """Called after each test run - save the completed chain."""
                nonlocal captured_chains, current_steps
                if current_steps:
                    chain = ChainCase(
                        chain_id=str(uuid.uuid4()),
                        steps=list(current_steps),
                    )
                    captured_chains.append(chain)
                current_steps = []

            def call(self, case, **kwargs) -> SchemathesisResponse:
                """Capture the case instead of making HTTP request."""
                nonlocal current_steps, step_counter

                # Extract operation info
                op_id = case.operation.definition.raw.get("operationId", "unknown")
                if op_id in generator_self._exclude:
                    # Still need to return a mock response for excluded operations
                    return self._mock_response(case)

                # Convert to our RequestCase
                request_case = generator_self._convert_case(case, op_id)

                # Create chain step
                step = ChainStep(
                    step_index=step_counter,
                    request_template=request_case,
                    link_source=None,  # Link source tracked by Schemathesis internally
                )
                current_steps.append(step)
                step_counter += 1

                return self._mock_response(case)

            def _mock_response(self, case) -> SchemathesisResponse:
                """Generate mock response for link resolution."""
                mock_body = self._generate_mock_body()

                # Create a mock PreparedRequest
                req = requests.Request(
                    method=case.method,
                    url=f"http://mock{case.path}",
                )
                prepared = req.prepare()

                return SchemathesisResponse(
                    status_code=201 if case.method == "POST" else 200,
                    headers={"content-type": ["application/json"]},
                    content=json.dumps(mock_body).encode(),
                    request=prepared,
                    elapsed=0.1,
                    verify=False,
                    http_version="1.1",
                )

            def _generate_mock_body(self) -> dict:
                """Generate mock response body with common fields for link resolution.

                Note: Only generates common field names (id, user_id, etc.). OpenAPI
                links referencing other field names won't resolve during generation.
                """
                return {
                    "id": str(uuid.uuid4()),
                    "name": "Mock Item",
                    "price": 9.99,
                    "status": "pending",
                    "user_id": str(uuid.uuid4()),
                    "order_id": str(uuid.uuid4()),
                    "widget_id": str(uuid.uuid4()),
                    "items": [{"id": str(uuid.uuid4())} for _ in range(3)],
                    "total": 3,
                    "created_at": "2024-01-01T00:00:00Z",
                }

        # Get the state machine class from schema
        try:
            OriginalStateMachine = self._schema.as_state_machine()
        except Exception as e:
            raise CaseGeneratorError(f"Failed to create state machine: {e}") from e

        # Create combined class
        class CombinedMachine(ChainCapturingStateMachine, OriginalStateMachine):
            pass

        # Run the state machine
        @settings(
            max_examples=max_chains,
            stateful_step_count=max_steps,
            database=None,
            phases=[Phase.generate],
            deadline=None,
            derandomize=seed is not None,
        )
        def run_generation():
            run_state_machine_as_test(CombinedMachine)

        try:
            run_generation()
        except HypothesisException:
            # Hypothesis raises when state space is exhausted
            pass

        # Filter to chains with multiple steps (single-step chains aren't useful)
        multi_step_chains = [c for c in captured_chains if len(c.steps) > 1]

        return multi_step_chains
