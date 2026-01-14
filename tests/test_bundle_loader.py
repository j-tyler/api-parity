"""Tests for bundle_loader module."""

import json
from pathlib import Path

import pytest

from api_parity.bundle_loader import (
    BundleLoadError,
    BundleType,
    LoadedBundle,
    detect_bundle_type,
    discover_bundles,
    extract_link_fields_from_chain,
    load_bundle,
)
from api_parity.models import ChainCase


# =============================================================================
# Test Data Fixtures
# =============================================================================


@pytest.fixture
def sample_request_case():
    """A minimal valid RequestCase as dict."""
    return {
        "case_id": "test-case-001",
        "operation_id": "getWidget",
        "method": "GET",
        "path_template": "/widgets/{id}",
        "path_parameters": {"id": "abc123"},
        "rendered_path": "/widgets/abc123",
        "query": {},
        "headers": {},
        "cookies": {},
        "body": None,
        "body_base64": None,
        "media_type": None,
    }


@pytest.fixture
def sample_chain_case():
    """A minimal valid ChainCase as dict."""
    return {
        "chain_id": "chain-001",
        "steps": [
            {
                "step_index": 0,
                "request_template": {
                    "case_id": "step-0",
                    "operation_id": "createWidget",
                    "method": "POST",
                    "path_template": "/widgets",
                    "path_parameters": {},
                    "rendered_path": "/widgets",
                    "query": {},
                    "headers": {},
                    "cookies": {},
                    "body": {"name": "test"},
                    "body_base64": None,
                    "media_type": "application/json",
                },
                "link_source": None,
            },
            {
                "step_index": 1,
                "request_template": {
                    "case_id": "step-1",
                    "operation_id": "getWidget",
                    "method": "GET",
                    "path_template": "/widgets/{id}",
                    "path_parameters": {},
                    "rendered_path": "/widgets/{id}",
                    "query": {},
                    "headers": {},
                    "cookies": {},
                    "body": None,
                    "body_base64": None,
                    "media_type": None,
                },
                "link_source": {"step": 0, "field": "$.id"},
            },
        ],
    }


@pytest.fixture
def sample_stateless_diff():
    """A minimal valid stateless diff as dict."""
    return {
        "type": "stateless",
        "match": False,
        "mismatch_type": "body",
        "summary": "body mismatch at $.id",
        "details": {
            "status_code": {"match": True, "differences": []},
            "headers": {"match": True, "differences": []},
            "body": {
                "match": False,
                "differences": [
                    {"path": "$.id", "target_a": "abc", "target_b": "xyz", "rule": "exact_match"}
                ],
            },
        },
    }


@pytest.fixture
def sample_chain_diff():
    """A minimal valid chain diff as dict."""
    return {
        "type": "chain",
        "match": False,
        "mismatch_step": 1,
        "total_steps": 2,
        "steps": [
            {
                "match": True,
                "mismatch_type": None,
                "summary": "match",
                "details": {
                    "status_code": {"match": True, "differences": []},
                    "headers": {"match": True, "differences": []},
                    "body": {"match": True, "differences": []},
                },
            },
            {
                "match": False,
                "mismatch_type": "body",
                "summary": "body mismatch at $.name",
                "details": {
                    "status_code": {"match": True, "differences": []},
                    "headers": {"match": True, "differences": []},
                    "body": {
                        "match": False,
                        "differences": [
                            {"path": "$.name", "target_a": "foo", "target_b": "bar", "rule": "exact_match"}
                        ],
                    },
                },
            },
        ],
    }


@pytest.fixture
def sample_metadata():
    """A minimal valid MismatchMetadata as dict."""
    return {
        "tool_version": "0.1.0",
        "timestamp": "2024-01-15T10:30:00Z",
        "seed": 42,
        "target_a": {"name": "production", "base_url": "https://api.example.com"},
        "target_b": {"name": "staging", "base_url": "https://staging.example.com"},
        "comparison_rules_applied": "operation",
    }


