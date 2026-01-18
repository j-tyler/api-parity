"""Internal data models for api-parity.

All models use Pydantic v2. See ARCHITECTURE.md "Internal Data Models" for specifications.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any

from typing_extensions import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# =============================================================================
# Core HTTP Models
# =============================================================================


class RequestCase(BaseModel):
    """One HTTP request that can be executed, recorded, and replayed.

    Header and query values are arrays to support repeated parameters.
    All three path fields are required: path_template + path_parameters for
    debugging which values caused failures, rendered_path for the actual URL.
    """

    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(description="Unique identifier for traceability")
    operation_id: str = Field(description="OpenAPI operationId")
    method: str = Field(description="HTTP method (GET, POST, etc.)")
    path_template: str = Field(description="Path with parameter placeholders, e.g., /items/{id}")
    path_parameters: dict[str, Any] = Field(
        default_factory=dict, description="Parameter values, e.g., {'id': 'abc123'}"
    )
    rendered_path: str = Field(description="Fully rendered path")
    query: dict[str, list[str]] = Field(
        default_factory=dict, description="Query parameters (arrays for repeated params)"
    )
    headers: dict[str, list[str]] = Field(
        default_factory=dict, description="Request headers (arrays for repeated headers)"
    )
    cookies: dict[str, str] = Field(default_factory=dict, description="Cookies")
    body: Any = Field(default=None, description="Body as JSON value if parseable")
    body_base64: str | None = Field(
        default=None, description="Body as base64 if binary (mutually exclusive with body)"
    )
    media_type: str | None = Field(default=None, description="Content-Type, e.g., application/json")

    @model_validator(mode="after")
    def check_body_exclusivity(self) -> Self:
        # Body is stored as either parsed JSON (body) or base64 (body_base64), never both.
        # This supports JSON APIs (body) and binary payloads like file uploads (body_base64).
        if self.body is not None and self.body_base64 is not None:
            raise ValueError(
                f"RequestCase body and body_base64 are mutually exclusive, "
                f"but both were provided (case_id={self.case_id!r})"
            )
        return self


class ResponseCase(BaseModel):
    """One HTTP response captured from a target.

    Header keys are lowercase. Header values are arrays for repeated headers.
    """

    model_config = ConfigDict(extra="forbid")

    status_code: int = Field(description="HTTP status code")
    headers: dict[str, list[str]] = Field(
        default_factory=dict, description="Response headers (lowercase keys, array values)"
    )
    body: Any = Field(default=None, description="Body as JSON value if parseable")
    body_base64: str | None = Field(default=None, description="Body as base64 if binary")
    elapsed_ms: float = Field(description="Response time in milliseconds")
    http_version: str = Field(default="1.1", description="Protocol version")

    @model_validator(mode="after")
    def check_body_exclusivity(self) -> Self:
        # Body is stored as either parsed JSON (body) or base64 (body_base64), never both.
        # This supports JSON APIs (body) and binary payloads like images (body_base64).
        if self.body is not None and self.body_base64 is not None:
            raise ValueError(
                f"ResponseCase body and body_base64 are mutually exclusive, "
                f"but both were provided (status_code={self.status_code})"
            )
        return self


# =============================================================================
# Stateful Chain Models
# =============================================================================


class ChainStep(BaseModel):
    """One step in a chain (template only, no execution data).

    The request_template has path_template populated but path_parameters
    empty until execution.
    """

    model_config = ConfigDict(extra="forbid")

    step_index: int = Field(description="0-based position in chain")
    request_template: RequestCase = Field(description="The request template")
    link_source: dict[str, Any] | None = Field(
        default=None, description="Which previous step and field provides data for this step"
    )


class ChainCase(BaseModel):
    """A stateful sequence of requests (template only, no execution data).

    Describes the chain structure and link relationships. Execution traces
    are stored separately in ChainExecution.
    """

    model_config = ConfigDict(extra="forbid")

    chain_id: str = Field(description="Unique identifier")
    steps: list[ChainStep] = Field(description="Ordered list of chain steps")


class ChainStepExecution(BaseModel):
    """Execution of one chain step on one target.

    Stores the actual request sent (with all fields populated), the response
    received, and any variables extracted for subsequent steps.
    """

    model_config = ConfigDict(extra="forbid")

    step_index: int = Field(description="Matches ChainStep.step_index")
    request: RequestCase = Field(description="The actual request sent")
    response: ResponseCase = Field(description="The response received")
    extracted: dict[str, Any] = Field(
        default_factory=dict, description="Variables extracted for subsequent steps"
    )


class ChainExecution(BaseModel):
    """Execution trace for one target (stored in target_a.json/target_b.json for chains)."""

    model_config = ConfigDict(extra="forbid")

    steps: list[ChainStepExecution] = Field(description="Ordered list of step executions")


# =============================================================================
# Comparison Rules Models
# =============================================================================


class PresenceMode(str, Enum):
    """Field presence requirements, checked before value comparison."""

    PARITY = "parity"  # Both have field, or both lack it (default)
    REQUIRED = "required"  # Both must have field
    FORBIDDEN = "forbidden"  # Both must lack field
    OPTIONAL = "optional"  # Compare if both have field; pass if either lacks it


class FieldRule(BaseModel):
    """Comparison rule for a single field.

    Specify either predefined (with optional params) or expr, not both.
    If only presence is specified, value comparison is skipped.
    """

    model_config = ConfigDict(extra="forbid")

    presence: PresenceMode = Field(default=PresenceMode.PARITY, description="Presence requirement")
    predefined: str | None = Field(default=None, description="Name of predefined comparison")
    expr: str | None = Field(default=None, description="Custom CEL expression")
    # Parameters for predefined rules (e.g., tolerance, seconds, pattern)
    tolerance: float | None = Field(default=None, description="For numeric_tolerance, array_length_tolerance")
    seconds: float | None = Field(default=None, description="For epoch_seconds_tolerance")
    millis: float | None = Field(default=None, description="For epoch_millis_tolerance")
    length: int | None = Field(default=None, description="For string_prefix, string_suffix")
    pattern: str | None = Field(default=None, description="For both_match_regex")
    substring: str | None = Field(default=None, description="For string_contains")
    min: float | None = Field(default=None, description="For both_in_range")
    max: float | None = Field(default=None, description="For both_in_range")

    @model_validator(mode="after")
    def check_rule_logic(self) -> Self:
        # A rule uses EITHER a predefined comparison (looked up from comparison_library.json)
        # OR a custom CEL expression, never both. This keeps rule resolution unambiguous.
        if self.predefined is not None and self.expr is not None:
            raise ValueError(
                f"FieldRule cannot specify both predefined and expr; "
                f"got predefined={self.predefined!r} and expr={self.expr!r}"
            )

        # Forbidden fields must not exist in either response, so there's nothing to compare.
        # Allowing a comparison rule would be misleading since it would never execute.
        if self.presence == PresenceMode.FORBIDDEN:
            if self.predefined is not None or self.expr is not None:
                raise ValueError(
                    f"FieldRule with presence=forbidden cannot have a comparison rule "
                    f"(got predefined={self.predefined!r}, expr={self.expr!r})"
                )

        return self


class BodyRules(BaseModel):
    """Body comparison rules containing field-level rules."""

    model_config = ConfigDict(extra="forbid")

    field_rules: dict[str, FieldRule] = Field(
        default_factory=dict, description="JSONPath -> FieldRule mapping"
    )
    binary_rule: FieldRule | None = Field(
        default=None,
        description="Rule for comparing binary (non-JSON) bodies. Compares base64-encoded strings.",
    )

    @model_validator(mode="after")
    def validate_binary_rule_presence(self) -> Self:
        # binary_rule only executes when BOTH responses are non-JSON (binary).
        # Presence modes like REQUIRED/OPTIONAL don't apply because the rule already
        # requires both sides to have binary content. Only PARITY (the default) is valid.
        if self.binary_rule is not None and self.binary_rule.presence != PresenceMode.PARITY:
            raise ValueError(
                f"BodyRules.binary_rule only supports presence=parity because it inherently "
                f"requires both responses to be binary; got presence={self.binary_rule.presence.value!r}"
            )
        return self


class OperationRules(BaseModel):
    """Comparison rules for a single operation.

    If specified, these completely override default_rules for any key defined.
    There is no deep merging.
    """

    model_config = ConfigDict(extra="forbid")

    status_code: FieldRule | None = Field(default=None, description="Status code comparison rule")
    headers: dict[str, FieldRule] = Field(
        default_factory=dict, description="Header name -> FieldRule mapping"
    )
    body: BodyRules | None = Field(default=None, description="Body comparison rules")


class ComparisonRulesFile(BaseModel):
    """Top-level comparison rules file structure.

    default_rules apply to all operations. operation_rules override defaults
    per operationId (complete override for any key defined, not merge).
    """

    model_config = ConfigDict(extra="forbid")

    version: str = Field(default="1", description="Schema version")
    description: str | None = Field(default=None, description="Optional description")
    default_rules: OperationRules = Field(
        default_factory=OperationRules, description="Rules applied to all operations"
    )
    operation_rules: dict[str, OperationRules] = Field(
        default_factory=dict, description="Per-operationId overrides"
    )


class PredefinedComparison(BaseModel):
    """A predefined comparison from the comparison library.

    Loaded from comparison_library.json. Contains the CEL expression template
    and required parameters.
    """

    model_config = ConfigDict(extra="forbid")

    description: str = Field(description="Human-readable description")
    params: list[str] = Field(default_factory=list, description="Required parameter names")
    expr: str = Field(description="CEL expression template")


class ComparisonLibrary(BaseModel):
    """The comparison library containing all predefined comparisons."""

    model_config = ConfigDict(extra="forbid")

    library_version: str = Field(description="Library version")
    description: str = Field(description="Library description")
    predefined: dict[str, PredefinedComparison] = Field(
        description="Predefined name -> comparison mapping"
    )


# =============================================================================
# Comparison Result Models
# =============================================================================


class FieldDifference(BaseModel):
    """A single failed comparison."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(description="JSONPath or header name")
    target_a: Any = Field(description="Value from Target A")
    target_b: Any = Field(description="Value from Target B")
    rule: str = Field(description="Rule that failed (predefined name or 'custom')")


