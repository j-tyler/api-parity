"""Executor - Sends requests to targets and captures responses.

The Executor sends HTTP requests to both targets (serially) and captures
the responses as ResponseCase objects for comparison.

See ARCHITECTURE.md "Executor" for specifications.
"""

from __future__ import annotations

import base64
import time
from typing import Any, Callable

import httpx

from api_parity.case_generator import extract_by_jsonpointer
from api_parity.models import (
    ChainCase,
    ChainExecution,
    ChainStepExecution,
    RequestCase,
    ResponseCase,
    TargetConfig,
)


class ExecutorError(Exception):
    """Base class for executor errors."""


class RequestError(ExecutorError):
    """Raised when a request fails (connection error, timeout, etc.)."""


class Executor:
    """Executes requests against two targets and captures responses.

    Usage:
        executor = Executor(target_a_config, target_b_config)
        try:
            resp_a, resp_b = executor.execute(request_case)
        finally:
            executor.close()

    Or with context manager:
        with Executor(target_a_config, target_b_config) as executor:
            resp_a, resp_b = executor.execute(request_case)
    """

    def __init__(
        self,
        target_a: TargetConfig,
        target_b: TargetConfig,
        default_timeout: float = 30.0,
        operation_timeouts: dict[str, float] | None = None,
        link_fields: set[str] | None = None,
    ) -> None:
        """Initialize the executor.

        Args:
            target_a: Configuration for target A.
            target_b: Configuration for target B.
            default_timeout: Default timeout in seconds for requests.
            operation_timeouts: Per-operation timeout overrides.
            link_fields: Set of JSONPointer paths to extract from responses
                         for chain variable substitution. Parsed from OpenAPI
                         link expressions (e.g., "id", "data/item/id").
        """
        self._target_a = target_a
        self._target_b = target_b
        self._default_timeout = default_timeout
        self._operation_timeouts = operation_timeouts or {}
        self._link_fields = link_fields or set()

        # Create HTTP clients for each target
        self._client_a = httpx.Client(
            base_url=target_a.base_url,
            headers=target_a.headers,
            timeout=default_timeout,
        )
        self._client_b = httpx.Client(
            base_url=target_b.base_url,
            headers=target_b.headers,
            timeout=default_timeout,
        )

    def __enter__(self) -> "Executor":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def close(self) -> None:
        """Close HTTP clients."""
        self._client_a.close()
        self._client_b.close()

    def execute(
        self,
        request: RequestCase,
    ) -> tuple[ResponseCase, ResponseCase]:
        """Execute a request against both targets.

        Requests are executed serially: Target A first, then Target B.

        Args:
            request: The request to execute.

        Returns:
            Tuple of (response_a, response_b).

        Raises:
            RequestError: If a request fails due to connection/timeout.
        """
        timeout = self._get_timeout(request.operation_id)

        # Execute against Target A
        response_a = self._execute_single(
            self._client_a, request, timeout, "Target A"
        )

        # Execute against Target B
        response_b = self._execute_single(
            self._client_b, request, timeout, "Target B"
        )

        return response_a, response_b

    def execute_chain(
        self,
        chain: ChainCase,
        on_step: Callable[[ResponseCase, ResponseCase], bool] | None = None,
    ) -> tuple[ChainExecution, ChainExecution]:
        """Execute a chain against both targets.

        Each step is executed against both targets before proceeding to the next.
        Each target uses its own extracted response data for subsequent steps
        (per DESIGN.md "Live Chain Generation").

        Args:
            chain: The chain to execute.
            on_step: Optional callback called after each step with (response_a, response_b).
                     Return False to stop execution (per DESIGN.md "Chain Stops at First Mismatch").
                     Return True to continue.

        Returns:
            Tuple of (execution_a, execution_b) containing step-by-step traces.
            If stopped early via on_step, contains only executed steps.

        Raises:
            RequestError: If a request fails due to connection/timeout.
        """
        steps_a: list[ChainStepExecution] = []
        steps_b: list[ChainStepExecution] = []
        extracted_vars_a: dict[str, Any] = {}
        extracted_vars_b: dict[str, Any] = {}

        for step in chain.steps:
            # Each target gets request populated with its own extracted variables
            request_a = self._apply_variables(step.request_template, extracted_vars_a)
            request_b = self._apply_variables(step.request_template, extracted_vars_b)

            timeout = self._get_timeout(request_a.operation_id)

            # Execute against both targets
            response_a = self._execute_single(
                self._client_a, request_a, timeout, "Target A"
            )
            response_b = self._execute_single(
                self._client_b, request_b, timeout, "Target B"
            )

            # Extract variables from each target's own response
            extracted_a = self._extract_variables(response_a)
            extracted_b = self._extract_variables(response_b)
            extracted_vars_a.update(extracted_a)
            extracted_vars_b.update(extracted_b)

            # Record step executions
            steps_a.append(ChainStepExecution(
                step_index=step.step_index,
                request=request_a,
                response=response_a,
                extracted=extracted_a,
            ))
            steps_b.append(ChainStepExecution(
                step_index=step.step_index,
                request=request_b,
                response=response_b,
                extracted=extracted_b,
            ))

            # Check if caller wants to stop (mismatch detected)
            if on_step is not None and not on_step(response_a, response_b):
                break

        return (
            ChainExecution(steps=steps_a),
            ChainExecution(steps=steps_b),
        )

    def _apply_variables(
        self,
        template: RequestCase,
        variables: dict[str, Any],
    ) -> RequestCase:
        """Apply extracted variables to a request template.

        Substitutes {variable_name} placeholders in path parameters, query
        parameters, and body.

        Args:
            template: Request template with potential placeholders.
            variables: Extracted variables from previous steps.

        Returns:
            New RequestCase with variables substituted.
        """
        # Deep copy the template to avoid mutation
        request_dict = template.model_dump()

        # Apply variables to path parameters
        for key, value in list(request_dict.get("path_parameters", {}).items()):
            if isinstance(value, str):
                for var_name, var_value in variables.items():
                    if f"{{{var_name}}}" in value:
                        value = value.replace(f"{{{var_name}}}", str(var_value))
                    # Also check for direct match (Schemathesis may have already resolved)
                    if value == var_name:
                        value = str(var_value)
                request_dict["path_parameters"][key] = value

        # Re-render the path with updated parameters
        rendered_path = request_dict["path_template"]
        for key, value in request_dict.get("path_parameters", {}).items():
            rendered_path = rendered_path.replace(f"{{{key}}}", str(value))
        request_dict["rendered_path"] = rendered_path

        # Apply variables to query parameters (dict[str, list[str]])
        for key, values in list(request_dict.get("query", {}).items()):
            new_values = []
            for value in values:
                for var_name, var_value in variables.items():
                    if f"{{{var_name}}}" in value:
                        value = value.replace(f"{{{var_name}}}", str(var_value))
                new_values.append(value)
            request_dict["query"][key] = new_values

        # Apply variables to body if it's a dict
        if isinstance(request_dict.get("body"), dict):
            request_dict["body"] = self._substitute_in_dict(
                request_dict["body"], variables
            )

        return RequestCase.model_validate(request_dict)

    def _substitute_in_dict(
        self,
        data: dict[str, Any],
        variables: dict[str, Any],
    ) -> dict[str, Any]:
        """Recursively substitute variables in a dictionary.

        Args:
            data: Dictionary to process.
            variables: Variables to substitute.

        Returns:
            New dictionary with substitutions applied.
        """
        result = {}
        for key, value in data.items():
            if isinstance(value, str):
                for var_name, var_value in variables.items():
                    if f"{{{var_name}}}" in value:
                        value = value.replace(f"{{{var_name}}}", str(var_value))
                result[key] = value
            elif isinstance(value, dict):
                result[key] = self._substitute_in_dict(value, variables)
            elif isinstance(value, list):
                result[key] = self._substitute_in_list(value, variables)
            else:
                result[key] = value
        return result

    def _substitute_in_list(
        self,
        data: list[Any],
        variables: dict[str, Any],
    ) -> list[Any]:
        """Recursively substitute variables in a list.

        Args:
            data: List to process.
            variables: Variables to substitute.

        Returns:
            New list with substitutions applied.
        """
        result = []
        for item in data:
            if isinstance(item, str):
                for var_name, var_value in variables.items():
                    if f"{{{var_name}}}" in item:
                        item = item.replace(f"{{{var_name}}}", str(var_value))
                result.append(item)
            elif isinstance(item, dict):
                result.append(self._substitute_in_dict(item, variables))
            elif isinstance(item, list):
                result.append(self._substitute_in_list(item, variables))
            else:
                result.append(item)
        return result

    def _extract_variables(self, response: ResponseCase) -> dict[str, Any]:
        """Extract variables from a response for chain substitution.

        Extracts fields referenced by OpenAPI link expressions. Uses the
        link_fields set parsed from the spec at initialization.

        Variables are stored under their full JSONPointer path. Additionally,
        if the last segment is unique (no collision with other paths), it's
        also stored under just the last segment for simpler references.

        Args:
            response: Response to extract from.

        Returns:
            Dictionary of extracted variable names to values.
        """
        extracted: dict[str, Any] = {}

        if not isinstance(response.body, dict):
            return extracted

        # First pass: extract all values under full pointer paths
        for field_pointer in self._link_fields:
            value = extract_by_jsonpointer(response.body, field_pointer)
            if value is not None:
                extracted[field_pointer] = value

        # Second pass: add last-segment shortcuts only if no collision
        # e.g., "data/item/id" also stores under "id" if no other path ends in "id"
        last_segments: dict[str, list[str]] = {}
        for field_pointer in self._link_fields:
            last_segment = field_pointer.split("/")[-1]
            if last_segment != field_pointer:  # Only for nested paths
                last_segments.setdefault(last_segment, []).append(field_pointer)

        for last_segment, pointers in last_segments.items():
            if len(pointers) == 1:
                # No collision - safe to add shortcut
                pointer = pointers[0]
                if pointer in extracted:
                    extracted[last_segment] = extracted[pointer]
            # If len(pointers) > 1, there's a collision - skip shortcut to avoid
            # silent data loss. Users must use full path.

        return extracted

    def _get_timeout(self, operation_id: str) -> float:
        """Get timeout for an operation."""
        return self._operation_timeouts.get(operation_id, self._default_timeout)

    def _execute_single(
        self,
        client: httpx.Client,
        request: RequestCase,
        timeout: float,
        target_name: str,
    ) -> ResponseCase:
        """Execute a request against a single target.

        Args:
            client: HTTP client for the target.
            request: The request to execute.
            timeout: Request timeout in seconds.
            target_name: Name for error messages.

        Returns:
            ResponseCase with the response.

        Raises:
            RequestError: If request fails.
        """
        # Build the URL
        url = request.rendered_path

        # Build query params (flatten lists for httpx)
        params: list[tuple[str, str]] = []
        for key, values in request.query.items():
            for value in values:
                params.append((key, value))

        # Build headers (flatten lists, take first value for each)
        headers: dict[str, str] = {}
        for key, values in request.headers.items():
            if values:
                headers[key] = values[0]

        # Build body content
        content: bytes | None = None
        json_body: Any = None

        if request.body is not None:
            if request.media_type and "json" in request.media_type.lower():
                json_body = request.body
            else:
                # Encode as string for non-JSON
                if isinstance(request.body, str):
                    content = request.body.encode("utf-8")
                elif isinstance(request.body, bytes):
                    content = request.body
                else:
                    content = str(request.body).encode("utf-8")
        elif request.body_base64:
            content = base64.b64decode(request.body_base64)

        # Add content-type if specified
        if request.media_type and "content-type" not in {k.lower() for k in headers}:
            headers["Content-Type"] = request.media_type

        # Add cookies
        cookies: dict[str, str] = request.cookies

        try:
            start_time = time.perf_counter()

            http_response = client.request(
                method=request.method,
                url=url,
                params=params if params else None,
                headers=headers if headers else None,
                content=content,
                json=json_body,
                cookies=cookies if cookies else None,
                timeout=timeout,
            )

            elapsed_ms = (time.perf_counter() - start_time) * 1000

        except httpx.TimeoutException as e:
            raise RequestError(f"{target_name} request timeout: {e}") from e
        except httpx.ConnectError as e:
            raise RequestError(f"{target_name} connection error: {e}") from e
        except httpx.RequestError as e:
            raise RequestError(f"{target_name} request error: {e}") from e

        return self._convert_response(http_response, elapsed_ms)

    def _convert_response(
        self,
        response: httpx.Response,
        elapsed_ms: float,
    ) -> ResponseCase:
        """Convert httpx Response to ResponseCase.

        Args:
            response: httpx Response object.
            elapsed_ms: Elapsed time in milliseconds.

        Returns:
            ResponseCase model instance.
        """
        # Headers - lowercase keys, list values
        headers: dict[str, list[str]] = {}
        for key, value in response.headers.multi_items():
            key_lower = key.lower()
            if key_lower not in headers:
                headers[key_lower] = []
            headers[key_lower].append(value)

        # Body - try to parse as JSON
        body: Any = None
        body_base64: str | None = None

        content_type = response.headers.get("content-type", "")

        if response.content:
            if "json" in content_type.lower():
                try:
                    body = response.json()
                except Exception:
                    # Not valid JSON despite content-type
                    body_base64 = base64.b64encode(response.content).decode("ascii")
            elif content_type.startswith("text/"):
                try:
                    body = response.text
                except Exception:
                    body_base64 = base64.b64encode(response.content).decode("ascii")
            else:
                # Binary content
                body_base64 = base64.b64encode(response.content).decode("ascii")

        # Get HTTP version
        http_version = "1.1"
        if hasattr(response, "http_version"):
            http_version = response.http_version

        return ResponseCase(
            status_code=response.status_code,
            headers=headers,
            body=body,
            body_base64=body_base64,
            elapsed_ms=elapsed_ms,
            http_version=http_version,
        )
