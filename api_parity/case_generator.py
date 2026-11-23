"""Case Generator - Wraps Schemathesis for test case generation.

Generates RequestCase objects from an OpenAPI specification using Schemathesis
as the underlying fuzzer. Supports both stateless (single request) generation
and stateful chain generation via OpenAPI links.

See ARCHITECTURE.md "Case Generator Component" for specifications.
"""

from __future__ import annotations

import base64
import json
import random
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

import requests
import schemathesis
import yaml
from hypothesis import Phase, seed as hypothesis_seed, settings
from hypothesis.errors import HypothesisException
from hypothesis.stateful import run_state_machine_as_test
from schemathesis.config import (
    PhasesConfig,
    ProjectConfig,
    ProjectsConfig,
    SchemathesisConfig,
    StatefulPhaseConfig,
)
from schemathesis.core import NotSet
from schemathesis.core.transport import Response as SchemathesisResponse
from schemathesis.transport.serialization import Binary

# Get InferenceConfig via public API indirection. InferenceConfig is not exported
# in schemathesis.config.__all__, but we need it to disable inference algorithms.
# See CLAUDE.md "Schemathesis Gotchas" for details.
_InferenceConfig = type(StatefulPhaseConfig().inference)
from schemathesis.specs.openapi.stateful import OpenAPIStateMachine

from api_parity.models import ChainCase, ChainStep, RequestCase, response_body_format
from api_parity.path_rendering import decode_path_value, render_path
from api_parity.schema_validator import build_operation_index, check_response_contract
from api_parity.schema_value_generator import SchemaValueGenerator


def _unwrap_binary(value: Any, *, json_field: bool = False) -> Any:
    if isinstance(value, Binary):
        return base64.b64encode(value.data).decode("ascii") if json_field else value.data
    if isinstance(value, dict):
        return {key: _unwrap_binary(item, json_field=json_field) for key, item in value.items()}
    if isinstance(value, list):
        return [_unwrap_binary(item, json_field=json_field) for item in value]
    return value


