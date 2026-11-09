import { useMemo, useState } from "react";
import { controlTwin, normalTwin, runs, sampleReports } from "./data";
import type { FeedItem, RunItem } from "./data";
import type { DecisionPayload, Envelope, ErrorPayload, FindingPayload, QueryPayload, ResponsePayload, TaskPayload } from "./types/message";
import type { Report } from "./types/report";

const WORKFLOW = ["Telemetry", "Normalization", "Specialist analysis", "Agent communication", "Correlation and verification", "Diagnosis", "Evidence-based report"];

const STATUS_LABEL: Record<Report["status"], string> = {
  diagnosed: "Diagnosed", probable: "Probable", healthy: "Healthy",
  insufficient_evidence: "Insufficient evidence", invalid_input: "Invalid input", failed: "Run failed",
};

function confidenceMeaning(report: Report): string {
  const method = report.confidence_method;
  switch (report.status) {
    case "diagnosed": return `${method} score, capped at 0.95. Passed every gate: score at least 0.70, coverage at least 0.75, two independent raw sources, a 0.15 lead and no unresolved rival. It is a documented heuristic, not a probability.`;
    case "probable": return `${method} score, capped at 0.69. The leading cause fits the evidence, but a gate is still open; the limitations say which. Verify before acting.`;
    case "healthy": return `${method} score, capped at 0.95. Fresh measurements are normal and service and path checks pass. It is a documented heuristic, not a probability.`;
    case "insufficient_evidence": return "The system abstained and names no cause. A score below 0.50 is the strongest candidate's, not a diagnosis.";
    default: return "No confidence: the run produced no assessment.";
  }
}

const payload = <T,>(m: Envelope) => m.payload as unknown as T;
const messagesOf = (item: RunItem) => (item.trace ?? []).flatMap((f) => (f.message ? [f.message] : []));
const decisionOf = (item: RunItem) => {
  const m = messagesOf(item).find((x) => x.type === "DECISION");
  return m ? payload<DecisionPayload>(m) : undefined;
};
const findingsOf = (item: RunItem) => messagesOf(item).filter((m) => m.type === "FINDING").map((m) => ({ m, p: payload<FindingPayload>(m) }));
const isRoutine = (f: FeedItem) => {
  if (f.message?.type !== "FINDING") return false;
  const p = payload<FindingPayload>(f.message);
  return p.hypothesis.startsWith("healthy@") && p.target_hypothesis.startsWith("upstream_dependency@") && !p.consumed_message_ids?.length;
};
const shortAgent = (id: string) => id.replace(/_agent$/, "");

function gist(m: Envelope): string {
  switch (m.type) {
    case "TASK": {
      const p = payload<TaskPayload>(m);
      return `${p.capability}${p.peer_message_ids?.length ? `, with ${p.peer_message_ids.length} peer finding(s) attached` : ""}`;
    }
    case "FINDING": {
      const p = payload<FindingPayload>(m);
      const on = p.target_hypothesis !== p.hypothesis ? ` against ${p.target_hypothesis}` : "";
      return `${p.hypothesis} ${p.stance}${on}: ${p.reason}`;
    }
    case "QUERY": return payload<QueryPayload>(m).question;
    case "RESPONSE": return payload<ResponsePayload>(m).answer ?? "";
    case "DECISION": {
      const p = payload<DecisionPayload>(m);
      return `${p.status}${p.ranking?.[0] ? `, leading ${p.ranking[0].hypothesis}` : ""}`;
    }
    default: {
      const p = payload<ErrorPayload>(m);
      return `${p.code}${p.field_path ? ` at ${p.field_path}` : ""}: ${p.detail}`;
    }
  }
}

function StatusBadge({ status }: { status: Report["status"] }) {
  return <span className={`badge s-${status}`}>{STATUS_LABEL[status]}</span>;
}

function Section({ title, note, children }: { title: string; note?: string; children: React.ReactNode }) {
  return (
    <section className="card">
      <h2>{title}</h2>
      {note && <p className="muted small">{note}</p>}
      {children}
    </section>
  );
}

