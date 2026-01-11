"""Executor - Sends requests to targets and captures responses.

The Executor sends HTTP requests to both targets (serially) and captures
the responses as ResponseCase objects for comparison.

See ARCHITECTURE.md "Executor" for specifications.
"""

from __future__ import annotations

import base64
import time
from typing import Any

import httpx

from api_parity.models import (
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
    ) -> None:
        """Initialize the executor.

        Args:
            target_a: Configuration for target A.
            target_b: Configuration for target B.
            default_timeout: Default timeout in seconds for requests.
            operation_timeouts: Per-operation timeout overrides.
        """
        self._target_a = target_a
        self._target_b = target_b
        self._default_timeout = default_timeout
        self._operation_timeouts = operation_timeouts or {}

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
