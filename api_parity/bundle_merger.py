"""Bundle Merger - Deduplicates mismatch bundles across multiple explore runs.

Combines bundles from multiple explore output directories into a single
deduplicated directory. Two bundles are "the same mismatch" if they have
the same operation, mismatch type, and failing paths. When duplicates are
found, the latest bundle (by timestamp in directory name) is kept.

Only accepts explore output. Replay output is rejected — replay is for
verifying fixes, not for building regression suites.

The merged output preserves the standard bundle directory structure and
is directly replayable with the replay command.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from api_parity.bundle_loader import BundleType, discover_bundles
from api_parity.mismatch_classifier import mismatch_dedup_key


class BundleMergeError(Exception):
    """Error during bundle merge operation."""
    pass


@dataclass
class MergeSummary:
    """Statistics from a merge operation."""
    total_bundles_scanned: int = 0
    unique_patterns: int = 0
    bundles_kept: int = 0
    bundles_deduplicated: int = 0
    errors: list[str] = field(default_factory=list)
    input_dir_counts: dict[str, int] = field(default_factory=dict)


def _detect_bundle_type_from_diff(diff_data: dict, bundle_path: Path) -> BundleType:
    """Determine bundle type from diff data and file presence."""
    bundle_type_str = diff_data.get("type")
    if bundle_type_str == "chain":
        return BundleType.CHAIN
    if bundle_type_str == "stateless":
        return BundleType.STATELESS
    # Fallback: check which case file exists
    if (bundle_path / "chain.json").is_file():
        return BundleType.CHAIN
    return BundleType.STATELESS


def _extract_operation_id(
    bundle_path: Path, bundle_type: BundleType, diff_data: dict
) -> str:
    """Extract operation_id from case.json or chain.json without full Pydantic validation.

    For chain bundles, uses the failing step's operation_id (from mismatch_step
    in diff_data) so that chains sharing a prefix but diverging later produce
    different dedup keys. Falls back to steps[0] if mismatch_step is out of range.

    Raises:
        KeyError: If required fields are missing from the JSON data.
        json.JSONDecodeError: If case/chain file contains invalid JSON.
        OSError: If case/chain file cannot be read.
    """
    if bundle_type == BundleType.STATELESS:
        case_path = bundle_path / "case.json"
        with open(case_path, encoding="utf-8") as f:
            case_data = json.load(f)
        return case_data["operation_id"]
    else:
        chain_path = bundle_path / "chain.json"
        with open(chain_path, encoding="utf-8") as f:
            chain_data = json.load(f)
        steps = chain_data.get("steps", [])
        if not steps:
            raise KeyError("No steps in chain.json")
        # Use the failing step's operation_id so chains that share a prefix
        # but diverge later (e.g. createOrder->getInvoice vs createOrder->getShipment)
        # produce distinct dedup keys.
        mismatch_step = diff_data.get("mismatch_step")
        if mismatch_step is not None and mismatch_step < len(steps):
            return steps[mismatch_step]["request_template"]["operation_id"]
        return steps[0]["request_template"]["operation_id"]


@dataclass
class _BundleInfo:
    """Lightweight bundle info for merge processing."""
    path: Path
    dedup_key: tuple
    timestamp: str  # Directory name; timestamp prefix ensures lexicographic = chronological


def _is_replay_output(directory: Path) -> bool:
    """Check if a directory is replay output rather than explore output.

    Replay writes replay_summary.json (with "mode": "replay"). Explore writes
    summary.json (no mode field). Merge should only accept explore output —
    replay output is for verifying fixes, not for building regression suites.
    """
    return (directory / "replay_summary.json").is_file()


def merge_bundles(input_dirs: list[Path], output_dir: Path) -> MergeSummary:
    """Merge mismatch bundles from multiple directories, deduplicating by failure pattern.

    Only accepts explore output directories. Raises BundleMergeError if any
    input directory is replay output (contains replay_summary.json).

    Algorithm:
    1. Validate all inputs are explore output (not replay)
    2. Discover bundles across all input directories
    3. For each bundle, read diff.json + extract operation_id (lightweight, no Pydantic)
    4. Compute dedup key (operation_id + mismatch_type + failing paths)
    5. Group by dedup key, keep latest per group (by directory name)
    6. Copy winners to output directory
    7. Write merge_summary.json

    Args:
        input_dirs: List of directories containing explore output.
        output_dir: Directory to write merged bundles to.

    Returns:
        MergeSummary with statistics.

    Raises:
        BundleMergeError: If any input directory is replay output.
    """
    # Reject replay output — merge is for combining explore runs only.
    # Replay output contains re-verified bundles (STILL MISMATCH, DIFFERENT
    # MISMATCH) which would create confusing duplicates when merged with
    # explore output. Use replay to verify fixes, use merge to build
    # deduplicated regression suites from explore runs.
    for input_dir in input_dirs:
        if _is_replay_output(input_dir):
            raise BundleMergeError(
                f"Input directory is replay output, not explore output: {input_dir}\n"
                f"Merge only accepts explore output directories. Replay output "
                f"contains re-verified bundles that should not be merged."
            )

    summary = MergeSummary()

    # Key: dedup_key -> BundleInfo (latest wins)
    dedup_groups: dict[tuple, _BundleInfo] = {}

    for input_dir in input_dirs:
        bundles = discover_bundles(input_dir)
        summary.input_dir_counts[str(input_dir)] = len(bundles)

        for bundle_path in bundles:
            summary.total_bundles_scanned += 1

            try:
                # Read diff.json
                diff_path = bundle_path / "diff.json"
                with open(diff_path, encoding="utf-8") as f:
                    diff_data = json.load(f)

                # Determine type and extract operation_id
                bundle_type = _detect_bundle_type_from_diff(diff_data, bundle_path)
                operation_id = _extract_operation_id(bundle_path, bundle_type, diff_data)

                # Compute dedup key
                key = mismatch_dedup_key(operation_id, diff_data)

                # Bundle names are timestamp-prefixed, so lexicographic > means newer
                timestamp = bundle_path.name

                bundle_info = _BundleInfo(path=bundle_path, dedup_key=key, timestamp=timestamp)

                # Keep latest (lexicographic comparison on timestamp-prefixed name)
                if key not in dedup_groups or timestamp > dedup_groups[key].timestamp:
                    dedup_groups[key] = bundle_info

            except (json.JSONDecodeError, KeyError, OSError) as e:
                summary.errors.append(f"{bundle_path.name}: {e}")

    # Copy winners to output
    summary.unique_patterns = len(dedup_groups)
    summary.bundles_kept = len(dedup_groups)
    summary.bundles_deduplicated = summary.total_bundles_scanned - summary.bundles_kept - len(summary.errors)

    if dedup_groups:
        mismatches_dir = output_dir / "mismatches"
        mismatches_dir.mkdir(parents=True, exist_ok=True)

        for bundle_info in dedup_groups.values():
            dest = mismatches_dir / bundle_info.path.name
            # dirs_exist_ok=True allows re-running merge into the same
            # output directory without FileExistsError. Previous winners
            # are overwritten with fresh copies.
            shutil.copytree(bundle_info.path, dest, dirs_exist_ok=True)

    # Write merge_summary.json
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_data = {
        "total_bundles_scanned": summary.total_bundles_scanned,
        "unique_patterns": summary.unique_patterns,
        "bundles_kept": summary.bundles_kept,
        "bundles_deduplicated": summary.bundles_deduplicated,
        "errors": summary.errors,
        "input_dir_counts": summary.input_dir_counts,
    }
    summary_path = output_dir / "merge_summary.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary_data, f, indent=2)

    return summary
