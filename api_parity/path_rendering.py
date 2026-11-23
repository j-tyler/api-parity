"""Render OpenAPI path templates without treating parameter data as URL syntax."""

import json
import re
from typing import Any
from urllib.parse import quote, unquote


_PLACEHOLDER = re.compile(r"\{([^{}]+)\}")


def path_parameter_names(template: str) -> list[str]:
    """Return the parameter slots recognized by render_path."""
    return _PLACEHOLDER.findall(template)


def decode_path_value(value: Any) -> Any:
    """Read an already serialized template value, preserving arbitrary escaped octets."""
    return unquote(value, errors="surrogateescape") if isinstance(value, str) else value


def _quote_segment(value: str) -> str:
    if value in (".", ".."):
        return "%2E" * len(value)
    return quote(value, safe="", errors="surrogateescape")


def _quote_uri_segment(segment: str) -> str:
    if segment in (".", ".."):
        return _quote_segment(segment)
    # RFC 3986 path characters and existing escapes in a URI reference already
    # have meaning. Escape only stray percent signs and non-path characters.
    segment = re.sub(r"%(?![0-9A-Fa-f]{2})", "%25", segment)
    return quote(segment, safe="%!$&'()*+,;=:@", errors="surrogateescape")


def collection_path(name: str, value: list | dict, options: dict) -> str | None:
    """Serialize a JSON collection for a path parameter that explicitly accepts its shape."""
    kind = "array" if isinstance(value, list) else "object"
    if kind not in options.get("types", ()):
        return None
    style = options.get("style", "simple")
    if style == "json":
        return _quote_segment(json.dumps(value, separators=(",", ":"), ensure_ascii=False))
    if style not in {"simple", "label", "matrix"} or not value:
        return None

    explode = options.get("explode", False)

    def encode(atom: Any) -> str:
        text = str(atom).lower() if isinstance(atom, bool) else str(atom)
        encoded = _quote_segment(text)
        return encoded.replace(".", "%2E") if style == "label" and explode else encoded

    values = value if isinstance(value, list) else value.values()
    if any(v is None or isinstance(v, (dict, list)) for v in values):
        return None
    prefix = {"simple": "", "label": ".", "matrix": ";"}[style]
    separator = {"simple": ",", "label": ".", "matrix": ";"}[style] if explode else ","
    if isinstance(value, dict):
        pair_separator = "=" if explode else ","
        parts = [encode(k) + pair_separator + encode(v) for k, v in value.items()]
    else:
        parts = [encode(v) for v in value]
        if style == "matrix" and explode:
            parts = [encode(name) + "=" + part for part in parts]
    if style == "matrix" and not explode:
        prefix += encode(name) + "="
    return prefix + separator.join(parts)


def render_path(template: str, values: dict[str, Any], uri_paths: dict[str, str] | None = None) -> str:
    """Interpolate logical parameter values exactly once, leaving literal template text intact."""
    def replace(match: re.Match) -> str:
        name = match.group(1)
        if name not in values:
            return match.group()
        if uri_paths and name in uri_paths:
            return "/".join(_quote_uri_segment(part) for part in uri_paths[name].split("/"))
        return _quote_segment(str(values[name]))

    return _PLACEHOLDER.sub(replace, template)
