"""Executor - Sends requests to targets and captures responses.

The Executor sends HTTP requests to both targets (serially) and captures
the responses as ResponseCase objects for comparison.

See ARCHITECTURE.md "Executor" for specifications.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import ssl
import time
from collections.abc import Collection
from email.message import Message
from threading import Lock
from typing import Any, Callable
from urllib.parse import unquote, urlencode

import httpx
from urllib3.filepost import encode_multipart_formdata

from api_parity.case_generator import LINK_BODY_PATTERN, LinkFields, _MISSING, extract_by_jsonpointer
from api_parity.models import (
    ChainCase,
    ChainExecution,
    ChainStepExecution,
    RequestCase,
    ResponseCase,
    TargetConfig,
    response_body_format,
)
from api_parity.session_cookies import BundleCookies
from api_parity.path_rendering import collection_path, decode_path_value, path_parameter_names, render_path
from api_parity.xml_body import dict_to_xml, xml_to_dict


class ExecutorError(Exception):
    """Base class for executor errors."""


class RequestError(ExecutorError):
    """Raised when a request fails (connection error, timeout, etc.)."""


# Patterns for resolving OpenAPI runtime expressions in link parameters.
# Used during chain execution to map link_source.parameters expressions
# (e.g., "$response.body#/id") to extracted variable keys or request data.
# See OpenAPI 3.0 spec section "Runtime Expressions".
_LINK_EXPR_RESPONSE_HEADER = re.compile(
    r'\$response\.header\.([A-Za-z0-9\-_]+)(?:\[(\d+)\])?$', re.IGNORECASE
)
_LINK_EXPR_REQUEST_PATH = re.compile(r'\$request\.path\.([A-Za-z0-9\-_]+)$')
_LINK_EXPR_REQUEST_HEADER = re.compile(
    r'\$request\.header\.([A-Za-z0-9\-_]+)$', re.IGNORECASE
)


# Pre-compiled pattern for ASCII control characters that httpx rejects in URLs.
# Covers 0x00-0x1F and 0x7F (DEL).  httpx handles spaces and non-ASCII (unicode)
# via its own percent-encoding, but control characters raise InvalidURL.
_URL_CONTROL_CHAR_RE = re.compile(r'[\x00-\x1f\x7f]')


def _reject_non_json_constant(token: str):
    # Python's JSON decoder accepts NaN and Infinity unless told otherwise.
    raise ValueError(f"Invalid JSON constant: {token}")


def _response_pointer(body: Any, pointer: str) -> Any:
    """Preserve literal member names accepted by older versions, then try the URI fragment."""
    value = extract_by_jsonpointer(body, pointer)
    if value is _MISSING and "%" in pointer:
        try:
            decoded = unquote(pointer, errors="strict")
        except UnicodeDecodeError:
            return _MISSING
        if decoded != pointer:
            return extract_by_jsonpointer(body, decoded)
    return value


class _SerializedPath(str):
    """A collection path whose components and style delimiters are already encoded."""


def _percent_encode_control_chars(path: str) -> str:
    """Percent-encode ASCII control characters in a URL path.

    Schemathesis may generate path parameter values containing arbitrary bytes
    (e.g., \\x1a, \\x00).  httpx rejects these with InvalidURL rather than
    percent-encoding them.  This function encodes only the control characters
    that httpx refuses, leaving everything else (slashes, unicode, spaces) for
    httpx to handle normally.

    Args:
        path: The rendered URL path (e.g., "/widgets/hello\\x1aworld").

    Returns:
        Path with control characters percent-encoded (e.g., "/widgets/hello%1Aworld").
    """
    return _URL_CONTROL_CHAR_RE.sub(
        lambda m: '%{:02X}'.format(ord(m.group())), path
    )


def _form_fields(body: dict) -> list[tuple[str, str | bytes]]:
    fields = []
    for name, value in body.items():
        for item in value if isinstance(value, list) else [value]:
            if isinstance(item, (dict, list)):
                # A structured form field is a single JSON part, not a Python repr.
                item = json.dumps(item, default=lambda raw: base64.b64encode(raw).decode("ascii"))
            fields.append((name, item if isinstance(item, bytes) else str(item)))
    return fields


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
        *, ignore_session_cookies: bool = False,
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
            ignore_session_cookies: Treat a saved bundle's inherited cookies as irrelevant on replay.
        """
        self._ignore_session_cookies = ignore_session_cookies
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

        session_a = BundleCookies.for_cases(self._client_a, [request], "target_a", self._ignore_session_cookies)
        session_b = BundleCookies.for_cases(self._client_b, [request], "target_b", self._ignore_session_cookies)
        response_a = self._execute_single(self._client_a, request, timeout, "Target A", session=session_a)
        response_b = self._execute_single(self._client_b, request, timeout, "Target B", session=session_b)
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

        templates = [step.request_template for step in chain.steps]
        session_a = BundleCookies.for_cases(self._client_a, templates, "target_a", self._ignore_session_cookies)
        session_b = BundleCookies.for_cases(self._client_b, templates, "target_b", self._ignore_session_cookies)

        for step in chain.steps:
            link = step.link_source or {}
            linked_parameters = link.get("parameters") or {}
            overrides_a: dict[str, Any] = {}
            overrides_b: dict[str, Any] = {}
            if linked_parameters:
                source = self._link_source_position(link, steps_a)
                if source is None:
                    raise RequestError(
                        f"Step {step.step_index} ({step.request_template.operation_id}) has no "
                        f"matching prior link source: source_step_index={link.get('source_step_index')!r}, "
                        f"source_operation={link.get('source_operation')!r}"
                    )
                source_a, source_b = steps_a[source], steps_b[source]
                # Use the same destination for deciding whether a step can run
                # and for applying the value. Qualified keys may share a name.
                required = set(link.get("required_parameters") or ())
                linked_parameters = {
                    ".".join(self._link_destination(key, step.request_template, required)): expr
                    for key, expr in linked_parameters.items()
                }
                link = {**link, "parameters": linked_parameters}
                path_keys = {key for key in linked_parameters if key.startswith("path.")}
                overrides_a = self._resolve_link_overrides(link, source_a.response, source_a.request, path_keys)
                overrides_b = self._resolve_link_overrides(link, source_b.response, source_b.request, path_keys)
                if not overrides_a and not overrides_b:
                    break
                unresolved = linked_parameters.keys() - overrides_a.keys() - overrides_b.keys()
                if any(
                    isinstance(linked_parameters[key], str)
                    and (linked_parameters[key].startswith("$") or "{$" in linked_parameters[key])
                    for key in unresolved & (required | path_keys)
                ):
                    break

            # Ordinary placeholders may use accumulated variables. Explicit link
            # parameters belong to their source, and resolved values are literal.
            request_a = self._apply_variables(step.request_template, extracted_vars_a, linked_parameters)
            request_b = self._apply_variables(step.request_template, extracted_vars_b, linked_parameters)
            if linked_parameters:
                request_a = self._apply_link_overrides(request_a, overrides_a, linked_parameters, source_a.request)
                request_b = self._apply_link_overrides(request_b, overrides_b, linked_parameters, source_b.request)

            timeout = self._get_timeout(request_a.operation_id)

            # Execute against both targets
            response_a = self._execute_single(
                self._client_a, request_a, timeout, "Target A", session=session_a
            )
            response_b = self._execute_single(
                self._client_b, request_b, timeout, "Target B", session=session_b
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

    @staticmethod
    def _link_source_position(link: dict, history: list[ChainStepExecution]) -> int | None:
        """Locate the recorded source, or the most recent matching operation."""
        operation = link.get("source_operation")
        if "source_step_index" in link:
            index = link["source_step_index"]
            if type(index) is int and 0 <= index < len(history):
                if operation is None or history[index].request.operation_id == operation:
                    return index
            return None
        for index in reversed(range(len(history))):
            if operation is None or history[index].request.operation_id == operation:
                return index
        return None

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
        linked_parameters: Collection[str] = (),
    ) -> RequestCase:
        """Apply extracted variables to a request template.

        Substitutes {variable_name} placeholders in path parameters, query
        parameters, and body.

        Args:
            template: Request template with potential placeholders.
            variables: Extracted variables from previous steps.
            linked_parameters: Parameters reserved for explicit link expressions.

        Returns:
            New RequestCase with variables substituted.
        """
        # Deep copy the template to avoid mutation
        request_dict = template.model_dump()

        # Template values may already be percent-escaped. Decode the template
        # text before rendering, but keep response values literal (including %).
        for key, value in template.path_parameters.items():
            if isinstance(value, str) and f"path.{key}" not in linked_parameters and value in variables:
                value = self._variable_to_string(variables[value])
            elif isinstance(value, str) and f"path.{key}" not in linked_parameters and "{" in value:
                # Decode only literal spans. Escaped braces in generated IDs are data.
                parts = re.split(r"(\{[^{}]+\})", value)
                value = "".join(
                    self._variable_to_string(variables[part[1:-1]])
                    if index % 2 and part[1:-1] in variables else decode_path_value(part)
                    for index, part in enumerate(parts)
                )
            else:
                value = decode_path_value(value)
            request_dict["path_parameters"][key] = value
        request_dict["rendered_path"] = render_path(template.path_template, request_dict["path_parameters"])

        # Apply variables to query parameters (dict[str, list[str]])
        for key, values in list(request_dict.get("query", {}).items()):
            if f"query.{key}" in linked_parameters:
                continue
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

        if isinstance(response.body, (dict, list)):
            # First pass: extract all values under full pointer paths
            for field_pointer in self._link_fields.body_pointers:
                value = _response_pointer(response.body, field_pointer)
                if value is not _MISSING:
                    extracted[field_pointer] = value

            # Add shortcut aliases: "userId" -> value (from "data/user/userId")
            # Only when unambiguous among all requested pointers, including root fields.
            last_segments: dict[str, list[str]] = {}
            for field_pointer in self._link_fields.body_pointers:
                last_segment = field_pointer.split("/")[-1]
                if last_segment != field_pointer:  # Only for nested paths
                    last_segments.setdefault(last_segment, []).append(field_pointer)

            for last_segment, pointers in last_segments.items():
                if len(pointers) == 1 and last_segment not in self._link_fields.body_pointers:
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

    @staticmethod
    def _resolve_link_expression(
        expression: str,
        source_response: ResponseCase,
        prev_request: RequestCase | None,
    ) -> Any:
        """Resolve an explicit link against its source exchange, or return _MISSING."""
        match = LINK_BODY_PATTERN.fullmatch(expression)
        if match:
            return _response_pointer(source_response.body, match.group(1))

        match = _LINK_EXPR_RESPONSE_HEADER.fullmatch(expression)
        if match:
            values = source_response.headers.get(match.group(1).lower(), [])
            index = int(match.group(2) or 0)
            return values[index] if index < len(values) else _MISSING

        if prev_request is not None and expression.startswith("$request.query."):
            values = prev_request.query.get(expression.removeprefix("$request.query."))
            if values:
                return values[0] if len(values) == 1 else values
            return _MISSING

        if prev_request is not None and expression.startswith("$request.body#/"):
            # A JSON Pointer selects from the body of this target's source
            # request, after any earlier substitutions in the chain.
            body = prev_request.body
            content_type = next((v[0] for h, v in prev_request.headers.items()
                                 if h.lower() == "content-type" and v), "")
            media = (prev_request.media_type or content_type).partition(";")[0].lower()
            if isinstance(body, (dict, list)) and "json" not in media:
                return _MISSING
            if prev_request.body_base64 is not None:
                body = base64.b64decode(prev_request.body_base64)
            if isinstance(body, bytes) or (isinstance(body, str) and not prev_request.media_type):
                try:
                    body = json.loads(body)
                except ValueError:
                    return _MISSING
            pointer = unquote(expression.removeprefix("$request.body#/"))
            if not pointer:  # "/" addresses an empty member name.
                return body.get("", _MISSING) if isinstance(body, dict) else _MISSING
            return extract_by_jsonpointer(body, pointer)

        # $request.path.paramName
        match = _LINK_EXPR_REQUEST_PATH.match(expression)
        if match and prev_request is not None:
            param_name = match.group(1)
            return prev_request.path_parameters.get(param_name, _MISSING)

        # $request.header.Name
        match = _LINK_EXPR_REQUEST_HEADER.match(expression)
        if match and prev_request is not None:
            header_name = match.group(1).lower()
            header_values = prev_request.headers.get(header_name, [])
            if header_values:
                return header_values[0]
            return _MISSING

        return _MISSING

    def _resolve_link_overrides(
        self,
        link_source: dict[str, Any],
        source_response: ResponseCase,
        prev_request: RequestCase | None,
        path_keys: Collection[str] = (),
    ) -> dict[str, Any]:
        """Resolve all link parameter expressions to concrete values.

        Reads link_source["parameters"] (a dict mapping target operation
        parameter names to OpenAPI runtime expressions) and resolves each
        expression to its value in the selected source response or request.

        Args:
            link_source: The step's link_source dict containing "parameters".
            source_response: The selected source response from this target.
            prev_request: The request from the prior step (for $request expressions).

        Returns:
            Dict mapping parameter names to resolved values. Only includes
            parameters that could be successfully resolved (expression matched
            and the referenced data was present).
        """
        overrides: dict[str, Any] = {}

        parameters = link_source.get("parameters")
        if not isinstance(parameters, dict):
            return overrides

        for param_name, expression in parameters.items():
            if not isinstance(expression, str):
                continue
            value = self._resolve_link_expression(
                expression, source_response, prev_request
            )
            if value is _MISSING:
                continue
            if (param_name in path_keys and isinstance(value, (list, dict))
                    and expression.startswith(("$response.body#/", "$request.body#/"))):
                name = param_name.removeprefix("path.")
                options = link_source.get("collection_paths", {}).get(name, {})
                wire = collection_path(name, value, options)
                if not wire:
                    continue
                value = _SerializedPath(wire)
            if param_name in path_keys and (value is None or self._variable_to_string(value) == ""):
                continue
            overrides[param_name] = value

        return overrides

    @staticmethod
    def _link_destination(key: str, template: RequestCase, required: Collection[str] = ()) -> tuple[str, str]:
        location, dot, name = key.partition(".")
        if dot and location in {"path", "query", "header", "cookie"}:
            return location, name.lower() if location == "header" else name
        if key in template.path_parameters or key in path_parameter_names(template.path_template):
            return "path", key
        if key in template.query or f"query.{key}" in required:
            return "query", key
        if any(h.lower() == key.lower() for h in template.headers) or f"header.{key.lower()}" in required:
            return "header", key.lower()
        if key in template.cookies or f"cookie.{key}" in required:
            return "cookie", key
        return "", key

    def _apply_link_overrides(
        self,
        template: RequestCase,
        overrides: dict[str, Any],
        expressions: dict[str, str] | None = None,
        source_request: RequestCase | None = None,
    ) -> RequestCase:
        """Override request template parameters with link-resolved values.

        Replaces fuzz-generated parameter values with real data extracted from
        prior step responses. This is the key step that makes stateful chains
        work: instead of sending random UUIDs as path parameters, we send the
        actual resource IDs from prior create/update responses.

        Args:
            template: Request template with fuzz-generated values.
            overrides: Dict mapping parameter names to resolved values.

        Returns:
            New RequestCase with parameters overridden and path re-rendered.
        """
        if not overrides:
            return template

        request_dict = template.model_dump()

        uri_paths = {}
        for key, value in overrides.items():
            location, param_name = self._link_destination(key, template)
            str_value = self._variable_to_string(value)

            if location == "path":
                expression = (expressions or {}).get(key, "")
                path_source = _LINK_EXPR_REQUEST_PATH.fullmatch(expression)
                if isinstance(value, _SerializedPath):
                    reference = str_value
                elif path_source and source_request is not None:
                    # An ordinary source segment may decode to a leading slash.
                    # Forward its original interpretation along with the value.
                    reference = source_request._uri_path_references.get(path_source.group(1))
                elif str_value.startswith("/") or re.fullmatch(
                    r"\$(?:request|response)\.header\.(?:content-)?location(?:\[\d+\])?", expression, re.I
                ):
                    reference = str_value.lstrip("/")
                else:
                    reference = None
                if reference is not None:
                    uri_paths[param_name] = reference
                    str_value = decode_path_value(reference)
                request_dict["path_parameters"][param_name] = str_value
            elif location == "query":
                if isinstance(value, list) and (expressions or {}).get(key, "").startswith("$request.query."):
                    request_dict["query"][param_name] = list(value)
                else:
                    request_dict["query"][param_name] = [str_value]
            elif location == "header":
                headers = request_dict["headers"]
                name = next((h for h in headers if h.lower() == param_name.lower()), param_name.lower())
                headers[name] = [str_value]
            elif location == "cookie":
                request_dict["cookies"][param_name] = str_value

        request_dict["rendered_path"] = render_path(template.path_template, request_dict["path_parameters"], uri_paths)
        request = RequestCase.model_validate(request_dict)
        request._uri_path_references = uri_paths
        return request

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
        *, session: BundleCookies | None = None,
    ) -> ResponseCase:
        """Execute a request against a single target.

        Args:
            client: HTTP client for the target.
            request: The request to execute.
            timeout: Request timeout in seconds.
            target_name: Name for error messages.
            session: Cookie state for the enclosing standalone case or chain.

        Returns:
            ResponseCase with the response.

        Raises:
            RequestError: If request fails.
        """
        # Build the URL — percent-encode control characters that httpx rejects.
        # Fuzz-generated path parameter values may contain arbitrary bytes.
        url = _percent_encode_control_chars(request.rendered_path)

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

        content_type_key = next((k for k in headers if k.lower() == "content-type"), "Content-Type")
        effective_content_type = request.media_type or headers.get(content_type_key, "")
        media = effective_content_type.partition(";")[0].lower().strip()

        # Header-only callers can supply pre-serialized JSON text or bytes.
        # Explicit media_type retains its existing JSON string/byte semantics.
        if not request.media_type and isinstance(request.body, (str, bytes)):
            content = request.body.encode("utf-8") if isinstance(request.body, str) else request.body
        elif isinstance(request.body, dict) and media == "application/x-www-form-urlencoded":
            content = urlencode(_form_fields(request.body)).encode("ascii")
        elif isinstance(request.body, dict) and media in {"multipart/form-data", "multipart/mixed"}:
            header = Message()
            header["Content-Type"] = headers.get(content_type_key, request.media_type)
            if header.get_boundary() is None:
                # Reuse the delimiter for both targets and when the saved case is replayed.
                boundary = hashlib.sha256(request.case_id.encode()).hexdigest()[:32]
                header.set_boundary(boundary)
            headers[content_type_key] = header["Content-Type"]
            parts = [(name, (name, value, "application/octet-stream") if isinstance(value, bytes) else value)
                     for name, value in _form_fields(request.body)]
            content, _ = encode_multipart_formdata(parts, boundary=header.get_boundary())
        elif request.body is not None:
            if "json" in media:
                if isinstance(request.body, bytes):
                    # Body is raw bytes in a JSON-typed request slot (e.g.,
                    # schema with format: binary under application/json).
                    # Try to parse as JSON; if that fails, send as raw content.
                    try:
                        json_body = json.loads(request.body)
                        if json_body is None:
                            content = b"null"
                    except (ValueError, UnicodeDecodeError):
                        content = request.body
                else:
                    json_body = request.body
            elif "xml" in media:
                # Convert dict body to XML bytes. The body stays as a dict in
                # RequestCase (inspectable in artifacts); XML serialization
                # happens only at send time.  See DESIGN.md "XML Body Conversion".
                # NOTE: Substring match "xml" can false-positive on media types
                # like application/vnd.openxmlformats-officedocument.spreadsheetml.sheet
                # (ZIP, not XML). Same limitation as the existing "json" in check.
                # Acceptable because api-parity targets REST APIs, not file upload services.
                if isinstance(request.body, dict):
                    try:
                        content = dict_to_xml(request.body)
                    except ValueError:
                        # Body dict doesn't have a single root element — fall
                        # back to string representation.  This happens when the
                        # OpenAPI schema defines multiple top-level properties
                        # without a wrapper element.
                        content = str(request.body).encode("utf-8")
                elif isinstance(request.body, str):
                    content = request.body.encode("utf-8")
                elif isinstance(request.body, bytes):
                    content = request.body
                else:
                    content = str(request.body).encode("utf-8")
            else:
                # Encode as string for non-JSON, non-XML
                if isinstance(request.body, str):
                    content = request.body.encode("utf-8")
                elif isinstance(request.body, bytes):
                    content = request.body
                else:
                    content = str(request.body).encode("utf-8")
        elif request.body_base64 is not None:
            content = base64.b64decode(request.body_base64)

        # Add content-type if specified
        if request.media_type:
            headers.setdefault(content_type_key, request.media_type)

        # Enforce rate limit before making request
        self._wait_for_rate_limit()

        try:
            start_time = time.perf_counter()

            request_args = dict(
                method=request.method,
                url=url,
                params=params if params else None,
                headers=headers if headers else None,
                content=content,
                json=json_body,
                cookies=request.cookies or None,
                timeout=timeout,
            )
            if session is None:
                http_response = client.request(**request_args)
            else:
                prepared = client.build_request(**request_args)
                origin, used, explicit = session.prepare(client, request, prepared)
                http_response = client.send(prepared)
                if session.replay:
                    session.jar.extract_cookies(http_response)

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

        response = self._convert_response(http_response, elapsed_ms)
        if session is not None:
            session.finish(request, response, origin, used, explicit)
        return response

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

        # Parse body based on content-type:
        #   JSON         -> parsed dict/list/scalar
        #   XML          -> parsed dict via xml_to_dict (see DESIGN.md "XML Body Conversion")
        #   text/*       -> str
        #   everything else -> base64
        # XML branch MUST come before text/* because text/xml is a valid content-type.
        body: Any = None
        body_base64: str | None = None

        json_body_state = None
        body_format = response_body_format(response.headers.get("content-type", ""))

        if response.content:
            if body_format == "json":
                try:
                    body = response.json(parse_constant=_reject_non_json_constant)
                    if body is None:
                        json_body_state = "null"
                except Exception:
                    json_body_state = "invalid"
                    body_base64 = base64.b64encode(response.content).decode("ascii")
            elif body_format == "xml":
                try:
                    body = xml_to_dict(response.content)
                except Exception:
                    # Not valid XML despite content-type — fall back to base64
                    body_base64 = base64.b64encode(response.content).decode("ascii")
            elif body_format == "text":
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
            json_body_state=json_body_state,
            elapsed_ms=elapsed_ms,
            http_version=http_version,
        )
