"""Case Generator - Wraps Schemathesis for test case generation.

Generates RequestCase objects from an OpenAPI specification using Schemathesis
as the underlying fuzzer. Supports both stateless (single request) generation
and stateful chain generation via OpenAPI links.

See ARCHITECTURE.md "Case Generator Component" for specifications.
"""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any, Iterator

import requests
import schemathesis
import yaml
from hypothesis import Phase, settings
from hypothesis.errors import HypothesisException
from hypothesis.stateful import run_state_machine_as_test
from schemathesis.config import (
    PhasesConfig,
    ProjectConfig,
    ProjectsConfig,
    SchemathesisConfig,
    StatefulPhaseConfig,
)
from schemathesis.core.transport import Response as SchemathesisResponse

# Get InferenceConfig via public API indirection. InferenceConfig is not exported
# in schemathesis.config.__all__, but we need it to disable inference algorithms.
# See CLAUDE.md "Schemathesis Gotchas" for details.
_InferenceConfig = type(StatefulPhaseConfig().inference)
from schemathesis.specs.openapi.stateful import OpenAPIStateMachine

from api_parity.models import ChainCase, ChainStep, RequestCase


# Pattern to extract field references from OpenAPI link expressions
# Matches: $response.body#/fieldname or $response.body#/nested/path
LINK_BODY_PATTERN = re.compile(r'\$response\.body#/(.+)$')


def _create_explicit_links_only_config() -> SchemathesisConfig:
    """Create Schemathesis config that disables inference algorithms.

    Stateful chain generation uses only explicit OpenAPI links, not inferred
    relationships from parameter name matching or other heuristics. This ensures
    chains follow documented API contracts, not guessed relationships.

    See DESIGN.md "Explicit Links Only for Chain Generation" for rationale.
    """
    # Disable all inference algorithms (LOCATION_HEADERS, DEPENDENCY_ANALYSIS)
    inference = _InferenceConfig(algorithms=[])
    stateful = StatefulPhaseConfig(inference=inference)
    phases = PhasesConfig(stateful=stateful)
    project = ProjectConfig(phases=phases)
    projects = ProjectsConfig(default=project)
    return SchemathesisConfig(projects=projects)


def extract_link_fields_from_spec(spec: dict) -> set[str]:
    """Extract all field names referenced by OpenAPI link expressions.

    Parses the OpenAPI spec to find all link definitions and extracts the
    field names they reference via $response.body#/... expressions.

    Args:
        spec: Parsed OpenAPI specification dict.

    Returns:
        Set of field names (top-level) referenced by links.
    """
    fields: set[str] = set()

    paths = spec.get("paths", {})
    for path_item in paths.values():
        if not isinstance(path_item, dict):
            continue
        for method_or_key, operation in path_item.items():
            # Skip non-operation keys like 'parameters', '$ref'
            if not isinstance(operation, dict) or method_or_key.startswith("$"):
                continue
            responses = operation.get("responses", {})
            for response in responses.values():
                if not isinstance(response, dict):
                    continue
                links = response.get("links", {})
                for link in links.values():
                    if not isinstance(link, dict):
                        continue
                    parameters = link.get("parameters", {})
                    for param_expr in parameters.values():
                        if not isinstance(param_expr, str):
                            continue
                        match = LINK_BODY_PATTERN.match(param_expr)
                        if match:
                            # Extract the full JSONPointer path
                            json_pointer = match.group(1)
                            # Store the full pointer for nested extraction
                            fields.add(json_pointer)

    return fields