@pytest.fixture
def stateless_bundle_dir(tmp_path, sample_request_case, sample_stateless_diff, sample_metadata):
    """Create a complete stateless bundle directory."""
    bundle_dir = tmp_path / "mismatches" / "20240115T103000__getWidget__test-cas"
    bundle_dir.mkdir(parents=True)

    (bundle_dir / "case.json").write_text(json.dumps(sample_request_case))
    (bundle_dir / "diff.json").write_text(json.dumps(sample_stateless_diff))
    (bundle_dir / "metadata.json").write_text(json.dumps(sample_metadata))
    # target_a.json and target_b.json not needed for loading case

    return bundle_dir


@pytest.fixture
def chain_bundle_dir(tmp_path, sample_chain_case, sample_chain_diff, sample_metadata):
    """Create a complete chain bundle directory."""
    bundle_dir = tmp_path / "mismatches" / "20240115T103000__chain__createWidget__chain-00"
    bundle_dir.mkdir(parents=True)

    (bundle_dir / "chain.json").write_text(json.dumps(sample_chain_case))
    (bundle_dir / "diff.json").write_text(json.dumps(sample_chain_diff))
    (bundle_dir / "metadata.json").write_text(json.dumps(sample_metadata))

    return bundle_dir


# =============================================================================
# discover_bundles Tests
# =============================================================================


class TestDiscoverBundles:
    def test_discovers_stateless_bundle(self, stateless_bundle_dir):
        """Test that stateless bundles are discovered."""
        root_dir = stateless_bundle_dir.parent.parent
        bundles = discover_bundles(root_dir)

        assert len(bundles) == 1
        assert bundles[0] == stateless_bundle_dir

    def test_discovers_chain_bundle(self, chain_bundle_dir):
        """Test that chain bundles are discovered."""
        root_dir = chain_bundle_dir.parent.parent
        bundles = discover_bundles(root_dir)

        assert len(bundles) == 1
        assert bundles[0] == chain_bundle_dir

    def test_discovers_multiple_bundles_sorted(self, tmp_path, sample_request_case, sample_stateless_diff, sample_metadata):
        """Test that multiple bundles are discovered and sorted by timestamp."""
        mismatches_dir = tmp_path / "mismatches"
        mismatches_dir.mkdir()

        # Create bundles with different timestamps
        for timestamp in ["20240115T100000", "20240115T120000", "20240115T110000"]:
            bundle_dir = mismatches_dir / f"{timestamp}__op__case123"
            bundle_dir.mkdir()
            (bundle_dir / "case.json").write_text(json.dumps(sample_request_case))
            (bundle_dir / "diff.json").write_text(json.dumps(sample_stateless_diff))
            (bundle_dir / "metadata.json").write_text(json.dumps(sample_metadata))

        bundles = discover_bundles(tmp_path)

        assert len(bundles) == 3
        # Should be sorted by name (timestamp)
        assert bundles[0].name.startswith("20240115T100000")
        assert bundles[1].name.startswith("20240115T110000")
        assert bundles[2].name.startswith("20240115T120000")

    def test_empty_directory(self, tmp_path):
        """Test that empty directory returns empty list."""
        bundles = discover_bundles(tmp_path)
        assert bundles == []

    def test_nonexistent_directory(self, tmp_path):
        """Test that nonexistent directory returns empty list."""
        bundles = discover_bundles(tmp_path / "nonexistent")
        assert bundles == []

    def test_ignores_non_bundle_directories(self, tmp_path, sample_request_case, sample_stateless_diff, sample_metadata):
        """Test that directories without case.json or chain.json are ignored."""
        mismatches_dir = tmp_path / "mismatches"
        mismatches_dir.mkdir()

        # Create a valid bundle
        valid_bundle = mismatches_dir / "20240115T100000__op__case123"
        valid_bundle.mkdir()
        (valid_bundle / "case.json").write_text(json.dumps(sample_request_case))
        (valid_bundle / "diff.json").write_text(json.dumps(sample_stateless_diff))
        (valid_bundle / "metadata.json").write_text(json.dumps(sample_metadata))

        # Create an invalid directory (no case.json)
        invalid_dir = mismatches_dir / "20240115T110000__invalid"
        invalid_dir.mkdir()
        (invalid_dir / "something.txt").write_text("not a bundle")

        bundles = discover_bundles(tmp_path)

        assert len(bundles) == 1
        assert bundles[0] == valid_bundle

    def test_searches_mismatches_subdirectory(self, tmp_path, sample_request_case, sample_stateless_diff, sample_metadata):
        """Test that bundles in 'mismatches' subdirectory are found."""
        # Create bundle in mismatches subdirectory
        bundle_dir = tmp_path / "mismatches" / "20240115T100000__op__case123"
        bundle_dir.mkdir(parents=True)
        (bundle_dir / "case.json").write_text(json.dumps(sample_request_case))
        (bundle_dir / "diff.json").write_text(json.dumps(sample_stateless_diff))
        (bundle_dir / "metadata.json").write_text(json.dumps(sample_metadata))

        bundles = discover_bundles(tmp_path)

        assert len(bundles) == 1


