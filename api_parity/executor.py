"""Executor - Sends requests to targets and captures responses."""

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
    """Network-level failure (connection error, timeout, encoding error)."""


def _sanitize_header_value(value: str) -> str:
    """Replace non-ASCII chars with '?' - HTTP headers must be ASCII per RFC 7230."""
    return value.encode('ascii', errors='replace').decode('ascii')


class Executor:
    """Executes requests against two targets and captures responses.

    Supports context manager protocol for automatic cleanup.
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
        self._target_a = target_a
        self._target_b = target_b
        self._default_timeout = default_timeout
        self._operation_timeouts = operation_timeouts or {}
        self._link_fields = link_fields or LinkFields()

        self._requests_per_second = requests_per_second
        self._min_interval = 1.0 / requests_per_second if requests_per_second else 0.0
        self._last_request_time: float = 0.0
        self._rate_limit_lock = Lock()

        self._client_a = httpx.Client(**self._build_client_kwargs(target_a, default_timeout))
        self._client_b = httpx.Client(**self._build_client_kwargs(target_b, default_timeout))

    def __enter__(self) -> "Executor":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def close(self) -> None:
        self._client_a.close()
        self._client_b.close()

    def _build_client_kwargs(self, target: TargetConfig, timeout: float) -> dict:
        kwargs: dict = {
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
        """Execute request serially: Target A first, then Target B. Raises RequestError on failure."""
        timeout = self._get_timeout(request.operation_id)

        response_a = self._execute_single(self._client_a, request, timeout, "Target A")
        response_b = self._execute_single(self._client_b, request, timeout, "Target B")

        return response_a, response_b

    def execute_chain(
        self,
        chain: ChainCase,
        on_step: Callable[[ResponseCase, ResponseCase], bool] | None = None,
    ) -> tuple[ChainExecution, ChainExecution]:
        """Execute chain against both targets. Each target uses its own extracted variables.

        on_step callback: return False to stop (e.g., on mismatch), True to continue.
        Raises RequestError on network failure.
        """
        steps_a: list[ChainStepExecution] = []
        steps_b: list[ChainStepExecution] = []
        extracted_vars_a: dict[str, Any] = {}
        extracted_vars_b: dict[str, Any] = {}

        for step in chain.steps:
            request_a = self._apply_variables(step.request_template, extracted_vars_a)
            request_b = self._apply_variables(step.request_template, extracted_vars_b)
            timeout = self._get_timeout(request_a.operation_id)

            response_a = self._execute_single(self._client_a, request_a, timeout, "Target A")
            response_b = self._execute_single(self._client_b, request_b, timeout, "Target B")

            extracted_a = self._extract_variables(response_a)
            extracted_b = self._extract_variables(response_b)
            extracted_vars_a.update(extracted_a)
            extracted_vars_b.update(extracted_b)

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

            if on_step is not None and not on_step(response_a, response_b):
                break

        return (
            ChainExecution(steps=steps_a),
            ChainExecution(steps=steps_b),
        )

    def _variable_to_string(self, var_value: Any) -> str:
        """Convert to string. Lists use first element (str(list) produces "['value']")."""
        if isinstance(var_value, list):
            return str(var_value[0]) if var_value else ""
        return str(var_value)

    def _apply_variables(
        self,
        template: RequestCase,
        variables: dict[str, Any],
    ) -> RequestCase:
        """Substitute {variable_name} placeholders in path params, query params, and body."""
        request_dict = template.model_dump()

        for key, value in list(request_dict.get("path_parameters", {}).items()):
            if isinstance(value, str):
                for var_name, var_value in variables.items():
                    if f"{{{var_name}}}" in value:
                        value = value.replace(f"{{{var_name}}}", self._variable_to_string(var_value))
                    # Schemathesis may have already resolved placeholder to just the var name
                    if value == var_name:
                        value = self._variable_to_string(var_value)
                request_dict["path_parameters"][key] = value

        rendered_path = request_dict["path_template"]
        for key, value in request_dict.get("path_parameters", {}).items():
            rendered_path = rendered_path.replace(f"{{{key}}}", str(value))
        request_dict["rendered_path"] = rendered_path

        for key, values in list(request_dict.get("query", {}).items()):
            new_values = []
            for value in values:
                for var_name, var_value in variables.items():
                    if f"{{{var_name}}}" in value:
                        value = value.replace(f"{{{var_name}}}", self._variable_to_string(var_value))
                new_values.append(value)
            request_dict["query"][key] = new_values

        if isinstance(request_dict.get("body"), dict):
            request_dict["body"] = self._substitute_in_dict(request_dict["body"], variables)

        return RequestCase.model_validate(request_dict)

    def _substitute_in_dict(
        self,
        data: dict[str, Any],
        variables: dict[str, Any],
    ) -> dict[str, Any]:
        """Recursively substitute {var} placeholders in a dictionary."""
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
        """Recursively substitute {var} placeholders in a list."""
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
        """Extract link fields from response for chain variable substitution.

        Key format: body fields use JSONPointer path ("data/id"), headers use
        "header/{name}" (list) or "header/{name}/{index}" (single value).
        """
        extracted: dict[str, Any] = {}

        if isinstance(response.body, dict):
            for field_pointer in self._link_fields.body_pointers:
                value = extract_by_jsonpointer(response.body, field_pointer)
                if value is not None:
                    extracted[field_pointer] = value

            # Add last-segment shortcuts (e.g., "data/item/id" -> also "id") only if unambiguous
            last_segments: dict[str, list[str]] = {}
            for field_pointer in self._link_fields.body_pointers:
                last_segment = field_pointer.split("/")[-1]
                if last_segment != field_pointer:
                    last_segments.setdefault(last_segment, []).append(field_pointer)

            for last_segment, pointers in last_segments.items():
                if len(pointers) == 1 and pointers[0] in extracted:
                    extracted[last_segment] = extracted[pointers[0]]
                # Multiple paths end in same segment - skip shortcut to avoid silent collision

        headers_to_extract: set[str] = set()
        indexed_headers: dict[str, set[int]] = {}

        for header_ref in self._link_fields.headers:
            headers_to_extract.add(header_ref.name)
            if header_ref.index is not None:
                indexed_headers.setdefault(header_ref.name, set()).add(header_ref.index)

        for header_name in headers_to_extract:
            header_values = response.headers.get(header_name, [])
            if header_values:
                extracted[f"header/{header_name}"] = header_values
                if header_name in indexed_headers:
                    for index in indexed_headers[header_name]:
                        if index < len(header_values):
                            extracted[f"header/{header_name}/{index}"] = header_values[index]

        return extracted

    def _get_timeout(self, operation_id: str) -> float:
        return self._operation_timeouts.get(operation_id, self._default_timeout)

    def _wait_for_rate_limit(self) -> None:
        """Thread-safe sleep to enforce minimum interval between requests."""
        if self._min_interval <= 0:
            return

        with self._rate_limit_lock:
            now = time.monotonic()
            elapsed = now - self._last_request_time
            if elapsed < self._min_interval:
                time.sleep(self._min_interval - elapsed)
            self._last_request_time = time.monotonic()

    def _execute_single(
        self,
        client: httpx.Client,
        request: RequestCase,
        timeout: float,
        target_name: str,
    ) -> ResponseCase:
        """Execute request, raise RequestError on network failure."""
        url = request.rendered_path

        # Flatten multi-value query params for httpx
        params: list[tuple[str, str]] = []
        for key, values in request.query.items():
            for value in values:
                params.append((key, value))

        # Flatten headers, sanitize to ASCII (Hypothesis may generate non-ASCII)
        headers: dict[str, str] = {}
        for key, values in request.headers.items():
            if values:
                headers[key] = _sanitize_header_value(values[0])

        content: bytes | None = None
        json_body: Any = None

        if request.body is not None:
            if request.media_type and "json" in request.media_type.lower():
                json_body = request.body
            else:
                if isinstance(request.body, str):
                    content = request.body.encode("utf-8")
                elif isinstance(request.body, bytes):
                    content = request.body
                else:
                    content = str(request.body).encode("utf-8")
        elif request.body_base64:
            content = base64.b64decode(request.body_base64)

        if request.media_type and "content-type" not in {k.lower() for k in headers}:
            headers["Content-Type"] = request.media_type

        cookies: dict[str, str] = request.cookies
        self._wait_for_rate_limit()

        # Error context for actionable messages
        req_context = f"{request.method} {url} (operation: {request.operation_id})"

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
            raise RequestError(
                f"{target_name} timeout after {timeout}s: {req_context}"
            ) from e
        except httpx.ConnectError as e:
            raise RequestError(
                f"{target_name} connection failed: {req_context} - {e}"
            ) from e
        except httpx.RequestError as e:
            raise RequestError(
                f"{target_name} request failed: {req_context} - {e}"
            ) from e
        except UnicodeEncodeError as e:
            raise RequestError(
                f"{target_name} encoding error (non-ASCII in request): {req_context} - {e}"
            ) from e

        return self._convert_response(http_response, elapsed_ms)

    def _convert_response(
        self,
        response: httpx.Response,
        elapsed_ms: float,
    ) -> ResponseCase:
        """Convert httpx.Response to ResponseCase (lowercase headers, parsed body)."""
        headers: dict[str, list[str]] = {}
        for key, value in response.headers.multi_items():
            key_lower = key.lower()
            if key_lower not in headers:
                headers[key_lower] = []
            headers[key_lower].append(value)

        body: Any = None
        body_base64: str | None = None
        content_type = response.headers.get("content-type", "")

        if response.content:
            if "json" in content_type.lower():
                try:
                    body = response.json()
                except Exception:
                    body_base64 = base64.b64encode(response.content).decode("ascii")
            elif content_type.startswith("text/"):
                try:
                    body = response.text
                except Exception:
                    body_base64 = base64.b64encode(response.content).decode("ascii")
            else:
                body_base64 = base64.b64encode(response.content).decode("ascii")

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