function Diagnosis({ item }: { item: RunItem }) {
  const r = item.report;
  const affected = [...(r.affected_segments ?? []), ...(r.affected_devices ?? [])];
  const w = item.wire;
  return (
    <section className="card hero">
      <div className="hero-top">
        <div>
          <h2 className="incident">{item.title}</h2>
          <p className="muted small">
            <code>{r.incident_id}</code>{item.complaint && <> · reported as “{item.complaint}”</>}
          </p>
        </div>
        <StatusBadge status={r.status} />
      </div>
      {item.sample && <p className="notice">This is a schema sample from <code>examples/</code>, not a run. Run <code>netcoop demo</code> to see real incidents.</p>}
      {item.isControl && <p className="notice">Control run: Connectivity received no peer findings.</p>}
      {r.summary && <p className="summary">{r.summary}</p>}
      <dl className="facts">
        <div><dt>Suspected root cause</dt><dd>{r.suspected_root_cause ? <code>{r.suspected_root_cause}</code> : <span className="muted">none named</span>}</dd></div>
        <div>
          <dt>Confidence</dt>
          <dd>
            {r.confidence != null
              ? <><strong>{r.confidence.toFixed(2)}</strong><div className="bar" role="img" aria-label={`confidence ${r.confidence.toFixed(2)}`}><div style={{ width: `${r.confidence * 100}%` }} /></div></>
              : <span className="muted">not applicable</span>}
          </dd>
        </div>
        <div><dt>Affected components</dt><dd>{affected.length ? affected.map((a) => <code key={a} className="chip">{a}</code>) : <span className="muted">none identified</span>}</dd></div>
        <div>
          <dt>Connectivity agent</dt>
          <dd>{!w ? <span className="muted">no call recorded</span>
            : w.error ? <span className="bad">failed: {w.error}</span>
            : w.httpStatus != null ? <>separate process, HTTP {w.httpStatus} ({w.sent} sent, {w.received} received)</>
            : <span className="muted">in-process (no HTTP in this run)</span>}</dd>
        </div>
      </dl>
      <p className="muted small">{confidenceMeaning(r)}</p>
      {!item.check.valid && <p className="bad">Report fails its schema: {item.check.reason}</p>}
      {item.traceError && <p className="bad">{item.traceError}</p>}
    </section>
  );
}

function Evidence({ item }: { item: RunItem }) {
  const r = item.report;
  const decision = decisionOf(item);
  const relevant = new Set([r.suspected_root_cause, ...(r.alternative_causes ?? []), ...(decision?.ranking ?? []).map((c) => c.hypothesis)]);
  const against = findingsOf(item).filter(({ p }) => p.stance === "contradicts" && relevant.has(p.target_hypothesis));
  return (
    <div className="grid">
      <Section title="Supporting evidence" note="Measurements behind the conclusion, each judged against its configured threshold.">
        {r.supporting_evidence?.length ? (
          <div className="scroll"><table>
            <thead><tr><th>ID</th><th>Metric</th><th>Value</th><th>Meaning</th><th>Source</th></tr></thead>
            <tbody>
              {r.supporting_evidence.map((e) => (
                <tr key={e.evidence_id}><td><code>{e.evidence_id}</code></td><td>{e.metric}</td><td className="nowrap">{String(e.value)} {e.unit}</td><td>{e.interpretation ?? ""}</td><td>{e.source_id}</td></tr>
              ))}
            </tbody>
          </table></div>
        ) : <p className="muted">No evidence is cited in this report.</p>}
      </Section>
      <Section title="Contradicting evidence" note="Findings that argue against a cause under consideration.">
        {against.length ? <ul className="plain">
          {against.map(({ m, p }) => <li key={m.message_id}><code>{p.finding_id}</code> <strong>{shortAgent(m.sender)}</strong> against <code>{p.target_hypothesis}</code><div className="muted small">{p.reason}</div></li>)}
        </ul> : <p className="muted">{item.trace ? "No finding argued against a ranked cause." : "This report has no trace to read findings from."}</p>}
        {r.alternative_causes?.length ? <><h3>Alternative causes still open</h3>{r.alternative_causes.map((a) => <code key={a} className="chip">{a}</code>)}</> : null}
      </Section>
    </div>
  );
}

function Causes({ item }: { item: RunItem }) {
  const ranking = decisionOf(item)?.ranking ?? [];
  return (
    <Section title="Competing causes" note="Correlation's ranking: heuristic-v1 scores before the status cap, after the verification rounds.">
      {ranking.length ? ranking.map((c) => (
        <div key={c.hypothesis} className="cause">
          <div className="row"><code>{c.hypothesis}</code><strong>{c.confidence.toFixed(2)}</strong></div>
          <div className="bar"><div style={{ width: `${c.confidence * 100}%` }} /></div>
          <div className="muted small">coverage {c.coverage} · supported by {c.supporting_finding_ids?.join(", ") || "none"} · contradicted by {c.contradicting_finding_ids?.join(", ") || "none"}</div>
        </div>
      )) : <p className="muted">{item.trace ? "No fault hypothesis was nominated with evidence." : "This report has no trace."}</p>}
    </Section>
  );
}

