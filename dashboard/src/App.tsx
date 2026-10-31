import reportJson from "../../examples/report_diagnosed.json";
import type { Report } from "./types/report";

const report = reportJson as unknown as Report;

const STATUS_COLOURS: Record<Report["status"], string> = {
  diagnosed: "#1a7f37", probable: "#9a6700", healthy: "#0969da",
  insufficient_evidence: "#6e7781", invalid_input: "#cf222e", failed: "#82071e",
};

export default function App() {
  return (
    <main style={{ fontFamily: "system-ui", maxWidth: 960, margin: "24px auto", padding: "0 16px" }}>
      <h1>NetCoop AI — incident {report.incident_id}</h1>
      <p>
        <span style={{ background: STATUS_COLOURS[report.status], color: "white", padding: "2px 10px", borderRadius: 12 }}>
          {report.status}
        </span>{" "}
        <strong>{report.suspected_root_cause ?? "no cause named"}</strong>
        {report.confidence != null && <> — confidence {report.confidence}</>}
      </p>
      <h2>Supporting evidence</h2>
      <table cellPadding={6}>
        <thead><tr><th>Evidence</th><th>Metric</th><th>Value</th><th>Source</th></tr></thead>
        <tbody>
          {report.supporting_evidence?.map((e) => (
            <tr key={e.evidence_id}><td>{e.evidence_id}</td><td>{e.metric}</td><td>{String(e.value)} {e.unit}</td><td>{e.source_id}</td></tr>
          ))}
        </tbody>
      </table>
      <h2>Raw JSON</h2>
      <pre style={{ background: "#f6f8fa", padding: 12, overflow: "auto" }}>{JSON.stringify(report, null, 2)}</pre>
    </main>
  );
}
