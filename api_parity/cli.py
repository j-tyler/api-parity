"""CLI entry point for api-parity.

Handles argument parsing and dispatches to explore or replay mode.
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from api_parity.artifact_writer import ArtifactWriter, ReplayStats, RunStats
    from api_parity.bundle_loader import LoadedBundle
    from api_parity.case_generator import CaseGenerator
    from api_parity.comparator import Comparator
    from api_parity.executor import Executor
    from api_parity.models import ChainCase, ComparisonResult, ComparisonRules, TargetInfo


DEFAULT_TIMEOUT = 30.0


class ProgressReporter:
    """Reports progress every 10 seconds in a background thread.

    Usage:
        reporter = ProgressReporter(total=100, unit="cases")
        reporter.start()
        for item in items:
            process(item)
            reporter.increment()
        reporter.stop()
    """

    def __init__(self, total: int | None = None, unit: str = "cases") -> None:
        """Initialize the progress reporter.

        Args:
            total: Total number of items (None if unknown).
            unit: Unit name for display (e.g., "cases", "chains", "bundles").
        """
        self._total = total
        self._unit = unit
        self._completed = 0
        self._start_time = 0.0
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_print_len = 0

    def start(self) -> None:
        """Start the progress reporter background thread."""
        self._start_time = time.monotonic()
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the progress reporter and clear the progress line."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        # Clear the progress line
        if self._last_print_len > 0:
            sys.stderr.write("\r" + " " * self._last_print_len + "\r")
            sys.stderr.flush()

    def increment(self, count: int = 1) -> None:
        """Increment the completed count."""
        with self._lock:
            self._completed += count

    def set_total(self, total: int) -> None:
        """Set the total count (useful when total becomes known later)."""
        with self._lock:
            self._total = total

    def _run(self) -> None:
        """Background thread that prints progress every 10 seconds."""
        while not self._stop_event.wait(timeout=10.0):
            self._print_progress()

    def _print_progress(self) -> None:
        """Print current progress to stderr."""
        with self._lock:
            completed = self._completed
            total = self._total

        elapsed = time.monotonic() - self._start_time
        if elapsed < 0.1:
            return  # Don't print immediately

        rate = completed / elapsed if elapsed > 0 else 0

        if total is not None and total > 0:
            percent = (completed / total) * 100
            remaining = total - completed
            eta_seconds = remaining / rate if rate > 0 else 0
            eta_str = self._format_duration(eta_seconds)
            line = f"\r[Progress] {completed}/{total} {self._unit} ({percent:.1f}%) | {rate:.1f}/s | ETA: {eta_str}"
        else:
            elapsed_str = self._format_duration(elapsed)
            line = f"\r[Progress] {completed} {self._unit} | {rate:.1f}/s | Elapsed: {elapsed_str}"

        # Pad to clear previous line if it was longer
        if len(line) < self._last_print_len:
            line = line + " " * (self._last_print_len - len(line))
        self._last_print_len = len(line)

        sys.stderr.write(line)
        sys.stderr.flush()

    @staticmethod
    def _format_duration(seconds: float) -> str:
        """Format a duration in seconds as a human-readable string."""
        if seconds < 60:
            return f"{seconds:.0f}s"
        elif seconds < 3600:
            minutes = int(seconds // 60)
            secs = int(seconds % 60)
            return f"{minutes}m{secs}s"
        else:
            hours = int(seconds // 3600)
            minutes = int((seconds % 3600) // 60)
            return f"{hours}h{minutes}m"


def positive_float(value: str) -> float:
    """Parse and validate a positive float value.

    Raises:
        argparse.ArgumentTypeError: If value is not a positive number.
    """
    try:
        result = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"Invalid number '{value}'.")
    if result <= 0:
        raise argparse.ArgumentTypeError(f"Value must be positive, got {result}.")
    return result


def positive_int(value: str) -> int:
    """Parse and validate a positive integer value.

    Raises:
        argparse.ArgumentTypeError: If value is not a positive integer.
    """
    try:
        result = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"Invalid integer '{value}'.")
    if result <= 0:
        raise argparse.ArgumentTypeError(f"Value must be positive, got {result}.")
    return result


def parse_operation_timeout(value: str) -> tuple[str, float]:
    """Parse OPERATION_ID:SECONDS format.

    Returns:
        Tuple of (operation_id, timeout_seconds).

    Raises:
        argparse.ArgumentTypeError: If format is invalid.
    """
    if ":" not in value:
        raise argparse.ArgumentTypeError(
            f"Invalid format '{value}'. Expected OPERATION_ID:SECONDS (e.g., 'getUser:60')"
        )
    parts = value.rsplit(":", 1)
    operation_id = parts[0]
    if not operation_id:
        raise argparse.ArgumentTypeError(
            f"Invalid format '{value}'. Operation ID cannot be empty."
        )
    try:
        timeout = float(parts[1])
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"Invalid timeout '{parts[1]}'. Must be a number."
        )
    if timeout <= 0:
        raise argparse.ArgumentTypeError(
            f"Invalid timeout '{timeout}'. Must be positive."
        )
    return (operation_id, timeout)


@dataclass
class LintSpecArgs:
    """Parsed arguments for lint-spec mode."""

    spec: Path
    output: str  # "text" or "json"


@dataclass
class ListOperationsArgs:
    """Parsed arguments for list-operations mode."""

    spec: Path


@dataclass
class GraphChainsArgs:
    """Parsed arguments for graph-chains mode."""

    spec: Path
    exclude: list[str]
    # New fields for --generated mode
    generated: bool = False  # Show actual generated chains
    max_chains: int = 20     # Match explore default
    max_steps: int = 6       # Match explore default
    seed: int | None = None  # For reproducibility


@dataclass
class ExploreArgs:
    """Parsed arguments for explore mode."""

    spec: Path
    config: Path
    target_a: str
    target_b: str
    out: Path
    seed: int | None
    validate: bool
    exclude: list[str]
    timeout: float
    operation_timeout: dict[str, float]
    # Stateful chain options
    stateful: bool
    max_chains: int | None
    max_steps: int
    log_chains: bool
    ensure_coverage: bool
    min_hits_per_op: int
    min_coverage: int


@dataclass
class ReplayArgs:
    """Parsed arguments for replay mode."""

    config: Path
    target_a: str
    target_b: str
    input_dir: Path
    out: Path
    validate: bool
    timeout: float
    operation_timeout: dict[str, float]


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser with explore and replay subcommands."""
    parser = argparse.ArgumentParser(
        prog="api-parity",
        description="Differential fuzzing tool for comparing API implementations against an OpenAPI specification.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True, help="Execution mode")

    # Lint-spec subcommand
    lint_spec_parser = subparsers.add_parser(
        "lint-spec",
        help="Analyze an OpenAPI spec for api-parity-specific issues",
    )
    lint_spec_parser.add_argument(
        "--spec",
        type=Path,
        required=True,
        help="Path to OpenAPI specification file (YAML or JSON)",
    )
    lint_spec_parser.add_argument(
        "--output",
        type=str,
        choices=["text", "json"],
        default="text",
        help="Output format (default: text)",
    )

    # List-operations subcommand
    list_ops_parser = subparsers.add_parser(
        "list-operations",
        help="List all operations from an OpenAPI spec with their links",
    )
    list_ops_parser.add_argument(
        "--spec",
        type=Path,
        required=True,
        help="Path to OpenAPI specification file (YAML or JSON)",
    )

    # Graph-chains subcommand
    graph_chains_parser = subparsers.add_parser(
        "graph-chains",
        help="Output a Mermaid flowchart showing OpenAPI link relationships",
    )
    graph_chains_parser.add_argument(
        "--spec",
        type=Path,
        required=True,
        help="Path to OpenAPI specification file (YAML or JSON)",
    )
    graph_chains_parser.add_argument(
        "--exclude",
        type=str,
        action="append",
        default=[],
        metavar="OPERATION_ID",
        help="Exclude an operation by operationId (can be repeated)",
    )
    graph_chains_parser.add_argument(
        "--generated",
        action="store_true",
        help="Show actual chains generated by Schemathesis (not just declared links)",
    )
    graph_chains_parser.add_argument(
        "--max-chains",
        type=int,
        default=20,
        dest="max_chains",
        help="Maximum number of chains to generate (default: 20). Only used with --generated.",
    )
    graph_chains_parser.add_argument(
        "--max-steps",
        type=int,
        default=6,
        metavar="INT",
        dest="max_steps",
        help="Maximum steps per chain (default: 6). Only used with --generated.",
    )
    graph_chains_parser.add_argument(
        "--seed",
        type=int,
        help="Random seed for reproducible generation. Only used with --generated.",
    )

    # Explore subcommand
    explore_parser = subparsers.add_parser(
        "explore",
        help="Generate test cases from OpenAPI spec and compare responses between two targets",
    )
    explore_parser.add_argument(
        "--spec",
        type=Path,
        required=True,
        help="Path to OpenAPI specification file (YAML or JSON)",
    )
    explore_parser.add_argument(
        "--config",
        type=Path,
        required=True,
        help="Path to runtime configuration file (YAML)",
    )
    explore_parser.add_argument(
        "--target-a",
        type=str,
        required=True,
        dest="target_a",
        help="Name of first target (must exist in config)",
    )
    explore_parser.add_argument(
        "--target-b",
        type=str,
        required=True,
        dest="target_b",
        help="Name of second target (must exist in config)",
    )
    explore_parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="Output directory for artifacts",
    )
    explore_parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Random seed for reproducible test generation",
    )
    explore_parser.add_argument(
        "--validate",
        action="store_true",
        default=False,
        help="Validate config and spec without executing requests",
    )
    explore_parser.add_argument(
        "--exclude",
        type=str,
        action="append",
        default=[],
        metavar="OPERATION_ID",
        help="Exclude an operation by operationId (can be repeated)",
    )
    explore_parser.add_argument(
        "--timeout",
        type=positive_float,
        default=DEFAULT_TIMEOUT,
        metavar="SECONDS",
        help=f"Default timeout for each API call (default: {DEFAULT_TIMEOUT}s)",
    )
    explore_parser.add_argument(
        "--operation-timeout",
        type=parse_operation_timeout,
        action="append",
        default=[],
        metavar="OPERATION_ID:SECONDS",
        dest="operation_timeout",
        help="Set timeout for a specific operation (can be repeated)",
    )
    # Stateful chain options
    explore_parser.add_argument(
        "--stateful",
        action="store_true",
        default=False,
        help="Enable stateful chain testing using OpenAPI links",
    )
    explore_parser.add_argument(
        "--max-chains",
        type=positive_int,
        default=None,
        dest="max_chains",
        help="Maximum number of chains to generate in stateful mode (default: 20)",
    )
    explore_parser.add_argument(
        "--max-steps",
        type=positive_int,
        default=6,
        dest="max_steps",
        help="Maximum steps per chain in stateful mode (default: 6)",
    )
    explore_parser.add_argument(
        "--log-chains",
        action="store_true",
        default=False,
        dest="log_chains",
        help="Write all executed chains to chains.txt for debugging (stateful mode only)",
    )
    explore_parser.add_argument(
        "--ensure-coverage",
        action="store_true",
        default=False,
        dest="ensure_coverage",
        help="Ensure all operations are tested at least once. Runs single-request tests "
        "on any operations not covered by chains (stateful mode only)",
    )
    explore_parser.add_argument(
        "--min-hits-per-op",
        type=positive_int,
        default=1,
        dest="min_hits_per_op",
        help="Minimum number of unique chains each linked operation must appear in "
        "before coverage is considered met. Higher values test each operation with "
        "more diverse inputs. Requires --seed. (default: 1, stateful mode only)",
    )
    explore_parser.add_argument(
        "--min-coverage",
        type=int,
        default=100,
        dest="min_coverage",
        help="Percentage (0-100) of linked operations that must meet --min-hits-per-op "
        "before seed walking stops. (default: 100, stateful mode only)",
    )

    # Replay subcommand
    replay_parser = subparsers.add_parser(
        "replay",
        help="Re-execute previously saved mismatch bundles to confirm regressions",
    )
    replay_parser.add_argument(
        "--config",
        type=Path,
        required=True,
        help="Path to runtime configuration file (YAML)",
    )
    replay_parser.add_argument(
        "--target-a",
        type=str,
        required=True,
        dest="target_a",
        help="Name of first target (must exist in config)",
    )
    replay_parser.add_argument(
        "--target-b",
        type=str,
        required=True,
        dest="target_b",
        help="Name of second target (must exist in config)",
    )
    replay_parser.add_argument(
        "--in",
        type=Path,
        required=True,
        dest="input_dir",
        help="Input directory containing mismatch bundles",
    )
    replay_parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="Output directory for replay artifacts",
    )
    replay_parser.add_argument(
        "--validate",
        action="store_true",
        default=False,
        help="Validate config and replay cases without executing requests",
    )
    replay_parser.add_argument(
        "--timeout",
        type=positive_float,
        default=DEFAULT_TIMEOUT,
        metavar="SECONDS",
        help=f"Default timeout for each API call (default: {DEFAULT_TIMEOUT}s)",
    )
    replay_parser.add_argument(
        "--operation-timeout",
        type=parse_operation_timeout,
        action="append",
        default=[],
        metavar="OPERATION_ID:SECONDS",
        dest="operation_timeout",
        help="Set timeout for a specific operation (can be repeated)",
    )

    return parser


