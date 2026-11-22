"""Render OpenAPI path templates without treating parameter data as URL syntax."""

import re
from typing import Any
from urllib.parse import quote, unquote


_PLACEHOLDER = re.compile(r"\{([^{}]+)\}")


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