function Verification({ item }: { item: RunItem }) {
  const messages = messagesOf(item);
  const queries = messages.filter((m) => m.type === "QUERY");
  const notes = decisionOf(item)?.limitations ?? [];
  return (
    <Section title="Verification rounds" note="Correlation asks the agent that owns the key measurement. At most two rounds.">
      {queries.length ? queries.map((q) => {
        const p = payload<QueryPayload>(q);
        const response = messages.find((m) => m.type === "RESPONSE" && m.in_reply_to === q.message_id);
        const failure = messages.find((m) => m.type === "ERROR" && m.in_reply_to === q.message_id);
        const line = (word: string) => notes.find((n) => n.startsWith(`${word} ${p.query_id}:`))?.slice(word.length + p.query_id.length + 3);
        return (
          <div key={q.message_id} className="round">
            <div className="row"><span><strong>{p.query_id}</strong> to {shortAgent(q.recipient)}: <code>{p.desired_measurement}</code> on <code>{p.target_id}</code></span></div>
            <p className="small">{response ? payload<ResponsePayload>(response).answer : failure ? <span className="bad">{payload<ErrorPayload>(failure).detail}</span> : "No response."}</p>
            {line("before") && <p className="small muted">Before: {line("before")}</p>}
            {line("after") && <p className="small muted">After: {line("after")}</p>}
          </div>
        );
      }) : <p className="muted">{item.trace ? "No QUERY in this run: nothing was nominated that an in-process agent could verify." : "This report has no trace."}</p>}
    </Section>
  );
}

function Timeline({ item }: { item: RunItem }) {
  const [open, setOpen] = useState<string | null>(null);
  const [hide, setHide] = useState(true);
  const trace = item.trace ?? [];
  const routine = trace.filter(isRoutine).length;
  const shown = hide ? trace.filter((f) => !isRoutine(f)) : trace;
  return (
    <Section title="Agent communication" note="Every message of the trace, in order, as delivered through the validated bus. Highlighted findings were produced after reading a peer's finding.">
      {!trace.length ? <p className="muted">This report has no message trace.</p> : <>
        {routine > 0 && <label className="toggle"><input type="checkbox" checked={hide} onChange={(e) => setHide(e.target.checked)} /> Hide {routine} routine path checks</label>}
        <ol className="timeline">
          {shown.map((f) => {
            const m = f.message;
            const type = String(f.raw.type ?? "?");
            const consumed = m?.type === "FINDING" ? payload<FindingPayload>(m).consumed_message_ids ?? [] : [];
            return (
              <li key={f.id} className={consumed.length ? "peer" : ""}>
                <button className="row-btn" aria-expanded={open === f.id} onClick={() => setOpen(open === f.id ? null : f.id)}>
                  <span className={`type t-${type}`}>{type}</span>
                  <span className="route">{shortAgent(String(f.raw.sender))} → {shortAgent(String(f.raw.recipient))}</span>
                  <span className="gist">{m ? gist(m) : `rejected: ${f.check.valid ? "" : f.check.reason}`}</span>
                </button>
                {consumed.length > 0 && <div className="consumed small">interprets {consumed.map((c) => <code key={c}>{c}</code>)}</div>}
                {open === f.id && <pre>{JSON.stringify(f.raw, null, 2)}</pre>}
              </li>
            );
          })}
        </ol>
      </>}
    </Section>
  );
}