def parse_lint_spec_args(namespace: argparse.Namespace) -> LintSpecArgs:
    """Convert parsed namespace to LintSpecArgs dataclass."""
    return LintSpecArgs(spec=namespace.spec, output=namespace.output)


def parse_list_ops_args(namespace: argparse.Namespace) -> ListOperationsArgs:
    """Convert parsed namespace to ListOperationsArgs dataclass."""
    return ListOperationsArgs(spec=namespace.spec)


def parse_graph_chains_args(namespace: argparse.Namespace) -> GraphChainsArgs:
    """Convert parsed namespace to GraphChainsArgs dataclass."""
    return GraphChainsArgs(
        spec=namespace.spec,
        exclude=namespace.exclude or [],
        generated=namespace.generated,
        max_chains=namespace.max_chains,
        max_steps=namespace.max_steps,
        seed=namespace.seed,
    )


def _build_operation_timeouts(timeout_list: list[tuple[str, float]]) -> dict[str, float]:
    """Build operation timeout dict, warning on duplicates."""
    result = {}
    for op_id, timeout in timeout_list:
        if op_id in result:
            print(
                f"Warning: --operation-timeout for '{op_id}' specified multiple times, "
                f"using last value ({timeout}s)",
                file=sys.stderr,
            )
        result[op_id] = timeout
    return result


def parse_explore_args(namespace: argparse.Namespace) -> ExploreArgs:
    """Convert parsed namespace to ExploreArgs dataclass."""
    op_timeouts = _build_operation_timeouts(namespace.operation_timeout or [])
    return ExploreArgs(
        spec=namespace.spec,
        config=namespace.config,
        target_a=namespace.target_a,
        target_b=namespace.target_b,
        out=namespace.out,
        seed=namespace.seed,
        validate=namespace.validate,
        exclude=namespace.exclude or [],
        timeout=namespace.timeout,
        operation_timeout=op_timeouts,
        stateful=namespace.stateful,
        max_chains=namespace.max_chains,
        max_steps=namespace.max_steps,
        log_chains=namespace.log_chains,
        ensure_coverage=namespace.ensure_coverage,
        min_hits_per_op=namespace.min_hits_per_op,
        min_coverage=namespace.min_coverage,
    )


def parse_replay_args(namespace: argparse.Namespace) -> ReplayArgs:
    """Convert parsed namespace to ReplayArgs dataclass."""
    op_timeouts = _build_operation_timeouts(namespace.operation_timeout or [])
    return ReplayArgs(
        config=namespace.config,
        target_a=namespace.target_a,
        target_b=namespace.target_b,
        input_dir=namespace.input_dir,
        out=namespace.out,
        validate=namespace.validate,
        timeout=namespace.timeout,
        operation_timeout=op_timeouts,
    )


def parse_args(args: list[str] | None = None) -> LintSpecArgs | ListOperationsArgs | GraphChainsArgs | ExploreArgs | ReplayArgs:
    """Parse command-line arguments and return typed args dataclass.

    Args:
        args: Command-line arguments to parse. If None, uses sys.argv[1:].

    Returns:
        LintSpecArgs, ListOperationsArgs, GraphChainsArgs, ExploreArgs, or ReplayArgs depending on the subcommand.

    Raises:
        SystemExit: If arguments are invalid (argparse behavior).
    """
    parser = build_parser()
    namespace = parser.parse_args(args)

    if namespace.command == "lint-spec":
        return parse_lint_spec_args(namespace)
    elif namespace.command == "list-operations":
        return parse_list_ops_args(namespace)
    elif namespace.command == "graph-chains":
        return parse_graph_chains_args(namespace)
    elif namespace.command == "explore":
        return parse_explore_args(namespace)
    elif namespace.command == "replay":
        return parse_replay_args(namespace)
    else:
        # Should not happen with required=True on subparsers
        parser.error(f"Unknown command: {namespace.command}")


def main() -> int:
    """Main entry point."""
    try:
        parsed = parse_args()

        if isinstance(parsed, LintSpecArgs):
            return run_lint_spec(parsed)
        elif isinstance(parsed, ListOperationsArgs):
            return run_list_operations(parsed)
        elif isinstance(parsed, GraphChainsArgs):
            return run_graph_chains(parsed)
        elif isinstance(parsed, ExploreArgs):
            return run_explore(parsed)
        else:
            return run_replay(parsed)

    except KeyboardInterrupt:
        print("\nInterrupted", file=sys.stderr)
        return 1


def run_lint_spec(args: LintSpecArgs) -> int:
    """Run lint-spec mode.

    Analyzes an OpenAPI spec for api-parity-specific issues including link
    connectivity, expression coverage, and schema completeness.
    """
    import json

    from api_parity.spec_linter import (
        SpecLinter,
        SpecLinterError,
        format_lint_result_text,
    )

    try:
        linter = SpecLinter(args.spec)
    except SpecLinterError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    result = linter.lint()

    if args.output == "json":
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print(format_lint_result_text(result))

    # Return non-zero if errors were found
    return 1 if result.has_errors() else 0


def run_list_operations(args: ListOperationsArgs) -> int:
    """Run list-operations mode.

    Lists all operations from the OpenAPI spec with their operationIds and links.
    """
    import schemathesis

    try:
        schema = schemathesis.openapi.from_path(str(args.spec))
    except Exception as e:
        print(f"Error loading spec: {e}", file=sys.stderr)
        return 1

    operations = []
    skipped = 0
    for result in schema.get_all_operations():
        op = result.ok()
        if op is None:
            skipped += 1
            continue
        raw = op.definition.raw
        operation_id = raw.get("operationId", "<unnamed>")
        method = op.method.upper()
        path = op.path

        # Extract links from responses
        links = []
        responses = raw.get("responses", {})
        for status_code, response_def in responses.items():
            if isinstance(response_def, dict) and "links" in response_def:
                for link_name, link_def in response_def["links"].items():
                    target_op = link_def.get("operationId", link_def.get("operationRef", "?"))
                    links.append(f"{status_code} → {link_name} → {target_op}")

        operations.append((operation_id, method, path, links))

    # Sort by operationId for consistent output
    operations.sort(key=lambda x: x[0])

    # Print operations
    for operation_id, method, path, links in operations:
        print(f"{operation_id}")
        print(f"  {method} {path}")
        if links:
            print("  Links:")
            for link in links:
                print(f"    {link}")
        print()

    total_msg = f"Total: {len(operations)} operations"
    if skipped:
        total_msg += f" ({skipped} skipped due to errors)"
    print(total_msg)
    return 0


def run_graph_chains(args: GraphChainsArgs) -> int:
    """Run graph-chains mode.

    Outputs a Mermaid flowchart showing OpenAPI link relationships,
    or with --generated, shows actual chains that Schemathesis generates.
    """
    if args.generated:
        return _run_graph_chains_generated(args)

    import schemathesis

    try:
        schema = schemathesis.openapi.from_path(str(args.spec))
    except Exception as e:
        print(f"Error loading spec: {e}", file=sys.stderr)
        return 1

    # Extract operations and links
    operations, edges = _extract_link_graph(schema, args.exclude)

    # Generate and output Mermaid flowchart
    mermaid = _format_mermaid_graph(operations, edges)
    print(mermaid)

    return 0


def _chain_signature(chain: "ChainCase") -> tuple[str, ...]:
    """Get unique signature for chain deduplication (operation sequence).

    Chains are considered duplicates if they have the same sequence of
    operation IDs, regardless of the actual parameter values generated.
    """
    return tuple(step.request_template.operation_id for step in chain.steps)


def _enumerate_possible_chain_signatures(
    edges: list[tuple[str, str]],
    linked_ops: set[str],
    max_steps: int,
    max_signatures: int = 50_000,
) -> set[tuple[str, ...]] | None:
    """Enumerate all possible unique chain signatures from the link graph.

    A chain signature is a tuple of operation IDs representing a valid
    multi-step sequence through the link graph. At each step, the next
    operation must be reachable via a declared link from ANY previous
    step in the chain (not just the immediately preceding step).

    Args:
        edges: Directed edges (source_op, target_op) from declared links.
        linked_ops: Set of operation IDs that participate in links.
        max_steps: Maximum chain length.
        max_signatures: Safety cap to prevent runaway enumeration on dense
            graphs. If exceeded, returns None (caller falls back to current
            behavior without per-op capping).

    Returns:
        Set of chain signature tuples (length >= 2), or None if the
        safety cap was hit (graph too dense for enumeration).
    """
    # Build adjacency dict from deduplicated edges
    adj: dict[str, set[str]] = {}
    for source, target in set(edges):
        if source not in adj:
            adj[source] = set()
        adj[source].add(target)

    signatures: set[tuple[str, ...]] = set()

    # DFS from every linked operation as the start
    for start_op in linked_ops:
        # Stack holds (current_chain_tuple, set_of_ops_in_chain)
        stack: list[tuple[tuple[str, ...], frozenset[str]]] = [
            ((start_op,), frozenset({start_op}))
        ]

        while stack:
            chain, ops_in_chain = stack.pop()

            if len(chain) >= 2:
                signatures.add(chain)
                if len(signatures) > max_signatures:
                    return None  # Safety cap hit

            if len(chain) >= max_steps:
                continue

            # Available next operations = union of adj[op] for all ops in chain
            next_ops: set[str] = set()
            for op in ops_in_chain:
                if op in adj:
                    next_ops.update(adj[op])

            for next_op in next_ops:
                new_chain = chain + (next_op,)
                new_ops = ops_in_chain | frozenset({next_op})
                stack.append((new_chain, new_ops))

    return signatures