def extract_by_jsonpointer(data: Any, pointer: str) -> Any:
    """Extract a value from nested data using a JSONPointer path.

    Args:
        data: The data structure to extract from (dict or list).
        pointer: JSONPointer path without leading slash (e.g., "id" or "data/items/0/id").

    Returns:
        The extracted value, or None if path doesn't exist.
    """
    if not pointer:
        return data

    parts = pointer.split("/")
    current = data

    for part in parts:
        if current is None:
            return None
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list):
            try:
                index = int(part)
                current = current[index] if 0 <= index < len(current) else None
            except (ValueError, IndexError):
                return None
        else:
            return None

    return current


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

        # Create config that disables inference algorithms for stateful testing.
        # Chain generation only follows explicit OpenAPI links, not inferred
        # relationships from parameter name matching or Location headers.
        schemathesis_config = _create_explicit_links_only_config()

        try:
            self._schema = schemathesis.openapi.from_path(
                str(spec_path), config=schemathesis_config
            )
        except Exception as e:
            raise CaseGeneratorError(f"Failed to load OpenAPI spec: {e}") from e

        # Load raw spec to extract link field references
        try:
            with open(spec_path) as f:
                if spec_path.suffix.lower() in (".yaml", ".yml"):
                    self._raw_spec = yaml.safe_load(f)
                else:
                    self._raw_spec = json.load(f)
        except Exception as e:
            raise CaseGeneratorError(f"Failed to parse spec for link extraction: {e}") from e

        # Extract field names referenced by OpenAPI links
        self._link_fields = extract_link_fields_from_spec(self._raw_spec)

    def get_link_fields(self) -> set[str]:
        """Get the set of field names referenced by OpenAPI links.

        Returns:
            Set of JSONPointer paths referenced by link expressions.
        """
        return self._link_fields

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

    def get_all_operation_ids(self) -> set[str]:
        """Get all operation IDs from the spec (ignores exclude filter).

        Useful for validation where you need to check against all spec
        operations, not just the filtered ones.

        Returns:
            Set of all operationIds in the spec.
        """
        operation_ids = set()
        for result in self._schema.get_all_operations():
            op = result.ok()
            if op is None:
                continue
            raw = op.definition.raw
            operation_id = raw.get("operationId", f"{op.method}_{op.path}")
            operation_ids.add(operation_id)
        return operation_ids

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
                """Capture the case instead of making HTTP request.

                During chain discovery, we don't make real HTTP calls. Instead we
                return synthetic responses with placeholder data so Schemathesis
                can resolve OpenAPI links and discover possible chain paths.
                """
                nonlocal current_steps, step_counter

                # Extract operation info
                op_id = case.operation.definition.raw.get("operationId", "unknown")
                if op_id in generator_self._exclude:
                    # Return synthetic response for excluded operations
                    return self._synthetic_response(case)

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

                return self._synthetic_response(case)

            def _synthetic_response(self, case) -> SchemathesisResponse:
                """Generate synthetic response for link resolution during chain discovery.

                This is NOT a mock for testing - it's a placeholder response that
                allows Schemathesis to resolve OpenAPI link expressions and discover
                possible chain paths. Real HTTP execution happens later in the Executor.
                """
                synthetic_body = self._generate_synthetic_body()

                # Create placeholder request object (required by Schemathesis)
                req = requests.Request(
                    method=case.method,
                    url=f"http://placeholder{case.path}",
                )
                prepared = req.prepare()

                return SchemathesisResponse(
                    status_code=201 if case.method == "POST" else 200,
                    headers={"content-type": ["application/json"]},
                    content=json.dumps(synthetic_body).encode(),
                    request=prepared,
                    elapsed=0.1,
                    verify=False,
                    http_version="1.1",
                )

            def _generate_synthetic_body(self) -> dict:
                """Generate synthetic response body for link resolution.

                Creates placeholder values for all fields referenced by OpenAPI links
                so Schemathesis can resolve link expressions during chain discovery.
                Real response data comes from actual HTTP execution in the Executor.
                """
                body: dict = {}

                # Add all fields referenced by links in the spec
                for field_pointer in generator_self._link_fields:
                    self._set_by_jsonpointer(body, field_pointer, str(uuid.uuid4()))

                # Add common non-ID fields for general compatibility
                body.setdefault("name", "Placeholder")
                body.setdefault("price", 9.99)
                body.setdefault("status", "pending")
                body.setdefault("total", 3)
                body.setdefault("created_at", "2024-01-01T00:00:00Z")

                # Ensure nested items have IDs if items array exists
                if "items" not in body:
                    body["items"] = [{"id": str(uuid.uuid4())} for _ in range(3)]

                return body

            def _set_by_jsonpointer(self, data: dict, pointer: str, value: Any) -> None:
                """Set a value in nested data using a JSONPointer path.

                Creates intermediate dicts/lists as needed. Handles both dict keys
                and array indices in the path (e.g., "items/0/id" creates
                {"items": [{"id": value}]}).
                """
                parts = pointer.split("/")
                current = data

                for i, part in enumerate(parts[:-1]):
                    next_part = parts[i + 1]
                    is_next_array = next_part.isdigit()

                    if isinstance(current, list):
                        # Current is a list - part must be an array index
                        idx = int(part)
                        while len(current) <= idx:
                            current.append({})
                        # Ensure element is correct type for next access
                        if is_next_array and not isinstance(current[idx], list):
                            current[idx] = []
                        elif not is_next_array and not isinstance(current[idx], dict):
                            current[idx] = {}
                        current = current[idx]
                    else:
                        # Current is a dict - part is a key
                        if part not in current:
                            current[part] = [] if is_next_array else {}
                        current = current[part]

                # Set the final value
                final_part = parts[-1]
                if isinstance(current, list) and final_part.isdigit():
                    idx = int(final_part)
                    while len(current) <= idx:
                        current.append({})
                    current[idx] = value
                else:
                    current[final_part] = value

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