function ControlComparison({ item }: { item: RunItem }) {
  if (item.sample || !item.trace) return null;
  const normal = item.isControl ? normalTwin(item) : item;
  const control = item.isControl ? item : controlTwin(item);
  if (!normal || !control) {
    return <Section title="Why the exchange matters"><p className="muted">No control run for this incident. Run the same scenario with <code>--control</code> so Connectivity gets no peer findings, then reload.</p></Section>;
  }
  const facts = (run: RunItem) => {
    const conn = findingsOf(run).filter(({ m }) => m.sender === "connectivity_agent");
    const task = messagesOf(run).find((m) => m.type === "TASK" && m.recipient === "connectivity_agent");
    return { peers: task ? payload<TaskPayload>(task).peer_message_ids?.length ?? 0 : 0, informed: conn.filter(({ p }) => p.consumed_message_ids?.length), total: conn.length, top: decisionOf(run)?.ranking?.[0] };
  };
  const a = facts(normal), b = facts(control);
  const rows: [string, React.ReactNode, React.ReactNode][] = [
    ["Peer findings sent to Connectivity", a.peers, b.peers],
    ["Connectivity findings", a.total, b.total],
    ["Findings built on a peer's message", a.informed.map(({ p }) => <code key={p.finding_id} className="chip">{p.finding_id} {p.hypothesis}</code>), b.informed.length || "none"],
    ["Leading cause, heuristic-v1 score", a.top ? `${a.top.hypothesis} ${a.top.confidence.toFixed(2)}` : "none", b.top ? `${b.top.hypothesis} ${b.top.confidence.toFixed(2)}` : "none"],
    ["Reported status and confidence", `${normal.report.status} ${normal.report.confidence ?? "-"}`, `${control.report.status} ${control.report.confidence ?? "-"}`],
  ];
  return (
    <Section title="Why the exchange matters" note="The same telemetry, with and without the peer findings forwarded to Connectivity.">
      <div className="scroll"><table>
        <thead><tr><th /><th>With peer findings</th><th>Control: none sent</th></tr></thead>
        <tbody>{rows.map(([label, x, y]) => <tr key={label}><th scope="row">{label}</th><td>{x}</td><td>{y}</td></tr>)}</tbody>
      </table></div>
    </Section>
  );
}

function Follow({ item }: { item: RunItem }) {
  const r = item.report;
  const limitations = (r.limitations ?? []).filter((l) => !/^(before|after) Q-\d+:/.test(l));
  return (
    <div className="grid">
      <Section title="Recommended follow-up checks">
        {r.recommended_actions?.length ? <ol>{r.recommended_actions.map((a) => <li key={a}>{a}</li>)}</ol> : <p className="muted">None.</p>}
      </Section>
      <Section title="Limitations and missing evidence">
        {limitations.length ? <ul>{limitations.map((l) => <li key={l}>{l}</li>)}</ul> : <p className="muted">None recorded.</p>}
        {r.validation_errors?.length ? <><h3>Rejected inputs</h3><ul className="bad">{r.validation_errors.map((e) => <li key={e}>{e}</li>)}</ul></> : null}
      </Section>
    </div>
  );
}

function IncidentList({ items, current, onPick }: { items: RunItem[]; current: RunItem; onPick: (i: RunItem) => void }) {
  return <>{items.map((r) => (
    <button key={r.file} className={`incident-btn ${r.file === current.file ? "on" : ""}`} aria-current={r.file === current.file} onClick={() => onPick(r)}>
      <span className={`dot s-${r.report.status}`} title={STATUS_LABEL[r.report.status]} />
      <span>{r.title}</span>
    </button>
  ))}</>;
}

export default function App() {
  const all = useMemo(() => [...runs, ...sampleReports], []);
  const [file, setFile] = useState(all[0]?.file ?? "");
  const current = all.find((r) => r.file === file) ?? all[0];

  return (
    <div className="app">
      <header className="top">
        <h1>NetCoop AI</h1>
        <p className="flow">{WORKFLOW.map((w, i) => <span key={w}>{i > 0 && <span className="arrow"> → </span>}{w}</span>)}</p>
      </header>
      {!runs.length && (
        <p className="notice wide">No runs found in <code>reports/</code>. From the repository root run <code>netcoop demo</code>, then reload this page. The list below shows schema samples only.</p>
      )}
      {!current ? <p className="notice wide">Nothing to show: no runs and no samples could be loaded.</p> : (
        <div className="layout">
          <nav className="side" aria-label="Incidents">
            {runs.length > 0 && <><h2>Runs <span className="muted small">{runs.length}</span></h2><IncidentList items={runs} current={current} onPick={(r) => setFile(r.file)} /></>}
            <h2>Schema samples</h2>
            <IncidentList items={sampleReports} current={current} onPick={(r) => setFile(r.file)} />
          </nav>
          <main>
            <Diagnosis item={current} />
            <Evidence item={current} />
            <div className="grid"><Causes item={current} /><Verification item={current} /></div>
            <ControlComparison item={current} />
            <Timeline key={current.file} item={current} />
            <Follow item={current} />
            <details className="card raw"><summary>Raw report JSON</summary><pre>{JSON.stringify(current.report, null, 2)}</pre></details>
          </main>
        </div>
      )}
    </div>
  );
}
