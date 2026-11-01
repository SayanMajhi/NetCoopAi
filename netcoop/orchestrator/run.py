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
from netcoop.agents.correlation import CorrelationAgent
from netcoop.agents.reporting import ReportingAgent, summarize, to_markdown
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
        self.conversions: list[str] = []
        self.known_evidence: Optional[set[str]] = None
        self.input_rejections: list[str] = []   # telemetry items the normalizer skipped (typed)

    def deliver(self, raw: Any) -> Optional[Envelope]:
        """Every message goes through the bus; rejects are kept as typed ERRORs. A FINDING
        citing evidence that is not in this incident is rejected with UNKNOWN_EVIDENCE."""
        accepted, error = self.bus.deliver(raw)
        if error is not None:
            self.messages.append(error)
            return None
        self.messages.append(accepted)
        for record in self.bus.conversions.pop(accepted.message_id, []):
            self.conversions.append(f"{accepted.message_id}: {record}")
        if accepted.type == MessageType.FINDING.value:
            unknown = sorted(set(accepted.payload["evidence_ids"]) - (self.known_evidence or set()))
            if self.known_evidence is not None and unknown:
                self.failure(accepted.sender, accepted.message_id, ErrorCode.UNKNOWN_EVIDENCE,
                             f"FINDING {accepted.payload['finding_id']} cites evidence not in this incident: {unknown}; "
                             "excluded from correlation")
                return None
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


def _shape_error(fixture: Any) -> Optional[str]:
    """Accept a scenario wrapper ({records: [...]}) or one untouched organizer-generator
    record ({timestamp, device_id, metrics, status}); field-level checks are the normalizer's."""
    if not isinstance(fixture, dict):
        return "scenario must be a JSON object"
    if "records" in fixture:
        records = fixture["records"]
        return None if isinstance(records, list) and records else "'records' must be a non-empty list"
    if isinstance(fixture.get("metrics"), dict):
        return None
    return "scenario must have a non-empty 'records' list or be one generator record with 'metrics'"


def run_incident(fixture: Any, topology: dict[str, Any], signatures: dict[str, Any], *,
                 components: Optional[Components] = None, control: bool = False,
                 connectivity: str = "remote", endpoint: str = DEFAULT_ENDPOINT,
                 incident_id: Optional[str] = None) -> RunResult:
    """`incident_id` is used when the input has none (an untouched generator record)."""
    result = _run_incident(fixture, topology, signatures, components=components, control=control,
                           connectivity=connectivity, endpoint=endpoint, incident_id=incident_id)
    result.report.summary = summarize(result.report)
    return result


def _run_incident(fixture: Any, topology: dict[str, Any], signatures: dict[str, Any], *,
                  components: Optional[Components], control: bool, connectivity: str, endpoint: str,
                  incident_id: Optional[str]) -> RunResult:
    comps = components if components is not None else default_components(connectivity, endpoint)
    version = str(signatures.get("version", "unknown"))
    given = fixture.get("incident_id") if isinstance(fixture, dict) else None
    incident_id = given if isinstance(given, str) and given else (incident_id or "INC-UNKNOWN")

    shape_error = _shape_error(fixture)
    if shape_error:
        return _terminal(incident_id, Status.invalid_input, version, validation_errors=[shape_error])
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
    ctx = IncidentContext(incident_id=incident_id, clock=clock, observations=list(norm.observations),
                          topology=topology, signatures=signatures, normalization_log=norm.normalization_log,
                          on_demand=list(norm.on_demand),
                          complaint=fixture.get("complaint") if isinstance(fixture.get("complaint"), str) else "",
                          input_warnings=list(norm.skipped) + list(norm.suspect))
    run = _Run(incident_id, clock)
    run.known_evidence = {o.evidence_id for o in norm.observations}
    run.input_rejections = list(norm.skipped)
    if norm.skipped:
        run.limitations.append(f"input: {len(norm.skipped)} telemetry item(s) skipped as unusable and listed in "
                               "validation_errors; the diagnosis uses the rest")
    run.limitations.extend(norm.suspect)
    run.limitations.extend(comps.unavailable)
    run.limitations.extend(_sparse_telemetry(norm, signatures))
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

    # 3. Correlation ranks the signatures, verifies with QUERY (<= 2 rounds) and decides; one retry.
    simulate = (fixture.get("inject") or {}).get("correlation") if isinstance(fixture.get("inject"), dict) else None
    info: Optional[dict[str, Any]] = None
    for attempt in (1, 2):
        try:
            info = _correlate(run, ctx, comps, simulate)
            break
        except TimeoutError as exc:
            failure = (ErrorCode.AGENT_TIMEOUT, f"attempt {attempt}: {exc}")
        except Exception as exc:  # a correlation bug must still end in a report
            failure = (ErrorCode.AGENT_FAILURE, f"attempt {attempt}: {type(exc).__name__}: {exc}")
    log = norm.normalization_log + [f"protocol upgrade {c}" for c in run.conversions]
    if info is None:
        run.failure(CORRELATION, None, failure[0], failure[1] + " (gave up after one retry)")
        run.limitations.append("correlation_failed: no ranking or decision; the findings below are unweighed")
        report = _fallback(run, version, log)
    else:
        ctx.normalization_log = log
        report = ReportingAgent().build(ctx, info, run.findings, version, run.limitations, _rejections(run))
    return RunResult(report=report, messages=run.messages, wire=run.wire, findings=run.findings)


