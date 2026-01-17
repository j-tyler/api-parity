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
    from api_parity.models import ComparisonResult, ComparisonRules, TargetInfo


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
class ListOperationsArgs:
    """Parsed arguments for list-operations mode."""

    spec: Path


@dataclass
class GraphChainsArgs:
    """Parsed arguments for graph-chains mode."""

    spec: Path
    exclude: list[str]


@dataclass
class ExploreArgs:
    """Parsed arguments for explore mode."""

    spec: Path
    config: Path
    target_a: str
    target_b: str
    out: Path
    seed: int | None
    max_cases: int | None
    validate: bool
    exclude: list[str]
    timeout: float
    operation_timeout: dict[str, float]
    # Stateful chain options
    stateful: bool
    max_chains: int | None
    max_steps: int


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
        "--max-cases",
        type=positive_int,
        default=None,
        dest="max_cases",
        help="Maximum number of test cases to generate (must be positive)",
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


def parse_list_ops_args(namespace: argparse.Namespace) -> ListOperationsArgs:
    """Convert parsed namespace to ListOperationsArgs dataclass."""
    return ListOperationsArgs(spec=namespace.spec)


def parse_graph_chains_args(namespace: argparse.Namespace) -> GraphChainsArgs:
    """Convert parsed namespace to GraphChainsArgs dataclass."""
    return GraphChainsArgs(spec=namespace.spec, exclude=namespace.exclude or [])


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
        max_cases=namespace.max_cases,
        validate=namespace.validate,
        exclude=namespace.exclude or [],
        timeout=namespace.timeout,
        operation_timeout=op_timeouts,
        stateful=namespace.stateful,
        max_chains=namespace.max_chains,
        max_steps=namespace.max_steps,
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


def parse_args(args: list[str] | None = None) -> ListOperationsArgs | GraphChainsArgs | ExploreArgs | ReplayArgs:
    """Parse command-line arguments and return typed args dataclass.

    Args:
        args: Command-line arguments to parse. If None, uses sys.argv[1:].

    Returns:
        ListOperationsArgs, GraphChainsArgs, ExploreArgs, or ReplayArgs depending on the subcommand.

    Raises:
        SystemExit: If arguments are invalid (argparse behavior).
    """
    parser = build_parser()
    namespace = parser.parse_args(args)

    if namespace.command == "list-operations":
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

        if isinstance(parsed, ListOperationsArgs):
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

    Outputs a Mermaid flowchart showing OpenAPI link relationships.
    """
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


def _extract_link_graph(
    schema: Any, exclude: list[str]
) -> tuple[dict[str, tuple[str, str]], list[tuple[str, str, str]]]:
    """Extract operations and links from OpenAPI spec.

    Args:
        schema: Loaded schemathesis schema.
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

    # Load comparison rules
    try:
        rules_path = resolve_comparison_rules_path(args.config, runtime_config.comparison_rules)
        comparison_rules = load_comparison_rules(rules_path)
    except ConfigError as e:
        print(f"Error loading comparison rules: {e}", file=sys.stderr)
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

    # Warn if stateful flags used without --stateful
    if not args.stateful and args.max_chains is not None:
        print("Warning: --max-chains is ignored without --stateful", file=sys.stderr)

    # Print run configuration
    mode = "stateful" if args.stateful else "stateless"
    print(f"Explore mode ({mode}): spec={args.spec}")
    print(f"  Targets: {args.target_a} ({target_a_config.base_url}) vs {args.target_b} ({target_b_config.base_url})")
    print(f"  Output: {args.out}")
    if args.seed is not None:
        print(f"  Seed: {args.seed}")
    if args.stateful:
        max_chains = args.max_chains or 20
        print(f"  Max chains: {max_chains}")
        print(f"  Max steps per chain: {args.max_steps}")
    elif args.max_cases is not None:
        print(f"  Max cases: {args.max_cases}")
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
        # For stateless: total is max_cases if specified
        # For stateful: total is set after chains are generated
        progress_unit = "chains" if args.stateful else "cases"
        progress_total = None if args.stateful else args.max_cases
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
                    max_cases=args.max_cases,
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
    max_cases: int | None,
    seed: int | None,
    get_operation_rules: Callable[[ComparisonRules, str], Any],
    progress_reporter: ProgressReporter | None = None,
) -> None:
    """Execute stateless (single-request) testing."""
    from api_parity.executor import RequestError

    for case in generator.generate(max_cases=max_cases, seed=seed):
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
) -> None:
    """Execute stateful chain testing."""
    from api_parity.executor import RequestError

    # Generate chains
    print("Generating chains...")
    chains = generator.generate_chains(
        max_chains=max_chains,
        max_steps=max_steps,
        seed=seed,
    )
    print(f"Generated {len(chains)} chains with multiple steps")
    print()

    # Update progress reporter with total now that we know it
    if progress_reporter is not None:
        progress_reporter.set_total(len(chains))

    for chain in chains:
        stats.total_chains += 1

        # Build chain description
        ops = [step.request_template.operation_id for step in chain.steps]
        chain_desc = " → ".join(ops)
        print(f"[Chain {stats.total_chains}] {chain_desc}")

        try:
            # Track comparison results as we execute
            step_diffs = []
            step_ops = []
            mismatch_found = False

            def on_step(response_a, response_b):
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
            else:
                stats.chain_mismatches += 1
                mismatch_step = len(step_diffs) - 1
                mismatch_op = step_ops[mismatch_step]
                print(f"  MISMATCH at step {mismatch_step} ({mismatch_op}): {step_diffs[mismatch_step].summary}")

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

        # Update progress reporter
        if progress_reporter is not None:
            progress_reporter.increment()


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

    # Load comparison rules
    try:
        rules_path = resolve_comparison_rules_path(args.config, runtime_config.comparison_rules)
        comparison_rules = load_comparison_rules(rules_path)
    except ConfigError as e:
        print(f"Error loading comparison rules: {e}", file=sys.stderr)
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

    # Pre-load all bundles to extract link_fields for chain replay
    loaded_bundles: list[LoadedBundle] = []
    link_fields: set[str] = set()
    load_errors: list[tuple[Path, str]] = []

    for bundle_path in bundles:
        try:
            bundle = load_bundle(bundle_path)
            loaded_bundles.append(bundle)
            # Extract link_fields from chain bundles for variable extraction
            if bundle.bundle_type == BundleType.CHAIN and bundle.chain_case is not None:
                link_fields.update(extract_link_fields_from_chain(bundle.chain_case))
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

        with Executor(
            target_a_config,
            target_b_config,
            default_timeout=args.timeout,
            operation_timeouts=args.operation_timeout,
            link_fields=link_fields if link_fields else None,
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
        # Track comparison results during execution
        step_diffs = []
        mismatch_found = False

        def on_step(response_a, response_b):
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
    """Determine if two mismatches are essentially the same.

    Compares:
    - mismatch_type (status_code, headers, body)
    - For body mismatches: same JSONPath fields failing
    - For header mismatches: same header names failing

    Doesn't require exact same values—just same failure pattern.
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
