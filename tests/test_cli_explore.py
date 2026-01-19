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
        assert args.max_cases is None
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

    def test_with_max_cases(self):
        """Test explore with optional --max-cases argument."""
        args = parse_args([
            "explore",
            "--spec", "openapi.yaml",
            "--config", "runtime.yaml",
            "--target-a", "prod",
            "--target-b", "stage",
            "--out", "./out",
            "--max-cases", "1000",
        ])

        assert isinstance(args, ExploreArgs)
        assert args.max_cases == 1000

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
            "--max-cases", "500",
        ])

        assert isinstance(args, ExploreArgs)
        assert args.seed == 123
        assert args.max_cases == 500

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

    def test_invalid_max_cases_type(self):
        """Test explore fails with non-integer --max-cases."""
        with pytest.raises(SystemExit) as exc_info:
            parse_args([
                "explore",
                "--spec", "openapi.yaml",
                "--config", "runtime.yaml",
                "--target-a", "prod",
                "--target-b", "stage",
                "--out", "./out",
                "--max-cases", "abc",
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
            "--max-cases", "50",
        ])

        assert isinstance(args, ExploreArgs)
        assert args.spec == Path("openapi.yaml")
        assert args.config == Path("runtime.yaml")
        assert args.target_a == "production"
        assert args.target_b == "staging"
        assert args.out == Path("./artifacts")
        assert args.seed == 99
        assert args.max_cases == 50

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
            max_cases=100,
            validate=False,
            exclude=["getUser"],
            timeout=60.0,
            operation_timeout=[("slowOp", 120.0)],
            stateful=False,
            max_chains=None,
            max_steps=6,
            log_chains=False,
            ensure_coverage=False,
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
            max_cases=None,
            validate=False,
            exclude=[],
            timeout=30.0,
            operation_timeout=[],
            stateful=True,
            max_chains=50,
            max_steps=10,
            log_chains=True,
            ensure_coverage=False,
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
            max_cases=None,
            validate=False,
            exclude=[],
            timeout=30.0,
            operation_timeout=[],
            stateful=True,
            max_chains=20,
            max_steps=6,
            log_chains=False,
            ensure_coverage=True,
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
            max_cases=None,
            validate=False,
            exclude=["excludedOp1", "excludedOp2"],
            timeout=30.0,
            operation_timeout=[],
            stateful=True,
            max_chains=20,
            max_steps=6,
            log_chains=False,
            ensure_coverage=True,
        )
        args = parse_explore_args(namespace)
        assert args.stateful is True
        assert args.ensure_coverage is True
        assert args.exclude == ["excludedOp1", "excludedOp2"]


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
            "--max-cases", "100",
            "--validate",
        ])
        assert args.validate is True
        assert args.seed == 42
        assert args.max_cases == 100

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
    """Tests for _generate_chains_with_seed_walking function."""

    def test_no_seed_single_pass(self, tmp_path):
        """Without seed, single pass is performed without seed walking."""
        from unittest.mock import MagicMock

        from api_parity.cli import _generate_chains_with_seed_walking
        from api_parity.models import ChainCase, ChainStep, RequestCase

        # Create mock generator
        mock_generator = MagicMock()

        def make_chain(op_ids: list[str], chain_id: str) -> ChainCase:
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

        # Return 2 chains from single pass
        mock_generator.generate_chains.return_value = [
            make_chain(["op1", "op2"], "c1"),
            make_chain(["op1", "op3"], "c2"),
        ]

        chains, seeds_used = _generate_chains_with_seed_walking(
            generator=mock_generator,
            max_chains=5,
            max_steps=6,
            starting_seed=None,
        )

        # Should have called generate_chains once with no seed
        mock_generator.generate_chains.assert_called_once_with(
            max_chains=5, max_steps=6, seed=None
        )
        assert len(chains) == 2
        assert seeds_used == []  # No seed walking

    def test_seed_walking_accumulates_chains(self, tmp_path):
        """With seed, walking accumulates unique chains across seeds."""
        from unittest.mock import MagicMock

        from api_parity.cli import _generate_chains_with_seed_walking
        from api_parity.models import ChainCase, ChainStep, RequestCase

        mock_generator = MagicMock()

        def make_chain(op_ids: list[str], chain_id: str) -> ChainCase:
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

        # Seed 42 returns 2 unique chains
        # Seed 43 returns 1 unique chain + 1 duplicate
        # Seed 44 returns 1 unique chain
        call_count = [0]

        def mock_generate(max_chains, max_steps, seed):
            call_count[0] += 1
            if seed == 42:
                return [
                    make_chain(["op1", "op2"], "c1"),
                    make_chain(["op1", "op3"], "c2"),
                ]
            elif seed == 43:
                return [
                    make_chain(["op1", "op2"], "c3"),  # Duplicate of c1
                    make_chain(["op2", "op3"], "c4"),  # New
                ]
            elif seed == 44:
                return [make_chain(["op3", "op4"], "c5")]  # New
            return []

        mock_generator.generate_chains.side_effect = mock_generate

        chains, seeds_used = _generate_chains_with_seed_walking(
            generator=mock_generator,
            max_chains=4,
            max_steps=6,
            starting_seed=42,
        )

        # Should have accumulated 4 unique chains from 3 seeds
        assert len(chains) == 4
        assert seeds_used == [42, 43, 44]

        # Verify the chains are deduplicated by signature
        sigs = [tuple(s.request_template.operation_id for s in c.steps) for c in chains]
        assert len(sigs) == len(set(sigs))  # All unique

    def test_seed_walking_stops_at_max_chains(self):
        """Seed walking stops when max_chains is reached."""
        from unittest.mock import MagicMock

        from api_parity.cli import _generate_chains_with_seed_walking
        from api_parity.models import ChainCase, ChainStep, RequestCase

        mock_generator = MagicMock()

        def make_chain(op_ids: list[str], chain_id: str) -> ChainCase:
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

        call_count = [0]

        def mock_generate(max_chains, max_steps, seed):
            call_count[0] += 1
            # Each seed returns a unique chain
            return [make_chain([f"op{seed}", f"op{seed+100}"], f"c{seed}")]

        mock_generator.generate_chains.side_effect = mock_generate

        chains, seeds_used = _generate_chains_with_seed_walking(
            generator=mock_generator,
            max_chains=3,
            max_steps=6,
            starting_seed=0,
        )

        # Should stop after 3 chains
        assert len(chains) == 3
        assert call_count[0] == 3
        assert seeds_used == [0, 1, 2]

    def test_seed_walking_respects_max_increments(self):
        """Seed walking stops after MAX_SEED_INCREMENTS even if target not reached."""
        from unittest.mock import MagicMock

        from api_parity.cli import MAX_SEED_INCREMENTS, _generate_chains_with_seed_walking
        from api_parity.models import ChainCase, ChainStep, RequestCase

        mock_generator = MagicMock()

        # Always return empty - no chains available
        mock_generator.generate_chains.return_value = []

        chains, seeds_used = _generate_chains_with_seed_walking(
            generator=mock_generator,
            max_chains=1000,  # More than we can possibly get
            max_steps=6,
            starting_seed=0,
        )

        # Should have tried MAX_SEED_INCREMENTS times
        assert mock_generator.generate_chains.call_count == MAX_SEED_INCREMENTS
        assert len(chains) == 0
        assert seeds_used == []  # No seeds contributed chains