def _correlate(run: _Run, ctx: IncidentContext, comps: Components, simulate: Optional[str]) -> dict[str, Any]:
    """Rank, run the QUERY rounds through the bus, and deliver the DECISION."""
    if simulate == "timeout":           # test hook from scenarios/dev/correlation_timeout.json
        raise TimeoutError("correlation_agent did not answer within its deadline (simulated)")
    corr = CorrelationAgent()
    corr.rank(ctx, run.findings)
    while True:
        queries = corr.plan_queries(ctx)
        if not queries:
            break
        for query in queries:
            if run.deliver(query.model_dump(mode="json")) is None:
                continue
            agent = comps.agents.get(query.recipient)
            if agent is None:
                run.limitations.append(f"QUERY {query.payload['query_id']}: {query.recipient} unavailable")
                continue
            response = run.deliver(agent.answer(query, ctx).model_dump(mode="json"))
            if response is not None:
                corr.absorb(response, ctx, run.findings)
    decision, info = corr.decide(ctx, None)
    run.deliver(decision.model_dump(mode="json"))
    return info


def _sparse_telemetry(norm: NormalizationResult, signatures: dict[str, Any]) -> list[str]:
    """Say so when no series is long enough for the window checks (e.g. one organizer reading)."""
    need = (signatures.get("window") or {}).get("congestion_min_consecutive_samples", 3)
    counts: dict[tuple[Any, ...], int] = {}
    for o in norm.observations:
        key = (o.device_id or o.link_id, o.metric, o.source_id)
        counts[key] = counts.get(key, 0) + 1
    longest = max(counts.values(), default=0)
    if longest >= need:
        return []
    return [f"sparse telemetry: at most {longest} sample(s) per metric; sustained-window checks need {need} "
            "and counter deltas need 2, so agents abstain rather than guess"]


def _task(orch: _Orchestrator, ctx: IncidentContext, recipient: str, capability: str, peers: list[str]) -> Envelope:
    owned = set(CARDS[recipient].owned_metrics)
    evidence = [o.evidence_id for o in ctx.observations if o.metric in owned] if owned else []
    scope = sorted({t for o in ctx.observations for t in (o.device_id, o.link_id) if t})
    payload = TaskPayload(task_id=f"{ctx.incident_id}-T-{recipient.split('_')[0].upper()}", capability=capability,
                          scope_ids=scope, evidence_ids=sorted(evidence), peer_message_ids=peers)
    return orch.make(ctx, recipient, MessageType.TASK, payload.model_dump(mode="json"), None)


def _fallback(run: _Run, version: str, normalization_log: list[str]) -> Report:
    """Report when Correlation failed twice (ARCHITECTURE §4 step 5): insufficient_evidence."""
    finding_ids = sorted(f.payload["finding_id"] for f in run.findings)
    sources = sorted({f.sender for f in run.findings})
    return Report(
        incident_id=run.incident_id, status=Status.insufficient_evidence, suspected_root_cause=None,
        confidence=0.0, confidence_method="none: correlation_failed (no heuristic-v1 score could be computed)",
        signature_version=version, recommended_actions=["Re-run the diagnosis once the correlation service is healthy",
                                                        "Review the listed agent findings manually meanwhile"],
        agent_findings=finding_ids, evidence_sources=sources, limitations=run.limitations,
        normalization_log=normalization_log, validation_errors=_rejections(run),
        agent_trace_id=f"{run.incident_id}-TRACE",
    )


# Typed ERRORs for messages the bus rejected: the report must name them, not only the trace.
PROTOCOL_ERRORS = {ErrorCode.SCHEMA_VERSION_UNSUPPORTED, ErrorCode.MISSING_FIELD, ErrorCode.INVALID_VALUE,
                   ErrorCode.MALFORMED_JSON, ErrorCode.DUPLICATE_MESSAGE, ErrorCode.UNKNOWN_EVIDENCE,
                   ErrorCode.UNKNOWN_RECIPIENT}


def _rejections(run: _Run) -> list[str]:
    codes = {c.value for c in PROTOCOL_ERRORS}
    out = list(run.input_rejections)
    for m in run.messages:
        if m.type != MessageType.ERROR.value or m.payload.get("code") not in codes:
            continue
        p = m.payload
        where = f", field {p['field_path']}" if p.get("field_path") else ""
        out.append(f"{p['code']} (message {p.get('offending_message_id') or 'unknown'}{where}): {p['detail']}")
    return out


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


def write_outputs(result: RunResult, out_dir: Path, trace_dir: Path, control: bool = False) -> tuple[Path, Path]:
    """reports/<incident>.json and traces/<incident>.jsonl (messages, then wire records);
    a --control run writes <incident>--control.* so it never overwrites the normal run."""
    out_dir.mkdir(parents=True, exist_ok=True)
    trace_dir.mkdir(parents=True, exist_ok=True)
    incident = result.report.incident_id + ("--control" if control else "")
    report_path = out_dir / f"{incident}.json"
    trace_path = trace_dir / f"{incident}.jsonl"
    report_path.write_text(json.dumps(result.report.model_dump(mode="json"), indent=2) + "\n",
                           encoding="utf-8", newline="\n")
    lines = [json.dumps({"kind": "message", **m.model_dump(mode="json")}) for m in result.messages]
    lines += [json.dumps({"kind": "wire", **w}) for w in result.wire]
    trace_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8", newline="\n")
    report_path.with_suffix(".md").write_text(to_markdown(result.report), encoding="utf-8", newline="\n")
    return report_path, trace_path
