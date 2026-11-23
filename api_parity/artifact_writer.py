"""Artifact Writer - Writes mismatch bundles to disk.

The Artifact Writer saves mismatch bundles for replay and analysis. Each bundle
contains the request, both target responses, the comparison diff, and metadata.

See ARCHITECTURE.md "Mismatch Report Bundle" for specifications.
"""

from __future__ import annotations

import base64
import copy
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from api_parity.schema_validator import SchemaValidator
from api_parity.session_cookies import StoredCookie
from api_parity.models import (
    ChainCase,
    ChainExecution,
    ComparisonResult,
    MismatchMetadata,
    RequestCase,
    ResponseCase,
    SecretsConfig,
    TargetInfo,
)

# Version of the tool (used in metadata)
TOOL_VERSION = "0.1.0"

# Per-bundle so merge and moving bundles preserve their validation contract.
BUNDLE_SPEC_FILENAME = "openapi.json"
BUNDLE_SPEC_FILENAMES = (BUNDLE_SPEC_FILENAME, "openapi.yaml")


@lru_cache(maxsize=128)
def _redaction_path(expression: str):
    from jsonpath_ng import parse
    return parse(expression)


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

    chain_incomplete: int = 0

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
    incomplete_bundles: list[str] = field(default_factory=list)
    # A bundle can restore one side's cookies and omit the other's.
    session_bundles: dict[str, dict[str, list[str]]] = field(default_factory=dict)


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
        spec_path: Path | None = None,
        *, schema_validator: SchemaValidator | None = None,
    ) -> None:
        """Initialize the artifact writer.

        Args:
            output_dir: Base directory for artifacts.
            secrets_config: Optional secret redaction configuration.
            spec_path: Optional OpenAPI snapshot to include in each bundle.
        """
        self._output_dir = output_dir
        self._secrets_config = secrets_config
        self._spec_bytes = None
        self._spec_filename = BUNDLE_SPEC_FILENAME
        if not (secrets_config and secrets_config.redact_fields):
            if schema_validator is None and spec_path is not None:
                schema_validator = SchemaValidator(spec_path)
            if schema_validator is not None:
                self._spec_bytes = schema_validator.source_bytes
                self._spec_filename = schema_validator.snapshot_name
        # Redaction paths describe artifacts, not OpenAPI schema keywords.
        # Copying the source can leak examples. Rewriting it could change
        # constraints such as enum. Leave the contract external in this mode.
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
        # Bundle naming: {timestamp}__{operation}__{case_id}
        # - timestamp: enables chronological sorting with `ls`
        # - operation_id: enables quick visual identification and glob filtering
        # - case_id: ensures uniqueness when same operation has multiple mismatches
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        operation_id = self._sanitize_filename(case.operation_id)
        case_id = case.case_id[:8]  # First 8 chars of UUID for brevity
        bundle_name = f"{timestamp}__{operation_id}__{case_id}"
        bundle_dir = self._mismatches_dir / bundle_name

        bundle_dir.mkdir(parents=True, exist_ok=True)
        self._write_spec(bundle_dir)

        # Redact secrets from case and responses if configured
        targets = (("target_a", response_a), ("target_b", response_b))
        case_data = case.model_dump(exclude={"session_cookies"})
        sessions = self._session_inputs(case_data, targets)
        case_data["session_cookies"] = sessions
        case_data = self._redact(case_data)
        case_data["session_cookies"] = self._safe_session_map(sessions, case_data.get("session_cookies"))
        self._protect_repeated_cookies([(case_data["session_cookies"], targets)])
        # An empty map contains no secret and must keep its type for replay.
        for field in ("cookies", "headers"):
            if not getattr(case, field) and case_data[field] == "[REDACTED]":
                case_data[field] = {}

        for side, response in targets:
            request = {k: v for k, v in case_data.items() if k != "session_cookies"}
            self._mark_session_trace(request, side, sessions)
            execution = self._redact({"request": copy.deepcopy(request), "response": response.model_dump()})
            self._prepare_trace(execution)
            self._write_json(bundle_dir / f"{side}.json", execution)

        self._encode_request_bytes(case_data)
        self._write_json(bundle_dir / "case.json", case_data)

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
        # Bundle naming: {timestamp}__chain__{first_op}__{chain_id}
        # - "chain" marker distinguishes from stateless bundles
        # - first_op: helps identify what the chain tests without opening files
        # - chain_id: ensures uniqueness for chains with same first operation
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        chain_id = chain.chain_id[:8]
        first_op = chain.steps[0].request_template.operation_id if chain.steps else "unknown"
        first_op = self._sanitize_filename(first_op)
        bundle_name = f"{timestamp}__chain__{first_op}__{chain_id}"
        bundle_dir = self._mismatches_dir / bundle_name

        bundle_dir.mkdir(parents=True, exist_ok=True)
        self._write_spec(bundle_dir)

        # Redact secrets from chain and executions
        chain_data = chain.model_dump(exclude={"steps": {"__all__": {"request_template": {"session_cookies"}}}})
        trace_exclude = {"steps": {"__all__": {"request": {"session_cookies"}}}}
        exec_a_data = execution_a.model_dump(exclude=trace_exclude)
        exec_b_data = execution_b.model_dump(exclude=trace_exclude)
        step_sessions = []
        protected_steps = []
        for i, step in enumerate(chain_data["steps"]):
            template = step["request_template"]
            sessions = {}
            targets = ()
            if i < len(execution_a.steps) and i < len(execution_b.steps):
                targets = (("target_a", execution_a.steps[i].response), ("target_b", execution_b.steps[i].response))
                sessions = self._session_inputs(template, targets, i, (len(chain.steps), len(execution_a.steps)))
                for side, trace in (("target_a", exec_a_data), ("target_b", exec_b_data)):
                    self._mark_session_trace(trace["steps"][i]["request"], side, sessions)
            template["session_cookies"] = sessions
            step_sessions.append((sessions, targets))
        chain_data = self._redact(chain_data)
        exec_a_data = self._redact(exec_a_data)
        exec_b_data = self._redact(exec_b_data)
        for i, (step, (sessions, targets)) in enumerate(zip(chain_data["steps"], step_sessions)):
            if isinstance(step, dict) and isinstance(step.get("request_template"), dict):
                template = step["request_template"]
                template["session_cookies"] = self._safe_session_map(sessions, template.get("session_cookies"))
                for field in ("cookies", "headers"):
                    if template.get(field) == "[REDACTED]" and not getattr(chain.steps[i].request_template, field):
                        template[field] = {}
                protected_steps.append((template["session_cookies"], targets))
            else:
                protected_steps.append(({}, targets))
        self._protect_repeated_cookies(protected_steps)

        for step in chain_data["steps"]:
            if isinstance(step, dict):
                self._encode_request_bytes(step["request_template"])
        for trace in (exec_a_data, exec_b_data):
            for step in trace["steps"]:
                if isinstance(step, dict):
                    self._prepare_trace(step)

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
        if any(sessions for sessions, _ in step_sessions):
            # A session used at an early step can authorize a later step whose
            # own cookies were all issued within the chain.
            diff_data["session_auth_denials"] = [
                [a.response.status_code in (401, 403), b.response.status_code in (401, 403)]
                for a, b in zip(execution_a.steps, execution_b.steps)
            ]
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
        compared_counts: list[int] | None = None,
    ) -> Path:
        """Write all executed chains to chains.txt for debugging.

        Format matches `graph-chains --generated` output for easy comparison.
        Each chain includes its execution outcome (match/mismatch/error).

        Args:
            chains: List of chains that were executed.
            outcomes: Outcome for each chain ("match", "mismatch", "incomplete", or "error").
            compared_counts: Number of compared steps per chain, if known.
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
            compared = len(chain.steps) if compared_counts is None else compared_counts[i - 1]
            lines.append(f"  Steps: {len(chain.steps)}")
            if compared < len(chain.steps):
                lines.append(f"  Compared: {compared}/{len(chain.steps)}")
            lines.append(f"  Outcome: {outcome.upper()}")

            for position, step in enumerate(chain.steps):
                step_num = step.step_index + 1
                op_id = step.request_template.operation_id
                method = step.request_template.method
                path = step.request_template.path_template

                suffix = " (not compared)" if position >= compared else ""
                lines.append(f"  {step_num}. {op_id}: {method} {path}{suffix}")

                # Show link info for steps after the first
                if step.step_index > 0:
                    if step.link_source is not None:
                        link_name = step.link_source.get("link_name", "unknown")
                        status_code = step.link_source.get("status_code", "?")
                        source_op = step.link_source.get("source_operation", "?")
                        lines.append(f"      via link: {link_name} ({status_code})")
                        if position < compared:
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
        if "incomplete" in outcomes:
            lines.append(f"Incomplete: {outcomes.count('incomplete')}")
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
            "chain_incomplete": stats.chain_incomplete,
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
            "incomplete": len(stats.incomplete_bundles),
            "incomplete_bundles": stats.incomplete_bundles,
            "session_bundles": stats.session_bundles,
        }
        self._write_json(self._output_dir / "replay_summary.json", summary)

    def _write_spec(self, bundle_dir: Path) -> None:
        """Keep the original validation contract with every unresolved failure."""
        for name in BUNDLE_SPEC_FILENAMES:
            if name != self._spec_filename or self._spec_bytes is None:
                # Repeated writes must not retain another bundle's contract.
                (bundle_dir / name).unlink(missing_ok=True)
        if self._spec_bytes is None:
            return
        spec_path = bundle_dir / self._spec_filename
        temp_path = spec_path.with_suffix(".tmp")
        temp_path.write_bytes(self._spec_bytes)
        temp_path.replace(spec_path)

    def _prepare_trace(self, trace: dict) -> None:
        self._encode_request_bytes(trace["request"])
        response = trace["response"]
        if isinstance(response, dict) and response.get("body") == "[REDACTED]":
            response["body_base64"] = None
            response.pop("json_body_state", None)

    @staticmethod
    def _encode_request_bytes(request: Any) -> None:
        """Encode only request payloads, after applying artifact redaction."""
        if not isinstance(request, dict):
            return
        if request.get("body") == "[REDACTED]" and request.get("body_base64") is not None:
            # Whole-body redaction supersedes the original encoded payload.
            request["body_base64"] = None
        if isinstance(request.get("body"), (list, dict)):
            paths = []

            def encode_fields(container, path):
                items = container.items() if isinstance(container, dict) else enumerate(container)
                for key, value in items:
                    if isinstance(value, bytes):
                        container[key] = base64.b64encode(value).decode("ascii")
                        paths.append([*path, key])
                    elif isinstance(value, (list, dict)):
                        encode_fields(value, [*path, key])

            encode_fields(request["body"], [])
            if paths:
                request["binary_field_paths"] = paths
            return
        if not isinstance(request.get("body"), bytes):
            return
        body = request["body"]
        request["body"] = None
        if request["body_base64"] is not None:
            # A redaction selected the encoded destination. Keep its replacement.
            return
        request["body_base64"] = base64.b64encode(body).decode("ascii")
        if "json" in (request["media_type"] or "").lower():
            # JSON body bytes use the executor's parse-or-send-raw behavior.
            # Explicit body_base64 requests are always sent verbatim.
            request["body_encoding"] = "json-input-bytes"

    def _write_json(self, path: Path, data: Any) -> None:
        """Write data as JSON to a file atomically.

        Uses write-to-temp-then-rename pattern to prevent partial files if the
        process is interrupted (SIGINT/SIGTERM) mid-write. Readers never see
        incomplete JSON.

        Args:
            path: Target file path.
            data: Data to write.
        """
        temp_path = path.with_suffix(".tmp")
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)
            f.write("\n")
        # Atomic rename - on POSIX this is guaranteed atomic within same filesystem
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

    def _session_inputs(
        self, template: dict, targets: tuple[tuple[str, ResponseCase], ...],
        step_index: int | None = None, step_counts: tuple[int, ...] = (),
    ) -> dict:
        sessions = {}
        for side, response in targets:
            use = response._session_cookie
            if use is None:
                continue
            saved = use.model_dump(exclude={"cookies"})
            saved["cookies"] = None
            if (self._secrets_config and self._secrets_config.store_session_cookies and use.cookies
                    and not self._cookie_is_redacted(template, response, use.cookies, step_index, step_counts)):
                saved["cookies"] = [cookie.model_dump(exclude_defaults=True) for cookie in use.cookies]
            sessions[side] = saved
        return sessions

    @staticmethod
    def _safe_session_map(original: dict, redacted: Any) -> dict:
        # A partially redacted cookie cannot be reconstructed. Keep the
        # dependency marker and never send a redaction placeholder as a value.
        result = {}
        for side, use in original.items():
            fields = redacted.get(side) if isinstance(redacted, dict) else None
            if fields == use:
                result[side] = use
            else:
                result[side] = {"cookies": None}
                for key in ("origin", "auth_denied"):
                    if isinstance(fields, dict) and key in fields and fields[key] == use[key]:
                        result[side][key] = fields[key]
        return result

    def _protect_repeated_cookies(self, steps: list) -> None:
        """A step-specific redaction must not leave another copy of the same inherited value."""
        if not (self._secrets_config and self._secrets_config.store_session_cookies and self._secrets_config.redact_fields):
            return
        withheld = set()
        for sessions, targets in steps:
            for side, response in targets:
                use = response._session_cookie
                if use and use.cookies and not sessions.get(side, {}).get("cookies"):
                    withheld.update((c.name, c.value) for c in use.cookies)
        if withheld:
            for sessions, _ in steps:
                for use in sessions.values():
                    if any((c["name"], c["value"]) in withheld for c in (use["cookies"] or [])):
                        use["cookies"] = None

    @staticmethod
    def _mark_session_trace(request: dict, side: str, sessions: dict) -> None:
        if side in sessions:
            # Values are stored only in the canonical case or chain template.
            request["session_cookies"] = {side: {"cookies": None}}

    def _cookie_is_redacted(
        self, template: dict, response: ResponseCase, cookies: list[StoredCookie],
        step_index: int | None, step_counts: tuple[int, ...],
    ) -> bool:
        rules = self._secrets_config.redact_fields
        if not rules:
            return False
        # Check user selectors against the request and response shapes they
        # already use. Mark cookie fields and their ancestors by identity so a
        # match on an unrelated body field does not disable cookie storage.
        marker = object()
        header = [marker]
        names = {**template["cookies"], **{cookie.name: marker for cookie in cookies}}
        req_headers = {**template["headers"], "Cookie": header, "cookie": header}
        res_headers = {**response.headers, "Set-Cookie": header, "set-cookie": header}
        request = {**template, "cookies": names, "headers": req_headers}
        reply = {**response.model_dump(), "headers": res_headers}
        trace = {"request": request, "response": reply}
        views = [request, reply, trace]
        protected = [marker, header, names, req_headers, res_headers, *views]
        if step_index is not None:
            step = {**trace, "request_template": request, "step_index": step_index}
            protected.append(step)
            for count in set(step_counts):
                steps = [{}] * count
                steps[step_index] = step
                document = {"steps": steps}
                protected.extend((steps, document))
                views.append(document)
        protected_ids = {id(item) for item in protected}
        for rule in rules:
            try:
                path = _redaction_path(rule)
                if any(id(match.value) in protected_ids for view in views for match in path.find(view)):
                    return True
            except Exception:
                continue  # Same best-effort semantics for unsupported selectors as _redact_path.
        return False

    def _redact(self, data: Any) -> Any:
        """Redact secret fields from data.

        Args:
            data: Data structure to redact.

        Returns:
            Redacted copy of data.
        """
        if not self._secrets_config or not self._secrets_config.redact_fields:
            return data

        # Deep copy to avoid modifying original - bundles are persisted to disk
        # and may be loaded later, so we must not mutate the caller's data
        data = copy.deepcopy(data)

        for jsonpath in self._secrets_config.redact_fields:
            data = self._redact_path(data, jsonpath)

        return data

    def _redact_path(self, data: Any, jsonpath: str) -> Any:
        """Redact a specific JSONPath from data.

        Redaction is best-effort: if a path doesn't exist in the data or is
        malformed, we skip it silently. This allows users to specify broad
        redaction patterns (e.g., "$.*.password") that may not match every
        response structure.

        Args:
            data: Data structure.
            jsonpath: JSONPath expression to redact (e.g., "$.body.api_key").

        Returns:
            Data with path redacted. Returns original data unchanged if path
            doesn't match or is invalid.
        """
        try:
            compiled = _redaction_path(jsonpath)
            matches = compiled.find(data)

            for match in matches:
                match.full_path.update(data, "[REDACTED]")

        except Exception:
            # Best-effort redaction: invalid paths or missing jsonpath_ng library
            # should not break artifact writing. The data just won't be redacted.
            pass

        return data