def _compute_max_achievable_hits(
    edges: list[tuple[str, str]],
    linked_ops: set[str],
    max_steps: int,
) -> dict[str, int] | None:
    """Compute per-operation maximum achievable unique chain hits.

    For each linked operation, counts how many structurally distinct chain
    signatures (from the link graph) include that operation. This is the
    theoretical maximum number of unique chain hits the operation can
    accumulate, regardless of how many seeds are tried.

    Returns:
        Dict mapping operation_id -> max achievable hits, or None if the
        link graph is too dense to enumerate (caller falls back to flat
        min_hits_per_op with no per-op capping).
    """
    signatures = _enumerate_possible_chain_signatures(edges, linked_ops, max_steps)
    if signatures is None:
        return None

    counts: dict[str, int] = {}
    for sig in signatures:
        # Count each unique operation in this signature once
        for op in set(sig):
            counts[op] = counts.get(op, 0) + 1

    return counts


# Maximum seed increments to prevent infinite loops when few chains are available.
# With 100 seeds tried, we give ample opportunity to find chains while avoiding
# runaway execution if the spec genuinely has fewer chains than requested.
MAX_SEED_INCREMENTS = 100


@dataclass
class ChainGenerationResult:
    """Result of chain generation with coverage tracking.

    Returned by _generate_chains_with_seed_walking(). Contains the generated
    chains plus metadata about how coverage was achieved, so callers can
    report to users without re-computing anything.

    Coverage is defined by two thresholds:
    - min_hits_per_op: each linked operation must appear in this many unique chains
    - min_coverage_pct: this percentage of linked operations must meet min_hits_per_op

    Example: min_hits_per_op=5, min_coverage_pct=100 means "every linked operation
    must appear in at least 5 unique chains."

    Attributes:
        chains: Deduplicated chains ready for execution.
        seeds_used: Seeds that contributed at least one unique chain.
        operations_covered: All operation IDs that appear in at least one chain step.
        operation_hit_counts: Number of unique chains each operation appears in.
            Only counts deduplicated chains (the ones that will be executed).
        linked_operations: Operations that participate in links (the reachable set).
            None if linked_operations was not provided to the generator.
        orphan_operations: Operations with no link involvement (invisible to chains).
            None if linked_operations was not provided.
        min_hits_per_op: The target hits-per-operation used for this generation.
        min_coverage_pct: The target coverage percentage used for this generation.
        stopped_reason: Why seed walking stopped. One of:
            "coverage_met" — coverage target achieved
            "max_chains" — accumulated enough unique chain sequences
            "max_seeds" — hit MAX_SEED_INCREMENTS without meeting other targets
            "no_seed" — single pass (no seed walking)
        seeds_tried: Total number of seeds attempted (0 if no seed walking).
        max_achievable_hits: Per-operation maximum structurally achievable
            unique chain hits, computed from the link graph. None if the graph
            was too dense to enumerate (safety cap hit).
        effective_targets: Per-operation effective target: min(achievable, requested)
            for each linked operation. When max_achievable_hits is None, all ops
            use the flat min_hits_per_op. Properties like coverage_complete and
            ops_below_hits_target use these per-op targets instead of the flat value.
    """

    chains: list["ChainCase"]
    seeds_used: list[int]
    operations_covered: set[str]
    operation_hit_counts: dict[str, int]
    linked_operations: set[str] | None
    orphan_operations: set[str] | None
    min_hits_per_op: int
    min_coverage_pct: float
    stopped_reason: str
    seeds_tried: int
    max_achievable_hits: dict[str, int] | None  # None if enumeration capped out
    effective_targets: dict[str, int]  # Per-operation effective target (min of achievable vs requested)

    @property
    def coverage_complete(self) -> bool:
        """True if coverage target is met (min_coverage_pct of linked ops at effective target)."""
        if self.linked_operations is None:
            return False
        if not self.linked_operations:
            return True
        ops_meeting_target = sum(
            1 for op in self.linked_operations
            if self.operation_hit_counts.get(op, 0) >= self.effective_targets.get(op, self.min_hits_per_op)
        )
        return (ops_meeting_target / len(self.linked_operations) * 100) >= self.min_coverage_pct

    @property
    def linked_covered_count(self) -> int:
        """Number of linked operations covered (at least 1 hit)."""
        if self.linked_operations is None:
            return len(self.operations_covered)
        return len(self.operations_covered & self.linked_operations)

    @property
    def linked_total_count(self) -> int:
        """Total number of linked operations."""
        if self.linked_operations is None:
            return 0
        return len(self.linked_operations)

    @property
    def linked_uncovered(self) -> set[str]:
        """Linked operations not yet covered (0 hits)."""
        if self.linked_operations is None:
            return set()
        return self.linked_operations - self.operations_covered

    @property
    def ops_meeting_hits_target(self) -> int:
        """Number of linked operations that have >= effective target hits."""
        if self.linked_operations is None:
            return 0
        return sum(
            1 for op in self.linked_operations
            if self.operation_hit_counts.get(op, 0) >= self.effective_targets.get(op, self.min_hits_per_op)
        )

    @property
    def ops_below_hits_target(self) -> dict[str, int]:
        """Linked operations below their effective target, with their current hit counts."""
        if self.linked_operations is None:
            return {}
        return {
            op: self.operation_hit_counts.get(op, 0)
            for op in self.linked_operations
            if self.operation_hit_counts.get(op, 0) < self.effective_targets.get(op, self.min_hits_per_op)
        }

    @property
    def min_linked_hits(self) -> int:
        """Lowest hit count among linked operations (0 if any uncovered)."""
        if not self.linked_operations:
            return 0
        return min(
            self.operation_hit_counts.get(op, 0)
            for op in self.linked_operations
        )

    @property
    def max_linked_hits(self) -> int:
        """Highest hit count among linked operations."""
        if not self.linked_operations:
            return 0
        return max(
            self.operation_hit_counts.get(op, 0)
            for op in self.linked_operations
        )


def _generate_chains_with_seed_walking(
    generator: "CaseGenerator",
    max_chains: int | None,
    max_steps: int,
    starting_seed: int | None,
    linked_operations: set[str] | None = None,
    all_operations: set[str] | None = None,
    min_hits_per_op: int = 1,
    min_coverage_pct: float = 100.0,
    max_achievable_hits: dict[str, int] | None = None,
) -> ChainGenerationResult:
    """Generate chains with coverage-guided seed walking.

    Seed walking continues until one of these conditions is met (checked in order):
    1. Coverage target met: min_coverage_pct of linked operations have >= min_hits_per_op
       hits in unique chains
    2. max_chains unique chain sequences accumulated (hard cap, if set)
    3. MAX_SEED_INCREMENTS seeds tried (hard safety limit)

    Coverage is the primary stopping criterion. The combination of min_hits_per_op
    and min_coverage_pct controls how deep the exploration goes:

        min_hits_per_op=1, min_coverage_pct=100  (default)
            Every linked operation in at least 1 chain. Typically 1-4 seeds.

        min_hits_per_op=5, min_coverage_pct=100
            Every linked operation in at least 5 unique chains. More seeds needed,
            but each operation gets tested with diverse inputs.

        min_hits_per_op=5, min_coverage_pct=80
            80% of linked operations in at least 5 chains. Tolerates hard-to-reach
            operations while ensuring most of the API is well-tested.

    Seed walking ONLY activates when a seed is explicitly provided. Without a seed,
    a single generation pass is performed.

    Args:
        generator: The CaseGenerator instance.
        max_chains: Maximum number of unique chains to accumulate. None = no limit
            (only coverage target and MAX_SEED_INCREMENTS apply).
        max_steps: Maximum steps per chain.
        starting_seed: The initial seed value, or None for non-deterministic.
        linked_operations: Set of operationIds that participate in links.
            When provided, enables coverage-guided stopping. Obtain from
            CaseGenerator.get_linked_operation_ids().
        all_operations: Set of ALL operationIds in the spec. Used to compute
            orphan operations (those not in linked_operations).
        min_hits_per_op: Minimum number of unique chains each linked operation must
            appear in before coverage is considered met. Default 1.
        min_coverage_pct: Percentage (0-100) of linked operations that must meet
            min_hits_per_op. Default 100 (all linked operations).

    Returns:
        ChainGenerationResult with chains, per-op hit counts, and stopping reason.
    """
    accumulated_chains: list["ChainCase"] = []
    seen_signatures: set[tuple[str, ...]] = set()
    seeds_used: list[int] = []
    operations_covered: set[str] = set()
    # Per-operation hit counts from deduplicated chains only.
    # A "hit" means the operation appears in a unique chain that will be executed.
    operation_hit_counts: dict[str, int] = {}

    # Compute orphans if both sets provided
    orphan_operations: set[str] | None = None
    if linked_operations is not None and all_operations is not None:
        orphan_operations = all_operations - linked_operations

    # Compute per-operation effective targets: min(achievable, requested).
    # When max_achievable_hits is None (enumeration capped out or not computed),
    # all operations use the flat min_hits_per_op as their target.
    # When max_achievable_hits IS computed, operations not in the dict have
    # 0 achievable hits (they don't appear in any chain signature), so their
    # effective target is 0 — they're immediately "met" and won't block stopping.
    def _effective_target(op: str) -> int:
        if max_achievable_hits is not None:
            return min(max_achievable_hits.get(op, 0), min_hits_per_op)
        return min_hits_per_op

    def _build_effective_targets() -> dict[str, int]:
        """Build the effective_targets dict for all linked operations."""
        if linked_operations is None:
            return {}
        return {op: _effective_target(op) for op in linked_operations}

    def _collect_coverage(chains: list["ChainCase"]) -> None:
        """Track which operations appear in the given chains (all, not just unique)."""
        for chain in chains:
            for step in chain.steps:
                operations_covered.add(step.request_template.operation_id)

    def _add_hits_from_chain(chain: "ChainCase") -> None:
        """Increment hit counts for each operation in a newly-added unique chain."""
        seen_ops_in_chain: set[str] = set()
        for step in chain.steps:
            op_id = step.request_template.operation_id
            # Count each operation once per chain, even if it appears multiple times
            # in the same chain (e.g., a chain that calls getUser twice)
            if op_id not in seen_ops_in_chain:
                seen_ops_in_chain.add(op_id)
                operation_hit_counts[op_id] = operation_hit_counts.get(op_id, 0) + 1

    def _coverage_target_met() -> bool:
        """Check if min_coverage_pct of linked operations have >= effective target hits."""
        if linked_operations is None:
            return False
        if not linked_operations:
            return True
        ops_meeting_target = sum(
            1 for op in linked_operations
            if operation_hit_counts.get(op, 0) >= _effective_target(op)
        )
        return (ops_meeting_target / len(linked_operations) * 100) >= min_coverage_pct

    def _make_result(stopped_reason: str, seeds_tried: int) -> ChainGenerationResult:
        return ChainGenerationResult(
            chains=accumulated_chains,
            seeds_used=seeds_used,
            operations_covered=operations_covered,
            operation_hit_counts=dict(operation_hit_counts),
            linked_operations=linked_operations,
            orphan_operations=orphan_operations,
            min_hits_per_op=min_hits_per_op,
            min_coverage_pct=min_coverage_pct,
            stopped_reason=stopped_reason,
            seeds_tried=seeds_tried,
            max_achievable_hits=max_achievable_hits,
            effective_targets=_build_effective_targets(),
        )

    # If no seed provided, do a single pass without seed walking
    if starting_seed is None:
        chains = generator.generate_chains(
            max_chains=max_chains or 20,
            max_steps=max_steps,
            seed=None,
        )
        accumulated_chains.extend(chains)
        _collect_coverage(chains)
        for chain in chains:
            _add_hits_from_chain(chain)
        return _make_result("no_seed", 0)

    # Seed walking: try incrementing seeds until coverage target met
    current_seed = starting_seed
    seeds_tried = 0
    stopped_reason = "max_seeds"

    while seeds_tried < MAX_SEED_INCREMENTS:
        seeds_tried += 1

        chains = generator.generate_chains(
            max_chains=max_chains or 20,
            max_steps=max_steps,
            seed=current_seed,
        )

        # Deduplicate and track hits from new unique chains
        seed_contributed = False
        new_chains_this_seed = 0

        for chain in chains:
            sig = _chain_signature(chain)
            if sig not in seen_signatures:
                seen_signatures.add(sig)
                accumulated_chains.append(chain)
                _add_hits_from_chain(chain)
                seed_contributed = True
                new_chains_this_seed += 1

        # Track binary coverage from ALL chains (including duplicates).
        # This is separate from _add_hits_from_chain because they serve
        # different purposes:
        # - operations_covered (binary set): "was this op seen at all?"
        #   Used by --ensure-coverage to know which ops need backfill.
        #   Counts ALL chains including duplicates.
        # - operation_hit_counts (per-op int): "how many unique chains
        #   include this op?" Used by --min-hits-per-op depth targeting.
        #   Only counts deduplicated chains.
        _collect_coverage(chains)

        if seed_contributed:
            seeds_used.append(current_seed)

        # Print progress showing coverage depth
        if linked_operations:
            total_linked = len(linked_operations)
            ops_at_target = sum(
                1 for op in linked_operations
                if operation_hit_counts.get(op, 0) >= _effective_target(op)
            )
            if min_hits_per_op > 1:
                # Show depth progress when user requested multiple hits.
                # When per-op capping is active, note how many ops are capped
                # so users understand why target was reached with fewer hits.
                capped_count = 0
                if max_achievable_hits is not None:
                    capped_count = sum(
                        1 for op in linked_operations
                        if max_achievable_hits.get(op, 0) < min_hits_per_op
                    )
                capped_note = f" ({capped_count} capped)" if capped_count > 0 else ""
                # When ops are capped, say "at target" not "at N+ hits" since
                # some ops meet their target at fewer than min_hits_per_op hits.
                target_label = "at target" if capped_count > 0 else f"at {min_hits_per_op}+ hits"
                if seeds_tried == 1:
                    print(f"  Seed {current_seed}: {len(accumulated_chains)} chains, "
                          f"{ops_at_target}/{total_linked} ops {target_label}{capped_note}")
                elif new_chains_this_seed > 0:
                    print(f"  Seed {current_seed}: +{new_chains_this_seed} new chains, "
                          f"{ops_at_target}/{total_linked} ops {target_label}{capped_note}")
            else:
                # Show simple coverage when min_hits_per_op is 1
                covered_count = len(operations_covered & linked_operations)
                if seeds_tried == 1:
                    print(f"  Seed {current_seed}: {len(accumulated_chains)} chains, "
                          f"{covered_count}/{total_linked} linked operations covered")
                elif new_chains_this_seed > 0:
                    print(f"  Seed {current_seed}: +{new_chains_this_seed} new chains, "
                          f"{covered_count}/{total_linked} linked operations covered")
        elif seeds_tried % 10 == 0:
            print(f"  Seed walking: tried {seeds_tried} seeds, "
                  f"{len(accumulated_chains)} unique chains found...")

        # Check stopping conditions in priority order:
        # 1. Coverage target met (primary goal)
        if _coverage_target_met():
            stopped_reason = "coverage_met"
            break
        # 2. Chain count reached (secondary limit, only if max_chains set)
        if max_chains is not None and len(accumulated_chains) >= max_chains:
            stopped_reason = "max_chains"
            break

        current_seed += 1

    # Print final summary line for seed walking
    if linked_operations and seeds_tried > 0:
        total_linked = len(linked_operations)
        # Use "at target" when per-op capping is active, since some ops
        # have effective targets below min_hits_per_op.
        has_capped = (max_achievable_hits is not None and any(
            max_achievable_hits.get(op, 0) < min_hits_per_op
            for op in linked_operations
        ))
        summary_label = "at target" if has_capped else f"at {min_hits_per_op}+ hits"
        if stopped_reason == "coverage_met":
            if min_hits_per_op > 1:
                ops_at_target = sum(
                    1 for op in linked_operations
                    if operation_hit_counts.get(op, 0) >= _effective_target(op)
                )
                print(f"  Coverage target met in {len(seeds_used)} seed(s) "
                      f"({len(accumulated_chains)} unique chains, "
                      f"{ops_at_target}/{total_linked} {summary_label})")
            else:
                print(f"  Full linked coverage in {len(seeds_used)} seed(s) "
                      f"({len(accumulated_chains)} unique chains)")
        else:
            ops_at_target = sum(
                1 for op in linked_operations
                if operation_hit_counts.get(op, 0) >= _effective_target(op)
            )
            print(f"  Stopped ({stopped_reason}): {ops_at_target}/{total_linked} ops "
                  f"{summary_label} after {seeds_tried} seeds")

    return _make_result(stopped_reason, seeds_tried if starting_seed is not None else 0)


