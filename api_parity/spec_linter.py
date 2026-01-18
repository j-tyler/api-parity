"""OpenAPI spec linting for api-parity-specific issues.

Analyzes OpenAPI specifications for issues that affect api-parity's behavior,
including link connectivity, expression coverage, and schema completeness.

See ARCHITECTURE.md for api-parity's use of OpenAPI links and schemas.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from api_parity.case_generator import LINK_BODY_PATTERN, LINK_HEADER_PATTERN


@dataclass
class LintMessage:
    """A single lint message (error, warning, or info)."""

    level: str  # "error", "warning", "info"
    code: str  # Short code for the issue type
    message: str  # Human-readable message
    operation_id: str | None = None  # Operation this applies to, if any
    details: dict[str, Any] | None = None  # Additional structured data


@dataclass
class LintResult:
    """Result of linting an OpenAPI spec."""

    errors: list[LintMessage] = field(default_factory=list)
    warnings: list[LintMessage] = field(default_factory=list)
    info: list[LintMessage] = field(default_factory=list)

    # Summary statistics
    total_operations: int = 0
    operations_with_links: int = 0
    operations_with_response_schemas: int = 0

    def add(self, msg: LintMessage) -> None:
        """Add a message to the appropriate list based on level."""
        if msg.level == "error":
            self.errors.append(msg)
        elif msg.level == "warning":
            self.warnings.append(msg)
        else:
            self.info.append(msg)

    def has_errors(self) -> bool:
        """Return True if there are any errors."""
        return len(self.errors) > 0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON output."""
        return {
            "errors": [self._msg_to_dict(m) for m in self.errors],
            "warnings": [self._msg_to_dict(m) for m in self.warnings],
            "info": [self._msg_to_dict(m) for m in self.info],
            "summary": {
                "total_operations": self.total_operations,
                "operations_with_links": self.operations_with_links,
                "operations_with_response_schemas": self.operations_with_response_schemas,
                "error_count": len(self.errors),
                "warning_count": len(self.warnings),
                "info_count": len(self.info),
            },
        }

    def _msg_to_dict(self, msg: LintMessage) -> dict[str, Any]:
        """Convert a LintMessage to dictionary."""
        result = {
            "level": msg.level,
            "code": msg.code,
            "message": msg.message,
        }
        if msg.operation_id:
            result["operation_id"] = msg.operation_id
        if msg.details:
            result["details"] = msg.details
        return result


