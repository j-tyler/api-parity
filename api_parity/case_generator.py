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
from dataclasses import dataclass, field
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
from api_parity.schema_value_generator import SchemaValueGenerator


# Pattern to extract field references from OpenAPI link expressions
# Matches: $response.body#/fieldname or $response.body#/nested/path
LINK_BODY_PATTERN = re.compile(r'\$response\.body#/(.+)$')

# Pattern for header expressions: $response.header.{HeaderName} or $response.header.{HeaderName}[index]
# Header names can contain alphanumeric, hyphens, and underscores
# Optional array index for multi-value header access (e.g., Set-Cookie[0])
LINK_HEADER_PATTERN = re.compile(
    r'\$response\.header\.([A-Za-z0-9\-_]+)(?:\[(\d+)\])?$', re.IGNORECASE
)


@dataclass
class HeaderRef:
    """Reference to a response header value with optional array indexing.

    HTTP headers can have multiple values. This dataclass tracks which header
    to extract and optionally which specific value index.

    HTTP headers are case-insensitive per RFC 7230, but OpenAPI link expressions
    use a specific case (e.g., $response.header.Location). We store both:
    - original_name: The case from the OpenAPI spec (e.g., "Location")
    - name: Lowercase for HTTP-compliant lookups (e.g., "location")

    Schemathesis resolves links using the spec's original case, so synthetic
    headers must use original_name as dict keys. Variable extraction uses
    lowercase name for case-insensitive matching against actual HTTP responses.

    Attributes:
        name: Lowercase header name for variable extraction (e.g., "location").
        original_name: Original case from OpenAPI spec for link resolution (e.g., "Location").
        index: If None, extracts all values as list. If int, extracts specific index.
    """

    name: str
    original_name: str
    index: int | None = None


@dataclass
class LinkFields:
    """Container for field references extracted from OpenAPI link expressions.

    Holds both body JSONPointer paths and header references that are used
    by link expressions in the spec. Used for variable extraction during
    chain execution.

    Attributes:
        body_pointers: Set of JSONPointer paths from body expressions
                       (e.g., {"id", "data/item/id"}).
        headers: List of HeaderRef objects specifying which headers to extract
                 and optional array indices for multi-value access.
    """

    body_pointers: set[str] = field(default_factory=set)
    headers: list[HeaderRef] = field(default_factory=list)


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


def extract_link_fields_from_spec(spec: dict) -> LinkFields:
    """Extract field references from all OpenAPI link expressions in the spec.

    Finds body expressions ($response.body#/path) and header expressions
    ($response.header.Name) used in link parameter mappings.
    """
    link_fields = LinkFields()

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

                        # Check for body expression
                        body_match = LINK_BODY_PATTERN.match(param_expr)
                        if body_match:
                            # Extract the full JSONPointer path
                            json_pointer = body_match.group(1)
                            link_fields.body_pointers.add(json_pointer)
                            continue

                        # Check for header expression
                        header_match = LINK_HEADER_PATTERN.match(param_expr)
                        if header_match:
                            # Preserve original case for Schemathesis link resolution,
                            # also store lowercase for HTTP-compliant variable extraction
                            original_name = header_match.group(1)
                            header_name = original_name.lower()
                            # Capture optional array index
                            index_str = header_match.group(2)
                            index = int(index_str) if index_str is not None else None
                            link_fields.headers.append(HeaderRef(
                                name=header_name,
                                original_name=original_name,
                                index=index,
                            ))

    return link_fields