def _run_graph_chains_generated(args: GraphChainsArgs) -> int:
    """Run graph-chains with --generated flag.

    Uses CaseGenerator to produce actual chains and shows which links were used.
    """
    from api_parity.case_generator import CaseGenerator, CaseGeneratorError

    # Validate inputs
    if args.max_chains < 1:
        print("Error: --max-chains must be >= 1", file=sys.stderr)
        return 1
    if args.max_steps < 1:
        print("Error: --max-steps must be >= 1", file=sys.stderr)
        return 1

    try:
        generator = CaseGenerator(args.spec, exclude_operations=args.exclude)
    except CaseGeneratorError as e:
        print(f"Error loading spec: {e}", file=sys.stderr)
        return 1

    # Get declared links for coverage comparison
    declared_links = _extract_declared_links(generator._raw_spec, set(args.exclude))

    # Compute linked operations for coverage-guided stopping
    excluded_set = set(args.exclude)
    linked_operations = generator.get_linked_operation_ids() - excluded_set
    all_operations = generator.get_all_operation_ids() - excluded_set

    # Generate chains with coverage-guided seed walking
    print(f"Generating chains (max_chains={args.max_chains}, max_steps={args.max_steps})...")
    gen_result = _generate_chains_with_seed_walking(
        generator=generator,
        max_chains=args.max_chains,
        max_steps=args.max_steps,
        starting_seed=args.seed,
        linked_operations=linked_operations,
        all_operations=all_operations,
    )
    chains = gen_result.chains

    # Report seed walking if it occurred
    if gen_result.seeds_used:
        if len(gen_result.seeds_used) == 1:
            print(f"Used seed: {gen_result.seeds_used[0]}")
        else:
            print(f"Seed walking: {len(gen_result.seeds_used)} seeds contributed unique chains "
                  f"(range: {gen_result.seeds_used[0]}-{gen_result.seeds_used[-1]})")

    if not chains:
        print("\nNo multi-step chains generated.")
        print("This may indicate the spec has no links or links form no valid paths.")
        return 0

    # Track which links were actually used
    used_links: set[tuple[str, str, str]] = set()  # (source_op, status_code, target_op)

    # Use coverage data from generation (already computed during seed walking)
    operations_called: set[str] = set(gen_result.operations_covered)

    # Output chains
    print()
    print(f"Generated Chains ({len(chains)} chains)")
    print("=" * 60)

    for i, chain in enumerate(chains, 1):
        ops = [step.request_template.operation_id for step in chain.steps]
        print(f"\n[Chain {i}] " + " -> ".join(ops))
        print(f"  Steps: {len(chain.steps)}")

        for step in chain.steps:
            step_num = step.step_index + 1
            op_id = step.request_template.operation_id
            method = step.request_template.method
            path = step.request_template.path_template

            print(f"  {step_num}. {op_id}: {method} {path}")

            # Show link info for steps after the first
            if step.step_index > 0:
                if step.link_source is not None:
                    link_name = step.link_source.get("link_name", "unknown")
                    status_code = step.link_source.get("status_code", "?")
                    source_op = step.link_source.get("source_operation", "?")
                    print(f"      via link: {link_name} ({status_code})")
                    used_links.add((source_op, str(status_code), op_id))
                else:
                    # No explicit link found - Schemathesis used inference or other mechanism
                    print("      via unknown link (not in spec)")

    # Output link coverage summary
    print()
    print("=" * 60)
    print("Link Coverage Summary")
    print("=" * 60)

    # Convert declared links to comparable format
    declared_set: set[tuple[str, str, str]] = set()
    for source_op, status_code, target_op, link_name in declared_links:
        declared_set.add((source_op, status_code, target_op))

    unused_links = declared_set - used_links

    print(f"Total declared links: {len(declared_set)}")
    print(f"Links actually used:  {len(used_links)}")
    print(f"Unused links:         {len(unused_links)}")

    if unused_links:
        print("\nUnused links (declared in spec but not traversed):")
        # Build a mapping to get link names for display
        link_names: dict[tuple[str, str, str], str] = {}
        for source_op, status_code, target_op, link_name in declared_links:
            link_names[(source_op, status_code, target_op)] = link_name

        for source_op, status_code, target_op in sorted(unused_links):
            link_name = link_names.get((source_op, status_code, target_op), "?")
            print(f"  {source_op} --({status_code})--> {target_op} [{link_name}]")

    # Output operation coverage summary - CRITICAL for api-parity core usage
    print()
    print("=" * 60)
    print("Operation Coverage Summary")
    print("=" * 60)

    # Get all operations from the spec (excluding any --exclude ops)
    all_operations = set(generator.get_all_operation_ids()) - set(args.exclude)
    uncovered_operations = all_operations - operations_called

    coverage_pct = (len(operations_called) / len(all_operations) * 100) if all_operations else 0

    print(f"Total operations in spec: {len(all_operations)}")
    print(f"Operations tested:        {len(operations_called)} ({coverage_pct:.0f}%)")
    print(f"Operations NEVER tested:  {len(uncovered_operations)} ({100 - coverage_pct:.0f}%)")

    if uncovered_operations:
        # Build operation info for better reporting
        op_info: dict[str, tuple[str, str]] = {}  # op_id -> (method, path)
        for op in generator.get_operations():
            op_info[op["operation_id"]] = (op["method"], op["path"])
        # Also include excluded operations that aren't in get_operations()
        for op_id in uncovered_operations:
            if op_id not in op_info:
                op_info[op_id] = ("?", "?")

        print("\n*** WARNING: The following operations are NEVER tested by chains ***")
        print("*** This is a critical issue - api-parity cannot verify these endpoints ***\n")
        for op_id in sorted(uncovered_operations):
            method, path = op_info.get(op_id, ("?", "?"))
            # Check if it has inbound links (someone can reach it)
            has_inbound = any(target == op_id for _, _, target, _ in declared_links)
            # Check if it has outbound links (it can start chains to others)
            has_outbound = any(source == op_id for source, _, _, _ in declared_links)

            if not has_inbound and not has_outbound:
                status = "(ORPHAN - no links)"
            elif not has_inbound:
                status = "(no inbound links - only reachable as entry point)"
            else:
                status = "(reachable but exploration didn't reach it)"
            print(f"  {op_id}: {method} {path} {status}")

        print("\nTo ensure complete API coverage, consider running:")
        print("  api-parity explore --stateful --ensure-coverage ...")
        print("This will run single-request tests on any operations not covered by chains.")

    return 0


