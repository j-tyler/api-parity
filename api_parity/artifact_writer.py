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
    ChainCase,
    ChainExecution,
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
    # Chain-specific stats
    total_chains: int = 0
    chain_matches: int = 0
    chain_mismatches: int = 0
    chain_errors: int = 0
    # Set to True if run was interrupted (SIGINT)
    interrupted: bool = False

    def add_operation(self, operation_id: str) -> None:
        """Record a case for an operation."""
        self.operations[operation_id] = self.operations.get(operation_id, 0) + 1


@dataclass
class ReplayStats:
    """Statistics for a replay run.

    Tracks outcomes for replayed mismatch bundles:
    - still_mismatch: Same mismatch persists
    - now_match: Previously mismatched, now matches (fixed)
    - different_mismatch: Mismatches differently than before
    """

    total_bundles: int = 0
    # Outcome counts
    still_mismatch: int = 0
    now_match: int = 0
    different_mismatch: int = 0
    errors: int = 0
    skipped: int = 0

    # Breakdown by type
    stateless_bundles: int = 0
    chain_bundles: int = 0

    # Track which bundles had which outcomes (bundle directory names)
    fixed_bundles: list[str] = field(default_factory=list)
    persistent_bundles: list[str] = field(default_factory=list)
    changed_bundles: list[str] = field(default_factory=list)

    # Set to True if run was interrupted (SIGINT)
    interrupted: bool = False


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

        # Write diff.json with type discriminator
        diff_data = {"type": "stateless", **diff.model_dump()}
        self._write_json(bundle_dir / "diff.json", diff_data)

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

    def write_chain_mismatch(
        self,
        chain: ChainCase,
        execution_a: ChainExecution,
        execution_b: ChainExecution,
        step_diffs: list[ComparisonResult],
        mismatch_step: int,
        target_a_info: TargetInfo,
        target_b_info: TargetInfo,
        seed: int | None = None,
    ) -> Path:
        """Write a chain mismatch bundle to disk.

        Chain bundles contain:
        - chain.json: The chain template (ChainCase)
        - target_a.json: Full execution trace for Target A (ChainExecution)
        - target_b.json: Full execution trace for Target B (ChainExecution)
        - diff.json: Step-by-step comparison results with mismatch info
        - metadata.json: Run context

        Args:
            chain: The chain that produced the mismatch.
            execution_a: Execution trace from Target A.
            execution_b: Execution trace from Target B.
            step_diffs: List of comparison results for each step.
            mismatch_step: Index of first step with mismatch.
            target_a_info: Target A information.
            target_b_info: Target B information.
            seed: Random seed used (if any).

        Returns:
            Path to the bundle directory.
        """
        # Generate bundle directory name
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        chain_id = chain.chain_id[:8]
        # Include first operation for context
        first_op = chain.steps[0].request_template.operation_id if chain.steps else "unknown"
        first_op = self._sanitize_filename(first_op)
        bundle_name = f"{timestamp}__chain__{first_op}__{chain_id}"
        bundle_dir = self._mismatches_dir / bundle_name

        bundle_dir.mkdir(parents=True, exist_ok=True)

        # Redact secrets from chain and executions
        chain_data = self._redact(chain.model_dump())
        exec_a_data = self._redact(execution_a.model_dump())
        exec_b_data = self._redact(execution_b.model_dump())

        # Write chain.json
        self._write_json(bundle_dir / "chain.json", chain_data)

        # Write target_a.json and target_b.json
        self._write_json(bundle_dir / "target_a.json", exec_a_data)
        self._write_json(bundle_dir / "target_b.json", exec_b_data)

        # Write diff.json with step-by-step results and type discriminator
        diff_data = {
            "type": "chain",
            "match": False,
            "mismatch_step": mismatch_step,
            "total_steps": len(chain.steps),
            "steps": [diff.model_dump() for diff in step_diffs],
        }
        self._write_json(bundle_dir / "diff.json", diff_data)

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

    def write_chains_log(
        self,
        chains: list[ChainCase],
        outcomes: list[str],
        max_chains: int | None,
        max_steps: int,
    ) -> Path:
        """Write all executed chains to chains.txt for debugging.

        Format matches `graph-chains --generated` output for easy comparison.
        Each chain includes its execution outcome (match/mismatch/error).

        Args:
            chains: List of chains that were executed.
            outcomes: Outcome for each chain ("match", "mismatch", or "error").
            max_chains: Max chains setting used.
            max_steps: Max steps setting used.

        Returns:
            Path to the chains.txt file.
        """
        lines: list[str] = []

        # Header matching graph-chains --generated format
        lines.append(f"Executed chains (max_chains={max_chains}, max_steps={max_steps})")
        lines.append("")
        lines.append(f"Executed Chains ({len(chains)} chains)")
        lines.append("=" * 60)

        # Track links used for coverage summary
        used_links: set[tuple[str, str, str]] = set()  # (source_op, status_code, target_op)

        for i, (chain, outcome) in enumerate(zip(chains, outcomes), 1):
            ops = [step.request_template.operation_id for step in chain.steps]
            lines.append("")
            lines.append(f"[Chain {i}] " + " -> ".join(ops))
            lines.append(f"  Steps: {len(chain.steps)}")
            lines.append(f"  Outcome: {outcome.upper()}")

            for step in chain.steps:
                step_num = step.step_index + 1
                op_id = step.request_template.operation_id
                method = step.request_template.method
                path = step.request_template.path_template

                lines.append(f"  {step_num}. {op_id}: {method} {path}")

                # Show link info for steps after the first
                if step.step_index > 0:
                    if step.link_source is not None:
                        link_name = step.link_source.get("link_name", "unknown")
                        status_code = step.link_source.get("status_code", "?")
                        source_op = step.link_source.get("source_operation", "?")
                        lines.append(f"      via link: {link_name} ({status_code})")
                        used_links.add((source_op, str(status_code), op_id))
                    else:
                        lines.append("      via unknown link (not in spec)")

        # Summary
        lines.append("")
        lines.append("=" * 60)
        lines.append("Execution Summary")
        lines.append("=" * 60)
        match_count = outcomes.count("match")
        mismatch_count = outcomes.count("mismatch")
        error_count = outcomes.count("error")
        lines.append(f"Total chains: {len(chains)}")
        lines.append(f"Matches: {match_count}")
        lines.append(f"Mismatches: {mismatch_count}")
        lines.append(f"Errors: {error_count}")
        lines.append(f"Links traversed: {len(used_links)}")

        # Write to file
        output_path = self._output_dir / "chains.txt"
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
            f.write("\n")

        return output_path

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
            "interrupted": stats.interrupted,
            "total_cases": stats.total_cases,
            "matches": stats.matches,
            "mismatches": stats.mismatches,
            "errors": stats.errors,
            "skipped": stats.skipped,
            "operations": stats.operations,
            # Chain stats (zero in stateless mode)
            "total_chains": stats.total_chains,
            "chain_matches": stats.chain_matches,
            "chain_mismatches": stats.chain_mismatches,
            "chain_errors": stats.chain_errors,
        }
        self._write_json(self._output_dir / "summary.json", summary)

    def write_replay_summary(
        self, stats: ReplayStats, input_dir: Path | str
    ) -> None:
        """Write replay run summary to disk.

        Args:
            stats: Replay statistics.
            input_dir: Input directory containing original bundles.
        """
        summary = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "tool_version": TOOL_VERSION,
            "mode": "replay",
            "input_dir": str(input_dir),
            "interrupted": stats.interrupted,
            # Counts
            "total_bundles": stats.total_bundles,
            "still_mismatch": stats.still_mismatch,
            "now_match": stats.now_match,
            "different_mismatch": stats.different_mismatch,
            "errors": stats.errors,
            "skipped": stats.skipped,
            # Type breakdown
            "stateless_bundles": stats.stateless_bundles,
            "chain_bundles": stats.chain_bundles,
            # Bundle lists for detailed reporting
            "fixed_bundles": stats.fixed_bundles,
            "persistent_bundles": stats.persistent_bundles,
            "changed_bundles": stats.changed_bundles,
        }
        self._write_json(self._output_dir / "replay_summary.json", summary)

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