def extract_by_jsonpointer(data: Any, pointer: str) -> Any:
    """Extract value at JSONPointer path (no leading slash), or None if missing."""
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

        # Initialize schema-aware value generator for synthetic responses
        self._schema_generator = SchemaValueGenerator(self._raw_spec)

    def get_link_fields(self) -> LinkFields:
        """Return field references extracted from OpenAPI link expressions."""
        return self._link_fields

    def get_operations(self) -> list[dict[str, Any]]:
        """Return filtered operations as dicts with operation_id, method, path."""
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
        """Return all operationIds in spec, ignoring exclude filter.

        Unlike get_operations(), includes excluded operations - useful for
        validation against the full spec.
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
        """Generate test cases for all non-excluded operations."""
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
        """Generate up to max_cases RequestCase objects for one operation."""
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
        """Convert a Schemathesis case to our RequestCase model."""
        path_params = dict(case.path_parameters) if case.path_parameters else {}

        rendered_path = case.path
        for key, value in path_params.items():
            rendered_path = rendered_path.replace(f"{{{key}}}", str(value))

        query: dict[str, list[str]] = {}
        if case.query:
            for key, value in case.query.items():
                if isinstance(value, list):
                    query[key] = [str(v) for v in value]
                else:
                    query[key] = [str(value)]

        headers: dict[str, list[str]] = {}
        if case.headers:
            for key, value in case.headers.items():
                if isinstance(value, list):
                    headers[key] = [str(v) for v in value]
                else:
                    headers[key] = [str(value)]

        cookies: dict[str, str] = {}
        if case.cookies:
            cookies = {str(k): str(v) for k, v in case.cookies.items()}

        body: Any = None
        media_type: str | None = None
        # Schemathesis uses NotSet sentinel for absent body; check by type name
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

        Uses Schemathesis state machine to discover multi-step sequences.
        Chains are templates only - no HTTP calls are made during generation.
        """
        max_chains = max_chains or 20

        # Create capturing state machine
        captured_chains: list[ChainCase] = []
        current_steps: list[ChainStep] = []
        step_counter = 0

        generator_self = self
        # Track all previous operations and status codes for link detection.
        # Schemathesis can use extracted variables from ANY previous step, not just
        # the immediately previous one. This list enables searching back through
        # the chain history to find the source of a link.
        prev_steps: list[tuple[str, int]] = []  # (operation_id, status_code)

        class ChainCapturingStateMachine(OpenAPIStateMachine):
            """State machine that captures chains without making HTTP calls."""

            def validate_response(self, response, case, **kwargs):
                """Skip validation - we're just generating chains."""
                pass

            def setup(self):
                """Called before each test run - start a new chain."""
                nonlocal current_steps, step_counter, prev_steps
                current_steps = []
                step_counter = 0
                prev_steps = []

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
                nonlocal current_steps, step_counter, prev_steps

                # Extract operation info
                op_id = case.operation.definition.raw.get("operationId", "unknown")
                if op_id in generator_self._exclude:
                    # Return synthetic response for excluded operations
                    return self._synthetic_response(case)

                # Convert to our RequestCase
                request_case = generator_self._convert_case(case, op_id)

                # Find link source if this is not the first step.
                # Search ALL previous steps (most recent first) for a matching link.
                link_source = None
                if prev_steps:
                    link_source = self._find_link_between(prev_steps, op_id)

                # Create chain step
                step = ChainStep(
                    step_index=step_counter,
                    request_template=request_case,
                    link_source=link_source,
                )
                current_steps.append(step)
                step_counter += 1

                # Append to history for future link lookups
                response = self._synthetic_response(case)
                prev_steps.append((op_id, response.status_code))

                return response

            def _find_link_between(
                self, prev_steps: list[tuple[str, int]], target_op: str
            ) -> dict | None:
                """Find link in spec connecting any previous step to target_op.

                Searches ALL previous steps (most recent first) because Schemathesis
                can use extracted variables from any earlier step, not just the
                immediately previous one.

                Returns dict with link_name, source_operation, status_code,
                is_inferred, field, parameters; or None if no explicit link found.
                """
                spec = generator_self._raw_spec
                paths = spec.get("paths", {})

                # Search most recent steps first - links are more likely to come
                # from recent operations
                for source_op, status_code in reversed(prev_steps):
                    # Find the source operation in the spec
                    for path_item in paths.values():
                        if not isinstance(path_item, dict):
                            continue
                        for method_or_key, operation in path_item.items():
                            if not isinstance(operation, dict) or method_or_key.startswith("$"):
                                continue

                            op_id = operation.get("operationId")
                            if op_id != source_op:
                                continue

                            # Look for links in responses
                            responses = operation.get("responses", {})
                            for resp_code, response_def in responses.items():
                                if not isinstance(response_def, dict):
                                    continue

                                # Match status code: exact, wildcard (2XX), or default
                                if not self._matches_status_code(str(resp_code), status_code):
                                    continue

                                links = response_def.get("links", {})
                                for link_name, link_def in links.items():
                                    if not isinstance(link_def, dict):
                                        continue
                                    link_target = link_def.get("operationId") or link_def.get("operationRef")
                                    if link_target == target_op:
                                        # Extract all parameter expressions for replay support
                                        # Store as dict mapping param name to expression
                                        parameters = link_def.get("parameters", {})
                                        param_expressions = {
                                            k: v for k, v in parameters.items()
                                            if isinstance(v, str)
                                        }

                                        # For backwards compatibility, also store first expression as "field"
                                        field_expr = next(iter(param_expressions.values()), None) if param_expressions else None

                                        return {
                                            "link_name": link_name,
                                            "source_operation": source_op,
                                            "status_code": status_code,
                                            "is_inferred": False,
                                            "field": field_expr,  # First expression (backwards compat)
                                            "parameters": param_expressions,  # All expressions
                                        }

                # No explicit link found in spec
                return None

            def _matches_status_code(self, spec_code: str, actual_code: int) -> bool:
                """Check if a spec response code matches an actual status code.

                Handles exact matches, wildcards (2XX), and 'default'.
                """
                actual_str = str(actual_code)
                # Exact match
                if spec_code == actual_str:
                    return True
                # 'default' matches any status code
                if spec_code == "default":
                    return True
                # Wildcard like '2XX' matches any 2xx code
                if len(spec_code) == 3 and spec_code.endswith("XX"):
                    return spec_code[0] == actual_str[0]
                return False

            def _find_status_code_with_links(self, operation_id: str, method: str) -> int:
                """Return lowest 2xx status code with links, or fallback (201/200).

                Without this, PUT/DELETE with links on 201/202 were missed because
                synthetic responses defaulted to 200 which had no links.
                """
                spec = generator_self._raw_spec
                paths = spec.get("paths", {})

                # Find this operation in the spec
                for path_item in paths.values():
                    if not isinstance(path_item, dict):
                        continue
                    for method_or_key, operation in path_item.items():
                        if not isinstance(operation, dict) or method_or_key.startswith("$"):
                            continue
                        if operation.get("operationId") != operation_id:
                            continue

                        # Found the operation - look for responses with links
                        responses = operation.get("responses", {})

                        # Collect all 2xx status codes that have links
                        status_codes_with_links = []
                        for resp_code, response_def in responses.items():
                            if not isinstance(response_def, dict):
                                continue
                            links = response_def.get("links", {})
                            if not links:
                                continue

                            # Parse status code (handle wildcards like "2XX" and "default")
                            if resp_code == "default":
                                # default could be any status, use fallback
                                continue
                            if resp_code.endswith("XX"):
                                # Wildcard like "2XX" - use representative value
                                try:
                                    base = int(resp_code[0])
                                    status_codes_with_links.append(base * 100)
                                except ValueError:
                                    continue
                            else:
                                try:
                                    code = int(resp_code)
                                    # Only consider 2xx success codes
                                    if 200 <= code < 300:
                                        status_codes_with_links.append(code)
                                except ValueError:
                                    continue

                        # Return lowest 2xx status code with links
                        if status_codes_with_links:
                            return min(status_codes_with_links)

                # Fallback: POST typically returns 201, others return 200
                return 201 if method.upper() == "POST" else 200

            def _synthetic_response(self, case) -> SchemathesisResponse:
                """Placeholder response for Schemathesis link resolution.

                NOT a mock - just enables chain discovery. Real HTTP calls happen
                in Executor during actual execution.
                """
                # Extract operation info for schema-aware generation
                op_id = case.operation.definition.raw.get("operationId", "unknown")
                status_code = self._find_status_code_with_links(op_id, case.method)
                synthetic_body = self._generate_synthetic_body(op_id, status_code)
                synthetic_headers = self._generate_synthetic_headers()

                # Create placeholder request object (required by Schemathesis)
                req = requests.Request(
                    method=case.method,
                    url=f"http://placeholder{case.path}",
                )
                prepared = req.prepare()

                return SchemathesisResponse(
                    status_code=status_code,
                    headers=synthetic_headers,
                    content=json.dumps(synthetic_body).encode(),
                    request=prepared,
                    elapsed=0.1,
                    verify=False,
                    http_version="1.1",
                )

            def _generate_synthetic_body(
                self, operation_id: str, status_code: int
            ) -> dict:
                """Generate placeholder values for link-referenced fields.

                Uses schema-aware generation to satisfy constraints (enums, formats).
                See DESIGN.md "Schema-Driven Synthetic Value Generation".
                """
                body: dict = {}

                # Get response schema for this operation
                response_schema = generator_self._schema_generator.get_response_schema(
                    operation_id, status_code
                )

                # Add all body fields referenced by links in the spec
                for field_pointer in generator_self._link_fields.body_pointers:
                    # Try to get field schema for schema-aware generation
                    field_schema = None
                    if response_schema is not None:
                        field_schema = generator_self._schema_generator.navigate_to_field(
                            response_schema, field_pointer
                        )

                    # Generate value satisfying schema constraints (enum, format, type)
                    # Falls back to UUID if no schema found
                    value = generator_self._schema_generator.generate(field_schema)
                    self._set_by_jsonpointer(body, field_pointer, value)

                return body

            def _generate_synthetic_headers(self) -> dict[str, list[str]]:
                """Generate placeholder values for link-referenced headers.

                GOTCHA: Dict keys use original case from spec (e.g., "Location" not
                "location") because Schemathesis matches $response.header.Location
                exactly. See HeaderRef docstring.
                """
                # Always include content-type
                headers: dict[str, list[str]] = {"content-type": ["application/json"]}

                # Collect header info: max index needed and original case name
                # Use lowercase as key for deduplication, store (original_name, max_index)
                header_info: dict[str, tuple[str, int]] = {}
                for header_ref in generator_self._link_fields.headers:
                    lowercase = header_ref.name
                    if lowercase in header_info:
                        orig_name, current_max = header_info[lowercase]
                    else:
                        orig_name = header_ref.original_name
                        current_max = 0

                    if header_ref.index is not None:
                        new_max = max(current_max, header_ref.index + 1)
                    else:
                        # No index means we need at least one value
                        new_max = max(current_max, 1)
                    header_info[lowercase] = (orig_name, new_max)

                # Add synthetic values for all headers referenced by links
                # Use original_name as dict key so Schemathesis can find it
                # We use uuid.uuid4() as a generic non-empty placeholder - the actual
                # format is defined by the OpenAPI spec, not by this code. We don't
                # assume any particular format (like URL for Location).
                # See DESIGN.md "Schema-Driven Synthetic Value Generation".
                for lowercase, (original_name, count) in header_info.items():
                    values = []
                    for i in range(count):
                        values.append(str(uuid.uuid4()))
                    headers[original_name] = values

                return headers

            def _set_by_jsonpointer(self, data: dict, pointer: str, value: Any) -> None:
                """Set value at JSONPointer path, creating intermediate dicts/lists."""
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
