"""Tests for explore subcommand argument parsing."""

from pathlib import Path

import pytest

from api_parity.cli import DEFAULT_TIMEOUT, ExploreArgs, parse_args, parse_explore_args


class TestExploreArgs:
    def test_all_required_args(self):
        """Test explore with all required arguments."""
        args = parse_args([
            "explore",
            "--spec", "openapi.yaml",
            "--config", "runtime.yaml",
            "--target-a", "production",
            "--target-b", "staging",
            "--out", "./artifacts",
        ])

        assert isinstance(args, ExploreArgs)
        assert args.spec == Path("openapi.yaml")
        assert args.config == Path("runtime.yaml")
        assert args.target_a == "production"
        assert args.target_b == "staging"
        assert args.out == Path("./artifacts")
        assert args.seed is None
        assert args.validate is False
        assert args.exclude == []
        assert args.timeout == DEFAULT_TIMEOUT
        assert args.operation_timeout == {}

    def test_with_seed(self):
        """Test explore with optional --seed argument."""
        args = parse_args([
            "explore",
            "--spec", "openapi.yaml",
            "--config", "runtime.yaml",
            "--target-a", "prod",
            "--target-b", "stage",
            "--out", "./out",
            "--seed", "42",
        ])

        assert isinstance(args, ExploreArgs)
        assert args.seed == 42

    def test_with_all_optional_args(self):
        """Test explore with all optional arguments."""
        args = parse_args([
            "explore",
            "--spec", "spec.yaml",
            "--config", "config.yaml",
            "--target-a", "a",
            "--target-b", "b",
            "--out", "./out",
            "--seed", "123",
        ])

        assert isinstance(args, ExploreArgs)
        assert args.seed == 123

    def test_missing_spec(self):
        """Test explore fails without --spec."""
        with pytest.raises(SystemExit) as exc_info:
            parse_args([
                "explore",
                "--config", "runtime.yaml",
                "--target-a", "prod",
                "--target-b", "stage",
                "--out", "./out",
            ])
        assert exc_info.value.code == 2

    def test_missing_config(self):
        """Test explore fails without --config."""
        with pytest.raises(SystemExit) as exc_info:
            parse_args([
                "explore",
                "--spec", "openapi.yaml",
                "--target-a", "prod",
                "--target-b", "stage",
                "--out", "./out",
            ])
        assert exc_info.value.code == 2

    def test_missing_target_a(self):
        """Test explore fails without --target-a."""
        with pytest.raises(SystemExit) as exc_info:
            parse_args([
                "explore",
                "--spec", "openapi.yaml",
                "--config", "runtime.yaml",
                "--target-b", "stage",
                "--out", "./out",
            ])
        assert exc_info.value.code == 2

    def test_missing_target_b(self):
        """Test explore fails without --target-b."""
        with pytest.raises(SystemExit) as exc_info:
            parse_args([
                "explore",
                "--spec", "openapi.yaml",
                "--config", "runtime.yaml",
                "--target-a", "prod",
                "--out", "./out",
            ])
        assert exc_info.value.code == 2

    def test_missing_out(self):
        """Test explore fails without --out."""
        with pytest.raises(SystemExit) as exc_info:
            parse_args([
                "explore",
                "--spec", "openapi.yaml",
                "--config", "runtime.yaml",
                "--target-a", "prod",
                "--target-b", "stage",
            ])
        assert exc_info.value.code == 2

    def test_invalid_seed_type(self):
        """Test explore fails with non-integer --seed."""
        with pytest.raises(SystemExit) as exc_info:
            parse_args([
                "explore",
                "--spec", "openapi.yaml",
                "--config", "runtime.yaml",
                "--target-a", "prod",
                "--target-b", "stage",
                "--out", "./out",
                "--seed", "not-an-int",
            ])
        assert exc_info.value.code == 2

    def test_path_with_spaces(self):
        """Test paths with spaces are handled correctly."""
        args = parse_args([
            "explore",
            "--spec", "path with spaces/openapi.yaml",
            "--config", "runtime.yaml",
            "--target-a", "prod",
            "--target-b", "stage",
            "--out", "./out dir",
        ])

        assert args.spec == Path("path with spaces/openapi.yaml")
        assert args.out == Path("./out dir")

    def test_absolute_paths(self):
        """Test absolute paths are preserved."""
        args = parse_args([
            "explore",
            "--spec", "/absolute/path/to/spec.yaml",
            "--config", "/etc/api-parity/config.yaml",
            "--target-a", "prod",
            "--target-b", "stage",
            "--out", "/var/artifacts",
        ])

        assert args.spec == Path("/absolute/path/to/spec.yaml")
        assert args.config == Path("/etc/api-parity/config.yaml")
        assert args.out == Path("/var/artifacts")

    def test_explore_args_any_order(self):
        """Test explore arguments can be in any order."""
        args = parse_args([
            "explore",
            "--out", "./artifacts",
            "--target-b", "staging",
            "--seed", "99",
            "--target-a", "production",
            "--config", "runtime.yaml",
            "--spec", "openapi.yaml",
        ])

        assert isinstance(args, ExploreArgs)
        assert args.spec == Path("openapi.yaml")
        assert args.config == Path("runtime.yaml")
        assert args.target_a == "production"
        assert args.target_b == "staging"
        assert args.out == Path("./artifacts")
        assert args.seed == 99

    def test_parse_explore_args_converts_correctly(self):
        """Test parse_explore_args converts namespace to dataclass."""
        import argparse
        namespace = argparse.Namespace(
            command="explore",
            spec=Path("spec.yaml"),
            config=Path("config.yaml"),
            target_a="a",
            target_b="b",
            out=Path("./out"),
            seed=42,
            validate=False,
            exclude=["getUser"],
            timeout=60.0,
            operation_timeout=[("slowOp", 120.0)],
            stateful=False,
            max_chains=None,
            max_steps=6,
            log_chains=False,
            ensure_coverage=False,
            min_hits_per_op=1,
            min_coverage=100,
        )
        args = parse_explore_args(namespace)
        assert isinstance(args, ExploreArgs)
        assert args.spec == Path("spec.yaml")
        assert args.seed == 42
        assert args.exclude == ["getUser"]
        assert args.timeout == 60.0
        assert args.operation_timeout == {"slowOp": 120.0}
        assert args.stateful is False
        assert args.max_chains is None
        assert args.max_steps == 6
        assert args.log_chains is False
        assert args.min_hits_per_op == 1
        assert args.min_coverage == 100

    def test_parse_explore_args_stateful_mode(self):
        """Test parse_explore_args handles stateful mode flags."""
        import argparse
        namespace = argparse.Namespace(
            command="explore",
            spec=Path("spec.yaml"),
            config=Path("config.yaml"),
            target_a="a",
            target_b="b",
            out=Path("./out"),
            seed=None,
            validate=False,
            exclude=[],
            timeout=30.0,
            operation_timeout=[],
            stateful=True,
            max_chains=50,
            max_steps=10,
            log_chains=True,
            ensure_coverage=False,
            min_hits_per_op=1,
            min_coverage=100,
        )
        args = parse_explore_args(namespace)
        assert args.stateful is True
        assert args.max_chains == 50
        assert args.max_steps == 10
        assert args.log_chains is True
        assert args.ensure_coverage is False

    def test_parse_explore_args_ensure_coverage_flag(self):
        """Test parse_explore_args handles ensure_coverage flag."""
        import argparse
        namespace = argparse.Namespace(
            command="explore",
            spec=Path("spec.yaml"),
            config=Path("config.yaml"),
            target_a="a",
            target_b="b",
            out=Path("./out"),
            seed=None,
            validate=False,
            exclude=[],
            timeout=30.0,
            operation_timeout=[],
            stateful=True,
            max_chains=20,
            max_steps=6,
            log_chains=False,
            ensure_coverage=True,
            min_hits_per_op=1,
            min_coverage=100,
        )
        args = parse_explore_args(namespace)
        assert args.stateful is True
        assert args.ensure_coverage is True

    def test_parse_explore_args_exclude_with_ensure_coverage(self):
        """Test that exclude and ensure_coverage can be used together.

        Verifies that the args are correctly parsed when both --exclude
        and --ensure-coverage are specified. The actual behavior (excluded
        operations not appearing in coverage warnings) is tested by the
        exclude parameter being passed to _run_stateful_explore.
        """
        import argparse
        namespace = argparse.Namespace(
            command="explore",
            spec=Path("spec.yaml"),
            config=Path("config.yaml"),
            target_a="a",
            target_b="b",
            out=Path("./out"),
            seed=None,
            validate=False,
            exclude=["excludedOp1", "excludedOp2"],
            timeout=30.0,
            operation_timeout=[],
            stateful=True,
            max_chains=20,
            max_steps=6,
            log_chains=False,
            ensure_coverage=True,
            min_hits_per_op=1,
            min_coverage=100,
        )
        args = parse_explore_args(namespace)
        assert args.stateful is True
        assert args.ensure_coverage is True
        assert args.exclude == ["excludedOp1", "excludedOp2"]

    def test_parse_explore_args_coverage_depth_flags(self):
        """Test parse_explore_args handles coverage depth flags."""
        import argparse
        namespace = argparse.Namespace(
            command="explore",
            spec=Path("spec.yaml"),
            config=Path("config.yaml"),
            target_a="a",
            target_b="b",
            out=Path("./out"),
            seed=42,
            validate=False,
            exclude=[],
            timeout=30.0,
            operation_timeout=[],
            stateful=True,
            max_chains=None,
            max_steps=6,
            log_chains=False,
            ensure_coverage=False,
            min_hits_per_op=5,
            min_coverage=80,
        )
        args = parse_explore_args(namespace)
        assert args.min_hits_per_op == 5
        assert args.min_coverage == 80


