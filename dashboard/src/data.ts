// Loads the repo's examples/ and checks each one against the generated JSON Schemas,
// the same contract the Python bus and the Connectivity REST service enforce.
import Ajv2020 from "ajv/dist/2020";
import addFormats from "ajv-formats";
import messageSchema from "../../schemas/message-1.0.json";
import reportSchema from "../../schemas/report-1.0.json";
import type { DecisionPayload, Envelope } from "./types/message";
import type { Report } from "./types/report";

const ajv = new Ajv2020({ allErrors: true, strict: false });
addFormats(ajv);
const validateMessage = ajv.compile(messageSchema);
const validateReport = ajv.compile(reportSchema);

export type Check = { valid: true } | { valid: false; reason: string };

export interface FeedItem {
  file: string;
  raw: Record<string, unknown>;
  message: Envelope | null; // null when the body is not a netcoop/1.x envelope
  check: Check;
}

// Story order for the mock incident; unknown files go last.
const ORDER = ["task", "finding", "query", "response", "decision", "error", "finding_v0_9", "message_v2_0"];

const files = import.meta.glob<Record<string, unknown>>("../../examples/*.json", { eager: true, import: "default" });
const byName = Object.fromEntries(
  Object.entries(files).map(([path, body]) => [path.split("/").pop()!.replace(/\.json$/, ""), body]),
);

function checkMessage(raw: Record<string, unknown>): Check {
  const protocol = typeof raw.protocol === "string" ? raw.protocol : "";
  const major = /^netcoop\/(\d+)\.\d+$/.exec(protocol)?.[1];
  if (major !== "1") {
    return { valid: false, reason: `SCHEMA_VERSION_UNSUPPORTED: ${protocol || "no protocol field"}` };
  }
  if (validateMessage(raw)) return { valid: true };
  const err = validateMessage.errors?.[0];
  const missing = err?.keyword === "required" ? `.${(err.params as { missingProperty: string }).missingProperty}` : "";
  return { valid: false, reason: `${err?.instancePath || "/"}${missing}: ${err?.message ?? "invalid"}` };
}

export const feed: FeedItem[] = Object.keys(byName)
  .filter((name) => !name.startsWith("report_"))
  .sort((a, b) => (ORDER.indexOf(a) + 1 || 99) - (ORDER.indexOf(b) + 1 || 99))
  .map((name) => {
    const raw = byName[name];
    const check = checkMessage(raw);
    return { file: `${name}.json`, raw, message: check.valid ? (raw as unknown as Envelope) : null, check };
  });

export const decision = feed.find((f) => f.message?.type === "DECISION")?.message?.payload as unknown as
  | DecisionPayload
  | undefined;

export interface ReportItem {
  file: string;
  report: Report;
  check: Check;
  trace?: FeedItem[]; // the real message trace, for reports written by `netcoop run` / `netcoop demo`
}

function checkReport(raw: Record<string, unknown>): Check {
  return validateReport(raw) ? { valid: true } : { valid: false, reason: validateReport.errors?.[0]?.message ?? "invalid" };
}

// Real runs: reports/*.json and traces/*.jsonl written by `netcoop demo` (git-ignored; empty on a fresh clone).
const runReports = import.meta.glob<Record<string, unknown>>("../../reports/*.json", { eager: true, import: "default" });
const runTraces = import.meta.glob<string>("../../traces/*.jsonl", { eager: true, query: "?raw", import: "default" });

function traceFeed(incident: string): FeedItem[] | undefined {
  const text = Object.entries(runTraces).find(([path]) => path.endsWith(`/${incident}.jsonl`))?.[1];
  if (!text) return undefined;
  return text.split("\n").filter((line) => line.trim()).map((line) => JSON.parse(line) as Record<string, unknown>)
    .filter((rec) => rec.kind === "message")
    .map(({ kind: _kind, ...raw }) => { // `kind` is the trace line's tag, not part of the envelope
      const check = checkMessage(raw);
      return { file: String(raw.message_id), raw, message: check.valid ? (raw as unknown as Envelope) : null, check };
    });
}

export const reports: ReportItem[] = [
  ...Object.entries(runReports).sort(([a], [b]) => a.localeCompare(b)).map(([path, raw]) => ({
    file: path.split("/").pop()!, report: raw as unknown as Report, check: checkReport(raw),
    trace: traceFeed(String(raw.incident_id)),
  })),
  ...Object.keys(byName).filter((name) => name.startsWith("report_")).sort().map((name) => ({
    file: `${name}.json`, report: byName[name] as unknown as Report, check: checkReport(byName[name]),
  })),
];