class ComponentResult(BaseModel):
    """Comparison result for one component (status_code, headers, or body)."""

    model_config = ConfigDict(extra="forbid")

    match: bool = Field(description="Whether this component matched")
    differences: list[FieldDifference] = Field(
        default_factory=list, description="Failed comparisons (empty if match=True)"
    )


class MismatchType(str, Enum):
    """Which component caused the mismatch."""

    SCHEMA_VIOLATION = "schema_violation"
    STATUS_CODE = "status_code"
    HEADERS = "headers"
    BODY = "body"


class ComparisonResult(BaseModel):
    """Overall comparison result for one request/response pair."""

    model_config = ConfigDict(extra="forbid")

    match: bool = Field(description="Whether responses matched")
    mismatch_type: MismatchType | None = Field(
        default=None, description="First component that failed (None if match=True)"
    )
    summary: str = Field(description="Human-readable one-liner for logs")
    details: dict[str, ComponentResult] = Field(
        description="Per-component results (status_code, headers, body)"
    )


# =============================================================================
# Mismatch Bundle Models
# =============================================================================


class StatelessExecution(BaseModel):
    """Execution data for a stateless test (stored in target_a.json/target_b.json)."""

    model_config = ConfigDict(extra="forbid")

    request: RequestCase = Field(description="The request sent")
    response: ResponseCase = Field(description="The response received")


