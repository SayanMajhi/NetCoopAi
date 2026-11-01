import { useState } from "react";
import { decision, feed, reports } from "./data";
import type { DecisionPayload } from "./types/message";
import type { Report } from "./types/report";

const STATUS_COLOURS: Record<Report["status"], string> = {
  diagnosed: "#1a7f37", probable: "#9a6700", healthy: "#0969da",
  insufficient_evidence: "#6e7781", invalid_input: "#cf222e", failed: "#82071e",
};
const STATUSES = Object.keys(STATUS_COLOURS) as Report["status"][];

function Badge({ status }: { status: Report["status"] }) {
  return <span className="badge" style={{ background: STATUS_COLOURS[status] }}>{status}</span>;
}

export default function App() {
  const [selected, setSelected] = useState<string>(feed[0]?.file ?? "");
  const [reportFile, setReportFile] = useState<string>(reports[0]?.file ?? "");
  const current = reports.find((r) => r.file === reportFile) ?? reports[0];
  const report = current?.report;
  const items = current?.trace ?? feed;
  const ranking = (current?.trace
    ? (items.find((f) => f.message?.type === "DECISION")?.message?.payload as unknown as DecisionPayload | undefined)
    : decision)?.ranking;
  const raw = items.find((f) => f.file === selected)?.raw ?? report;

  return (
    <main>
      <header>
        <h1>NetCoop AI</h1>
        <p className="muted">Real runs from <code>reports/</code> + <code>traces/</code> (run <code>netcoop demo</code> first), then the samples in <code>examples/</code>; every message and report is validated in the browser against <code>schemas/*.json</code>.</p>
        <div className="legend">{STATUSES.map((s) => <Badge key={s} status={s} />)}</div>
      </header>

      <section>
        <h2>Report</h2>
        <div className="tabs">
          {reports.map((r) => (
            <button key={r.file} className={r.file === reportFile ? "on" : ""} onClick={() => { setReportFile(r.file); setSelected(""); }}>
              {r.file}
            </button>
          ))}
        </div>
        {report && (
          <div className="card">
            {report.summary && <p><strong>{report.summary}</strong></p>}
            <p>
              <Badge status={report.status} /> <strong>{report.suspected_root_cause ?? "no cause named"}</strong>
              {report.confidence != null && <> — confidence {report.confidence}</>}
              {!current.check.valid && <span className="bad"> schema error: {current.check.reason}</span>}
            </p>
            {report.affected_devices?.length ? <p>Affected: {[...report.affected_devices, ...(report.affected_segments ?? [])].join(", ")}</p> : null}
            {report.limitations?.length ? <p>Limitations: {report.limitations.join("; ")}</p> : null}
            {report.validation_errors?.length ? <p className="bad">Validation errors: {report.validation_errors.join("; ")}</p> : null}
            {report.recommended_actions?.length ? <p>Next: {report.recommended_actions.join("; ")}</p> : null}
          </div>
        )}
      </section>

      <div className="grid">
        <section>
          <h2>Agent messages {current?.trace ? <span className="muted small">({items.length} in {report?.agent_trace_id})</span> : <span className="muted small">(sample)</span>}</h2>
          <table>
            <thead><tr><th>Type</th><th>From → To</th><th>Message</th><th>Check</th></tr></thead>
            <tbody>
              {items.map((f) => {
                const sender = String(f.raw.sender ?? f.raw.from ?? "?");
                const recipient = String(f.raw.recipient ?? f.raw.to ?? "?");
                return (
                  <tr key={f.file} className={f.file === selected ? "sel" : ""} onClick={() => setSelected(f.file)}>
                    <td><code>{String(f.raw.type ?? f.raw.kind ?? "?")}</code></td>
                    <td>{sender} → {recipient}</td>
                    <td><code>{String(f.raw.message_id ?? f.raw.msg_id ?? f.file)}</code></td>
                    <td>{f.check.valid ? <span className="ok">valid</span> : <span className="bad" title={f.check.reason}>rejected: {f.check.reason}</span>}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </section>

        <section>
          <h2>Competing causes</h2>
          {ranking?.map((c) => (
            <div key={c.hypothesis} className="cause">
              <div className="row"><code>{c.hypothesis}</code><span>{c.confidence.toFixed(2)}</span></div>
              <div className="bar"><div style={{ width: `${c.confidence * 100}%` }} /></div>
              <div className="muted small">
                coverage {c.coverage} · supports {c.supporting_finding_ids?.join(", ") || "—"} · contradicts {c.contradicting_finding_ids?.join(", ") || "—"}
              </div>
            </div>
          )) ?? <p className="muted">No DECISION message.</p>}

          <h2>Evidence</h2>
          <table>
            <thead><tr><th>ID</th><th>Metric</th><th>Value</th><th>Meaning</th><th>Source</th></tr></thead>
            <tbody>
              {report?.supporting_evidence?.length ? report.supporting_evidence.map((e) => (
                <tr key={e.evidence_id}><td><code>{e.evidence_id}</code></td><td>{e.metric}</td><td>{String(e.value)} {e.unit}</td><td>{e.interpretation ?? ""}</td><td>{e.source_id}</td></tr>
              )) : <tr><td colSpan={5} className="muted">No supporting evidence in this report.</td></tr>}
            </tbody>
          </table>
        </section>
      </div>

      <section>
        <h2>Raw JSON <span className="muted small">{selected || current?.file}</span></h2>
        <pre>{JSON.stringify(raw, null, 2)}</pre>
      </section>
    </main>
  );
}