def _extract_declared_links(
    spec: dict, exclude: set[str]
) -> list[tuple[str, str, str, str]]:
    """Extract all declared links from OpenAPI spec.

    Args:
        spec: Parsed OpenAPI specification dict.
        exclude: Set of operationIds to exclude.

    Returns:
        List of (source_op, status_code, target_op, link_name) tuples.
    """
    links: list[tuple[str, str, str, str]] = []

    paths = spec.get("paths", {})
    for path_item in paths.values():
        if not isinstance(path_item, dict):
            continue
        for method_or_key, operation in path_item.items():
            if not isinstance(operation, dict) or method_or_key.startswith("$"):
                continue

            source_op = operation.get("operationId")
            if not source_op or source_op in exclude:
                continue

            responses = operation.get("responses", {})
            for status_code, response_def in responses.items():
                if not isinstance(response_def, dict):
                    continue
                response_links = response_def.get("links", {})
                for link_name, link_def in response_links.items():
                    if not isinstance(link_def, dict):
                        continue
                    target_op = link_def.get("operationId") or link_def.get("operationRef")
                    if target_op and target_op not in exclude:
                        links.append((source_op, str(status_code), target_op, link_name))

    return links


def _extract_link_graph(
    schema: Any,  # schemathesis.OpenAPISchema, but not worth adding import for internal func
    exclude: list[str],
) -> tuple[dict[str, tuple[str, str]], list[tuple[str, str, str]]]:
    """Extract operations and links from OpenAPI spec.

    Args:
        schema: Loaded schemathesis schema (from schemathesis.openapi.from_path).
        exclude: List of operationIds to exclude.

    Returns:
        Tuple of (operations dict, edges list) where:
        - operations: {op_id: (method, path)}
        - edges: [(source_op, status_code, target_op), ...]
    """
    operations: dict[str, tuple[str, str]] = {}
    edges: list[tuple[str, str, str]] = []
    exclude_set = set(exclude)

    for result in schema.get_all_operations():
        op = result.ok()
        if op is None:
            continue

        raw = op.definition.raw
        operation_id = raw.get("operationId")
        if not operation_id:
            continue

        if operation_id in exclude_set:
            continue

        method = op.method.upper()
        path = op.path

        operations[operation_id] = (method, path)

        # Extract links from responses
        responses = raw.get("responses", {})
        for status_code, response_def in responses.items():
            if isinstance(response_def, dict) and "links" in response_def:
                for link_name, link_def in response_def["links"].items():
                    target_op = link_def.get("operationId") or link_def.get("operationRef")
                    if target_op and target_op not in exclude_set:
                        edges.append((operation_id, str(status_code), target_op))

    return operations, edges


def _format_mermaid_graph(
    operations: dict[str, tuple[str, str]], edges: list[tuple[str, str, str]]
) -> str:
    """Format link graph as Mermaid flowchart.

    Args:
        operations: {op_id: (method, path)}
        edges: [(source_op, status_code, target_op), ...]

    Returns:
        Mermaid flowchart string.
    """
    lines = ["flowchart LR"]

    # Track which operations have edges
    ops_with_outbound: set[str] = set()
    ops_with_inbound: set[str] = set()
    for source, _, target in edges:
        ops_with_outbound.add(source)
        ops_with_inbound.add(target)

    # Find orphans (no inbound AND no outbound links)
    orphans = set(operations.keys()) - ops_with_outbound - ops_with_inbound

    # Generate edge lines
    for source, status_code, target in edges:
        if source not in operations or target not in operations:
            continue
        source_method, source_path = operations[source]
        target_method, target_path = operations[target]
        source_node = _format_mermaid_node(source, source_method, source_path)
        target_node = _format_mermaid_node(target, target_method, target_path)
        lines.append(f"    {source_node} -->|{status_code}| {target_node}")

    # Generate orphan subgraph
    if orphans:
        lines.append("    subgraph orphans[ORPHANS - no links]")
        for op_id in sorted(orphans):
            if op_id in operations:
                method, path = operations[op_id]
                node = _format_mermaid_node(op_id, method, path)
                lines.append(f"        {node}")
        lines.append("    end")

    return "\n".join(lines)


def _format_mermaid_node(op_id: str, method: str, path: str) -> str:
    """Format a Mermaid node with operationId as ID and METHOD /path as label.

    Args:
        op_id: Operation ID (used as node ID).
        method: HTTP method.
        path: URL path with path params simplified.

    Returns:
        Mermaid node definition, e.g., createWidget[POST /widgets]
    """
    import re

    # Simplify path params: {param} -> param
    simplified_path = re.sub(r"\{([^}]+)\}", r"\1", path)
    return f'{op_id}[{method} {simplified_path}]'


def run_explore(args: ExploreArgs) -> int:
    """Run explore mode.

    Generates test cases from OpenAPI spec and compares responses between two targets.
    """
    from api_parity.artifact_writer import ArtifactWriter, RunStats
    from api_parity.case_generator import CaseGenerator, CaseGeneratorError
    from api_parity.cel_evaluator import CELEvaluator, CELSubprocessError
    from api_parity.comparator import Comparator
    from api_parity.config_loader import (
        ConfigError,
        get_operation_rules,
        load_comparison_library,
        load_comparison_rules,
        load_runtime_config,
        resolve_comparison_rules_path,
        validate_cli_operation_ids,
        validate_comparison_rules,
        validate_targets,
    )
    from api_parity.executor import Executor, RequestError
    from api_parity.models import TargetInfo
    from api_parity.schema_validator import SchemaValidator, SchemaExtractionError

    # Load configuration
    try:
        runtime_config = load_runtime_config(args.config)
    except ConfigError as e:
        print(f"Error loading config: {e}", file=sys.stderr)
        return 1

    # Validate targets
    try:
        target_a_config, target_b_config = validate_targets(
            runtime_config, args.target_a, args.target_b
        )
    except ConfigError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    # Load comparison rules (path is relative to config file, not CWD)
    try:
        rules_path = resolve_comparison_rules_path(args.config, runtime_config.comparison_rules)
        comparison_rules = load_comparison_rules(rules_path)
    except ConfigError as e:
        print(f"Error loading comparison rules: {e}", file=sys.stderr)
        print(f"  Check that 'comparison_rules' path in {args.config} is correct", file=sys.stderr)
        return 1

    # Load comparison library
    try:
        comparison_library = load_comparison_library()
    except ConfigError as e:
        print(f"Error loading comparison library: {e}", file=sys.stderr)
        return 1

    # Initialize case generator
    try:
        generator = CaseGenerator(args.spec, exclude_operations=args.exclude)
    except CaseGeneratorError as e:
        print(f"Error loading OpenAPI spec: {e}", file=sys.stderr)
        return 1

    # Validate mode - check config validity and cross-validate against spec
    if args.validate:
        print(f"Validating: spec={args.spec}, config={args.config}")
        print(f"  Targets: {args.target_a}, {args.target_b}")
        print(f"    {args.target_a}: {target_a_config.base_url}")
        print(f"    {args.target_b}: {target_b_config.base_url}")
        if args.exclude:
            print(f"  Excluding: {', '.join(args.exclude)}")

        # List operations that will be tested
        operations = generator.get_operations()
        print(f"  Operations: {len(operations)}")
        for op in operations:
            print(f"    {op['operation_id']} ({op['method']} {op['path']})")

        # Cross-validate comparison rules against spec and library
        print()
        print("Cross-validating configuration...")

        # Get all operationIds from spec (including excluded ones for validation)
        spec_operation_ids = generator.get_all_operation_ids()

        # Validate comparison rules
        rules_result = validate_comparison_rules(
            comparison_rules, comparison_library, spec_operation_ids
        )

        # Validate CLI operationIds
        cli_result = validate_cli_operation_ids(
            args.exclude, args.operation_timeout, spec_operation_ids
        )

        # Merge results
        rules_result.merge(cli_result)

        # Report warnings
        if rules_result.warnings:
            print()
            print("Warnings:")
            for warning in rules_result.warnings:
                print(f"  WARNING: {warning}")

        # Report errors
        if rules_result.errors:
            print()
            print("Errors:")
            for error in rules_result.errors:
                print(f"  ERROR: {error}")

        # Final result
        print()
        if rules_result.is_valid:
            if rules_result.warnings:
                print("Validation passed with warnings")
            else:
                print("Validation successful")
            return 0
        else:
            print("Validation failed")
            return 1

    # Validate --min-coverage range
    if args.min_coverage < 0 or args.min_coverage > 100:
        print("Error: --min-coverage must be between 0 and 100", file=sys.stderr)
        return 1

    # Warn if stateful flags used without --stateful
    if not args.stateful and args.max_chains is not None:
        print("Warning: --max-chains is ignored without --stateful", file=sys.stderr)
    if not args.stateful and args.log_chains:
        print("Warning: --log-chains is ignored without --stateful", file=sys.stderr)
    if not args.stateful and args.ensure_coverage:
        print("Warning: --ensure-coverage is ignored without --stateful", file=sys.stderr)
    if not args.stateful and args.min_hits_per_op != 1:
        print("Warning: --min-hits-per-op is ignored without --stateful", file=sys.stderr)
    if not args.stateful and args.min_coverage != 100:
        print("Warning: --min-coverage is ignored without --stateful", file=sys.stderr)

    # Warn if coverage depth flags used without --seed (seed walking required)
    if args.stateful and args.seed is None and args.min_hits_per_op > 1:
        print("Warning: --min-hits-per-op > 1 requires --seed for seed walking. "
              "Without --seed, only a single generation pass occurs.", file=sys.stderr)
    if args.stateful and args.seed is None and args.min_coverage < 100:
        print("Warning: --min-coverage requires --seed for seed walking. "
              "Without --seed, only a single generation pass occurs.", file=sys.stderr)

    # Print run configuration
    mode = "stateful" if args.stateful else "stateless"
    print(f"Explore mode ({mode}): spec={args.spec}")
    print(f"  Targets: {args.target_a} ({target_a_config.base_url}) vs {args.target_b} ({target_b_config.base_url})")
    print(f"  Output: {args.out}")
    if args.seed is not None:
        print(f"  Seed: {args.seed}")
    if args.stateful:
        if args.max_chains is not None:
            print(f"  Max chains: {args.max_chains}")
        elif args.min_hits_per_op > 1:
            print("  Max chains: unlimited (coverage depth target set)")
        else:
            print("  Max chains: 20 (default)")
        print(f"  Max steps per chain: {args.max_steps}")
        if args.min_hits_per_op > 1 or args.min_coverage != 100:
            print(f"  Coverage target: {args.min_coverage}% of linked ops at {args.min_hits_per_op}+ hits")
        if args.ensure_coverage:
            print("  Ensure coverage: enabled (will run single-request tests on uncovered operations)")
    if args.exclude:
        print(f"  Excluding: {', '.join(args.exclude)}")
    print(f"  Timeout: {args.timeout}s")
    if args.operation_timeout:
        for op_id, timeout in args.operation_timeout.items():
            print(f"  Timeout for {op_id}: {timeout}s")
    if runtime_config.rate_limit:
        print(f"  Rate limit: {runtime_config.rate_limit.requests_per_second} req/s")
    print()

    # Initialize components
    stats = RunStats()
    writer = ArtifactWriter(args.out, runtime_config.secrets)

    target_a_info = TargetInfo(name=args.target_a, base_url=target_a_config.base_url)
    target_b_info = TargetInfo(name=args.target_b, base_url=target_b_config.base_url)

    # Initialize schema validator for OpenAPI Spec as Field Authority
    try:
        schema_validator = SchemaValidator(args.spec)
    except SchemaExtractionError as e:
        print(f"Error loading schema for validation: {e}", file=sys.stderr)
        return 1

    # Start CEL evaluator
    try:
        cel_evaluator = CELEvaluator()
    except CELSubprocessError as e:
        print(f"Error starting CEL evaluator: {e}", file=sys.stderr)
        return 1

    progress_reporter: ProgressReporter | None = None

    try:
        comparator = Comparator(cel_evaluator, comparison_library, schema_validator)

        # Start executor
        requests_per_second = (
            runtime_config.rate_limit.requests_per_second
            if runtime_config.rate_limit
            else None
        )

        # Create progress reporter
        # For stateless: total unknown (no max_cases limit)
        # For stateful: total is set after chains are generated
        progress_unit = "chains" if args.stateful else "cases"
        progress_total = None  # Unknown for both modes until generation completes
        progress_reporter = ProgressReporter(total=progress_total, unit=progress_unit)
        progress_reporter.start()

        with Executor(
            target_a_config,
            target_b_config,
            default_timeout=args.timeout,
            operation_timeouts=args.operation_timeout,
            link_fields=generator.get_link_fields(),
            requests_per_second=requests_per_second,
        ) as executor:

            if args.stateful:
                # Stateful chain testing
                _run_stateful_explore(
                    generator=generator,
                    executor=executor,
                    comparator=comparator,
                    comparison_rules=comparison_rules,
                    writer=writer,
                    stats=stats,
                    target_a_info=target_a_info,
                    target_b_info=target_b_info,
                    max_chains=args.max_chains,
                    max_steps=args.max_steps,
                    seed=args.seed,
                    get_operation_rules=get_operation_rules,
                    progress_reporter=progress_reporter,
                    log_chains=args.log_chains,
                    ensure_coverage=args.ensure_coverage,
                    exclude=args.exclude,
                    min_hits_per_op=args.min_hits_per_op,
                    min_coverage=args.min_coverage,
                )
            else:
                # Stateless testing
                _run_stateless_explore(
                    generator=generator,
                    executor=executor,
                    comparator=comparator,
                    comparison_rules=comparison_rules,
                    writer=writer,
                    stats=stats,
                    target_a_info=target_a_info,
                    target_b_info=target_b_info,
                    seed=args.seed,
                    get_operation_rules=get_operation_rules,
                    progress_reporter=progress_reporter,
                )

    except CELSubprocessError as e:
        print(f"\nFatal: CEL evaluator crashed: {e}", file=sys.stderr)
        if progress_reporter is not None:
            progress_reporter.stop()
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted by user")
        stats.interrupted = True
    finally:
        if progress_reporter is not None:
            progress_reporter.stop()
        cel_evaluator.close()

    # Write summary (includes any mismatches found before interrupt)
    writer.write_summary(stats, seed=args.seed)

    # Print summary
    print()
    print("=" * 60)
    if args.stateful:
        print(f"Total chains: {stats.total_chains}")
        print(f"  Matches:    {stats.chain_matches}")
        print(f"  Mismatches: {stats.chain_mismatches}")
        print(f"  Errors:     {stats.chain_errors}")
    else:
        print(f"Total cases: {stats.total_cases}")
        print(f"  Matches:    {stats.matches}")
        print(f"  Mismatches: {stats.mismatches}")
        print(f"  Errors:     {stats.errors}")
    print(f"Summary written to: {args.out / 'summary.json'}")

    return 0