class TargetInfo(BaseModel):
    """Target information for metadata."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="Target name from config")
    base_url: str = Field(description="Base URL used")


# ISO 8601 timestamp pattern for MismatchMetadata.timestamp validation.
# Accepts: 2024-01-15T10:30:00, 2024-01-15T10:30:00.123, 2024-01-15T10:30:00Z, 2024-01-15T10:30:00+05:00
MISMATCH_METADATA_TIMESTAMP_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})?$"
)


class MismatchMetadata(BaseModel):
    """Run context stored in metadata.json."""

    model_config = ConfigDict(extra="forbid")

    tool_version: str = Field(description="api-parity version")
    timestamp: str = Field(description="ISO 8601 timestamp")
    seed: int | None = Field(default=None, description="Random seed if used")
    target_a: TargetInfo = Field(description="Target A info")
    target_b: TargetInfo = Field(description="Target B info")
    comparison_rules_applied: str = Field(
        description="Which rules were used (e.g., 'default' or operation-specific)"
    )

    @field_validator("timestamp")
    @classmethod
    def validate_timestamp(cls, v: str) -> str:
        if not MISMATCH_METADATA_TIMESTAMP_PATTERN.match(v):
            raise ValueError(
                f"MismatchMetadata.timestamp must be ISO 8601 format "
                f"(YYYY-MM-DDTHH:MM:SS with optional fractional seconds and timezone); "
                f"got {v!r}"
            )
        return v


# =============================================================================
# Runtime Configuration Models
# =============================================================================


class TargetConfig(BaseModel):
    """Configuration for a single target."""

    model_config = ConfigDict(extra="forbid")

    base_url: str = Field(description="Base URL for the target")
    headers: dict[str, str] = Field(
        default_factory=dict,
        description="Headers to include (supports ${ENV_VAR} substitution)",
    )
    # TLS/SSL configuration
    cert: str | None = Field(
        default=None, description="Path to client certificate file (PEM format)"
    )
    key: str | None = Field(
        default=None, description="Path to client private key file (PEM format)"
    )
    key_password: str | None = Field(
        default=None, description="Password for encrypted private key"
    )
    ca_bundle: str | None = Field(
        default=None, description="Path to CA bundle for server verification"
    )
    verify_ssl: bool = Field(
        default=True, description="Verify server certificate (ignored if ca_bundle is set)"
    )
    ciphers: str | None = Field(
        default=None,
        description="OpenSSL cipher string to restrict allowed ciphers (e.g., 'ECDHE+AESGCM')",
    )

    @model_validator(mode="after")
    def validate_cert_key_pair(self) -> Self:
        # mTLS requires both certificate and private key. Having only one is a config error.
        if (self.cert is None) != (self.key is None):
            provided = "cert" if self.cert is not None else "key"
            missing = "key" if self.cert is not None else "cert"
            raise ValueError(
                f"TargetConfig mTLS requires both cert and key; "
                f"got {provided}={getattr(self, provided)!r} but {missing} is missing"
            )
        return self


class RateLimitConfig(BaseModel):
    """Rate limiting configuration."""

    model_config = ConfigDict(extra="forbid")

    requests_per_second: float = Field(description="Maximum requests per second")


class SecretsConfig(BaseModel):
    """Secret redaction configuration."""

    model_config = ConfigDict(extra="forbid")

    redact_fields: list[str] = Field(
        default_factory=list, description="JSONPaths of fields to redact in artifacts"
    )


class RuntimeConfig(BaseModel):
    """Top-level runtime configuration file structure."""

    model_config = ConfigDict(extra="forbid")

    targets: dict[str, TargetConfig] = Field(description="Target name -> config mapping")
    comparison_rules: str = Field(description="Path to comparison rules JSON file")
    rate_limit: RateLimitConfig | None = Field(default=None, description="Rate limiting settings")
    secrets: SecretsConfig | None = Field(default=None, description="Secret redaction settings")


# =============================================================================
# CEL Evaluator IPC Models
# =============================================================================


class CELRequest(BaseModel):
    """Request sent to CEL evaluator subprocess."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="Request ID for correlation")
    expr: str = Field(description="CEL expression to evaluate")
    data: dict[str, Any] = Field(description="Variables available to the expression")


class CELResponse(BaseModel):
    """Response from CEL evaluator subprocess."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="Request ID (matches request)")
    ok: bool = Field(description="Whether evaluation succeeded")
    result: bool | None = Field(default=None, description="Evaluation result (if ok=True)")
    error: str | None = Field(default=None, description="Error message (if ok=False)")