class SpecLinter:
    """Analyzes OpenAPI specs for api-parity-specific issues.

    Usage:
        linter = SpecLinter(Path("openapi.yaml"))
        result = linter.lint()
        if result.has_errors():
            print("Spec has issues")
    """

    def __init__(self, spec_path: Path) -> None:
        """Initialize the linter.

        Args:
            spec_path: Path to OpenAPI specification file (YAML or JSON).

        Raises:
            SpecLinterError: If spec cannot be loaded or parsed.
        """
        self._spec_path = spec_path
        self._raw_content: str | None = None

        try:
            with open(spec_path) as f:
                self._raw_content = f.read()
                f.seek(0)
                if spec_path.suffix.lower() in (".yaml", ".yml"):
                    self._spec = yaml.safe_load(f)
                else:
                    self._spec = json.load(f)
        except FileNotFoundError:
            raise SpecLinterError(f"Spec file not found: {spec_path}")
        except (yaml.YAMLError, json.JSONDecodeError) as e:
            raise SpecLinterError(f"Failed to parse spec: {e}")

        # Handle empty files (yaml.safe_load returns None for empty content)
        if self._spec is None:
            self._spec = {}

        # Build operation index for validation
        self._operation_ids: set[str] = set()
        self._operations: dict[str, dict[str, Any]] = {}  # op_id -> {method, path, raw}
        self._build_operation_index()

    def _build_operation_index(self) -> None:
        """Build index of all operations in the spec."""
        paths = self._spec.get("paths", {})
        for path, path_item in paths.items():
            if not isinstance(path_item, dict):
                continue
            for method, operation in path_item.items():
                if not isinstance(operation, dict) or method.startswith("$"):
                    continue
                op_id = operation.get("operationId")
                if op_id:
                    self._operation_ids.add(op_id)
                    self._operations[op_id] = {
                        "method": method.upper(),
                        "path": path,
                        "raw": operation,
                    }

    def lint(self) -> LintResult:
        """Run all lint checks on the spec.

        Returns:
            LintResult with errors, warnings, and info messages.
        """
        result = LintResult()
        result.total_operations = len(self._operation_ids)

        # Run all checks
        self._check_link_connectivity(result)
        self._check_explicit_links_warning(result)
        self._check_link_expression_coverage(result)
        self._check_non_200_status_code_links(result)
        self._check_response_schema_coverage(result)
        self._check_duplicate_link_names(result)

        return result

    def _check_link_connectivity(self, result: LintResult) -> None:
        """Check link connectivity and identify isolated operations.

        Identifies:
        - Operations with no outbound links (chain terminators)
        - Operations with no inbound links (entry points only)
        - Completely isolated operations (no links at all)
        - Invalid link targets (operationId doesn't exist)
        """
        outbound: dict[str, list[str]] = {op: [] for op in self._operation_ids}
        inbound: dict[str, list[str]] = {op: [] for op in self._operation_ids}

        paths = self._spec.get("paths", {})
        for path_item in paths.values():
            if not isinstance(path_item, dict):
                continue
            for method, operation in path_item.items():
                if not isinstance(operation, dict) or method.startswith("$"):
                    continue

                source_op = operation.get("operationId")
                if not source_op:
                    continue

                responses = operation.get("responses", {})
                for status_code, response_def in responses.items():
                    if not isinstance(response_def, dict):
                        continue
                    links = response_def.get("links", {})
                    for link_name, link_def in links.items():
                        if not isinstance(link_def, dict):
                            continue
                        target_op = link_def.get("operationId") or link_def.get("operationRef")
                        if not target_op:
                            continue

                        # Check for invalid target
                        if target_op not in self._operation_ids:
                            # operationRef is a JSON pointer, not validated here
                            if not target_op.startswith("#"):
                                result.add(LintMessage(
                                    level="error",
                                    code="invalid-link-target",
                                    message=f"Link '{link_name}' references non-existent operationId: {target_op}",
                                    operation_id=source_op,
                                    details={
                                        "link_name": link_name,
                                        "target": target_op,
                                        "status_code": status_code,
                                    },
                                ))
                            continue

                        outbound[source_op].append(target_op)
                        inbound[target_op].append(source_op)

        # Count operations with links
        ops_with_outbound = {op for op, targets in outbound.items() if targets}
        ops_with_inbound = {op for op, sources in inbound.items() if sources}
        ops_with_any_links = ops_with_outbound | ops_with_inbound
        result.operations_with_links = len(ops_with_any_links)

        # Identify isolated operations (no links at all)
        isolated = self._operation_ids - ops_with_any_links
        for op_id in sorted(isolated):
            op_info = self._operations[op_id]
            result.add(LintMessage(
                level="info",
                code="isolated-operation",
                message=f"Operation has no inbound or outbound links (isolated)",
                operation_id=op_id,
                details={
                    "method": op_info["method"],
                    "path": op_info["path"],
                },
            ))

        # Identify entry points (inbound only, no outbound)
        entry_only = ops_with_inbound - ops_with_outbound
        for op_id in sorted(entry_only):
            result.add(LintMessage(
                level="info",
                code="chain-terminator",
                message=f"Operation has inbound links but no outbound links (chain terminator)",
                operation_id=op_id,
            ))

        # Identify terminators (outbound only, no inbound)
        # These are entry points for chains
        outbound_only = ops_with_outbound - ops_with_inbound
        for op_id in sorted(outbound_only):
            result.add(LintMessage(
                level="info",
                code="chain-entry-point",
                message=f"Operation has outbound links but no inbound links (entry point only)",
                operation_id=op_id,
            ))

    def _check_explicit_links_warning(self, result: LintResult) -> None:
        """Warn if spec has zero explicit links.

        api-parity disables inference algorithms, so specs without explicit
        links won't generate any chains.
        """
        has_any_links = False
        paths = self._spec.get("paths", {})
        for path_item in paths.values():
            if not isinstance(path_item, dict):
                continue
            for method, operation in path_item.items():
                if not isinstance(operation, dict) or method.startswith("$"):
                    continue
                responses = operation.get("responses", {})
                for response_def in responses.values():
                    if not isinstance(response_def, dict):
                        continue
                    if response_def.get("links"):
                        has_any_links = True
                        break
                if has_any_links:
                    break
            if has_any_links:
                break

        if not has_any_links:
            result.add(LintMessage(
                level="warning",
                code="no-explicit-links",
                message=(
                    "Spec has no explicit OpenAPI links. "
                    "api-parity disables link inference algorithms, so stateful chain "
                    "testing (--stateful) will not generate any multi-step chains. "
                    "Add explicit links to responses to enable chain generation."
                ),
            ))

    def _check_link_expression_coverage(self, result: LintResult) -> None:
        """Categorize link expressions by type.

        Reports on the types of expressions used in link parameters:
        - $response.body#/... (body expressions)
        - $response.header.X (header expressions)
        - $request.* (request expressions)
        - Literal values
        """
        body_count = 0
        header_count = 0
        request_count = 0
        literal_count = 0
        body_fields: set[str] = set()
        header_names: set[str] = set()

        paths = self._spec.get("paths", {})
        for path_item in paths.values():
            if not isinstance(path_item, dict):
                continue
            for method, operation in path_item.items():
                if not isinstance(operation, dict) or method.startswith("$"):
                    continue
                responses = operation.get("responses", {})
                for response_def in responses.values():
                    if not isinstance(response_def, dict):
                        continue
                    links = response_def.get("links", {})
                    for link_def in links.values():
                        if not isinstance(link_def, dict):
                            continue
                        parameters = link_def.get("parameters", {})
                        for param_expr in parameters.values():
                            if not isinstance(param_expr, str):
                                continue

                            # Check expression type
                            if LINK_BODY_PATTERN.match(param_expr):
                                body_count += 1
                                # Extract field path
                                match = LINK_BODY_PATTERN.match(param_expr)
                                if match:
                                    body_fields.add(match.group(1))
                            elif LINK_HEADER_PATTERN.match(param_expr):
                                header_count += 1
                                # Extract header name
                                match = LINK_HEADER_PATTERN.match(param_expr)
                                if match:
                                    header_names.add(match.group(1).lower())
                            elif param_expr.startswith("$request."):
                                request_count += 1
                            else:
                                literal_count += 1

        total = body_count + header_count + request_count + literal_count
        if total > 0:
            result.add(LintMessage(
                level="info",
                code="link-expression-coverage",
                message=(
                    f"Link expression types: "
                    f"{body_count} body, {header_count} header, "
                    f"{request_count} request, {literal_count} literal"
                ),
                details={
                    "body_expressions": body_count,
                    "header_expressions": header_count,
                    "request_expressions": request_count,
                    "literal_expressions": literal_count,
                    "body_fields": sorted(body_fields),
                    "header_names": sorted(header_names),
                },
            ))

    def _check_non_200_status_code_links(self, result: LintResult) -> None:
        """Detect links on non-200 status codes.

        Links on 201, 202, other non-200, wildcard (2XX), and "default" are
        supported but were historically buggy. Inform users about these.
        """
        non_200_links: list[dict[str, Any]] = []

        paths = self._spec.get("paths", {})
        for path_item in paths.values():
            if not isinstance(path_item, dict):
                continue
            for method, operation in path_item.items():
                if not isinstance(operation, dict) or method.startswith("$"):
                    continue

                source_op = operation.get("operationId")
                if not source_op:
                    continue

                responses = operation.get("responses", {})
                for status_code, response_def in responses.items():
                    if not isinstance(response_def, dict):
                        continue
                    links = response_def.get("links", {})
                    if not links:
                        continue

                    # Check if status code is non-200
                    code_str = str(status_code)
                    if code_str != "200":
                        for link_name, link_def in links.items():
                            if not isinstance(link_def, dict):
                                continue
                            target_op = link_def.get("operationId") or link_def.get("operationRef")
                            non_200_links.append({
                                "source_operation": source_op,
                                "status_code": code_str,
                                "link_name": link_name,
                                "target_operation": target_op,
                            })

        if non_200_links:
            # Group by status code type
            code_201 = [l for l in non_200_links if l["status_code"] == "201"]
            code_202 = [l for l in non_200_links if l["status_code"] == "202"]
            code_2xx = [l for l in non_200_links if l["status_code"].endswith("XX")]
            code_default = [l for l in non_200_links if l["status_code"] == "default"]
            code_other = [l for l in non_200_links if l not in code_201 + code_202 + code_2xx + code_default]

            result.add(LintMessage(
                level="info",
                code="non-200-status-links",
                message=(
                    f"Found {len(non_200_links)} links on non-200 status codes: "
                    f"{len(code_201)} on 201, {len(code_202)} on 202, "
                    f"{len(code_2xx)} on wildcards (2XX), {len(code_default)} on default, "
                    f"{len(code_other)} on other codes. "
                    "These are supported but verify chain generation works as expected."
                ),
                details={
                    "links": non_200_links,
                },
            ))

    def _check_response_schema_coverage(self, result: LintResult) -> None:
        """Check response schema coverage.

        Count operations with/without 2xx response schemas and list those
        missing schemas (affects schema validation feature).
        """
        with_schema: list[str] = []
        without_schema: list[str] = []

        paths = self._spec.get("paths", {})
        for path_item in paths.values():
            if not isinstance(path_item, dict):
                continue
            for method, operation in path_item.items():
                if not isinstance(operation, dict) or method.startswith("$"):
                    continue

                op_id = operation.get("operationId")
                if not op_id:
                    continue

                # Check for 2xx response schema
                responses = operation.get("responses", {})
                has_2xx_schema = False
                for status_code, response_def in responses.items():
                    if not isinstance(response_def, dict):
                        continue
                    # Check if this is a 2xx response
                    code_str = str(status_code)
                    is_2xx = (
                        code_str.startswith("2") or
                        (code_str.endswith("XX") and code_str[0] == "2") or
                        code_str == "default"
                    )
                    if not is_2xx:
                        continue

                    # Check for content schema
                    content = response_def.get("content", {})
                    for media_type_def in content.values():
                        if isinstance(media_type_def, dict) and media_type_def.get("schema"):
                            has_2xx_schema = True
                            break
                    if has_2xx_schema:
                        break

                if has_2xx_schema:
                    with_schema.append(op_id)
                else:
                    without_schema.append(op_id)

        result.operations_with_response_schemas = len(with_schema)

        if without_schema:
            result.add(LintMessage(
                level="warning",
                code="missing-response-schema",
                message=(
                    f"{len(without_schema)} operation(s) have no 2xx response schema. "
                    "Schema validation (OpenAPI Spec as Field Authority) cannot validate "
                    "responses for these operations."
                ),
                details={
                    "operations_without_schema": sorted(without_schema),
                },
            ))

    def _check_duplicate_link_names(self, result: LintResult) -> None:
        """Detect duplicate link names in the same links section.

        YAML parsers silently dedupe duplicate keys, so we need to check the
        raw file content to detect this issue.
        """
        if self._raw_content is None:
            return

        # Pattern to find links sections with potential duplicates
        # This is a heuristic - we look for repeated link names under 'links:'
        in_links_section = False
        links_indent = 0
        link_child_indent: int | None = None  # Detected dynamically from first child
        link_names_in_section: dict[int, list[tuple[str, int]]] = {}  # section_line -> [(name, line_num)]
        section_start_line = 0

        lines = self._raw_content.split("\n")
        for line_num, line in enumerate(lines, 1):
            stripped = line.lstrip()
            if not stripped or stripped.startswith("#"):
                continue

            indent = len(line) - len(stripped)

            # Check if we're entering a links section
            if stripped.startswith("links:"):
                in_links_section = True
                links_indent = indent
                link_child_indent = None  # Reset - will detect from first child
                section_start_line = line_num
                link_names_in_section[section_start_line] = []
                continue

            # Check if we've left the links section (back to same or lower indent)
            if in_links_section and indent <= links_indent and not stripped.startswith("-"):
                in_links_section = False
                link_child_indent = None

            # If in links section, detect child indent from first key, then match it
            if in_links_section and indent > links_indent:
                # First child after 'links:' sets the expected child indent
                if link_child_indent is None:
                    link_child_indent = indent

                # Only process lines at the link child indent (direct children of links:)
                if indent == link_child_indent:
                    # Extract key name
                    if ":" in stripped:
                        key = stripped.split(":")[0].strip()
                        if key and not key.startswith("$"):
                            link_names_in_section[section_start_line].append((key, line_num))

        # Check for duplicates in each section
        for section_line, names in link_names_in_section.items():
            seen: dict[str, int] = {}
            for name, line_num in names:
                if name in seen:
                    result.add(LintMessage(
                        level="error",
                        code="duplicate-link-name",
                        message=(
                            f"Duplicate link name '{name}' at line {line_num}. "
                            f"First occurrence at line {seen[name]}. "
                            "YAML parsers silently use the last definition."
                        ),
                        details={
                            "link_name": name,
                            "first_line": seen[name],
                            "duplicate_line": line_num,
                        },
                    ))
                else:
                    seen[name] = line_num


