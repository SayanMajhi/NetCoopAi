"""End-to-end pipeline for one incident (docs/PHASE2_TASKS.md, decision D3).

normalize -> TASK Performance + TASK Infrastructure -> their FINDINGs through the bus
-> TASK Connectivity as a Bundle with the peer FINDINGs (none with control=True)
-> Connectivity replies through the bus -> (Phase 3: Correlation) -> report + trace.

run_incident never raises: bad input gives an invalid_input report, a missing or
failing component becomes a typed ERROR plus a limitation, and the run still
returns a schema-valid Report.
"""
from __future__ import annotations

import importlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

import yaml

from netcoop.agents.base import BaseAgent, IncidentContext
from netcoop.normalizer.result import NormalizationResult
from netcoop.orchestrator.bus import MessageBus
from netcoop.orchestrator.eventlog import EventLog
from netcoop.orchestrator.transport import ConnectivityReply, Transport, local_transport
from netcoop.protocol.cards import CARDS
from netcoop.protocol.models import (
    Bundle,
    Envelope,
    ErrorCode,
    ErrorPayload,
    MessageType,
    Report,
    Status,
    TaskPayload,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SIGNATURES = REPO_ROOT / "netcoop" / "config" / "signatures-1.0.yaml"
DEFAULT_TOPOLOGY = REPO_ROOT / "scenarios" / "topology.json"
DEFAULT_ENDPOINT = CARDS["connectivity_agent"].endpoint

PERFORMANCE = "performance_agent"
INFRASTRUCTURE = "infrastructure_agent"
CONNECTIVITY = "connectivity_agent"
CORRELATION = "correlation_agent"

Normalizer = Callable[[dict[str, Any], dict[str, Any], dict[str, Any]], NormalizationResult]


def load_signatures(path: Path = DEFAULT_SIGNATURES) -> dict[str, Any]:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def load_topology(path: Path = DEFAULT_TOPOLOGY) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- components


@dataclass
class Components:
    """What the pipeline runs. Any part may be None while it is still being built;
    the run then records a limitation instead of failing."""

    normalize: Optional[Normalizer]
    agents: dict[str, BaseAgent]                 # PERFORMANCE / INFRASTRUCTURE -> agent instance
    connectivity: Optional[Transport]
    unavailable: list[str] = field(default_factory=list)


def _load(module: str, attr: str, unavailable: list[str], label: str) -> Any:
    try:
        return getattr(importlib.import_module(module), attr)
    except (ImportError, AttributeError) as exc:
        unavailable.append(f"{label} unavailable: {type(exc).__name__}: {exc}")
        return None


def default_components(connectivity: str = "remote", endpoint: str = DEFAULT_ENDPOINT) -> Components:
    """Wire the real modules. Owners: normalize.py (B), performance.py (B),
    infrastructure.py (C), connectivity_service/logic.py + orchestrator/remote.py (C)."""
    unavailable: list[str] = []
    normalize = _load("netcoop.normalizer.normalize", "normalize", unavailable, "normalizer")
    agents: dict[str, BaseAgent] = {}
    for agent_id, module, cls in [(PERFORMANCE, "netcoop.agents.performance", "PerformanceAgent"),
                                  (INFRASTRUCTURE, "netcoop.agents.infrastructure", "InfrastructureAgent")]:
        factory = _load(module, cls, unavailable, agent_id)
        if factory is not None:
            agents[agent_id] = factory()
    if connectivity == "local":
        handle = _load("connectivity_service.logic", "handle_bundle", unavailable, CONNECTIVITY)
        transport = local_transport(handle) if handle is not None else None
    else:
        send = _load("netcoop.orchestrator.remote", "send_bundle", unavailable, CONNECTIVITY)
        transport = (lambda bundle: send(bundle, endpoint)) if send is not None else None
    return Components(normalize=normalize, agents=agents, connectivity=transport, unavailable=unavailable)


# --------------------------------------------------------------------------- result


@dataclass
class RunResult:
    report: Report
    messages: list[Envelope]               # every accepted message and every ERROR, in order
    wire: list[dict[str, Any]]             # raw HTTP request/response bodies to and from Connectivity
    findings: list[Envelope]               # accepted FINDINGs, in order


class _Orchestrator(BaseAgent):
    agent_id = "orchestrator"
    abbrev = "ORCH"


class _Run:
    """State for one incident run."""

    def __init__(self, incident_id: str, clock: datetime) -> None:
        self.incident_id = incident_id
        self.clock = clock
        self.bus = MessageBus(EventLog(), clock=lambda: clock)
        self.messages: list[Envelope] = []
        self.findings: list[Envelope] = []
        self.limitations: list[str] = []
        self.wire: list[dict[str, Any]] = []

    def deliver(self, raw: Any) -> Optional[Envelope]:
        """Every message goes through the bus; rejects are kept as typed ERRORs."""
        accepted, error = self.bus.deliver(raw)
        if error is not None:
            self.messages.append(error)
            return None
        self.messages.append(accepted)
        if accepted.type == MessageType.FINDING.value:
            self.findings.append(accepted)
        return accepted

    def failure(self, recipient: str, in_reply_to: Optional[str], code: ErrorCode, detail: str) -> None:
        """Record a component failure as an ERROR message plus a limitation."""
        n = sum(1 for m in self.messages if m.sender == "orchestrator" and m.type == MessageType.ERROR.value) + 1
        error = Envelope(
            protocol="netcoop/1.0", message_id=f"{self.incident_id}-ORCH-ERR-{n:02d}", incident_id=self.incident_id,
            in_reply_to=in_reply_to, sender="orchestrator", recipient=recipient, type=MessageType.ERROR,
            timestamp=self.clock,
            payload=ErrorPayload(code=code, detail=detail, offending_message_id=in_reply_to).model_dump(mode="json"),
        )
        self.deliver(error.model_dump(mode="json"))
        self.limitations.append(f"{recipient}: {code.value}: {detail}")


# --------------------------------------------------------------------------- pipeline


def run_incident(fixture: Any, topology: dict[str, Any], signatures: dict[str, Any], *,
                 components: Optional[Components] = None, control: bool = False,
                 connectivity: str = "remote", endpoint: str = DEFAULT_ENDPOINT) -> RunResult:
    comps = components if components is not None else default_components(connectivity, endpoint)
    version = str(signatures.get("version", "unknown"))
    incident_id = fixture.get("incident_id") if isinstance(fixture, dict) else None
    incident_id = incident_id if isinstance(incident_id, str) and incident_id else "INC-UNKNOWN"

    if not isinstance(fixture, dict) or not isinstance(fixture.get("records"), list) or not fixture["records"]:
        return _terminal(incident_id, Status.invalid_input, version,
                         validation_errors=["scenario must be a JSON object with a non-empty 'records' list"])
    if comps.normalize is None:
        return _terminal(incident_id, Status.failed, version, limitations=comps.unavailable)

    try:
        norm = comps.normalize(fixture, topology, signatures)
    except Exception as exc:  # a normalizer bug must still produce a report
        return _terminal(incident_id, Status.failed, version,
                         limitations=[f"normalizer failed: {type(exc).__name__}: {exc}"])
    if norm.validation_errors:
        return _terminal(incident_id, Status.invalid_input, version, validation_errors=norm.validation_errors,
                         normalization_log=norm.normalization_log)

    clock = norm.window_end or _parse_time(fixture.get("window_end")) or _latest(norm)
    if clock is None:
        return _terminal(incident_id, Status.invalid_input, version,
                         validation_errors=["no window_end and no timestamped observations"])
    ctx = IncidentContext(incident_id=incident_id, clock=clock, observations=norm.observations,
                          topology=topology, signatures=signatures, normalization_log=norm.normalization_log)
    run = _Run(incident_id, clock)
    run.limitations.extend(comps.unavailable)
    orch = _Orchestrator()

    for raw in fixture.get("messages") or []:          # injected protocol cases (0.9, 2.x, duplicates ...)
        run.deliver(raw)

    # 1. Performance and Infrastructure analyse independent evidence.
    for agent_id in (PERFORMANCE, INFRASTRUCTURE):
        task = _task(orch, ctx, agent_id, CARDS[agent_id].capabilities[0], peers=[])
        run.deliver(task.model_dump(mode="json"))
        agent = comps.agents.get(agent_id)
        if agent is None:
            continue
        try:
            outputs = agent.handle(task, ctx)
        except Exception as exc:
            run.failure(agent_id, task.message_id, ErrorCode.AGENT_FAILURE, f"{type(exc).__name__}: {exc}")
            continue
        for out in outputs:
            run.deliver(out.model_dump(mode="json"))

    # 2. Connectivity, out of process, interprets the peer FINDINGs (none in the control run).
    peers = [] if control else [f for f in run.findings if f.sender in (PERFORMANCE, INFRASTRUCTURE)]
    capability = "analyze_connectivity" if control else "interpret_peer_findings"
    task = _task(orch, ctx, CONNECTIVITY, capability, peers=[p.message_id for p in peers])
    run.deliver(task.model_dump(mode="json"))
    if control:
        run.limitations.append("control run: Connectivity received no peer findings")
    if comps.connectivity is not None:  # if None, comps.unavailable already says why
        bundle = Bundle(messages=[task, *peers], observations=norm.observations, topology=topology)
        reply = comps.connectivity(bundle)
        run.wire.append(_wire_record(reply))
        if reply.error:
            run.failure(CONNECTIVITY, task.message_id, _error_code(reply.error), reply.error)
        for raw in reply.messages:
            run.deliver(raw)

    # 3. Phase 3 replaces this with Correlation (ranking, QUERY rounds, gates) and Reporting.
    run.limitations.append("correlation not implemented (Phase 3): no cause is named yet")
    report = _report(run, version, norm.normalization_log)
    return RunResult(report=report, messages=run.messages, wire=run.wire, findings=run.findings)


def _task(orch: _Orchestrator, ctx: IncidentContext, recipient: str, capability: str, peers: list[str]) -> Envelope:
    owned = set(CARDS[recipient].owned_metrics)
    evidence = [o.evidence_id for o in ctx.observations if o.metric in owned] if owned else []
    scope = sorted({t for o in ctx.observations for t in (o.device_id, o.link_id) if t})
    payload = TaskPayload(task_id=f"{ctx.incident_id}-T-{recipient.split('_')[0].upper()}", capability=capability,
                          scope_ids=scope, evidence_ids=sorted(evidence), peer_message_ids=peers)
    return orch.make(ctx, recipient, MessageType.TASK, payload.model_dump(mode="json"), None)


def _report(run: _Run, version: str, normalization_log: list[str]) -> Report:
    finding_ids = sorted(f.payload["finding_id"] for f in run.findings)
    sources = sorted({f.sender for f in run.findings})
    return Report(
        incident_id=run.incident_id, status=Status.insufficient_evidence, suspected_root_cause=None,
        confidence=0.0, confidence_method="not computed (Phase 2)", signature_version=version,
        agent_findings=finding_ids, evidence_sources=sources, limitations=run.limitations,
        normalization_log=normalization_log, agent_trace_id=f"{run.incident_id}-TRACE",
    )


def _terminal(incident_id: str, status: Status, version: str, *, validation_errors: Optional[list[str]] = None,
              limitations: Optional[list[str]] = None, normalization_log: Optional[list[str]] = None) -> RunResult:
    report = Report(
        incident_id=incident_id, status=status, suspected_root_cause=None, confidence=None,
        signature_version=version, validation_errors=validation_errors or [], limitations=limitations or [],
        normalization_log=normalization_log or [], agent_trace_id=f"{incident_id}-TRACE",
        recommended_actions=["Fix the input file and re-run"] if status == Status.invalid_input else [],
    )
    return RunResult(report=report, messages=[], wire=[], findings=[])


def _wire_record(reply: ConnectivityReply) -> dict[str, Any]:
    return {"agent": CONNECTIVITY, "http_status": reply.http_status, "request": reply.request_body,
            "response": reply.response_body, "error": reply.error}


def _error_code(error: str) -> ErrorCode:
    prefix = error.split(":", 1)[0]
    return ErrorCode(prefix) if prefix in ErrorCode.__members__ else ErrorCode.AGENT_FAILURE


def _parse_time(value: Any) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else None


def _latest(norm: NormalizationResult) -> Optional[datetime]:
    return max((o.observed_at for o in norm.observations), default=None)


# --------------------------------------------------------------------------- outputs


def write_outputs(result: RunResult, out_dir: Path, trace_dir: Path) -> tuple[Path, Path]:
    """reports/<incident>.json and traces/<incident>.jsonl (messages, then wire records)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    trace_dir.mkdir(parents=True, exist_ok=True)
    incident = result.report.incident_id
    report_path = out_dir / f"{incident}.json"
    trace_path = trace_dir / f"{incident}.jsonl"
    report_path.write_text(json.dumps(result.report.model_dump(mode="json"), indent=2) + "\n",
                           encoding="utf-8", newline="\n")
    lines = [json.dumps({"kind": "message", **m.model_dump(mode="json")}) for m in result.messages]
    lines += [json.dumps({"kind": "wire", **w}) for w in result.wire]
    trace_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8", newline="\n")
    return report_path, trace_path