def _run_stateless_explore(
    generator: CaseGenerator,
    executor: Executor,
    comparator: Comparator,
    comparison_rules: ComparisonRules,
    writer: ArtifactWriter,
    stats: RunStats,
    target_a_info: TargetInfo,
    target_b_info: TargetInfo,
    seed: int | None,
    get_operation_rules: Callable[[ComparisonRules, str], Any],
    progress_reporter: ProgressReporter | None = None,
) -> None:
    """Execute stateless (single-request) testing."""
    from api_parity.executor import RequestError

    for case in generator.generate(seed=seed):
        stats.total_cases += 1
        stats.add_operation(case.operation_id)

        print(f"[{stats.total_cases}] {case.operation_id}: {case.method} {case.rendered_path}", end=" ")

        try:
            # Execute request against both targets
            response_a, response_b = executor.execute(case)

            # Get rules for this operation
            rules = get_operation_rules(comparison_rules, case.operation_id)

            # Compare responses (with operation_id for schema validation)
            result = comparator.compare(response_a, response_b, rules, case.operation_id)

            if result.match:
                stats.matches += 1
                print("MATCH")
            else:
                stats.mismatches += 1
                print(f"MISMATCH: {result.summary}")

                # Write mismatch bundle
                bundle_path = writer.write_mismatch(
                    case=case,
                    response_a=response_a,
                    response_b=response_b,
                    diff=result,
                    target_a_info=target_a_info,
                    target_b_info=target_b_info,
                    seed=seed,
                )
                print(f"         Bundle: {bundle_path}")

        except RequestError as e:
            stats.errors += 1
            print(f"ERROR: {e}")

        # Update progress reporter
        if progress_reporter is not None:
            progress_reporter.increment()


def _run_stateful_explore(
    generator: CaseGenerator,
    executor: Executor,
    comparator: Comparator,
    comparison_rules: ComparisonRules,
    writer: ArtifactWriter,
    stats: RunStats,
    target_a_info: TargetInfo,
    target_b_info: TargetInfo,
    max_chains: int | None,
    max_steps: int,
    seed: int | None,
    get_operation_rules: Callable[[ComparisonRules, str], Any],
    progress_reporter: ProgressReporter | None = None,
    log_chains: bool = False,
    ensure_coverage: bool = False,
    exclude: list[str] | None = None,
    min_hits_per_op: int = 1,
    min_coverage: int = 100,
) -> None:
    """Execute stateful chain testing with coverage-guided seed walking.

    Seed walking stops when the coverage target is met (or max_chains /
    max_seeds is hit). The coverage target is: min_coverage% of linked
    operations must appear in at least min_hits_per_op unique chains.

    If ensure_coverage=True, also runs single-request tests on any operations
    that weren't covered by the generated chains (orphans).
    """
    from api_parity.executor import RequestError

    # Compute linked and all operations for coverage-guided stopping
    excluded_set = set(exclude or [])
    linked_operations = generator.get_linked_operation_ids() - excluded_set
    all_operations = generator.get_all_operation_ids() - excluded_set

    # Compute per-operation achievable hit limits from link graph structure.
    # This enables smart stopping: seed walking stops when each operation
    # has hit min(achievable, requested) instead of grinding through 100
    # seeds for operations that can never reach the requested target.
    link_edges = generator.get_link_edges()
    max_achievable = _compute_max_achievable_hits(link_edges, linked_operations, max_steps)

    # Warn if any operations are capped below the requested min_hits_per_op
    if max_achievable is not None and min_hits_per_op > 1:
        capped_ops = {
            op: achievable
            for op, achievable in max_achievable.items()
            if op in linked_operations and achievable < min_hits_per_op
        }
        if capped_ops:
            print(f"Note: {len(capped_ops)} operation(s) can appear in fewer than "
                  f"{min_hits_per_op} unique chain structures:")
            for op, achievable in sorted(capped_ops.items(), key=lambda x: x[1]):
                print(f"  {op}: max {achievable} unique chains (target: {min_hits_per_op})")
            print(f"  Using effective target: min(achievable, {min_hits_per_op}) per operation")

    # Generate chains with coverage-guided seed walking.
    # When min_hits_per_op > 1 and no explicit --max-chains, use unlimited chains
    # so seed walking continues until the coverage depth target is met.
    # When min_hits_per_op is 1 (default), fall back to max_chains or 20.
    if max_chains is not None:
        effective_max_chains = max_chains
    elif min_hits_per_op > 1:
        effective_max_chains = None  # Unlimited — let coverage target drive stopping
    else:
        effective_max_chains = 20  # Legacy default
    print("Generating chains...")
    gen_result = _generate_chains_with_seed_walking(
        generator=generator,
        max_chains=effective_max_chains,
        max_steps=max_steps,
        starting_seed=seed,
        linked_operations=linked_operations,
        all_operations=all_operations,
        min_hits_per_op=min_hits_per_op,
        min_coverage_pct=float(min_coverage),
        max_achievable_hits=max_achievable,
    )
    chains = gen_result.chains

    # Report generation results
    if gen_result.seeds_used:
        if len(gen_result.seeds_used) == 1:
            print(f"Generated {len(chains)} chains with multiple steps (seed: {gen_result.seeds_used[0]})")
        else:
            print(f"Generated {len(chains)} chains with multiple steps")
            print(f"Seed walking: {len(gen_result.seeds_used)} seeds contributed unique chains "
                  f"(range: {gen_result.seeds_used[0]}-{gen_result.seeds_used[-1]})")
    else:
        print(f"Generated {len(chains)} chains with multiple steps")

    # Print coverage summary after generation
    if linked_operations:
        covered = gen_result.linked_covered_count
        total = gen_result.linked_total_count
        pct = (covered / total * 100) if total > 0 else 100
        print(f"Linked operation coverage: {covered}/{total} ({pct:.0f}%)")
        if gen_result.linked_uncovered:
            print(f"  Not covered by chains: {sorted(gen_result.linked_uncovered)}")
        if gen_result.orphan_operations:
            print(f"  Orphan operations (no links, need --ensure-coverage): "
                  f"{sorted(gen_result.orphan_operations)}")
        # Show per-operation hit depth when min_hits_per_op > 1
        if min_hits_per_op > 1:
            met = gen_result.ops_meeting_hits_target
            print(f"  Depth target ({min_hits_per_op}+ hits): "
                  f"{met}/{total} ops met target")
            if gen_result.min_linked_hits != gen_result.max_linked_hits:
                print(f"  Hit range: {gen_result.min_linked_hits}-{gen_result.max_linked_hits} "
                      f"hits per linked operation")
            below = gen_result.ops_below_hits_target
            if below:
                sorted_below = sorted(below.items(), key=lambda x: x[1])
                for op, hits in sorted_below[:5]:  # Show up to 5 worst
                    effective = gen_result.effective_targets.get(op, min_hits_per_op)
                    print(f"    {op}: {hits}/{effective} hits")
                if len(sorted_below) > 5:
                    print(f"    ... and {len(sorted_below) - 5} more")
    print()

    # Use coverage data from generation (already computed during seed walking)
    operations_covered_by_chains: set[str] = set(gen_result.operations_covered)

    # Update progress reporter with total now that we know it
    if progress_reporter is not None:
        progress_reporter.set_total(len(chains))

    # Track chains and outcomes for --log-chains
    executed_chains: list = []
    chain_outcomes: list[str] = []

    for chain in chains:
        stats.total_chains += 1

        # Build chain description
        ops = [step.request_template.operation_id for step in chain.steps]
        chain_desc = " → ".join(ops)
        print(f"[Chain {stats.total_chains}] {chain_desc}")

        try:
            # Track comparison results as we execute.
            # We use a callback (on_step) instead of post-execution comparison because:
            # 1. Chains should stop at first mismatch to avoid wasting requests
            # 2. Later steps may depend on earlier responses (variable extraction)
            # 3. Executor owns response lifecycle; callback lets us compare before cleanup
            step_diffs: list[ComparisonResult] = []
            step_ops: list[str] = []
            mismatch_found = False

            def on_step(response_a, response_b) -> bool:
                """Compare responses after each step; return False to stop on mismatch."""
                nonlocal mismatch_found
                step_idx = len(step_diffs)
                op_id = chain.steps[step_idx].request_template.operation_id
                step_ops.append(op_id)

                rules = get_operation_rules(comparison_rules, op_id)
                result = comparator.compare(response_a, response_b, rules, op_id)
                step_diffs.append(result)

                if not result.match:
                    mismatch_found = True
                    return False  # Stop chain execution
                return True  # Continue

            # Execute chain, stopping at first mismatch
            execution_a, execution_b = executor.execute_chain(chain, on_step=on_step)

            # Determine overall result
            if not mismatch_found:
                stats.chain_matches += 1
                print("  MATCH (all steps)")
                executed_chains.append(chain)
                chain_outcomes.append("match")
            else:
                stats.chain_mismatches += 1
                mismatch_step = len(step_diffs) - 1
                mismatch_op = step_ops[mismatch_step]
                print(f"  MISMATCH at step {mismatch_step} ({mismatch_op}): {step_diffs[mismatch_step].summary}")
                executed_chains.append(chain)
                chain_outcomes.append("mismatch")

                # Write chain mismatch bundle
                bundle_path = writer.write_chain_mismatch(
                    chain=chain,
                    execution_a=execution_a,
                    execution_b=execution_b,
                    step_diffs=step_diffs,
                    mismatch_step=mismatch_step,
                    target_a_info=target_a_info,
                    target_b_info=target_b_info,
                    seed=seed,
                )
                print(f"  Bundle: {bundle_path}")

        except RequestError as e:
            stats.chain_errors += 1
            print(f"  ERROR: {e}")
            executed_chains.append(chain)
            chain_outcomes.append("error")

        # Update progress reporter
        if progress_reporter is not None:
            progress_reporter.increment()

    # Write chains log if requested
    if log_chains and executed_chains:
        chains_path = writer.write_chains_log(
            chains=executed_chains,
            outcomes=chain_outcomes,
            max_chains=max_chains,
            max_steps=max_steps,
        )
        print(f"\nChains log written to: {chains_path}")

    # Ensure coverage: run single-request tests on uncovered operations
    if ensure_coverage:
        # Exclude explicitly excluded operations from coverage tracking
        excluded_set = set(exclude or [])
        all_operations = set(generator.get_all_operation_ids()) - excluded_set
        uncovered_operations = all_operations - operations_covered_by_chains

        if uncovered_operations:
            print()
            print("=" * 60)
            print("Coverage Gap: Testing Uncovered Operations")
            print("=" * 60)
            print(f"Chains covered {len(operations_covered_by_chains)}/{len(all_operations)} operations")
            print(f"Running single-request tests on {len(uncovered_operations)} uncovered operations:")
            for op_id in sorted(uncovered_operations):
                print(f"  - {op_id}")
            print()

            # Generate single-request tests for uncovered operations
            # Use a small number of cases per operation for coverage (not exhaustive fuzzing)
            cases_per_op = 3  # Just enough to verify the endpoint works
            coverage_case_count = 0
            op_coverage_counts: dict[str, int] = {}

            for case in generator.generate(max_cases=len(uncovered_operations) * cases_per_op * 2, seed=seed):
                if case.operation_id not in uncovered_operations:
                    continue  # Skip operations already covered by chains

                # Limit to a few cases per uncovered operation
                current_count = op_coverage_counts.get(case.operation_id, 0)
                if current_count >= cases_per_op:
                    continue
                op_coverage_counts[case.operation_id] = current_count + 1

                coverage_case_count += 1
                # Mark as covered for tracking
                operations_covered_by_chains.add(case.operation_id)

                print(f"[Coverage {coverage_case_count}] {case.operation_id}: {case.method} {case.rendered_path}")

                try:
                    # Execute against both targets
                    response_a, response_b = executor.execute(case)

                    # Compare responses
                    rules = get_operation_rules(comparison_rules, case.operation_id)
                    result = comparator.compare(response_a, response_b, rules, case.operation_id)

                    if result.match:
                        stats.total_cases += 1
                        stats.matches += 1
                        print("  MATCH")
                    else:
                        stats.total_cases += 1
                        stats.mismatches += 1
                        print(f"  MISMATCH: {result.summary}")

                        # Write mismatch bundle
                        bundle_path = writer.write_mismatch(
                            case=case,
                            response_a=response_a,
                            response_b=response_b,
                            diff=result,
                            target_a_info=target_a_info,
                            target_b_info=target_b_info,
                            seed=seed,
                        )
                        print(f"  Bundle: {bundle_path}")

                except RequestError as e:
                    stats.total_cases += 1
                    stats.errors += 1
                    print(f"  ERROR: {e}")

            # Report final coverage
            still_uncovered = all_operations - operations_covered_by_chains
            if still_uncovered:
                print()
                print(f"Warning: {len(still_uncovered)} operations still uncovered after coverage tests:")
                for op_id in sorted(still_uncovered):
                    print(f"  - {op_id}")
                print("These operations may have generation constraints preventing test creation.")
            else:
                print()
                print(f"Coverage complete: all {len(all_operations)} operations tested.")
        else:
            print()
            print(f"Coverage complete: chains covered all {len(all_operations)} operations.")


