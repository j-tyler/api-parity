"""Bundle Loader - Loads mismatch bundles from disk for replay.

The Bundle Loader reads mismatch bundles written by ArtifactWriter during
explore runs and reconstructs them for replay execution.

See ARCHITECTURE.md "Mismatch Report Bundle" for bundle specifications.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from api_parity.case_generator import HeaderRef, LinkFields, LINK_HEADER_PATTERN
from api_parity.models import (
    ChainCase,
    MismatchMetadata,
    RequestCase,
)


class BundleType(Enum):
    """Type of mismatch bundle."""

    STATELESS = "stateless"
    CHAIN = "chain"


class BundleLoadError(Exception):
    """Error loading a mismatch bundle."""

    pass


@dataclass
class LoadedBundle:
    """A mismatch bundle loaded from disk.

    Contains the original case (request or chain), the original comparison
    result, and metadata for context. For stateless bundles, request_case
    is populated. For chain bundles, chain_case is populated.
    """

    bundle_path: Path
    bundle_type: BundleType
    # For stateless bundles
    request_case: RequestCase | None
    # For chain bundles
    chain_case: ChainCase | None
    # Original comparison result (for detecting if mismatch changed)
    original_diff: dict[str, Any]
    # Metadata for context
    metadata: MismatchMetadata


def extract_link_fields_from_chain(chain: ChainCase) -> LinkFields:
    """Extract link_fields from a ChainCase for variable extraction.

    During replay, we don't have the OpenAPI spec to extract link_fields.
    Instead, we analyze the chain's link_source fields to determine which
    response fields need to be extracted.

    Args:
        chain: The ChainCase to extract link_fields from.

    Returns:
        LinkFields with body_pointers and headers needed for
        variable extraction during chain execution.
    """
    link_fields = LinkFields()

    def _extract_from_expression(expr: str) -> None:
        """Extract link field from a single expression."""
        # Check for header expression: $response.header.HeaderName or HeaderName[index]
        header_match = LINK_HEADER_PATTERN.match(expr)
        if header_match:
            # Preserve original case for HeaderRef consistency (matches case_generator.py)
            original_name = header_match.group(1)
            header_name = original_name.lower()
            index_str = header_match.group(2)
            index = int(index_str) if index_str is not None else None
            link_fields.headers.append(HeaderRef(
                name=header_name,
                original_name=original_name,
                index=index,
            ))
            return

        # Check for body expression: $response.body#/path
        if expr.startswith("$response.body#/"):
            json_pointer = expr[len("$response.body#/"):]
            if json_pointer:
                link_fields.body_pointers.add(json_pointer)

    for step in chain.steps:
        if step.link_source is None:
            continue

        # Try new format: "parameters" dict with all expressions
        parameters = step.link_source.get("parameters")
        if isinstance(parameters, dict):
            for expr in parameters.values():
                if isinstance(expr, str):
                    _extract_from_expression(expr)
        else:
            # Fall back to old format: single "field" expression
            field = step.link_source.get("field")
            if isinstance(field, str):
                _extract_from_expression(field)

    return link_fields


def discover_bundles(directory: Path) -> list[Path]:
    """Find all mismatch bundle directories in a given path.

    Searches the 'mismatches' subdirectory (if present) or the directory
    itself for bundle directories. A bundle directory must contain either
    case.json (stateless) or chain.json (chain).

    Args:
        directory: Directory to search for bundles.

    Returns:
        List of bundle directory paths, sorted by name (oldest first,
        since bundle names start with timestamps).
    """
    bundles: list[Path] = []

    # Check for mismatches subdirectory (standard explore output structure)
    mismatches_dir = directory / "mismatches"
    search_dir = mismatches_dir if mismatches_dir.is_dir() else directory

    if not search_dir.is_dir():
        return []

    # Find all subdirectories that look like bundles
    for item in search_dir.iterdir():
        if not item.is_dir():
            continue

        # Check for case.json (stateless) or chain.json (chain)
        has_case = (item / "case.json").is_file()
        has_chain = (item / "chain.json").is_file()

        if has_case or has_chain:
            bundles.append(item)

    # Sort by name (timestamp prefix ensures chronological order)
    bundles.sort(key=lambda p: p.name)

    return bundles


def _detect_bundle_type_from_data(
    diff_data: dict | None, bundle_path: Path
) -> BundleType:
    """Determine bundle type from already-loaded diff data and file presence.

    Args:
        diff_data: Parsed diff.json contents, or None if not available.
        bundle_path: Path to the bundle directory for file existence checks.

    Returns:
        BundleType indicating stateless or chain.

    Raises:
        BundleLoadError: If bundle type cannot be determined.
    """
    # Primary: Check diff_data type field
    if diff_data is not None:
        bundle_type = diff_data.get("type")
        if bundle_type == "stateless":
            return BundleType.STATELESS
        elif bundle_type == "chain":
            return BundleType.CHAIN

    # Fallback: Check which case file exists
    if (bundle_path / "chain.json").is_file():
        return BundleType.CHAIN
    elif (bundle_path / "case.json").is_file():
        return BundleType.STATELESS

    raise BundleLoadError(
        f"Cannot determine bundle type: no diff.json type field and "
        f"neither case.json nor chain.json found in {bundle_path}"
    )


def detect_bundle_type(bundle_path: Path) -> BundleType:
    """Determine if bundle is stateless or chain type.

    Primary detection: Check diff.json for 'type' field.
    Fallback: Check for chain.json vs case.json file.

    Args:
        bundle_path: Path to the bundle directory.

    Returns:
        BundleType indicating stateless or chain.

    Raises:
        BundleLoadError: If bundle type cannot be determined.
    """
    diff_path = bundle_path / "diff.json"
    diff_data = None

    # Try to load diff.json for type detection
    if diff_path.is_file():
        try:
            with open(diff_path, encoding="utf-8") as f:
                diff_data = json.load(f)
        except (json.JSONDecodeError, OSError):
            pass  # Fall through to filename detection

    return _detect_bundle_type_from_data(diff_data, bundle_path)


def load_bundle(bundle_path: Path) -> LoadedBundle:
    """Load a single mismatch bundle from disk.

    Reads the bundle files and reconstructs the appropriate models.
    Validates that required files exist and contain valid data.

    Args:
        bundle_path: Path to the bundle directory.

    Returns:
        LoadedBundle containing the case and metadata.

    Raises:
        BundleLoadError: If bundle cannot be loaded.
    """
    if not bundle_path.is_dir():
        raise BundleLoadError(f"Bundle path is not a directory: {bundle_path}")

    # Load diff.json first (required, also used for type detection)
    diff_path = bundle_path / "diff.json"
    if not diff_path.is_file():
        raise BundleLoadError(f"Missing diff.json in {bundle_path}")
    try:
        with open(diff_path, encoding="utf-8") as f:
            original_diff = json.load(f)
    except json.JSONDecodeError as e:
        raise BundleLoadError(f"Invalid JSON in diff.json: {e}") from e
    except OSError as e:
        raise BundleLoadError(f"Cannot read diff.json: {e}") from e

    # Detect bundle type from already-loaded diff data
    bundle_type = _detect_bundle_type_from_data(original_diff, bundle_path)

    # Load metadata.json (required)
    metadata_path = bundle_path / "metadata.json"
    if not metadata_path.is_file():
        raise BundleLoadError(f"Missing metadata.json in {bundle_path}")
    try:
        with open(metadata_path, encoding="utf-8") as f:
            metadata_data = json.load(f)
        metadata = MismatchMetadata.model_validate(metadata_data)
    except json.JSONDecodeError as e:
        raise BundleLoadError(f"Invalid JSON in metadata.json: {e}") from e
    except Exception as e:
        raise BundleLoadError(f"Invalid metadata.json: {e}") from e

    # Load case based on type
    request_case: RequestCase | None = None
    chain_case: ChainCase | None = None

    if bundle_type == BundleType.STATELESS:
        case_path = bundle_path / "case.json"
        if not case_path.is_file():
            raise BundleLoadError(f"Missing case.json in stateless bundle {bundle_path}")
        try:
            with open(case_path, encoding="utf-8") as f:
                case_data = json.load(f)
            request_case = RequestCase.model_validate(case_data)
        except json.JSONDecodeError as e:
            raise BundleLoadError(f"Invalid JSON in case.json: {e}") from e
        except Exception as e:
            raise BundleLoadError(f"Invalid case.json: {e}") from e

    else:  # BundleType.CHAIN
        chain_path = bundle_path / "chain.json"
        if not chain_path.is_file():
            raise BundleLoadError(f"Missing chain.json in chain bundle {bundle_path}")
        try:
            with open(chain_path, encoding="utf-8") as f:
                chain_data = json.load(f)
            chain_case = ChainCase.model_validate(chain_data)
        except json.JSONDecodeError as e:
            raise BundleLoadError(f"Invalid JSON in chain.json: {e}") from e
        except Exception as e:
            raise BundleLoadError(f"Invalid chain.json: {e}") from e

    return LoadedBundle(
        bundle_path=bundle_path,
        bundle_type=bundle_type,
        request_case=request_case,
        chain_case=chain_case,
        original_diff=original_diff,
        metadata=metadata,
    )