def _get_operation_id(raw_operation: dict[str, Any], method: str, path: str) -> str:
    """Get operationId from operation definition, or generate a consistent default.

    When an operation doesn't have an explicit operationId, generates one using
    the format "{method}_{path}" (e.g., "get_/users/{id}"). This ensures consistent
    identification across all code paths that need to reference operations.

    Args:
        raw_operation: The raw operation definition dict from the spec.
        method: HTTP method (GET, POST, etc.)
        path: The path template (e.g., "/users/{id}")

    Returns:
        The operationId or a generated default.
    """
    return raw_operation.get("operationId", f"{method}_{path}")


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
    """Extract all field references from OpenAPI link expressions.

    Parses the OpenAPI spec to find all link definitions and extracts the
    field references they contain:
    - Body expressions ($response.body#/...) are stored as JSONPointer paths
    - Header expressions ($response.header.X) are stored as lowercase header names

    Args:
        spec: Parsed OpenAPI specification dict.

    Returns:
        LinkFields with body_pointers and header_names sets.
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


# Sentinel for "path not found" in extract_by_jsonpointer.
# Distinguishes "path exists with value None (JSON null)" from "path doesn't
# exist." Without this, JSON null values are silently dropped during variable
# extraction, causing the chain to substitute fuzz values instead of null.
_MISSING = object()


def _decode_jsonpointer_segment(segment: str) -> str:
    """Decode RFC 6901 escape sequences in a JSONPointer segment.

    Per RFC 6901, escape sequences are:
    - ~1 → / (slash)
    - ~0 → ~ (tilde)

    Order matters: ~1 must be decoded before ~0 to avoid turning ~01 into ~1 then /.
    """
    return segment.replace("~1", "/").replace("~0", "~")


def extract_by_jsonpointer(data: Any, pointer: str) -> Any:
    """Extract a value from nested data using a JSONPointer path.

    Follows RFC 6901 JSONPointer semantics with slash-separated path segments.
    Returns _MISSING for any invalid/absent path rather than raising.

    Escape sequences per RFC 6901:
    - ~0 decodes to ~ (tilde)
    - ~1 decodes to / (slash)

    Args:
        data: The data structure to extract from (dict or list).
        pointer: JSONPointer path without leading slash (e.g., "id" or "data/items/0/id").

    Returns:
        The extracted value (may be None for JSON null), or _MISSING if
        path doesn't exist. Callers must check ``value is not _MISSING``
        to distinguish JSON null from absent paths.
    """
    if not pointer:
        return data

    parts = pointer.split("/")
    current = data

    for part in parts:
        # Decode RFC 6901 escape sequences
        part = _decode_jsonpointer_segment(part)

        if current is _MISSING:
            return _MISSING
        if current is None:
            # JSON null value mid-traversal — path doesn't continue
            return _MISSING
        if isinstance(current, dict):
            current = current.get(part, _MISSING)
        elif isinstance(current, list):
            try:
                index = int(part)
                current = current[index] if 0 <= index < len(current) else _MISSING
            except (ValueError, IndexError):
                return _MISSING
        else:
            return _MISSING

    return current


def _collection_types(schema: Any) -> set[str]:
    """Possible collection shapes, including composed parameter schemas."""
    kinds = {"array", "object"}
    if not isinstance(schema, dict):
        return kinds if schema is True else set()
    declared = schema.get("type")
    if declared is not None:
        kinds.intersection_update([declared] if isinstance(declared, str) else declared)
    allowed = schema.get("enum")
    if "const" in schema:
        allowed = [schema["const"]]
    if allowed is not None:
        if not any(isinstance(value, list) for value in allowed):
            kinds.discard("array")
        if not any(isinstance(value, dict) for value in allowed):
            kinds.discard("object")
    for branch in schema.get("allOf", ()):
        kinds &= _collection_types(branch)
    for keyword in ("anyOf", "oneOf"):
        if keyword in schema:
            kinds &= set().union(*(_collection_types(branch) for branch in schema[keyword]))
    return kinds


class CaseGeneratorError(Exception):
    """Raised when case generation fails.

    Covers: spec loading failures, spec parsing errors, and state machine
    creation errors. The error message includes the spec path and root cause.
    """


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
            raise CaseGeneratorError(
                f"Failed to load OpenAPI spec from '{spec_path}': {e}"
            ) from e

        # Load raw spec to extract link field references
        try:
            with open(spec_path) as f:
                if spec_path.suffix.lower() in (".yaml", ".yml"):
                    self._raw_spec = yaml.safe_load(f)
                else:
                    self._raw_spec = json.load(f)
        except Exception as e:
            raise CaseGeneratorError(
                f"Failed to parse spec '{spec_path}' for link extraction: {e}"
            ) from e

        # Extract field names referenced by OpenAPI links
        self._link_fields = extract_link_fields_from_spec(self._raw_spec)

        # Initialize schema-aware value generator for synthetic responses
        self._schema_generator = SchemaValueGenerator(self._raw_spec)

        # Pre-build operationId -> operation definition index for O(1) lookups.
        # Used by _find_status_code_with_links() instead of scanning all paths × methods.
        self._operation_index = build_operation_index(self._raw_spec)

        # Cache for chain topology reuse across seeds. After the first
        # generate_chains() call discovers chain structures via the expensive
        # Hypothesis state machine, subsequent calls with different seeds
        # reuse these structures and only regenerate fuzz parameter values.
        # This avoids re-running the full state machine engine per seed.
        # See DESIGN.md "Chain Topology Caching" for rationale.
        self._cached_chain_topologies: list[list[dict[str, Any]]] | None = None

        # Index mapping operation_id -> Schemathesis operation object, built
        # lazily on first use. Needed to generate fresh fuzz values for cached
        # chain topologies without re-running the state machine.
        self._schemathesis_op_index: dict[str, Any] | None = None

    def _get_schemathesis_op_index(self) -> dict[str, Any]:
        """Build or return cached index of operation_id -> Schemathesis operation object.

        Lazily built on first call. Used by _regenerate_chains_from_cache()
        to generate fresh fuzz values for specific operations without running
        the full state machine.
        """
        if self._schemathesis_op_index is not None:
            return self._schemathesis_op_index

        index: dict[str, Any] = {}
        for result in self._schema.get_all_operations():
            op = result.ok()
            if op is None:
                continue
            raw = op.definition.raw
            operation_id = raw.get("operationId", f"{op.method}_{op.path}")
            index[operation_id] = op

        self._schemathesis_op_index = index
        return index

    def _extract_chain_topologies(
        self, chains: list[ChainCase]
    ) -> list[list[dict[str, Any]]]:
        """Extract reusable chain topologies from generated chains.

        A topology captures the structural aspects of a chain (which operations
        in which order, with which link sources) but NOT the fuzz-generated
        parameter values. This allows regenerating chains with different seeds
        without re-running the expensive Hypothesis state machine.

        Args:
            chains: List of ChainCase objects from a generate_chains() run.

        Returns:
            List of topologies. Each topology is a list of step descriptors
            containing operation_id, method, path_template, and link_source.
        """
        topologies: list[list[dict[str, Any]]] = []
        for chain in chains:
            topo: list[dict[str, Any]] = []
            for step in chain.steps:
                topo.append({
                    "operation_id": step.request_template.operation_id,
                    "method": step.request_template.method,
                    "path_template": step.request_template.path_template,
                    "link_source": step.link_source,
                })
            topologies.append(topo)
        return topologies

    def _regenerate_chains_from_cache(
        self,
        topologies: list[list[dict[str, Any]]],
        seed: int | None,
    ) -> list[ChainCase]:
        """Regenerate chains from cached topologies with fresh fuzz values.

        Reuses the chain structure (operation sequence + link sources) from a
        previous run but generates new parameter values using the given seed.
        This is much cheaper than running the full Hypothesis state machine.

        Args:
            topologies: Cached chain topologies from _extract_chain_topologies().
            seed: Random seed for fuzz value generation.

        Returns:
            List of ChainCase objects with fresh parameter values.
        """
        op_index = self._get_schemathesis_op_index()

        # Seed Python's random module for reproducibility (same as generate_chains)
        if seed is not None:
            random.seed(seed)

        chains: list[ChainCase] = []
        for topo in topologies:
            steps: list[ChainStep] = []
            for step_idx, step_desc in enumerate(topo):
                op_id = step_desc["operation_id"]
                schemathesis_op = op_index.get(op_id)

                if schemathesis_op is None:
                    # Operation not found in spec (e.g., excluded). Skip chain.
                    break

                # Generate one fuzz case for this operation with the current seed.
                # Use a per-step seed derived from the base seed so each step
                # gets different but reproducible values.
                step_seed = (seed + step_idx) if seed is not None else None
                fuzz_cases = list(
                    self._generate_for_operation(schemathesis_op, op_id, 1, step_seed)
                )
                if not fuzz_cases:
                    # Could not generate a case for this operation. Skip chain.
                    break

                request_case = fuzz_cases[0]

                step = ChainStep(
                    step_index=step_idx,
                    request_template=request_case,
                    link_source=step_desc["link_source"],
                )
                steps.append(step)
            else:
                # All steps generated successfully
                if len(steps) > 1:
                    chain = ChainCase(
                        chain_id=str(uuid.uuid4()),
                        steps=steps,
                    )
                    chains.append(chain)

        return chains

    def get_link_fields(self) -> LinkFields:
        """Get the field references extracted from OpenAPI link expressions.

        Returns:
            LinkFields with body_pointers and header_names sets.
        """
        return self._link_fields

    def missing_response_references(self) -> list[str]:
        return sorted(self.check_response_contract().missing)

    def check_response_contract(self):
        return check_response_contract(self._raw_spec)

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

    def get_linked_operation_ids(self) -> set[str]:
        """Get operation IDs that participate in at least one OpenAPI link.

        An operation is "linked" if it is the source of a link (has outbound
        links in one of its responses) or the target of a link (another
        operation's link references it via operationId).

        Linked operations get state machine rules in Schemathesis and are
        reachable during chain generation. Operations NOT in this set are
        "orphans" — invisible to the state machine and only testable via
        --ensure-coverage single-request tests.

        Returns:
            Set of operationIds that participate in at least one link.
        """
        link_sources: set[str] = set()
        link_targets: set[str] = set()

        paths = self._raw_spec.get("paths", {})
        for path_template, path_item in paths.items():
            if not isinstance(path_item, dict):
                continue
            for method_or_key, operation in path_item.items():
                if not isinstance(operation, dict) or method_or_key.startswith("$"):
                    continue
                op_id = _get_operation_id(operation, method_or_key, path_template)

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

        return link_sources | link_targets

    def get_link_edges(self) -> list[tuple[str, str]]:
        """Get directed edges (source_op, target_op) from declared OpenAPI links.

        Returns all structurally possible link transitions, ignoring status codes
        (since different seeds can produce different status codes, all declared
        links are structurally reachable).

        Returns:
            List of (source_operation_id, target_operation_id) tuples.
            Duplicates are possible if multiple links connect the same pair
            (different status codes or link names), but they represent the
            same structural edge for chain enumeration purposes.
        """
        edges: list[tuple[str, str]] = []

        paths = self._raw_spec.get("paths", {})
        for path_template, path_item in paths.items():
            if not isinstance(path_item, dict):
                continue
            for method_or_key, operation in path_item.items():
                if not isinstance(operation, dict) or method_or_key.startswith("$"):
                    continue
                op_id = _get_operation_id(operation, method_or_key, path_template)

                # Skip excluded operations
                if op_id in self._exclude:
                    continue

                for resp in operation.get("responses", {}).values():
                    if not isinstance(resp, dict):
                        continue
                    links = resp.get("links", {})
                    for link_def in links.values():
                        if not isinstance(link_def, dict):
                            continue
                        target = link_def.get("operationId")
                        if target and target not in self._exclude:
                            edges.append((op_id, target))

        return edges

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

        # When a seed is provided, set Hypothesis's internal seed attribute.
        # This makes different seed values produce different (but reproducible) results.
        # Without this, derandomize=True alone just uses a hash of the function.
        if seed is not None:
            collect_cases._hypothesis_internal_use_seed = seed

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
        # Schemathesis quote_plus-encodes its serialized path parameters. In a
        # URL path, a space must be %20 and an actual plus is already %2B.
        path_params = {
            key: value.replace("+", "%20") if isinstance(value, str) else value
            for key, value in (case.path_parameters or {}).items()
        }
        rendered_path = render_path(case.path, {k: decode_path_value(v) for k, v in path_params.items()})

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
        body_base64 = None
        if case.body is None and response_body_format(case.media_type or "") == "json":
            # HTTPX treats json=None as an omitted body. Carry the four wire
            # bytes in the same lossless field used by direct and replay cases.
            media_type = case.media_type
            body_base64 = base64.b64encode(b"null").decode("ascii")
        elif case.body is not None and not isinstance(case.body, NotSet):
            media_type = case.media_type
            structured_json = isinstance(case.body, (list, dict)) and "json" in (media_type or "").lower()
            body = _unwrap_binary(case.body, json_field=structured_json)

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
            body_base64=body_base64,
            media_type=media_type,
        )

    def generate_chains(
        self,
        max_chains: int | None = None,
        max_steps: int = 6,
        seed: int | None = None,
        *,
        reuse_topologies: bool = True,
    ) -> list[ChainCase]:
        """Generate stateful request chains following OpenAPI links.

        Uses Schemathesis state machine to generate multi-step sequences.
        Chains are generated without making HTTP calls - the executor handles
        actual execution.

        Args:
            max_chains: Maximum number of chains to generate (default 20).
            max_steps: Maximum steps per chain (default 6).
            seed: Seed for sampling. To repeat operation sequences across Python
                processes, also fix PYTHONHASHSEED at process startup.
            reuse_topologies: Reuse previously sampled structures with fresh inputs.
                Set to False to sample structures independently for this seed.

        Returns:
            List of ChainCase objects ready for execution.
        """
        max_chains = max_chains or 20

        if reuse_topologies and self._cached_chain_topologies is not None:
            eligible = [topology for topology in self._cached_chain_topologies
                        if len(topology) <= max_steps]
            if eligible:
                return self._regenerate_chains_from_cache(eligible[:max_chains], seed)

        # Seed Python's random module for reproducibility of status code selection.
        # Hypothesis has its own seeding via _hypothesis_internal_use_seed, but
        # _find_status_code_with_links() uses random.choice() which needs this.
        if seed is not None:
            random.seed(seed)

        # Create capturing state machine
        captured_chains: list[ChainCase] = []
        generator_self = self

        class ChainCapturingStateMachine(OpenAPIStateMachine):
            """State machine that captures chains without making HTTP calls."""

            def validate_response(self, response, case, **kwargs):
                """Skip validation - we're just generating chains."""
                pass

            def setup(self):
                """Called before each test run - start a new chain."""
                self.steps: list[ChainStep] = []
                self.sources: dict[str, tuple[int, int]] = {}

            def teardown(self):
                if self.steps:
                    captured_chains.append(ChainCase(chain_id=str(uuid.uuid4()), steps=self.steps))

            def call(self, case, **kwargs) -> SchemathesisResponse:
                return self._synthetic_response(case)

            def step(self, input):
                output = super().step(input)
                case = input.case
                op_id = _get_operation_id(case.operation.definition.raw, case.method, case.path)
                if op_id in generator_self._exclude:
                    return output

                source = None
                transition = input.transition
                # Keep an included operation's generated template when its
                # parent was excluded. There is no captured exchange to link.
                parent = self.sources.get(transition.parent_id) if transition is not None else None
                if parent is not None:
                    position, status = parent
                    link = links_by_id[transition.id]
                    parameters = {
                        (f"{p.location.value}.{p.name}" if p.location else p.name): p.expression
                        for p in link.parameters if isinstance(p.expression, str)
                    }
                    source = {
                        "link_name": link.name,
                        "source_operation": self.steps[position].request_template.operation_id,
                        "source_step_index": position,
                        "status_code": status,
                        "is_inferred": link.is_inferred,
                        "field": next(iter(parameters.values()), None),
                        "parameters": parameters,
                    }
                    # Path slots are intrinsic to the template. Persist the
                    # operation's other requirements for standalone replay.
                    required = []
                    collection_paths = {}
                    for p in case.operation.iter_parameters():
                        location = p.location.value
                        if location != "path":
                            if p.is_required:
                                name = p.name.lower() if location == "header" else p.name
                                required.append(f"{location}.{name}")
                            continue
                        expression = parameters.get(f"path.{p.name}", parameters.get(p.name, ""))
                        if expression.startswith(("$response.body#/", "$request.body#/")):
                            types = _collection_types(p.raw_schema)
                            if types:
                                definition = p.definition
                                style = definition.get("style", "simple")
                                if "content" in definition:
                                    style = "json" if "application/json" in definition["content"] else "unsupported"
                                collection_paths[p.name] = {"types": sorted(types), "style": style,
                                                            "explode": definition.get("explode", False)}
                    if collection_paths:
                        source["collection_paths"] = collection_paths
                    if required:
                        source["required_parameters"] = required

                position = len(self.steps)
                self.sources[case.id] = (position, output.response.status_code)
                self.steps.append(ChainStep(
                    step_index=position,
                    request_template=generator_self._convert_case(case, op_id),
                    link_source=source,
                ))
                return output

            def _find_status_code_with_links(self, operation_id: str, method: str) -> int:
                """Find a status code that has links defined for this operation.

                Uses pre-built operation index for O(1) lookup instead of
                scanning all paths × methods. Randomly selects from all 2xx
                status codes that have links to ensure all links can be
                discovered over multiple chain generations. Falls back to 201
                for POST / 200 otherwise.

                Random selection is necessary because links on different status
                codes may lead to different operations. For example, an operation
                with links on both 200 (update) and 201 (create) would have
                different target operations for each case. Selecting only the
                lowest status code would make the 201-specific links unreachable.
                """
                operation = generator_self._operation_index.get(operation_id)
                if operation is None:
                    return 201 if method.upper() == "POST" else 200

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

                # Randomly select from all status codes with links
                # This ensures links on all status codes can be discovered
                if status_codes_with_links:
                    return random.choice(status_codes_with_links)

                # Fallback: POST typically returns 201, others return 200
                return 201 if method.upper() == "POST" else 200

            def _synthetic_response(self, case) -> SchemathesisResponse:
                """Generate synthetic response for link resolution during chain discovery.

                This is NOT a mock for testing - it's a placeholder response that
                allows Schemathesis to resolve OpenAPI link expressions and discover
                possible chain paths. Real HTTP execution happens later in the Executor.
                """
                # Extract operation info for schema-aware generation - use consistent ID
                op_id = _get_operation_id(
                    case.operation.definition.raw, case.method, case.path
                )
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
                """Generate synthetic response body for link resolution.

                Creates placeholder values ONLY for fields referenced by OpenAPI links
                so Schemathesis can resolve link expressions during chain discovery.
                Real response data comes from actual HTTP execution in the Executor.

                Uses schema-aware generation to produce values that satisfy constraints
                (e.g., enums) defined in the OpenAPI spec. This enables chain discovery
                through operations with constrained parameters.

                See DESIGN.md "Schema-Driven Synthetic Value Generation" for rationale.
                """
                body: dict = {}

                # Get response schema for this operation
                response_schema = generator_self._schema_generator.get_response_schema(
                    operation_id, status_code
                )

                # Add all body fields referenced by links in the spec
                # Sort by length descending (longest first) to ensure nested fields
                # are set before their parents. This prevents parent pointers from
                # overwriting nested structures. E.g., if we have both "entries/0"
                # and "entries/0/assetTerm", process the longer one first.
                # Also filter out pointers that are prefixes of longer pointers,
                # since they would be created as intermediate structures anyway.
                sorted_pointers = sorted(
                    generator_self._link_fields.body_pointers,
                    key=len,
                    reverse=True
                )
                # Filter out pointers that are prefixes of other pointers
                filtered_pointers = []
                for ptr in sorted_pointers:
                    # Check if this pointer is a prefix of any longer pointer
                    is_prefix = any(
                        other.startswith(ptr + "/")
                        for other in sorted_pointers if len(other) > len(ptr)
                    )
                    if not is_prefix:
                        filtered_pointers.append(ptr)

                for field_pointer in filtered_pointers:
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
                """Generate synthetic response headers for link resolution.

                Creates placeholder values for all headers referenced by OpenAPI link
                expressions (e.g., $response.header.Location). Header values are lists
                per Schemathesis Response requirements. Multi-value headers get multiple
                synthetic values to support array indexing.

                IMPORTANT: Uses original case from OpenAPI spec as dict keys (e.g., "Location"
                not "location"). Schemathesis resolves $response.header.Location by looking
                for the exact case from the spec. See HeaderRef for details.
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
                """Set a value in nested data using a JSONPointer path.

                Creates intermediate dicts/lists as needed. Handles both dict keys
                and array indices in the path (e.g., "items/0/id" creates
                {"items": [{"id": value}]}).

                Note: Empty pointer ("") means "whole document" per RFC 6901, but we
                can't replace the dict itself. Skip silently - this edge case only
                occurs if someone uses $response.body#/ which is extremely rare.
                """
                if not pointer:
                    return  # Can't replace document, skip edge case

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
            raise CaseGeneratorError(
                f"Failed to create state machine from '{self._spec_path}'. "
                f"Verify the spec has valid operations and links: {e}"
            ) from e

        links_by_id = {
            link.full_name: link
            for operation in OriginalStateMachine._transitions.operations.values()
            for link in operation.outgoing
        }

        # Create combined class
        class CombinedMachine(ChainCapturingStateMachine, OriginalStateMachine):
            pass

        if seed is not None:
            hypothesis_seed(seed)(CombinedMachine)
        generation_settings = settings(
            CombinedMachine.TestCase.settings,
            max_examples=max_chains,
            stateful_step_count=max_steps,
            database=None,
            phases=[Phase.generate],
            deadline=None,
        )

        try:
            run_state_machine_as_test(CombinedMachine, settings=generation_settings)
        except HypothesisException:
            # Normal termination: Hypothesis raises when it has explored all reachable
            # states in the state machine (e.g., fewer valid chains than max_chains).
            # This is expected behavior, not an error - we continue with whatever
            # chains were captured before the state space was exhausted.
            pass

        # Filter to chains with multiple steps (single-step chains aren't useful)
        # Hypothesis may run additional examples that it subsequently discards.
        multi_step_chains = [c for c in captured_chains if len(c.steps) > 1][:max_chains]

        # Cache the chain topologies for reuse on subsequent calls with
        # different seeds. This avoids re-running the expensive Hypothesis
        # state machine when only the fuzz parameter values need to change.
        if reuse_topologies and multi_step_chains:
            self._cached_chain_topologies = self._extract_chain_topologies(
                multi_step_chains
            )

        return multi_step_chains