def run_replay(args: ReplayArgs) -> int:
    """Run replay mode.

    Re-executes previously discovered mismatches to determine if they still occur,
    have been fixed, or have changed.
    """
    from api_parity.artifact_writer import ArtifactWriter, ReplayStats
    from api_parity.bundle_loader import (
        BundleLoadError,
        BundleType,
        LoadedBundle,
        discover_bundles,
        extract_link_fields_from_chain,
        load_bundle,
    )
    from api_parity.case_generator import LinkFields
    from api_parity.cel_evaluator import CELEvaluator, CELSubprocessError
    from api_parity.comparator import Comparator
    from api_parity.config_loader import (
        ConfigError,
        get_operation_rules,
        load_comparison_library,
        load_comparison_rules,
        load_runtime_config,
        resolve_comparison_rules_path,
        validate_targets,
    )
    from api_parity.executor import Executor, RequestError
    from api_parity.models import ComparisonResult, TargetInfo

    # Validate input directory exists
    if not args.input_dir.exists():
        print(f"Error: Input directory does not exist: {args.input_dir}", file=sys.stderr)
        return 1
    if not args.input_dir.is_dir():
        print(f"Error: Input path is not a directory: {args.input_dir}", file=sys.stderr)
        return 1

    # Load configuration
    try:
        runtime_config = load_runtime_config(args.config)
    except ConfigError as e:
        print(f"Error loading config: {e}", file=sys.stderr)
        return 1

    # Validate targets
    try:
        target_a_config, target_b_config = validate_targets(
            runtime_config, args.target_a, args.target_b
        )
    except ConfigError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    # Load comparison rules (path is relative to config file, not CWD)
    try:
        rules_path = resolve_comparison_rules_path(args.config, runtime_config.comparison_rules)
        comparison_rules = load_comparison_rules(rules_path)
    except ConfigError as e:
        print(f"Error loading comparison rules: {e}", file=sys.stderr)
        print(f"  Check that 'comparison_rules' path in {args.config} is correct", file=sys.stderr)
        return 1

    # Load comparison library
    try:
        comparison_library = load_comparison_library()
    except ConfigError as e:
        print(f"Error loading comparison library: {e}", file=sys.stderr)
        return 1

    # Discover bundles
    bundles = discover_bundles(args.input_dir)

    # Validate mode - just check config validity without executing
    if args.validate:
        print(f"Validating: config={args.config}")
        print(f"  Targets: {args.target_a}, {args.target_b}")
        print(f"    {args.target_a}: {target_a_config.base_url}")
        print(f"    {args.target_b}: {target_b_config.base_url}")
        print(f"  Input: {args.input_dir}")
        print(f"  Bundles found: {len(bundles)}")

        # Validate each bundle can be loaded
        valid_bundles = 0
        invalid_bundles = 0
        for bundle_path in bundles:
            try:
                load_bundle(bundle_path)
                valid_bundles += 1
            except BundleLoadError as e:
                print(f"    Invalid bundle: {bundle_path.name} - {e}", file=sys.stderr)
                invalid_bundles += 1

        print(f"    Valid: {valid_bundles}")
        if invalid_bundles > 0:
            print(f"    Invalid: {invalid_bundles}")

        if invalid_bundles > 0:
            print("Validation failed: some bundles are invalid")
            return 1

        print("Validation successful")
        return 0

    if not bundles:
        print(f"No mismatch bundles found in {args.input_dir}")
        return 0

    # Pre-load all bundles to extract link_fields for chain replay.
    # We need link_fields before creating the Executor because the Executor uses them
    # to extract variables from responses for subsequent chain steps.
    loaded_bundles: list[LoadedBundle] = []
    link_fields = LinkFields()
    load_errors: list[tuple[Path, str]] = []
    # Track seen headers to avoid duplicates - headers are referenced by (name, index)
    # and multiple bundles may use the same header reference.
    seen_headers: set[tuple[str, int | None]] = set()

    for bundle_path in bundles:
        try:
            bundle = load_bundle(bundle_path)
            loaded_bundles.append(bundle)
            # Extract link_fields from chain bundles for variable extraction
            if bundle.bundle_type == BundleType.CHAIN and bundle.chain_case is not None:
                chain_link_fields = extract_link_fields_from_chain(bundle.chain_case)
                link_fields.body_pointers.update(chain_link_fields.body_pointers)
                # Deduplicate headers when merging
                for header_ref in chain_link_fields.headers:
                    key = (header_ref.name, header_ref.index)
                    if key not in seen_headers:
                        link_fields.headers.append(header_ref)
                        seen_headers.add(key)
        except BundleLoadError as e:
            load_errors.append((bundle_path, str(e)))

    # Print run configuration
    print(f"Replay mode: config={args.config}")
    print(f"  Targets: {args.target_a} ({target_a_config.base_url}) vs {args.target_b} ({target_b_config.base_url})")
    print(f"  Input: {args.input_dir}")
    print(f"  Output: {args.out}")
    print(f"  Bundles to replay: {len(loaded_bundles)}")
    if load_errors:
        print(f"  Failed to load: {len(load_errors)}")
    print(f"  Timeout: {args.timeout}s")
    if args.operation_timeout:
        for op_id, timeout in args.operation_timeout.items():
            print(f"  Timeout for {op_id}: {timeout}s")
    if runtime_config.rate_limit:
        print(f"  Rate limit: {runtime_config.rate_limit.requests_per_second} req/s")
    print()

    # Report load errors
    for bundle_path, error in load_errors:
        print(f"SKIP: {bundle_path.name} - {error}")

    if not loaded_bundles:
        print("No valid bundles to replay")
        return 0

    # Initialize components
    stats = ReplayStats()
    stats.skipped = len(load_errors)
    writer = ArtifactWriter(args.out, runtime_config.secrets)

    target_a_info = TargetInfo(name=args.target_a, base_url=target_a_config.base_url)
    target_b_info = TargetInfo(name=args.target_b, base_url=target_b_config.base_url)

    # Start CEL evaluator
    try:
        cel_evaluator = CELEvaluator()
    except CELSubprocessError as e:
        print(f"Error starting CEL evaluator: {e}", file=sys.stderr)
        return 1

    progress_reporter: ProgressReporter | None = None

    try:
        comparator = Comparator(cel_evaluator, comparison_library)

        # Start executor with link_fields for chain variable extraction
        requests_per_second = (
            runtime_config.rate_limit.requests_per_second
            if runtime_config.rate_limit
            else None
        )

        # Create progress reporter for replay
        progress_reporter = ProgressReporter(total=len(loaded_bundles), unit="bundles")
        progress_reporter.start()

        # Check if we have any link fields to extract (either body or headers)
        has_link_fields = link_fields.body_pointers or link_fields.headers

        with Executor(
            target_a_config,
            target_b_config,
            default_timeout=args.timeout,
            operation_timeouts=args.operation_timeout,
            link_fields=link_fields if has_link_fields else None,
            requests_per_second=requests_per_second,
        ) as executor:

            # Replay each pre-loaded bundle
            for bundle in loaded_bundles:
                _replay_loaded_bundle(
                    bundle=bundle,
                    executor=executor,
                    comparator=comparator,
                    comparison_rules=comparison_rules,
                    writer=writer,
                    stats=stats,
                    target_a_info=target_a_info,
                    target_b_info=target_b_info,
                    get_operation_rules=get_operation_rules,
                )
                progress_reporter.increment()

    except CELSubprocessError as e:
        print(f"\nFatal: CEL evaluator crashed: {e}", file=sys.stderr)
        if progress_reporter is not None:
            progress_reporter.stop()
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted by user")
        stats.interrupted = True
    finally:
        if progress_reporter is not None:
            progress_reporter.stop()
        cel_evaluator.close()

    # Write summary (includes results from before interrupt)
    writer.write_replay_summary(stats, args.input_dir)

    # Print summary
    print()
    print("=" * 60)
    print(f"Total bundles: {stats.total_bundles}")
    print(f"  Fixed (now match):     {stats.now_match}")
    print(f"  Still mismatch:        {stats.still_mismatch}")
    print(f"  Different mismatch:    {stats.different_mismatch}")
    print(f"  Errors:                {stats.errors}")
    if stats.skipped > 0:
        print(f"  Skipped:               {stats.skipped}")
    print(f"Summary written to: {args.out / 'replay_summary.json'}")

    if stats.now_match > 0:
        print(f"\nFixed bundles:")
        for name in stats.fixed_bundles:
            print(f"  {name}")

    return 0


