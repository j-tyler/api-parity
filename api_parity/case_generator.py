"""Case Generator - Wraps Schemathesis for test case generation.

Generates RequestCase objects from an OpenAPI specification using Schemathesis
as the underlying fuzzer. Currently supports stateless (single request) generation
only. Stateful chain generation via OpenAPI links is planned but not yet implemented.

See ARCHITECTURE.md "Case Generator Component" for specifications.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Iterator

import schemathesis
from hypothesis import Phase, settings

from api_parity.models import RequestCase


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

        # Body
        body: Any = None
        media_type: str | None = None
        if case.body is not None:
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
