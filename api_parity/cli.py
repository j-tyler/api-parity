"""CLI entry point for api-parity.

Handles argument parsing and dispatches to explore or replay mode.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path


DEFAULT_TIMEOUT = 30.0


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


def parse_args(args: list[str] | None = None) -> ListOperationsArgs | ExploreArgs | ReplayArgs:
    """Parse command-line arguments and return typed args dataclass.

    Args:
        args: Command-line arguments to parse. If None, uses sys.argv[1:].

    Returns:
        ListOperationsArgs, ExploreArgs, or ReplayArgs depending on the subcommand.

    Raises:
        SystemExit: If arguments are invalid (argparse behavior).
    """
    parser = build_parser()
    namespace = parser.parse_args(args)

    if namespace.command == "list-operations":
        return parse_list_ops_args(namespace)
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


def run_explore(args: ExploreArgs) -> int:
    """Run explore mode."""
    # Placeholder until components are implemented
    if args.validate:
        print(f"Validating: spec={args.spec}, config={args.config}")
        print(f"  Targets: {args.target_a}, {args.target_b}")
        if args.exclude:
            print(f"  Excluding: {', '.join(args.exclude)}")
        # TODO: Load and validate spec, config, check targets exist, validate excluded operationIds
        print("Validation successful")
        return 0

    print(f"Explore mode: spec={args.spec}, config={args.config}")
    print(f"  Targets: {args.target_a} vs {args.target_b}")
    print(f"  Output: {args.out}")
    if args.seed is not None:
        print(f"  Seed: {args.seed}")
    if args.max_cases is not None:
        print(f"  Max cases: {args.max_cases}")
    if args.exclude:
        print(f"  Excluding: {', '.join(args.exclude)}")
    print(f"  Timeout: {args.timeout}s")
    if args.operation_timeout:
        for op_id, timeout in args.operation_timeout.items():
            print(f"  Timeout for {op_id}: {timeout}s")
    return 0


def run_replay(args: ReplayArgs) -> int:
    """Run replay mode."""
    # Placeholder until components are implemented
    if args.validate:
        print(f"Validating: config={args.config}")
        print(f"  Targets: {args.target_a}, {args.target_b}")
        print(f"  Input: {args.input_dir}")
        # TODO: Load and validate config, check targets exist, validate replay cases
        print("Validation successful")
        return 0

    print(f"Replay mode: config={args.config}")
    print(f"  Targets: {args.target_a} vs {args.target_b}")
    print(f"  Input: {args.input_dir}")
    print(f"  Output: {args.out}")
    print(f"  Timeout: {args.timeout}s")
    if args.operation_timeout:
        for op_id, timeout in args.operation_timeout.items():
            print(f"  Timeout for {op_id}: {timeout}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