class SpecLinterError(Exception):
    """Raised when spec linting fails."""


def format_lint_result_text(result: LintResult) -> str:
    """Format lint result as human-readable text.

    Args:
        result: The lint result to format.

    Returns:
        Formatted text output.
    """
    lines: list[str] = []

    # Summary header
    lines.append(f"Spec Analysis Summary")
    lines.append("=" * 60)
    lines.append(f"Total operations: {result.total_operations}")
    lines.append(f"Operations with links: {result.operations_with_links}")
    lines.append(f"Operations with response schemas: {result.operations_with_response_schemas}")
    lines.append("")

    # Errors
    if result.errors:
        lines.append(f"ERRORS ({len(result.errors)})")
        lines.append("-" * 40)
        for msg in result.errors:
            prefix = f"[{msg.operation_id}] " if msg.operation_id else ""
            lines.append(f"  {prefix}{msg.code}: {msg.message}")
        lines.append("")

    # Warnings
    if result.warnings:
        lines.append(f"WARNINGS ({len(result.warnings)})")
        lines.append("-" * 40)
        for msg in result.warnings:
            prefix = f"[{msg.operation_id}] " if msg.operation_id else ""
            lines.append(f"  {prefix}{msg.code}: {msg.message}")
        lines.append("")

    # Info
    if result.info:
        lines.append(f"INFO ({len(result.info)})")
        lines.append("-" * 40)
        for msg in result.info:
            prefix = f"[{msg.operation_id}] " if msg.operation_id else ""
            lines.append(f"  {prefix}{msg.code}: {msg.message}")
        lines.append("")

    # Final summary
    if result.has_errors():
        lines.append("Result: FAIL (errors found)")
    elif result.warnings:
        lines.append("Result: PASS (with warnings)")
    else:
        lines.append("Result: PASS")

    return "\n".join(lines)