class TestValidateMode:
    def test_explore_validate_flag(self):
        """Test explore with --validate flag."""
        args = parse_args([
            "explore",
            "--spec", "spec.yaml",
            "--config", "config.yaml",
            "--target-a", "a",
            "--target-b", "b",
            "--out", "./out",
            "--validate",
        ])
        assert isinstance(args, ExploreArgs)
        assert args.validate is True

    def test_explore_validate_with_other_options(self):
        """Test explore --validate works with other optional args."""
        args = parse_args([
            "explore",
            "--spec", "spec.yaml",
            "--config", "config.yaml",
            "--target-a", "a",
            "--target-b", "b",
            "--out", "./out",
            "--seed", "42",
            "--validate",
        ])
        assert args.validate is True
        assert args.seed == 42

    def test_validate_flag_position_independent(self):
        """Test --validate can appear anywhere in args."""
        args = parse_args([
            "explore",
            "--validate",
            "--spec", "spec.yaml",
            "--config", "config.yaml",
            "--target-a", "a",
            "--target-b", "b",
            "--out", "./out",
        ])
        assert args.validate is True


class TestExcludeOption:
    def test_single_exclude(self):
        """Test explore with single --exclude."""
        args = parse_args([
            "explore",
            "--spec", "spec.yaml",
            "--config", "config.yaml",
            "--target-a", "a",
            "--target-b", "b",
            "--out", "./out",
            "--exclude", "getUser",
        ])
        assert isinstance(args, ExploreArgs)
        assert args.exclude == ["getUser"]

    def test_multiple_excludes(self):
        """Test explore with multiple --exclude options."""
        args = parse_args([
            "explore",
            "--spec", "spec.yaml",
            "--config", "config.yaml",
            "--target-a", "a",
            "--target-b", "b",
            "--out", "./out",
            "--exclude", "getUser",
            "--exclude", "createItem",
            "--exclude", "deleteOrder",
        ])
        assert args.exclude == ["getUser", "createItem", "deleteOrder"]

    def test_exclude_with_other_options(self):
        """Test --exclude works with other optional args."""
        args = parse_args([
            "explore",
            "--spec", "spec.yaml",
            "--config", "config.yaml",
            "--target-a", "a",
            "--target-b", "b",
            "--out", "./out",
            "--seed", "42",
            "--exclude", "getUser",
            "--validate",
        ])
        assert args.exclude == ["getUser"]
        assert args.seed == 42
        assert args.validate is True

    def test_exclude_empty_by_default(self):
        """Test exclude is empty list when not specified."""
        args = parse_args([
            "explore",
            "--spec", "spec.yaml",
            "--config", "config.yaml",
            "--target-a", "a",
            "--target-b", "b",
            "--out", "./out",
        ])
        assert args.exclude == []

    def test_exclude_position_independent(self):
        """Test --exclude can appear anywhere in args."""
        args = parse_args([
            "explore",
            "--exclude", "firstOp",
            "--spec", "spec.yaml",
            "--exclude", "secondOp",
            "--config", "config.yaml",
            "--target-a", "a",
            "--target-b", "b",
            "--out", "./out",
        ])
        assert args.exclude == ["firstOp", "secondOp"]


