"""CLI entry point for api-parity.

Handles argument parsing and dispatches to explore or replay mode.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from api_parity.artifact_writer import ArtifactWriter, RunStats
    from api_parity.case_generator import CaseGenerator
    from api_parity.comparator import Comparator
    from api_parity.executor import Executor
    from api_parity.models import ComparisonRules, TargetInfo


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
        validate_targets,
    )
    from api_parity.executor import Executor, RequestError
    from api_parity.models import TargetInfo

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

    # Validate mode - just check config validity without executing
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

        print("Validation successful")
        return 0

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
    print()

    # Initialize components
    stats = RunStats()
    writer = ArtifactWriter(args.out, runtime_config.secrets)

    target_a_info = TargetInfo(name=args.target_a, base_url=target_a_config.base_url)
    target_b_info = TargetInfo(name=args.target_b, base_url=target_b_config.base_url)

    # Start CEL evaluator
    try:
        cel_evaluator = CELEvaluator()
    except CELSubprocessError as e:
        print(f"Error starting CEL evaluator: {e}", file=sys.stderr)
        return 1

    try:
        comparator = Comparator(cel_evaluator, comparison_library)

        # Start executor
        with Executor(
            target_a_config,
            target_b_config,
            default_timeout=args.timeout,
            operation_timeouts=args.operation_timeout,
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
                )

    except CELSubprocessError as e:
        print(f"\nFatal: CEL evaluator crashed: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted by user")
    finally:
        cel_evaluator.close()

    # Write summary
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

            # Compare responses
            result = comparator.compare(response_a, response_b, rules)

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
                result = comparator.compare(response_a, response_b, rules)
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