# =============================================================================
# detect_bundle_type Tests
# =============================================================================


class TestDetectBundleType:
    def test_detects_stateless_from_diff_json(self, stateless_bundle_dir):
        """Test that stateless type is detected from diff.json."""
        bundle_type = detect_bundle_type(stateless_bundle_dir)
        assert bundle_type == BundleType.STATELESS

    def test_detects_chain_from_diff_json(self, chain_bundle_dir):
        """Test that chain type is detected from diff.json."""
        bundle_type = detect_bundle_type(chain_bundle_dir)
        assert bundle_type == BundleType.CHAIN

    def test_fallback_to_case_json(self, tmp_path, sample_request_case):
        """Test fallback to case.json when diff.json has no type."""
        bundle_dir = tmp_path / "bundle"
        bundle_dir.mkdir()
        (bundle_dir / "case.json").write_text(json.dumps(sample_request_case))
        (bundle_dir / "diff.json").write_text(json.dumps({"match": False}))  # No type field

        bundle_type = detect_bundle_type(bundle_dir)
        assert bundle_type == BundleType.STATELESS

    def test_fallback_to_chain_json(self, tmp_path, sample_chain_case):
        """Test fallback to chain.json when diff.json has no type."""
        bundle_dir = tmp_path / "bundle"
        bundle_dir.mkdir()
        (bundle_dir / "chain.json").write_text(json.dumps(sample_chain_case))
        (bundle_dir / "diff.json").write_text(json.dumps({"match": False}))  # No type field

        bundle_type = detect_bundle_type(bundle_dir)
        assert bundle_type == BundleType.CHAIN

    def test_error_when_cannot_determine_type(self, tmp_path):
        """Test error when bundle type cannot be determined."""
        bundle_dir = tmp_path / "bundle"
        bundle_dir.mkdir()
        # No case.json, chain.json, or valid diff.json

        with pytest.raises(BundleLoadError, match="Cannot determine bundle type"):
            detect_bundle_type(bundle_dir)


# =============================================================================
# load_bundle Tests
# =============================================================================