class TestTimeoutOptions:
    def test_explore_default_timeout(self):
        """Test explore uses default timeout when not specified."""
        args = parse_args([
            "explore",
            "--spec", "spec.yaml",
            "--config", "config.yaml",
            "--target-a", "a",
            "--target-b", "b",
            "--out", "./out",
        ])
        assert args.timeout == DEFAULT_TIMEOUT

    def test_explore_custom_timeout(self):
        """Test explore with custom --timeout."""
        args = parse_args([
            "explore",
            "--spec", "spec.yaml",
            "--config", "config.yaml",
            "--target-a", "a",
            "--target-b", "b",
            "--out", "./out",
            "--timeout", "60",
        ])
        assert args.timeout == 60.0

    def test_explore_timeout_float(self):
        """Test explore timeout accepts float values."""
        args = parse_args([
            "explore",
            "--spec", "spec.yaml",
            "--config", "config.yaml",
            "--target-a", "a",
            "--target-b", "b",
            "--out", "./out",
            "--timeout", "5.5",
        ])
        assert args.timeout == 5.5

    def test_explore_single_operation_timeout(self):
        """Test explore with single --operation-timeout."""
        args = parse_args([
            "explore",
            "--spec", "spec.yaml",
            "--config", "config.yaml",
            "--target-a", "a",
            "--target-b", "b",
            "--out", "./out",
            "--operation-timeout", "getUser:60",
        ])
        assert args.operation_timeout == {"getUser": 60.0}

    def test_explore_multiple_operation_timeouts(self):
        """Test explore with multiple --operation-timeout options."""
        args = parse_args([
            "explore",
            "--spec", "spec.yaml",
            "--config", "config.yaml",
            "--target-a", "a",
            "--target-b", "b",
            "--out", "./out",
            "--operation-timeout", "getUser:60",
            "--operation-timeout", "createItem:120",
            "--operation-timeout", "slowReport:300",
        ])
        assert args.operation_timeout == {
            "getUser": 60.0,
            "createItem": 120.0,
            "slowReport": 300.0,
        }

    def test_operation_timeout_with_colon_in_id(self):
        """Test operation timeout handles operationId with colon (uses rsplit)."""
        args = parse_args([
            "explore",
            "--spec", "spec.yaml",
            "--config", "config.yaml",
            "--target-a", "a",
            "--target-b", "b",
            "--out", "./out",
            "--operation-timeout", "namespace:getUser:60",
        ])
        assert args.operation_timeout == {"namespace:getUser": 60.0}


class TestChainSignature:
    """Tests for _chain_signature function."""

    def test_signature_extracts_operation_ids(self):
        """Chain signature is tuple of operation IDs."""
        from api_parity.cli import _chain_signature
        from api_parity.models import ChainCase, ChainStep, RequestCase

        # Create a mock chain with 3 steps
        steps = []
        for i, op_id in enumerate(["createWidget", "getWidget", "updateWidget"]):
            request = RequestCase(
                case_id=f"case-{i}",
                operation_id=op_id,
                method="GET",
                path_template="/test",
                rendered_path="/test",
            )
            steps.append(ChainStep(step_index=i, request_template=request))

        chain = ChainCase(chain_id="test-chain", steps=steps)
        sig = _chain_signature(chain)

        assert sig == ("createWidget", "getWidget", "updateWidget")

    def test_signature_same_for_same_ops(self):
        """Chains with same ops have same signature."""
        from api_parity.cli import _chain_signature
        from api_parity.models import ChainCase, ChainStep, RequestCase

        def make_chain(chain_id: str) -> ChainCase:
            steps = []
            for i, op_id in enumerate(["op1", "op2"]):
                request = RequestCase(
                    case_id=f"{chain_id}-{i}",
                    operation_id=op_id,
                    method="GET",
                    path_template="/test",
                    rendered_path="/test",
                )
                steps.append(ChainStep(step_index=i, request_template=request))
            return ChainCase(chain_id=chain_id, steps=steps)

        chain1 = make_chain("chain-1")
        chain2 = make_chain("chain-2")

        assert _chain_signature(chain1) == _chain_signature(chain2)

    def test_signature_different_for_different_ops(self):
        """Chains with different ops have different signatures."""
        from api_parity.cli import _chain_signature
        from api_parity.models import ChainCase, ChainStep, RequestCase

        def make_chain(op_ids: list[str]) -> ChainCase:
            steps = []
            for i, op_id in enumerate(op_ids):
                request = RequestCase(
                    case_id=f"case-{i}",
                    operation_id=op_id,
                    method="GET",
                    path_template="/test",
                    rendered_path="/test",
                )
                steps.append(ChainStep(step_index=i, request_template=request))
            return ChainCase(chain_id="test", steps=steps)

        chain1 = make_chain(["op1", "op2"])
        chain2 = make_chain(["op1", "op3"])

        assert _chain_signature(chain1) != _chain_signature(chain2)


