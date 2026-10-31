"""netcoop/1.0 protocol models — the single executable source of truth.

Every other artifact (schemas/*.json, the PROTOCOL.md field table, dashboard
TypeScript types) is generated from these models. Change them here, then run
`netcoop export-schemas`.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PROTOCOL_VERSION = "netcoop/1.0"
SCHEMA_VERSION = "1.0"


class MessageType(str, Enum):
    TASK = "TASK"
    FINDING = "FINDING"
    QUERY = "QUERY"
    RESPONSE = "RESPONSE"
    DECISION = "DECISION"
    ERROR = "ERROR"


class Stance(str, Enum):
    supports = "supports"
    contradicts = "contradicts"


class Status(str, Enum):
    diagnosed = "diagnosed"
    probable = "probable"
    healthy = "healthy"
    insufficient_evidence = "insufficient_evidence"
    invalid_input = "invalid_input"
    failed = "failed"


class Cause(str, Enum):
    """Left side of a hypothesis string `<cause>@<device_or_link_id>`."""

    healthy = "healthy"
    link_congestion = "link_congestion"
    weak_wifi = "weak_wifi"
    interface_fault = "interface_fault"
    device_overload = "device_overload"
    dns_failure = "dns_failure"
    dhcp_failure = "dhcp_failure"
    hardware_failure = "hardware_failure"
    upstream_dependency = "upstream_dependency"


class ErrorCode(str, Enum):
    SCHEMA_VERSION_UNSUPPORTED = "SCHEMA_VERSION_UNSUPPORTED"
    MISSING_FIELD = "MISSING_FIELD"
    INVALID_VALUE = "INVALID_VALUE"
    MALFORMED_JSON = "MALFORMED_JSON"
    DUPLICATE_MESSAGE = "DUPLICATE_MESSAGE"
    UNKNOWN_EVIDENCE = "UNKNOWN_EVIDENCE"
    UNKNOWN_RECIPIENT = "UNKNOWN_RECIPIENT"
    AGENT_TIMEOUT = "AGENT_TIMEOUT"
    AGENT_FAILURE = "AGENT_FAILURE"


class QualityFlag(str, Enum):
    ok = "ok"
    missing = "missing"
    stale = "stale"
    malformed = "malformed"
    counter_reset = "counter_reset"
    unit_converted = "unit_converted"
    derived = "derived"
    alias_mapped = "alias_mapped"
    out_of_window = "out_of_window"


HYPOTHESIS_PATTERN = r"^[a-z_]+@[A-Za-z0-9_.:\-]+$"


def _check_hypothesis(value: str) -> str:
    cause = value.split("@", 1)[0]
    if cause not in Cause.__members__:
        raise ValueError(f"unknown cause '{cause}' in hypothesis '{value}'")
    return value


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)


# --------------------------------------------------------------------------- payloads


class TaskPayload(_Strict):
    task_id: str = Field(description="Deterministic task ID, e.g. INC-0001-T-PERF-01")
    capability: str = Field(description="Capability requested, must match an agent card")
    scope_ids: list[str] = Field(default_factory=list, description="Device/link IDs in scope")
    evidence_ids: list[str] = Field(default_factory=list, description="Evidence the agent may read")
    peer_message_ids: list[str] = Field(
        default_factory=list, description="Peer FINDING message IDs forwarded for interpretation"
    )
    instructions: str = ""


class FindingPayload(_Strict):
    finding_id: str = Field(description="e.g. F-PERF-01")
    hypothesis: str = Field(pattern=HYPOTHESIS_PATTERN, description="<cause>@<device_or_link_id>")
    stance: Stance
    target_hypothesis: str = Field(pattern=HYPOTHESIS_PATTERN)
    evidence_ids: list[str] = Field(min_length=1, description="Canonical evidence IDs cited")
    reason: str = Field(min_length=1)
    score: Optional[float] = Field(default=None, ge=0, le=1, description="Agent-local signal fit [0,1]")
    consumed_message_ids: list[str] = Field(
        default_factory=list, description="Peer messages interpreted to produce this finding"
    )

    _v_h = field_validator("hypothesis", "target_hypothesis")(_check_hypothesis)


class QueryPayload(_Strict):
    query_id: str
    question: str = Field(min_length=1)
    target_id: str = Field(description="Device or link ID to check")
    desired_measurement: str = Field(description="Canonical metric or service check name")
    related_hypotheses: list[str] = Field(default_factory=list)


class ResponsePayload(_Strict):
    query_id: Optional[str] = Field(default=None, description="Query answered, if any")
    evidence_ids: list[str] = Field(default_factory=list)
    answer: str = ""


class RankedCause(_Strict):
    hypothesis: str = Field(pattern=HYPOTHESIS_PATTERN)
    confidence: float = Field(ge=0, le=1)
    coverage: float = Field(ge=0, le=1)
    supporting_finding_ids: list[str] = Field(default_factory=list)
    contradicting_finding_ids: list[str] = Field(default_factory=list)

    _v_h = field_validator("hypothesis")(_check_hypothesis)


class DecisionPayload(_Strict):
    decision_id: str
    status: Status
    ranking: list[RankedCause] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class ErrorPayload(_Strict):
    code: ErrorCode
    detail: str
    field_path: Optional[str] = None
    offending_message_id: Optional[str] = None


PAYLOAD_MODELS: dict[str, type[BaseModel]] = {
    MessageType.TASK.value: TaskPayload,
    MessageType.FINDING.value: FindingPayload,
    MessageType.QUERY.value: QueryPayload,
    MessageType.RESPONSE.value: ResponsePayload,
    MessageType.DECISION.value: DecisionPayload,
    MessageType.ERROR.value: ErrorPayload,
}


# --------------------------------------------------------------------------- envelope


class Envelope(_Strict):
    protocol: str = Field(pattern=r"^netcoop/\d+\.\d+$", description="Protocol and version")
    message_id: str = Field(min_length=1, description="Deterministic, unique per incident")
    incident_id: str = Field(min_length=1)
    in_reply_to: Optional[str] = Field(
        description="null only for a new incident or an unsolicited finding"
    )
    sender: str
    recipient: str
    type: MessageType
    timestamp: datetime = Field(description="UTC, ISO 8601")
    payload: dict[str, Any]
    extra: dict[str, Any] = Field(
        default_factory=dict, description="Unknown optional fields preserved from compatible 1.x senders"
    )

    @model_validator(mode="after")
    def _payload_matches_type(self) -> "Envelope":
        model = PAYLOAD_MODELS[self.type if isinstance(self.type, str) else self.type.value]
        self.payload = model.model_validate(self.payload).model_dump(mode="json")
        return self

    def typed_payload(self) -> BaseModel:
        return PAYLOAD_MODELS[self.type].model_validate(self.payload)


# --------------------------------------------------------------------------- telemetry


class Observation(_Strict):
    evidence_id: str = Field(description="e.g. EV-014, deterministic per fixture")
    source_id: str = Field(description="Raw sensor/probe identity, e.g. switch-telemetry")
    observed_at: datetime
    device_id: Optional[str] = None
    link_id: Optional[str] = None
    metric: str = Field(description="Canonical metric name, see PROTOCOL.md")
    value: Optional[float | str | bool] = Field(description="null when missing/malformed — never 0")
    unit: str
    original_field: str
    original_value: Any = None
    normalization_steps: list[str] = Field(default_factory=list)
    quality_flags: list[QualityFlag] = Field(default_factory=lambda: [QualityFlag.ok])

    @model_validator(mode="after")
    def _has_target(self) -> "Observation":
        if not (self.device_id or self.link_id):
            raise ValueError("observation needs device_id or link_id")
        return self


# --------------------------------------------------------------------------- transport


class Bundle(_Strict):
    """HTTP body for out-of-process agents: the messages being delivered plus the
    evidence and topology they may cite. Responses use the same shape."""

    messages: list[Envelope]
    observations: list[Observation] = Field(default_factory=list)
    topology: dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------- report


class EvidenceItem(_Strict):
    evidence_id: str
    metric: str
    value: Optional[float | str | bool]
    unit: str
    source_id: str


class Report(_Strict):
    incident_id: str
    status: Status
    suspected_root_cause: Optional[str]
    confidence: Optional[float] = Field(ge=0, le=1)
    confidence_method: str = "heuristic-v1"
    signature_version: str
    affected_devices: list[str] = Field(default_factory=list)
    affected_segments: list[str] = Field(default_factory=list)
    supporting_evidence: list[EvidenceItem] = Field(default_factory=list)
    agent_findings: list[str] = Field(default_factory=list)
    alternative_causes: list[str] = Field(default_factory=list)
    recommended_actions: list[str] = Field(default_factory=list)
    evidence_sources: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    normalization_log: list[str] = Field(default_factory=list)
    validation_errors: list[str] = Field(default_factory=list)
    agent_trace_id: str

    @model_validator(mode="after")
    def _status_rules(self) -> "Report":
        named = self.status in (Status.diagnosed.value, Status.probable.value)
        if named and not self.suspected_root_cause:
            raise ValueError(f"status {self.status} requires suspected_root_cause")
        if not named and self.suspected_root_cause is not None:
            raise ValueError(f"status {self.status} requires suspected_root_cause = null")
        no_conf = self.status in (Status.invalid_input.value, Status.failed.value)
        if no_conf and self.confidence is not None:
            raise ValueError(f"status {self.status} requires confidence = null")
        if not no_conf and self.confidence is None:
            raise ValueError(f"status {self.status} requires a confidence value")
        if self.status == Status.invalid_input.value and not self.validation_errors:
            raise ValueError("invalid_input requires validation_errors")
        return self