class TestLoadBundle:
    def test_load_stateless_bundle(self, stateless_bundle_dir):
        """Test loading a complete stateless bundle."""
        bundle = load_bundle(stateless_bundle_dir)

        assert isinstance(bundle, LoadedBundle)
        assert bundle.bundle_type == BundleType.STATELESS
        assert bundle.request_case is not None
        assert bundle.chain_case is None
        assert bundle.request_case.operation_id == "getWidget"
        assert bundle.original_diff["type"] == "stateless"
        assert bundle.metadata.tool_version == "0.1.0"

    def test_load_chain_bundle(self, chain_bundle_dir):
        """Test loading a complete chain bundle."""
        bundle = load_bundle(chain_bundle_dir)

        assert isinstance(bundle, LoadedBundle)
        assert bundle.bundle_type == BundleType.CHAIN
        assert bundle.chain_case is not None
        assert bundle.request_case is None
        assert len(bundle.chain_case.steps) == 2
        assert bundle.original_diff["type"] == "chain"

    def test_error_on_missing_diff_json(self, tmp_path, sample_request_case, sample_metadata):
        """Test error when diff.json is missing."""
        bundle_dir = tmp_path / "bundle"
        bundle_dir.mkdir()
        (bundle_dir / "case.json").write_text(json.dumps(sample_request_case))
        (bundle_dir / "metadata.json").write_text(json.dumps(sample_metadata))
        # No diff.json

        with pytest.raises(BundleLoadError, match="Missing diff.json"):
            load_bundle(bundle_dir)

    def test_error_on_missing_metadata_json(self, tmp_path, sample_request_case, sample_stateless_diff):
        """Test error when metadata.json is missing."""
        bundle_dir = tmp_path / "bundle"
        bundle_dir.mkdir()
        (bundle_dir / "case.json").write_text(json.dumps(sample_request_case))
        (bundle_dir / "diff.json").write_text(json.dumps(sample_stateless_diff))
        # No metadata.json

        with pytest.raises(BundleLoadError, match="Missing metadata.json"):
            load_bundle(bundle_dir)

    def test_error_on_missing_case_json_for_stateless(self, tmp_path, sample_stateless_diff, sample_metadata):
        """Test error when case.json is missing for stateless bundle."""
        bundle_dir = tmp_path / "bundle"
        bundle_dir.mkdir()
        (bundle_dir / "diff.json").write_text(json.dumps(sample_stateless_diff))
        (bundle_dir / "metadata.json").write_text(json.dumps(sample_metadata))
        # No case.json

        with pytest.raises(BundleLoadError, match="Missing case.json"):
            load_bundle(bundle_dir)

    def test_error_on_missing_chain_json_for_chain(self, tmp_path, sample_chain_diff, sample_metadata):
        """Test error when chain.json is missing for chain bundle."""
        bundle_dir = tmp_path / "bundle"
        bundle_dir.mkdir()
        (bundle_dir / "diff.json").write_text(json.dumps(sample_chain_diff))
        (bundle_dir / "metadata.json").write_text(json.dumps(sample_metadata))
        # No chain.json

        with pytest.raises(BundleLoadError, match="Missing chain.json"):
            load_bundle(bundle_dir)

    def test_error_on_invalid_json(self, tmp_path, sample_request_case, sample_metadata):
        """Test error when JSON is invalid."""
        bundle_dir = tmp_path / "bundle"
        bundle_dir.mkdir()
        (bundle_dir / "case.json").write_text(json.dumps(sample_request_case))
        (bundle_dir / "diff.json").write_text("not valid json {")
        (bundle_dir / "metadata.json").write_text(json.dumps(sample_metadata))

        with pytest.raises(BundleLoadError, match="Invalid JSON"):
            load_bundle(bundle_dir)

    def test_error_on_invalid_request_case(self, tmp_path, sample_stateless_diff, sample_metadata):
        """Test error when case.json doesn't match RequestCase schema."""
        bundle_dir = tmp_path / "bundle"
        bundle_dir.mkdir()
        # Invalid case: missing required fields
        (bundle_dir / "case.json").write_text(json.dumps({"invalid": "data"}))
        (bundle_dir / "diff.json").write_text(json.dumps(sample_stateless_diff))
        (bundle_dir / "metadata.json").write_text(json.dumps(sample_metadata))

        with pytest.raises(BundleLoadError, match="Invalid case.json"):
            load_bundle(bundle_dir)

    def test_error_on_not_directory(self, tmp_path):
        """Test error when path is not a directory."""
        file_path = tmp_path / "not_a_dir.txt"
        file_path.write_text("hello")

        with pytest.raises(BundleLoadError, match="not a directory"):
            load_bundle(file_path)

    def test_bundle_path_stored(self, stateless_bundle_dir):
        """Test that bundle_path is stored in LoadedBundle."""
        bundle = load_bundle(stateless_bundle_dir)
        assert bundle.bundle_path == stateless_bundle_dir