def _replay_loaded_bundle(
    bundle: "LoadedBundle",
    executor: "Executor",
    comparator: "Comparator",
    comparison_rules: "ComparisonRules",
    writer: "ArtifactWriter",
    stats: "ReplayStats",
    target_a_info: "TargetInfo",
    target_b_info: "TargetInfo",
    get_operation_rules: Callable,
) -> None:
    """Replay a pre-loaded mismatch bundle."""
    from api_parity.bundle_loader import BundleType

    stats.total_bundles += 1

    # Execute based on type
    if bundle.bundle_type == BundleType.STATELESS:
        stats.stateless_bundles += 1
        _replay_stateless_bundle(
            bundle=bundle,
            executor=executor,
            comparator=comparator,
            comparison_rules=comparison_rules,
            writer=writer,
            stats=stats,
            target_a_info=target_a_info,
            target_b_info=target_b_info,
            get_operation_rules=get_operation_rules,
        )
    else:
        stats.chain_bundles += 1
        _replay_chain_bundle(
            bundle=bundle,
            executor=executor,
            comparator=comparator,
            comparison_rules=comparison_rules,
            writer=writer,
            stats=stats,
            target_a_info=target_a_info,
            target_b_info=target_b_info,
            get_operation_rules=get_operation_rules,
        )


def _replay_stateless_bundle(
    bundle: "LoadedBundle",
    executor: "Executor",
    comparator: "Comparator",
    comparison_rules: "ComparisonRules",
    writer: "ArtifactWriter",
    stats: "ReplayStats",
    target_a_info: "TargetInfo",
    target_b_info: "TargetInfo",
    get_operation_rules: Callable,
) -> None:
    """Replay a stateless (single-request) bundle."""
    from api_parity.executor import RequestError

    case = bundle.request_case
    if case is None:
        stats.errors += 1
        print(f"ERROR: {bundle.bundle_path.name} - missing request_case")
        return

    print(f"[{stats.total_bundles}] {case.operation_id}: {case.method} {case.rendered_path}", end=" ")

    try:
        # Re-execute
        response_a, response_b = executor.execute(case)

        # Compare (operation_id passed for consistency, but replay has no schema validation)
        rules = get_operation_rules(comparison_rules, case.operation_id)
        result = comparator.compare(response_a, response_b, rules, case.operation_id)

        # Classify outcome
        if result.match:
            stats.now_match += 1
            stats.fixed_bundles.append(bundle.bundle_path.name)
            print("FIXED")
        elif _is_same_mismatch(bundle.original_diff, result):
            stats.still_mismatch += 1
            stats.persistent_bundles.append(bundle.bundle_path.name)
            print(f"STILL MISMATCH: {result.summary}")
            # Write new bundle to output
            writer.write_mismatch(
                case=case,
                response_a=response_a,
                response_b=response_b,
                diff=result,
                target_a_info=target_a_info,
                target_b_info=target_b_info,
            )
        else:
            stats.different_mismatch += 1
            stats.changed_bundles.append(bundle.bundle_path.name)
            print(f"DIFFERENT MISMATCH: {result.summary}")
            # Write new bundle to output
            writer.write_mismatch(
                case=case,
                response_a=response_a,
                response_b=response_b,
                diff=result,
                target_a_info=target_a_info,
                target_b_info=target_b_info,
            )

    except RequestError as e:
        stats.errors += 1
        print(f"ERROR: {e}")


def _replay_chain_bundle(
    bundle: "LoadedBundle",
    executor: "Executor",
    comparator: "Comparator",
    comparison_rules: "ComparisonRules",
    writer: "ArtifactWriter",
    stats: "ReplayStats",
    target_a_info: "TargetInfo",
    target_b_info: "TargetInfo",
    get_operation_rules: Callable,
) -> None:
    """Replay a chain (stateful) bundle."""
    from api_parity.executor import RequestError

    chain = bundle.chain_case
    if chain is None:
        stats.errors += 1
        print(f"ERROR: {bundle.bundle_path.name} - missing chain_case")
        return

    ops = [step.request_template.operation_id for step in chain.steps]
    chain_desc = " -> ".join(ops)
    print(f"[Chain {stats.total_bundles}] {chain_desc}")

    try:
        # Track comparison results during execution.
        # Same callback pattern as _run_stateful_explore - see comments there.
        step_diffs: list[ComparisonResult] = []
        mismatch_found = False

        def on_step(response_a, response_b) -> bool:
            nonlocal mismatch_found
            step_idx = len(step_diffs)
            op_id = chain.steps[step_idx].request_template.operation_id

            rules = get_operation_rules(comparison_rules, op_id)
            result = comparator.compare(response_a, response_b, rules, op_id)
            step_diffs.append(result)

            if not result.match:
                mismatch_found = True
                return False  # Stop chain execution
            return True

        # Execute chain
        execution_a, execution_b = executor.execute_chain(chain, on_step=on_step)

        # Classify outcome
        if not mismatch_found:
            stats.now_match += 1
            stats.fixed_bundles.append(bundle.bundle_path.name)
            print("  FIXED (all steps)")
        elif _is_same_chain_mismatch(bundle.original_diff, step_diffs):
            stats.still_mismatch += 1
            stats.persistent_bundles.append(bundle.bundle_path.name)
            mismatch_step = len(step_diffs) - 1
            print(f"  STILL MISMATCH at step {mismatch_step}: {step_diffs[mismatch_step].summary}")
            # Write new bundle to output
            writer.write_chain_mismatch(
                chain=chain,
                execution_a=execution_a,
                execution_b=execution_b,
                step_diffs=step_diffs,
                mismatch_step=mismatch_step,
                target_a_info=target_a_info,
                target_b_info=target_b_info,
            )
        else:
            stats.different_mismatch += 1
            stats.changed_bundles.append(bundle.bundle_path.name)
            mismatch_step = len(step_diffs) - 1
            print(f"  DIFFERENT MISMATCH at step {mismatch_step}: {step_diffs[mismatch_step].summary}")
            # Write new bundle to output
            writer.write_chain_mismatch(
                chain=chain,
                execution_a=execution_a,
                execution_b=execution_b,
                step_diffs=step_diffs,
                mismatch_step=mismatch_step,
                target_a_info=target_a_info,
                target_b_info=target_b_info,
            )

    except RequestError as e:
        stats.errors += 1
        print(f"  ERROR: {e}")


def _is_same_mismatch(original_diff: dict, new_result: "ComparisonResult") -> bool:
    """Determine if two mismatches are essentially the same failure pattern.

    Why pattern matching instead of exact value comparison:
    - Values change between runs (timestamps, IDs, etc.)
    - We care about "still failing at the same place" not "exact same failure"
    - DIFFERENT MISMATCH after rule changes is expected and useful to track

    Comparison strategy by mismatch_type:
    - status_code: Type match is sufficient (specific codes may vary legitimately)
    - headers: Same header names must fail (values ignored)
    - body: Same JSONPath fields must fail (values ignored)
    """
    # Get original mismatch type
    original_type = original_diff.get("mismatch_type")
    new_type = new_result.mismatch_type.value if new_result.mismatch_type else None

    if original_type != new_type:
        return False

    # For body mismatches, check if same paths failed
    if original_type == "body":
        original_details = original_diff.get("details", {})
        original_body = original_details.get("body", {})
        original_differences = original_body.get("differences", [])
        original_paths = {d.get("path") for d in original_differences}

        new_body = new_result.details.get("body")
        new_paths = {d.path for d in new_body.differences} if new_body else set()

        return original_paths == new_paths

    # For header mismatches, check if same header names failed
    if original_type == "headers":
        original_details = original_diff.get("details", {})
        original_headers = original_details.get("headers", {})
        original_differences = original_headers.get("differences", [])
        original_names = {d.get("header") for d in original_differences}

        new_headers = new_result.details.get("headers")
        new_names = {d.header for d in new_headers.differences} if new_headers else set()

        return original_names == new_names

    # For status_code mismatches, mismatch_type being the same is sufficient
    return True


def _is_same_chain_mismatch(
    original_diff: dict, new_step_diffs: list["ComparisonResult"]
) -> bool:
    """Determine if chain mismatches are essentially the same.

    Compares:
    - Same step number failed
    - Same mismatch type at that step
    """
    original_mismatch_step = original_diff.get("mismatch_step")
    new_mismatch_step = len(new_step_diffs) - 1 if new_step_diffs else None

    if original_mismatch_step != new_mismatch_step:
        return False

    # Get original step diff
    original_steps = original_diff.get("steps", [])
    if original_mismatch_step is None or original_mismatch_step >= len(original_steps):
        return False

    original_step_diff = original_steps[original_mismatch_step]

    # Compare the mismatch at that step
    if not new_step_diffs:
        return False

    new_step_diff = new_step_diffs[new_mismatch_step]
    return _is_same_mismatch(original_step_diff, new_step_diff)


if __name__ == "__main__":
    sys.exit(main())
