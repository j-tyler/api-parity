"""XML-to-dict and dict-to-XML conversion for API request/response bodies.

Converts XML response bodies into Python dicts so the comparator pipeline
(JSONPath rules, CEL expressions) works identically for XML and JSON APIs.
Converts Python dicts into XML request bodies so the executor can send
valid XML for operations that require it (e.g., S3 DeleteObjects).

Limitation: OpenAPI XML annotations (xml:name, xml:attribute, xml:wrapped,
xml:prefix, xml:namespace) are NOT respected. Element names are taken directly
from dict keys. This works for APIs where XML element names match JSON Schema
property names (e.g., S3), but will produce incorrect XML for APIs that rely
on these annotations to rename or restructure elements. See DESIGN.md
"XML Body Conversion" for the rationale.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any


# ---------------------------------------------------------------------------
# XML bytes → Python dict  (response parsing)
# ---------------------------------------------------------------------------


def xml_to_dict(
    xml_bytes: bytes,
    force_list: set[str] | None = None,
) -> dict[str, Any]:
    """Convert XML bytes into a JSON-compatible dict.

    Strips XML namespace URIs from tag names so that
    ``{http://s3.amazonaws.com/doc/2006-03-01/}Name`` becomes ``Name``.

    Args:
        xml_bytes: Raw XML response body.
        force_list: Tag names that must always be wrapped in a list, even
            when only a single child element exists.  Solves the
            single-vs-list ambiguity inherent in XML-to-dict conversion.
            Example: ``{"Contents", "Bucket"}`` for S3 responses.

    Returns:
        Dict with the root element tag as the single top-level key.

    Raises:
        ET.ParseError: If *xml_bytes* is not well-formed XML.
    """
    force_list = force_list or set()
    root = ET.fromstring(xml_bytes)
    return {_strip_ns(root.tag): _element_to_dict(root, force_list)}


def _strip_ns(tag: str) -> str:
    """Remove namespace URI prefix: ``{http://...}Name`` → ``Name``."""
    if tag.startswith("{"):
        return tag.split("}", 1)[1]
    return tag


def _element_to_dict(
    element: ET.Element,
    force_list: set[str],
) -> dict[str, Any] | str | None:
    """Recursively convert a single XML element to a dict, string, or None.

    Conversion rules:
    - Attributes → ``@attr_name`` keys (xmlns declarations are skipped).
    - Child elements → grouped by tag name.  If a tag appears more than once
      OR is in *force_list*, the value is a list.  Otherwise it is a scalar.
    - Text-only leaf elements → plain string.
    - Empty elements (``<Prefix/>``) → None.
    - Elements with both attributes/children AND text → ``#text`` key.
    """
    result: dict[str, Any] = {}

    # --- Attributes (skip namespace declarations) ---
    for attr_name, attr_value in element.attrib.items():
        if attr_name.startswith("xmlns") or attr_name.startswith("{"):
            continue
        result[f"@{attr_name}"] = attr_value

    # --- Child elements, grouped by stripped tag name ---
    children_by_tag: dict[str, list[Any]] = {}
    for child in element:
        tag = _strip_ns(child.tag)
        children_by_tag.setdefault(tag, []).append(
            _element_to_dict(child, force_list)
        )

    for tag, values in children_by_tag.items():
        if tag in force_list or len(values) > 1:
            result[tag] = values
        else:
            result[tag] = values[0]

    # --- Text content ---
    text = (element.text or "").strip()
    if text:
        if result:
            # Element has attributes or children AND text
            result["#text"] = text
        else:
            # Leaf element with only text content
            return text

    # Empty element with no attributes, children, or text
    if not result:
        return None

    return result


# ---------------------------------------------------------------------------
# Python dict → XML bytes  (request serialization)
# ---------------------------------------------------------------------------


def dict_to_xml(data: dict[str, Any]) -> bytes:
    """Convert a Python dict to XML bytes for use as an HTTP request body.

    The dict must have exactly one top-level key, which becomes the root
    element name.  Nested dicts become child elements.  Lists become
    repeated sibling elements with the same tag name.  ``None`` values
    become empty elements (``<Tag/>``).  Scalar values (str, int, float,
    bool) become text content.

    This is a simple recursive serializer that does NOT support:
    - XML attributes (``@attr`` keys are skipped with a warning-level comment)
    - XML namespaces
    - OpenAPI XML annotations (xml:name, xml:wrapped, etc.)

    Args:
        data: Dict with exactly one top-level key (the root element name).

    Returns:
        UTF-8 encoded XML bytes with an XML declaration.

    Raises:
        ValueError: If *data* does not have exactly one top-level key.
    """
    if not isinstance(data, dict) or len(data) != 1:
        raise ValueError(
            f"dict_to_xml expects a dict with exactly one top-level key "
            f"(the root element), got {type(data).__name__} with "
            f"{len(data) if isinstance(data, dict) else 'N/A'} keys"
        )

    root_tag = next(iter(data))
    root_value = data[root_tag]

    root_element = _dict_to_element(root_tag, root_value)

    ET.indent(root_element)
    return ET.tostring(root_element, encoding="utf-8", xml_declaration=True)


def _dict_to_element(tag: str, value: Any) -> ET.Element:
    """Recursively convert a tag + value pair into an XML Element.

    Conversion rules (inverse of xml_to_dict):
    - dict → element with child sub-elements for each key
    - list → caller handles by creating repeated sibling elements
    - str/int/float/bool → element with text content
    - None → empty element
    """
    element = ET.Element(tag)

    if value is None:
        # Empty element: <Tag/>
        pass
    elif isinstance(value, dict):
        for key, child_value in value.items():
            # Skip attribute keys — dict_to_xml does not emit XML attributes
            # because the dicts originate from JSON Schema-generated data
            # which has no concept of XML attributes.
            if key.startswith("@") or key == "#text":
                if key == "#text":
                    element.text = str(child_value)
                continue
            if isinstance(child_value, list):
                # List → repeated sibling elements with the same tag
                for item in child_value:
                    element.append(_dict_to_element(key, item))
            else:
                element.append(_dict_to_element(key, child_value))
    elif isinstance(value, list):
        # Top-level list should not happen (root must be a dict), but handle
        # gracefully by wrapping each item as a child named "item".
        for item in value:
            element.append(_dict_to_element("item", item))
    else:
        # Scalar: str, int, float, bool
        element.text = str(value)

    return element