# =============================================================================
# BundleType Enum Tests
# =============================================================================


class TestBundleType:
    def test_stateless_value(self):
        """Test BundleType.STATELESS has correct value."""
        assert BundleType.STATELESS.value == "stateless"

    def test_chain_value(self):
        """Test BundleType.CHAIN has correct value."""
        assert BundleType.CHAIN.value == "chain"


# =============================================================================
# extract_link_fields_from_chain Tests
# =============================================================================


class TestExtractLinkFieldsFromChain:
    def test_extracts_simple_field(self):
        """Test extraction of simple JSONPath field."""
        chain_data = {
            "chain_id": "test",
            "steps": [
                {
                    "step_index": 0,
                    "request_template": {
                        "case_id": "s0", "operation_id": "create", "method": "POST",
                        "path_template": "/items", "path_parameters": {},
                        "rendered_path": "/items", "query": {}, "headers": {},
                        "cookies": {}, "body": None, "body_base64": None, "media_type": None
                    },
                    "link_source": None
                },
                {
                    "step_index": 1,
                    "request_template": {
                        "case_id": "s1", "operation_id": "get", "method": "GET",
                        "path_template": "/items/{id}", "path_parameters": {},
                        "rendered_path": "/items/{id}", "query": {}, "headers": {},
                        "cookies": {}, "body": None, "body_base64": None, "media_type": None
                    },
                    "link_source": {"step": 0, "field": "$.id"}
                }
            ]
        }
        chain = ChainCase.model_validate(chain_data)
        fields = extract_link_fields_from_chain(chain)
        assert fields == {"id"}

    def test_extracts_nested_field(self):
        """Test extraction of nested JSONPath field."""
        chain_data = {
            "chain_id": "test",
            "steps": [
                {
                    "step_index": 0,
                    "request_template": {
                        "case_id": "s0", "operation_id": "create", "method": "POST",
                        "path_template": "/items", "path_parameters": {},
                        "rendered_path": "/items", "query": {}, "headers": {},
                        "cookies": {}, "body": None, "body_base64": None, "media_type": None
                    },
                    "link_source": None
                },
                {
                    "step_index": 1,
                    "request_template": {
                        "case_id": "s1", "operation_id": "get", "method": "GET",
                        "path_template": "/items/{id}", "path_parameters": {},
                        "rendered_path": "/items/{id}", "query": {}, "headers": {},
                        "cookies": {}, "body": None, "body_base64": None, "media_type": None
                    },
                    "link_source": {"step": 0, "field": "$.data.item.id"}
                }
            ]
        }
        chain = ChainCase.model_validate(chain_data)
        fields = extract_link_fields_from_chain(chain)
        assert fields == {"data/item/id"}

    def test_extracts_array_index(self):
        """Test extraction of field with array index."""
        chain_data = {
            "chain_id": "test",
            "steps": [
                {
                    "step_index": 0,
                    "request_template": {
                        "case_id": "s0", "operation_id": "list", "method": "GET",
                        "path_template": "/items", "path_parameters": {},
                        "rendered_path": "/items", "query": {}, "headers": {},
                        "cookies": {}, "body": None, "body_base64": None, "media_type": None
                    },
                    "link_source": None
                },
                {
                    "step_index": 1,
                    "request_template": {
                        "case_id": "s1", "operation_id": "get", "method": "GET",
                        "path_template": "/items/{id}", "path_parameters": {},
                        "rendered_path": "/items/{id}", "query": {}, "headers": {},
                        "cookies": {}, "body": None, "body_base64": None, "media_type": None
                    },
                    "link_source": {"step": 0, "field": "$.items[0].id"}
                }
            ]
        }
        chain = ChainCase.model_validate(chain_data)
        fields = extract_link_fields_from_chain(chain)
        assert fields == {"items/0/id"}

    def test_extracts_multiple_fields(self):
        """Test extraction of multiple different fields."""
        chain_data = {
            "chain_id": "test",
            "steps": [
                {
                    "step_index": 0,
                    "request_template": {
                        "case_id": "s0", "operation_id": "create", "method": "POST",
                        "path_template": "/items", "path_parameters": {},
                        "rendered_path": "/items", "query": {}, "headers": {},
                        "cookies": {}, "body": None, "body_base64": None, "media_type": None
                    },
                    "link_source": None
                },
                {
                    "step_index": 1,
                    "request_template": {
                        "case_id": "s1", "operation_id": "get", "method": "GET",
                        "path_template": "/items/{id}", "path_parameters": {},
                        "rendered_path": "/items/{id}", "query": {}, "headers": {},
                        "cookies": {}, "body": None, "body_base64": None, "media_type": None
                    },
                    "link_source": {"step": 0, "field": "$.id"}
                },
                {
                    "step_index": 2,
                    "request_template": {
                        "case_id": "s2", "operation_id": "update", "method": "PUT",
                        "path_template": "/items/{id}", "path_parameters": {},
                        "rendered_path": "/items/{id}", "query": {}, "headers": {},
                        "cookies": {}, "body": None, "body_base64": None, "media_type": None
                    },
                    "link_source": {"step": 0, "field": "$.version"}
                }
            ]
        }
        chain = ChainCase.model_validate(chain_data)
        fields = extract_link_fields_from_chain(chain)
        assert fields == {"id", "version"}

    def test_empty_chain(self):
        """Test with chain with no link_source fields."""
        chain_data = {
            "chain_id": "test",
            "steps": [
                {
                    "step_index": 0,
                    "request_template": {
                        "case_id": "s0", "operation_id": "list", "method": "GET",
                        "path_template": "/items", "path_parameters": {},
                        "rendered_path": "/items", "query": {}, "headers": {},
                        "cookies": {}, "body": None, "body_base64": None, "media_type": None
                    },
                    "link_source": None
                }
            ]
        }
        chain = ChainCase.model_validate(chain_data)
        fields = extract_link_fields_from_chain(chain)
        assert fields == set()

    def test_ignores_invalid_jsonpath(self):
        """Test that invalid JSONPath formats are ignored."""
        chain_data = {
            "chain_id": "test",
            "steps": [
                {
                    "step_index": 0,
                    "request_template": {
                        "case_id": "s0", "operation_id": "create", "method": "POST",
                        "path_template": "/items", "path_parameters": {},
                        "rendered_path": "/items", "query": {}, "headers": {},
                        "cookies": {}, "body": None, "body_base64": None, "media_type": None
                    },
                    "link_source": None
                },
                {
                    "step_index": 1,
                    "request_template": {
                        "case_id": "s1", "operation_id": "get", "method": "GET",
                        "path_template": "/items/{id}", "path_parameters": {},
                        "rendered_path": "/items/{id}", "query": {}, "headers": {},
                        "cookies": {}, "body": None, "body_base64": None, "media_type": None
                    },
                    # Invalid: doesn't start with $.
                    "link_source": {"step": 0, "field": "id"}
                }
            ]
        }
        chain = ChainCase.model_validate(chain_data)
        fields = extract_link_fields_from_chain(chain)
        assert fields == set()