class TestGenerateChainsWithSeedWalking:
    """Tests for _generate_chains_with_seed_walking function.

    The function returns a ChainGenerationResult dataclass with:
    - chains: deduplicated chain list
    - seeds_used: seeds that contributed unique chains
    - operations_covered: all operation IDs seen in chains
    - linked_operations / orphan_operations: classification from spec
    - stopped_reason: why walking stopped (coverage_met, max_chains, max_seeds, no_seed)
    - seeds_tried: total seeds attempted
    """

    @staticmethod
    def _make_chain(op_ids: list[str], chain_id: str) -> "ChainCase":
        """Helper to create a ChainCase from operation ID list."""
        from api_parity.models import ChainCase, ChainStep, RequestCase
        steps = []
        for i, op_id in enumerate(op_ids):
            request = RequestCase(
                case_id=f"case-{i}",
                operation_id=op_id,
                method="GET",
                path_template="/test",
                rendered_path="/test",
            )
            steps.append(ChainStep(step_index=i, request_template=request))
        return ChainCase(chain_id=chain_id, steps=steps)

    def test_no_seed_single_pass(self):
        """Without seed, single pass is performed without seed walking."""
        from unittest.mock import MagicMock

        from api_parity.cli import _generate_chains_with_seed_walking

        mock_generator = MagicMock()
        mock_generator.generate_chains.return_value = [
            self._make_chain(["op1", "op2"], "c1"),
            self._make_chain(["op1", "op3"], "c2"),
        ]

        result = _generate_chains_with_seed_walking(
            generator=mock_generator,
            max_chains=5,
            max_steps=6,
            starting_seed=None,
        )

        mock_generator.generate_chains.assert_called_once_with(
            max_chains=5, max_steps=6, seed=None
        )
        assert len(result.chains) == 2
        assert result.seeds_used == []
        assert result.stopped_reason == "no_seed"
        assert result.operations_covered == {"op1", "op2", "op3"}

    def test_seed_walking_accumulates_chains(self):
        """With seed, walking accumulates unique chains across seeds."""
        from unittest.mock import MagicMock

        from api_parity.cli import _generate_chains_with_seed_walking

        mock_generator = MagicMock()

        def mock_generate(max_chains, max_steps, seed):
            if seed == 42:
                return [
                    self._make_chain(["op1", "op2"], "c1"),
                    self._make_chain(["op1", "op3"], "c2"),
                ]
            elif seed == 43:
                return [
                    self._make_chain(["op1", "op2"], "c3"),  # Duplicate of c1
                    self._make_chain(["op2", "op3"], "c4"),  # New
                ]
            elif seed == 44:
                return [self._make_chain(["op3", "op4"], "c5")]  # New
            return []

        mock_generator.generate_chains.side_effect = mock_generate

        result = _generate_chains_with_seed_walking(
            generator=mock_generator,
            max_chains=4,
            max_steps=6,
            starting_seed=42,
        )

        assert len(result.chains) == 4
        assert result.seeds_used == [42, 43, 44]
        assert result.stopped_reason == "max_chains"

        # Verify the chains are deduplicated by signature
        sigs = [tuple(s.request_template.operation_id for s in c.steps) for c in result.chains]
        assert len(sigs) == len(set(sigs))

    def test_seed_walking_stops_at_max_chains(self):
        """Seed walking stops when max_chains is reached."""
        from unittest.mock import MagicMock

        from api_parity.cli import _generate_chains_with_seed_walking

        mock_generator = MagicMock()
        call_count = [0]

        def mock_generate(max_chains, max_steps, seed):
            call_count[0] += 1
            return [self._make_chain([f"op{seed}", f"op{seed+100}"], f"c{seed}")]

        mock_generator.generate_chains.side_effect = mock_generate

        result = _generate_chains_with_seed_walking(
            generator=mock_generator,
            max_chains=3,
            max_steps=6,
            starting_seed=0,
        )

        assert len(result.chains) == 3
        assert call_count[0] == 3
        assert result.seeds_used == [0, 1, 2]
        assert result.stopped_reason == "max_chains"

    def test_seed_walking_respects_max_increments(self):
        """Seed walking stops after MAX_SEED_INCREMENTS even if target not reached."""
        from unittest.mock import MagicMock

        from api_parity.cli import MAX_SEED_INCREMENTS, _generate_chains_with_seed_walking

        mock_generator = MagicMock()
        mock_generator.generate_chains.return_value = []

        result = _generate_chains_with_seed_walking(
            generator=mock_generator,
            max_chains=1000,
            max_steps=6,
            starting_seed=0,
        )

        assert mock_generator.generate_chains.call_count == MAX_SEED_INCREMENTS
        assert len(result.chains) == 0
        assert result.seeds_used == []
        assert result.stopped_reason == "max_seeds"

    def test_coverage_guided_stops_when_all_linked_covered(self):
        """Seed walking stops early when all linked operations are covered.

        This is the primary coverage-guided stopping behavior. Given linked
        operations {A, B, C, D}, walking stops as soon as chains cover all four,
        even if max_chains hasn't been reached yet.
        """
        from unittest.mock import MagicMock

        from api_parity.cli import _generate_chains_with_seed_walking

        mock_generator = MagicMock()
        linked_ops = {"opA", "opB", "opC", "opD"}

        def mock_generate(max_chains, max_steps, seed):
            if seed == 0:
                # Covers opA, opB
                return [self._make_chain(["opA", "opB"], "c1")]
            elif seed == 1:
                # Covers opC, opD — now all linked ops are covered
                return [self._make_chain(["opC", "opD"], "c2")]
            elif seed == 2:
                # Would add more chains, but shouldn't be called
                return [self._make_chain(["opA", "opD"], "c3")]
            return []

        mock_generator.generate_chains.side_effect = mock_generate

        result = _generate_chains_with_seed_walking(
            generator=mock_generator,
            max_chains=100,  # High limit — coverage should stop us first
            max_steps=6,
            starting_seed=0,
            linked_operations=linked_ops,
            all_operations=linked_ops | {"orphanOp"},
        )

        # Should stop after seed 1 (coverage met), NOT continue to seed 2
        assert result.stopped_reason == "coverage_met"
        assert len(result.chains) == 2
        assert result.seeds_used == [0, 1]
        assert result.coverage_complete is True
        assert result.linked_covered_count == 4
        assert result.linked_total_count == 4
        assert result.linked_uncovered == set()
        # Seed 2 should NOT have been called
        assert mock_generator.generate_chains.call_count == 2

    def test_coverage_tracking_reports_uncovered(self):
        """When coverage isn't fully met, result shows which ops are missing."""
        from unittest.mock import MagicMock

        from api_parity.cli import _generate_chains_with_seed_walking

        mock_generator = MagicMock()
        linked_ops = {"opA", "opB", "opC"}

        # Only covers opA and opB, never opC
        mock_generator.generate_chains.return_value = [
            self._make_chain(["opA", "opB"], "c1"),
        ]

        result = _generate_chains_with_seed_walking(
            generator=mock_generator,
            max_chains=1,  # Stops at 1 chain
            max_steps=6,
            starting_seed=0,
            linked_operations=linked_ops,
        )

        assert result.stopped_reason == "max_chains"
        assert result.coverage_complete is False
        assert result.linked_covered_count == 2
        assert result.linked_uncovered == {"opC"}

    def test_orphan_operations_computed_correctly(self):
        """Orphan operations are all_operations minus linked_operations."""
        from unittest.mock import MagicMock

        from api_parity.cli import _generate_chains_with_seed_walking

        mock_generator = MagicMock()
        mock_generator.generate_chains.return_value = [
            self._make_chain(["opA", "opB"], "c1"),
        ]

        result = _generate_chains_with_seed_walking(
            generator=mock_generator,
            max_chains=5,
            max_steps=6,
            starting_seed=None,
            linked_operations={"opA", "opB"},
            all_operations={"opA", "opB", "healthCheck", "listAll"},
        )

        assert result.orphan_operations == {"healthCheck", "listAll"}

    def test_no_linked_operations_falls_back_to_chain_count(self):
        """Without linked_operations, seed walking uses only chain count stopping."""
        from unittest.mock import MagicMock

        from api_parity.cli import _generate_chains_with_seed_walking

        mock_generator = MagicMock()
        call_count = [0]

        def mock_generate(max_chains, max_steps, seed):
            call_count[0] += 1
            return [self._make_chain([f"op{seed}", f"op{seed+10}"], f"c{seed}")]

        mock_generator.generate_chains.side_effect = mock_generate

        # No linked_operations provided — should behave like old chain-count mode
        result = _generate_chains_with_seed_walking(
            generator=mock_generator,
            max_chains=3,
            max_steps=6,
            starting_seed=0,
            linked_operations=None,
        )

        assert len(result.chains) == 3
        assert result.stopped_reason == "max_chains"
        assert result.linked_operations is None
        assert result.coverage_complete is False  # Can't be True without linked_operations

    def test_coverage_met_overrides_max_chains(self):
        """Coverage stopping takes priority: stops before max_chains if all ops covered."""
        from unittest.mock import MagicMock

        from api_parity.cli import _generate_chains_with_seed_walking

        mock_generator = MagicMock()
        linked_ops = {"opA", "opB"}

        # Single seed covers everything
        mock_generator.generate_chains.return_value = [
            self._make_chain(["opA", "opB"], "c1"),
        ]

        result = _generate_chains_with_seed_walking(
            generator=mock_generator,
            max_chains=100,  # Would need 100 chains without coverage stopping
            max_steps=6,
            starting_seed=0,
            linked_operations=linked_ops,
        )

        # Coverage met on first seed — should not continue walking
        assert result.stopped_reason == "coverage_met"
        assert mock_generator.generate_chains.call_count == 1
        assert result.coverage_complete is True

    def test_min_hits_per_op_requires_multiple_chains(self):
        """With min_hits_per_op=3, walking continues until each op appears in 3 chains.

        Each linked operation must appear in at least 3 unique (deduplicated) chains
        before the coverage target is met. Seed walking should continue past what
        simple 1-hit coverage would require.

        Chains are deduplicated by their operation-ID signature (the sequence of
        operation IDs in the chain). So each seed must produce a chain with a
        different signature to count as unique.
        """
        from unittest.mock import MagicMock

        from api_parity.cli import _generate_chains_with_seed_walking

        mock_generator = MagicMock()
        linked_ops = {"opA", "opB"}

        def mock_generate(max_chains, max_steps, seed):
            # Each seed produces a unique chain (different signature via unique extra op)
            # All chains contain opA and opB, so both get a hit per unique chain
            return [self._make_chain(["opA", "opB", f"extra{seed}"], f"c{seed}")]

        mock_generator.generate_chains.side_effect = mock_generate

        result = _generate_chains_with_seed_walking(
            generator=mock_generator,
            max_chains=None,  # Unlimited — coverage depth drives stopping
            max_steps=6,
            starting_seed=0,
            linked_operations=linked_ops,
            min_hits_per_op=3,
            min_coverage_pct=100.0,
        )

        # Need 3 unique chains (each covers both ops), so 3 seeds
        assert result.stopped_reason == "coverage_met"
        assert len(result.chains) == 3
        assert result.operation_hit_counts["opA"] == 3
        assert result.operation_hit_counts["opB"] == 3
        assert result.min_hits_per_op == 3
        assert result.min_coverage_pct == 100.0
        assert result.coverage_complete is True
        assert result.ops_meeting_hits_target == 2

    def test_min_hits_per_op_partial_coverage(self):
        """With min_coverage_pct < 100, stops when enough ops meet the hit target.

        min_hits_per_op=5, min_coverage_pct=50 means: stop when 50% of linked ops
        have 5+ hits. This tolerates hard-to-reach operations.
        """
        from unittest.mock import MagicMock

        from api_parity.cli import _generate_chains_with_seed_walking

        mock_generator = MagicMock()
        linked_ops = {"opA", "opB"}  # 2 ops, 50% = 1 op

        def mock_generate(max_chains, max_steps, seed):
            # Only opA gets hit every seed; opB never appears.
            # Each seed produces a unique chain (different signature via seed suffix).
            return [self._make_chain(["opA", f"filler{seed}"], f"c{seed}")]

        mock_generator.generate_chains.side_effect = mock_generate

        result = _generate_chains_with_seed_walking(
            generator=mock_generator,
            max_chains=None,
            max_steps=6,
            starting_seed=0,
            linked_operations=linked_ops,
            min_hits_per_op=5,
            min_coverage_pct=50.0,
        )

        # opA hits 5 on seed 4 (0-indexed). 50% of 2 ops = 1 op needed at 5+ hits.
        assert result.stopped_reason == "coverage_met"
        assert result.operation_hit_counts["opA"] == 5
        assert result.operation_hit_counts.get("opB", 0) == 0
        assert result.coverage_complete is True
        assert result.ops_meeting_hits_target == 1

    def test_operation_hit_counts_track_unique_chains_only(self):
        """Hit counts only count deduplicated chains, not duplicates.

        If seed 0 and seed 1 both produce chain [opA, opB], that's only 1 hit
        for opA and opB (the duplicate is filtered out).
        """
        from unittest.mock import MagicMock

        from api_parity.cli import _generate_chains_with_seed_walking

        mock_generator = MagicMock()
        linked_ops = {"opA", "opB"}

        # Seed 0 and 1 produce the SAME chain (duplicate)
        # Seed 2 produces a different chain
        def mock_generate(max_chains, max_steps, seed):
            if seed < 2:
                return [self._make_chain(["opA", "opB"], f"c{seed}")]
            else:
                return [self._make_chain(["opB", "opA"], f"c{seed}")]

        mock_generator.generate_chains.side_effect = mock_generate

        result = _generate_chains_with_seed_walking(
            generator=mock_generator,
            max_chains=None,
            max_steps=6,
            starting_seed=0,
            linked_operations=linked_ops,
            min_hits_per_op=2,
            min_coverage_pct=100.0,
        )

        # Seed 0: chain [opA, opB] -> unique, opA=1, opB=1
        # Seed 1: chain [opA, opB] -> DUPLICATE, ignored
        # Seed 2: chain [opB, opA] -> unique, opA=2, opB=2 -> target met
        assert result.stopped_reason == "coverage_met"
        assert len(result.chains) == 2  # Only 2 unique chains
        assert result.operation_hit_counts["opA"] == 2
        assert result.operation_hit_counts["opB"] == 2

    def test_operation_hit_counts_one_per_chain_even_if_op_appears_twice(self):
        """An operation appearing multiple times in one chain counts as 1 hit.

        Chain [opA, opB, opA] counts opA once (not twice), because we're
        counting "number of unique chains containing this operation."
        """
        from unittest.mock import MagicMock

        from api_parity.cli import _generate_chains_with_seed_walking

        mock_generator = MagicMock()

        # Chain has opA twice — should count as 1 hit for opA
        mock_generator.generate_chains.return_value = [
            self._make_chain(["opA", "opB", "opA"], "c1"),
        ]

        result = _generate_chains_with_seed_walking(
            generator=mock_generator,
            max_chains=5,
            max_steps=6,
            starting_seed=None,
        )

        assert result.operation_hit_counts["opA"] == 1
        assert result.operation_hit_counts["opB"] == 1

    def test_ops_below_hits_target_shows_shortfall(self):
        """ops_below_hits_target shows which linked ops haven't reached the target."""
        from unittest.mock import MagicMock

        from api_parity.cli import _generate_chains_with_seed_walking

        mock_generator = MagicMock()
        linked_ops = {"opA", "opB", "opC"}

        # opA appears in 2 chains, opB in 1, opC in 0
        mock_generator.generate_chains.return_value = [
            self._make_chain(["opA", "opB"], "c1"),
            self._make_chain(["opA"], "c2"),
        ]

        result = _generate_chains_with_seed_walking(
            generator=mock_generator,
            max_chains=5,
            max_steps=6,
            starting_seed=None,
            linked_operations=linked_ops,
            min_hits_per_op=3,
        )

        below = result.ops_below_hits_target
        assert "opA" in below  # 2 < 3
        assert below["opA"] == 2
        assert "opB" in below  # 1 < 3
        assert below["opB"] == 1
        assert "opC" in below  # 0 < 3
        assert below["opC"] == 0

    def test_cli_min_hits_per_op_default(self):
        """--min-hits-per-op defaults to 1."""
        args = parse_args([
            "explore",
            "--spec", "openapi.yaml",
            "--config", "runtime.yaml",
            "--target-a", "prod",
            "--target-b", "stage",
            "--out", "./out",
            "--stateful",
        ])

        assert isinstance(args, ExploreArgs)
        assert args.min_hits_per_op == 1
        assert args.min_coverage == 100

    def test_cli_min_hits_per_op_custom(self):
        """--min-hits-per-op can be set to a custom value."""
        args = parse_args([
            "explore",
            "--spec", "openapi.yaml",
            "--config", "runtime.yaml",
            "--target-a", "prod",
            "--target-b", "stage",
            "--out", "./out",
            "--stateful",
            "--min-hits-per-op", "5",
            "--min-coverage", "80",
            "--seed", "42",
        ])

        assert isinstance(args, ExploreArgs)
        assert args.min_hits_per_op == 5
        assert args.min_coverage == 80

    def test_unlimited_max_chains_when_depth_target_set(self):
        """When min_hits_per_op > 1 and no --max-chains, max_chains is None (unlimited).

        This ensures seed walking continues until the depth target is met,
        not stopped by an arbitrary chain count limit.
        """
        args = parse_args([
            "explore",
            "--spec", "openapi.yaml",
            "--config", "runtime.yaml",
            "--target-a", "prod",
            "--target-b", "stage",
            "--out", "./out",
            "--stateful",
            "--min-hits-per-op", "5",
            "--seed", "42",
        ])

        assert isinstance(args, ExploreArgs)
        assert args.min_hits_per_op == 5
        # max_chains should be None (not explicitly set by user)
        assert args.max_chains is None

    def test_min_coverage_rejects_negative(self):
        """--min-coverage rejects values below 0."""
        # Validation is deferred to run_explore(), so argparse accepts it.
        # We test that parse_args accepts -1 but run_explore would reject it.
        # Since run_explore requires real config/spec, test the validation
        # boundary directly via the parsed value.
        args = parse_args([
            "explore",
            "--spec", "openapi.yaml",
            "--config", "runtime.yaml",
            "--target-a", "prod",
            "--target-b", "stage",
            "--out", "./out",
            "--min-coverage", "-1",
        ])
        assert isinstance(args, ExploreArgs)
        assert args.min_coverage == -1  # Accepted by argparse, rejected by run_explore

    def test_min_coverage_rejects_over_100(self):
        """--min-coverage rejects values above 100."""
        args = parse_args([
            "explore",
            "--spec", "openapi.yaml",
            "--config", "runtime.yaml",
            "--target-a", "prod",
            "--target-b", "stage",
            "--out", "./out",
            "--min-coverage", "200",
        ])
        assert isinstance(args, ExploreArgs)
        assert args.min_coverage == 200  # Accepted by argparse, rejected by run_explore

    def test_min_coverage_validation_in_run_explore(self):
        """run_explore returns error code 1 for --min-coverage outside 0-100."""
        from api_parity.cli import run_explore

        args = ExploreArgs(
            spec=Path("nonexistent.yaml"),
            config=Path("nonexistent.yaml"),
            target_a="a",
            target_b="b",
            out=Path("./out"),
            seed=None,
            validate=False,
            exclude=[],
            timeout=30.0,
            operation_timeout={},
            stateful=True,
            max_chains=None,
            max_steps=6,
            log_chains=False,
            ensure_coverage=False,
            min_hits_per_op=1,
            min_coverage=200,
        )
        # Should fail with error code 1 before trying to load any files
        result = run_explore(args)
        assert result == 1

    def test_min_coverage_validation_negative_in_run_explore(self):
        """run_explore returns error code 1 for negative --min-coverage."""
        from api_parity.cli import run_explore

        args = ExploreArgs(
            spec=Path("nonexistent.yaml"),
            config=Path("nonexistent.yaml"),
            target_a="a",
            target_b="b",
            out=Path("./out"),
            seed=None,
            validate=False,
            exclude=[],
            timeout=30.0,
            operation_timeout={},
            stateful=True,
            max_chains=None,
            max_steps=6,
            log_chains=False,
            ensure_coverage=False,
            min_hits_per_op=1,
            min_coverage=-1,
        )
        result = run_explore(args)
        assert result == 1


