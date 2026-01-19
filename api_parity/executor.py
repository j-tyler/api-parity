"""Executor - Sends requests to targets and captures responses.

The Executor sends HTTP requests to both targets (serially) and captures
the responses as ResponseCase objects for comparison.

See ARCHITECTURE.md "Executor" for specifications.
"""

from __future__ import annotations

import base64
import ssl
import time
from threading import Lock
from typing import Any, Callable

import httpx

from api_parity.case_generator import LinkFields, extract_by_jsonpointer
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


def _sanitize_header_value(value: str) -> str:
    """Sanitize a header value to ensure it's ASCII-safe.

    HTTP headers must contain only ASCII characters per RFC 7230. Schemathesis
    may generate non-ASCII characters during fuzzing. This function replaces
    non-ASCII characters with '?' to ensure the request can be sent.

    Args:
        value: The header value to sanitize.

    Returns:
        ASCII-safe header value with non-ASCII characters replaced by '?'.
    """
    # Use 'replace' error handler to convert non-ASCII to '?'
    return value.encode('ascii', errors='replace').decode('ascii')


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
        link_fields: LinkFields | None = None,
        requests_per_second: float | None = None,
    ) -> None:
        """Initialize the executor.

        Args:
            target_a: Configuration for target A.
            target_b: Configuration for target B.
            default_timeout: Default timeout in seconds for requests.
            operation_timeouts: Per-operation timeout overrides.
            link_fields: LinkFields object containing body_pointers and headers
                         for variable extraction during chain execution.
            requests_per_second: Maximum requests per second (rate limit).
                                 If None, no rate limiting is applied.
        """
        self._target_a = target_a
        self._target_b = target_b
        self._default_timeout = default_timeout
        self._operation_timeouts = operation_timeouts or {}
        self._link_fields = link_fields or LinkFields()

        # Rate limiting state
        self._requests_per_second = requests_per_second
        self._min_interval = 1.0 / requests_per_second if requests_per_second else 0.0
        self._last_request_time: float = 0.0
        self._rate_limit_lock = Lock()

        # Create HTTP clients for each target. If second client creation fails,
        # ensure first client is closed to prevent connection leak.
        self._client_a = httpx.Client(**self._build_client_kwargs(target_a, default_timeout))
        try:
            self._client_b = httpx.Client(**self._build_client_kwargs(target_b, default_timeout))
        except Exception:
            self._client_a.close()
            raise

    def __enter__(self) -> "Executor":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        self.close()

    def close(self) -> None:
        """Close HTTP clients.

        Uses try/finally to ensure both clients are closed even if the first
        close() raises an exception. This prevents HTTP connection leaks.
        """
        try:
            self._client_a.close()
        finally:
            self._client_b.close()

    def _build_client_kwargs(self, target: TargetConfig, timeout: float) -> dict[str, Any]:
        """Build kwargs for httpx.Client including TLS configuration.

        Args:
            target: Target configuration with optional TLS settings.
            timeout: Default timeout in seconds.

        Returns:
            Dictionary of kwargs for httpx.Client constructor.
        """
        kwargs: dict[str, Any] = {
            "base_url": target.base_url,
            "headers": target.headers,
            "timeout": timeout,
        }

        # Handle client certificate (mTLS)
        if target.cert and target.key:
            if target.key_password:
                kwargs["cert"] = (target.cert, target.key, target.key_password)
            else:
                kwargs["cert"] = (target.cert, target.key)

        # Handle ciphers - requires creating a custom SSL context
        if target.ciphers:
            ssl_context = ssl.create_default_context()
            try:
                ssl_context.set_ciphers(target.ciphers)
            except ssl.SSLError as e:
                raise ExecutorError(f"Invalid cipher string '{target.ciphers}': {e}") from e

            # Load CA bundle if specified
            if target.ca_bundle:
                ssl_context.load_verify_locations(target.ca_bundle)
            elif not target.verify_ssl:
                # Disable verification
                ssl_context.check_hostname = False
                ssl_context.verify_mode = ssl.CERT_NONE

            kwargs["verify"] = ssl_context
        # Handle server verification without custom ciphers
        elif target.ca_bundle:
            kwargs["verify"] = target.ca_bundle
        elif not target.verify_ssl:
            kwargs["verify"] = False
        # else: use httpx default (True)

        return kwargs

    def execute(
        self,
        request: RequestCase,
    ) -> tuple[ResponseCase, ResponseCase]:
        """Execute a request against both targets (serially, A then B).

        Serial execution simplifies debugging (timing differences don't mask issues)
        and keeps rate limiting predictable.

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

    def _variable_to_string(self, var_value: Any) -> str:
        """Convert a variable value to string for path/body substitution.

        Lists (e.g., header values) use first element to avoid "['value']" in URLs.
        """
        if isinstance(var_value, list):
            return str(var_value[0]) if var_value else ""
        return str(var_value)

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
                        value = value.replace(f"{{{var_name}}}", self._variable_to_string(var_value))
                    # Also check for direct match (Schemathesis may have already resolved)
                    if value == var_name:
                        value = self._variable_to_string(var_value)
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
                        value = value.replace(f"{{{var_name}}}", self._variable_to_string(var_value))
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
                        value = value.replace(f"{{{var_name}}}", self._variable_to_string(var_value))
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
                        item = item.replace(f"{{{var_name}}}", self._variable_to_string(var_value))
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

        Body fields stored at JSONPointer paths. Headers at "header/{name}" (list)
        or "header/{name}/{index}" (single value).
        """
        extracted: dict[str, Any] = {}

        # Extract body fields (if response has a dict body)
        if isinstance(response.body, dict):
            # First pass: extract all values under full pointer paths
            for field_pointer in self._link_fields.body_pointers:
                value = extract_by_jsonpointer(response.body, field_pointer)
                if value is not None:
                    extracted[field_pointer] = value

            # Add shortcut aliases: "userId" -> value (from "data/user/userId")
            # Only when unambiguous (single pointer with that last segment)
            last_segments: dict[str, list[str]] = {}
            for field_pointer in self._link_fields.body_pointers:
                last_segment = field_pointer.split("/")[-1]
                if last_segment != field_pointer:  # Only for nested paths
                    last_segments.setdefault(last_segment, []).append(field_pointer)

            for last_segment, pointers in last_segments.items():
                if len(pointers) == 1:
                    pointer = pointers[0]
                    if pointer in extracted:
                        extracted[last_segment] = extracted[pointer]

        # Extract header values: "header/{name}" (list) and "header/{name}/{index}" (single)
        headers_to_extract: set[str] = set()
        indexed_headers: dict[str, set[int]] = {}

        for header_ref in self._link_fields.headers:
            headers_to_extract.add(header_ref.name)
            if header_ref.index is not None:
                indexed_headers.setdefault(header_ref.name, set()).add(header_ref.index)

        for header_name in headers_to_extract:
            # ResponseCase headers are already lowercase keys with list values
            header_values = response.headers.get(header_name, [])
            if header_values:
                # Store all values as list at header/{name}
                extracted[f"header/{header_name}"] = header_values

                # Store specific indexed values at header/{name}/{index}
                if header_name in indexed_headers:
                    for index in indexed_headers[header_name]:
                        if index < len(header_values):
                            extracted[f"header/{header_name}/{index}"] = header_values[index]

        return extracted

    def _get_timeout(self, operation_id: str) -> float:
        """Get timeout for an operation."""
        return self._operation_timeouts.get(operation_id, self._default_timeout)

    def _wait_for_rate_limit(self) -> None:
        """Wait if necessary to respect rate limit."""
        if self._min_interval <= 0:
            return

        with self._rate_limit_lock:
            now = time.monotonic()
            elapsed = now - self._last_request_time
            if elapsed < self._min_interval:
                sleep_time = self._min_interval - elapsed
                time.sleep(sleep_time)
            self._last_request_time = time.monotonic()

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

        # Build headers (flatten lists, sanitize non-ASCII per RFC 7230)
        headers: dict[str, str] = {}
        for key, values in request.headers.items():
            if values:
                headers[key] = _sanitize_header_value(values[0])

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

        # Enforce rate limit before making request
        self._wait_for_rate_limit()

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
        except UnicodeEncodeError as e:
            # Fallback for non-ASCII in places we don't sanitize: header keys, query
            # params, paths. These are protocol violations that should fail loudly
            # rather than silently corrupt data.
            raise RequestError(
                f"{target_name} encoding error: non-ASCII characters in request "
                f"(header key, query param, or path). Character: {e.object[e.start:e.end]!r} "
                f"at position {e.start}. HTTP requires ASCII for these fields."
            ) from e

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

        # Parse body based on content-type: JSON -> parsed (dict/list/etc), text -> str, binary -> base64
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
