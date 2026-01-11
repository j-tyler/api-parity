"""Artifact Writer - Writes mismatch bundles to disk.

The Artifact Writer saves mismatch bundles for replay and analysis. Each bundle
contains the request, both target responses, the comparison diff, and metadata.

See ARCHITECTURE.md "Mismatch Report Bundle" for specifications.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from api_parity.models import (
    ComparisonResult,
    MismatchMetadata,
    RequestCase,
    ResponseCase,
    SecretsConfig,
    StatelessExecution,
    TargetInfo,
)

# Version of the tool (used in metadata)
TOOL_VERSION = "0.1.0"


@dataclass
class RunStats:
    """Statistics for an explore run."""

    total_cases: int = 0
    matches: int = 0
    mismatches: int = 0
    errors: int = 0
    skipped: int = 0
    operations: dict[str, int] = field(default_factory=dict)

    def add_operation(self, operation_id: str) -> None:
        """Record a case for an operation."""
        self.operations[operation_id] = self.operations.get(operation_id, 0) + 1


class ArtifactWriter:
    """Writes mismatch bundles to disk.

    Usage:
        writer = ArtifactWriter(Path("./artifacts"), secrets_config)
        bundle_path = writer.write_mismatch(case, exec_a, exec_b, diff, metadata)
        writer.write_summary(stats)
    """

    def __init__(
        self,
        output_dir: Path,
        secrets_config: SecretsConfig | None = None,
    ) -> None:
        """Initialize the artifact writer.

        Args:
            output_dir: Base directory for artifacts.
            secrets_config: Optional secret redaction configuration.
        """
        self._output_dir = output_dir
        self._secrets_config = secrets_config
        self._mismatches_dir = output_dir / "mismatches"

        # Create directories
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._mismatches_dir.mkdir(parents=True, exist_ok=True)

    def write_mismatch(
        self,
        case: RequestCase,
        response_a: ResponseCase,
        response_b: ResponseCase,
        diff: ComparisonResult,
        target_a_info: TargetInfo,
        target_b_info: TargetInfo,
        seed: int | None = None,
    ) -> Path:
        """Write a mismatch bundle to disk.

        Args:
            case: The request case that produced the mismatch.
            response_a: Response from target A.
            response_b: Response from target B.
            diff: The comparison result.
            target_a_info: Target A information.
            target_b_info: Target B information.
            seed: Random seed used (if any).

        Returns:
            Path to the bundle directory.
        """
        # Generate bundle directory name
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        operation_id = self._sanitize_filename(case.operation_id)
        case_id = case.case_id[:8]  # First 8 chars of UUID
        bundle_name = f"{timestamp}__{operation_id}__{case_id}"
        bundle_dir = self._mismatches_dir / bundle_name

        bundle_dir.mkdir(parents=True, exist_ok=True)

        # Redact secrets from case and responses if configured
        case_data = self._redact(case.model_dump())
        exec_a_data = self._redact(
            StatelessExecution(request=case, response=response_a).model_dump()
        )
        exec_b_data = self._redact(
            StatelessExecution(request=case, response=response_b).model_dump()
        )

        # Write case.json
        self._write_json(bundle_dir / "case.json", case_data)

        # Write target_a.json and target_b.json
        self._write_json(bundle_dir / "target_a.json", exec_a_data)
        self._write_json(bundle_dir / "target_b.json", exec_b_data)

        # Write diff.json
        self._write_json(bundle_dir / "diff.json", diff.model_dump())

        # Write metadata.json
        metadata = MismatchMetadata(
            tool_version=TOOL_VERSION,
            timestamp=datetime.now(timezone.utc).isoformat(),
            seed=seed,
            target_a=target_a_info,
            target_b=target_b_info,
            comparison_rules_applied="operation",
        )
        self._write_json(bundle_dir / "metadata.json", metadata.model_dump())

        return bundle_dir

    def write_summary(self, stats: RunStats, seed: int | None = None) -> None:
        """Write run summary to disk.

        Args:
            stats: Run statistics.
            seed: Random seed used (if any).
        """
        summary = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "tool_version": TOOL_VERSION,
            "seed": seed,
            "total_cases": stats.total_cases,
            "matches": stats.matches,
            "mismatches": stats.mismatches,
            "errors": stats.errors,
            "skipped": stats.skipped,
            "operations": stats.operations,
        }
        self._write_json(self._output_dir / "summary.json", summary)

    def _write_json(self, path: Path, data: Any) -> None:
        """Write data as JSON to a file atomically.

        Args:
            path: Target file path.
            data: Data to write.
        """
        # Write to temp file first, then rename for atomicity
        temp_path = path.with_suffix(".tmp")
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)
            f.write("\n")
        temp_path.rename(path)

    def _sanitize_filename(self, name: str) -> str:
        """Sanitize a string for use in filenames.

        Args:
            name: String to sanitize.

        Returns:
            Filesystem-safe string.
        """
        # Replace unsafe characters with underscores
        safe = re.sub(r"[^\w\-.]", "_", name)
        # Collapse multiple underscores
        safe = re.sub(r"_+", "_", safe)
        # Trim underscores from ends
        safe = safe.strip("_")
        # Limit length
        if len(safe) > 50:
            safe = safe[:50]
        return safe or "unnamed"

    def _redact(self, data: Any) -> Any:
        """Redact secret fields from data.

        Args:
            data: Data structure to redact.

        Returns:
            Redacted copy of data.
        """
        if not self._secrets_config or not self._secrets_config.redact_fields:
            return data

        # Deep copy to avoid modifying original
        import copy
        data = copy.deepcopy(data)

        for jsonpath in self._secrets_config.redact_fields:
            data = self._redact_path(data, jsonpath)

        return data

    def _redact_path(self, data: Any, jsonpath: str) -> Any:
        """Redact a specific JSONPath from data.

        Args:
            data: Data structure.
            jsonpath: JSONPath expression to redact.

        Returns:
            Data with path redacted.
        """
        try:
            from jsonpath_ng import parse as jsonpath_parse

            compiled = jsonpath_parse(jsonpath)
            matches = compiled.find(data)

            for match in matches:
                # Navigate to parent and set value to redacted marker
                self._set_value(data, str(match.full_path), "[REDACTED]")

        except Exception:
            # Ignore invalid paths
            pass

        return data

    def _set_value(self, data: Any, path: str, value: Any) -> None:
        """Set a value at a JSONPath-like path.

        Args:
            data: Root data structure.
            path: Path string (e.g., "body.password").
            value: Value to set.
        """
        # Simple path parser for common cases
        parts = path.replace("[", ".").replace("]", "").split(".")
        parts = [p for p in parts if p]

        current = data
        for i, part in enumerate(parts[:-1]):
            if isinstance(current, dict):
                if part in current:
                    current = current[part]
                else:
                    return
            elif isinstance(current, list):
                try:
                    idx = int(part)
                    if 0 <= idx < len(current):
                        current = current[idx]
                    else:
                        return
                except ValueError:
                    return
            else:
                return

        # Set the final value
        final_part = parts[-1]
        if isinstance(current, dict) and final_part in current:
            current[final_part] = value
        elif isinstance(current, list):
            try:
                idx = int(final_part)
                if 0 <= idx < len(current):
                    current[idx] = value
            except ValueError:
                pass