class TestProgressReporterTimingInStatefulExplore:
    """Integration tests for progress reporter lifecycle in stateful explore.

    Bug: The progress reporter was started in run_explore() BEFORE calling
    _run_stateful_explore(). During the chain generation phase (which can take
    minutes with --min-hits-per-op 2 and 100% coverage), the reporter shows
    stale output like:
        [Progress] 0 chains | 0.0/s | Elapsed: 1m0s

    This happens because:
    1. reporter.start() sets _start_time and begins the background print thread
    2. Chain generation runs (seed walking, no progress increments)
    3. The thread prints every 10s with completed=0, total=None -> stale line
    4. After generation, set_total() is called, but _start_time still reflects
       pre-generation time, distorting rate and ETA calculations

    Fix: _run_stateful_explore() must call start() on the reporter after
    chain generation completes (right after set_total), so the timer and
    rate calculations align with chain execution -- not generation.
    """

    @staticmethod
    def _make_chain(op_ids: list[str], chain_id: str) -> "ChainCase":
        """Helper to create a ChainCase from operation ID list."""
        from api_parity.models import ChainCase, ChainStep, RequestCase
        steps = []
        for i, op_id in enumerate(op_ids):
            request = RequestCase(
                case_id=f"case-{i}",
                operation_id=op_id,
                method="GET",
                path_template="/test",
                rendered_path="/test",
            )
            steps.append(ChainStep(step_index=i, request_template=request))
        return ChainCase(chain_id=chain_id, steps=steps)

    def test_progress_reporter_started_after_chain_generation(self):
        """_run_stateful_explore must start the progress reporter after generation.

        The reporter is passed unstarted. The function must call start() itself
        after chain generation completes, so the timer aligns with execution.
        With the bug, the function never called start() -- the caller did it
        too early, before generation.
        """
        import time
        from unittest.mock import MagicMock

        from api_parity.artifact_writer import RunStats
        from api_parity.cli import ProgressReporter, _run_stateful_explore
        from api_parity.models import ChainExecution, TargetInfo

        # Track when generate_chains finishes to verify start_time ordering
        generation_end_time = None

        def mock_generate_chains(max_chains, max_steps, seed):
            nonlocal generation_end_time
            chains = [
                self._make_chain(["op1", "op2"], "c1"),
                self._make_chain(["op2", "op3"], "c2"),
            ]
            generation_end_time = time.monotonic()
            return chains

        mock_generator = MagicMock()
        mock_generator.generate_chains.side_effect = mock_generate_chains
        mock_generator.get_linked_operation_ids.return_value = {"op1", "op2", "op3"}
        mock_generator.get_all_operation_ids.return_value = {"op1", "op2", "op3"}
        mock_generator.get_link_edges.return_value = [("op1", "op2"), ("op2", "op3")]

        mock_executor = MagicMock()
        # execute_chain returns (ChainExecution, ChainExecution).
        # Don't call on_step -- chains count as matches (no comparator needed).
        mock_executor.execute_chain.return_value = (
            ChainExecution(steps=[]),
            ChainExecution(steps=[]),
        )

        mock_comparator = MagicMock()
        mock_writer = MagicMock()

        # Create reporter but do NOT start it.
        # The fix requires _run_stateful_explore to start it after generation.
        reporter = ProgressReporter(unit="chains")
        assert reporter._thread is None, "reporter should not be started before the call"

        try:
            _run_stateful_explore(
                generator=mock_generator,
                executor=mock_executor,
                comparator=mock_comparator,
                comparison_rules=MagicMock(),
                writer=mock_writer,
                stats=RunStats(),
                target_a_info=TargetInfo(name="a", base_url="http://a"),
                target_b_info=TargetInfo(name="b", base_url="http://b"),
                max_chains=None,
                max_steps=6,
                seed=42,
                get_operation_rules=lambda rules, op_id: MagicMock(),
                progress_reporter=reporter,
            )

            # After the call, the reporter must have been started
            assert reporter._thread is not None, (
                "_run_stateful_explore must call progress_reporter.start() -- "
                "without this, the caller starts it too early (before generation) "
                "and the user sees stale '0 chains | 0.0/s' during seed walking"
            )

            # The start time must be AFTER chain generation ended.
            # This ensures the rate calculation (completed/elapsed) reflects
            # execution time only, not generation + execution time.
            assert generation_end_time is not None
            assert reporter._start_time >= generation_end_time, (
                f"reporter._start_time ({reporter._start_time}) must be >= "
                f"generation_end_time ({generation_end_time}) -- "
                "starting before generation distorts rate and ETA"
            )
        finally:
            reporter.stop()

    def test_progress_reporter_has_total_before_start(self):
        """The progress reporter's total must be set before it starts printing.

        When total is None and completed is 0, the reporter prints the stale:
            [Progress] 0 chains | 0.0/s | Elapsed: 1m0s
        The fix ensures set_total() is called before start(), so the very first
        progress line shows a meaningful percentage.
        """
        from unittest.mock import MagicMock

        from api_parity.artifact_writer import RunStats
        from api_parity.cli import ProgressReporter, _run_stateful_explore
        from api_parity.models import ChainExecution, TargetInfo

        mock_generator = MagicMock()
        mock_generator.generate_chains.return_value = [
            self._make_chain(["op1", "op2"], "c1"),
        ]
        mock_generator.get_linked_operation_ids.return_value = {"op1", "op2"}
        mock_generator.get_all_operation_ids.return_value = {"op1", "op2"}
        mock_generator.get_link_edges.return_value = [("op1", "op2")]

        mock_executor = MagicMock()
        mock_executor.execute_chain.return_value = (
            ChainExecution(steps=[]),
            ChainExecution(steps=[]),
        )

        reporter = ProgressReporter(unit="chains")

        # Track when start() is called and what total is at that moment
        total_at_start_time = None
        original_start = reporter.start

        def spy_start():
            nonlocal total_at_start_time
            total_at_start_time = reporter._total
            original_start()

        reporter.start = spy_start

        try:
            _run_stateful_explore(
                generator=mock_generator,
                executor=mock_executor,
                comparator=MagicMock(),
                comparison_rules=MagicMock(),
                writer=MagicMock(),
                stats=RunStats(),
                target_a_info=TargetInfo(name="a", base_url="http://a"),
                target_b_info=TargetInfo(name="b", base_url="http://b"),
                max_chains=None,
                max_steps=6,
                seed=42,
                get_operation_rules=lambda rules, op_id: MagicMock(),
                progress_reporter=reporter,
            )

            # start() must have been called
            assert total_at_start_time is not None, (
                "progress_reporter.start() was never called by _run_stateful_explore"
            )

            # At the time start() was called, total must already be set
            assert total_at_start_time > 0, (
                f"total was {total_at_start_time} when start() was called -- "
                "must be set before starting so the first progress line shows a percentage"
            )
        finally:
            reporter.stop()
