// Loads real runs (reports/, traces/) and the repo's examples/, and checks each one against the
// generated JSON Schemas: the same contract the Python bus and the Connectivity REST service enforce.
import Ajv2020 from "ajv/dist/2020";
import addFormats from "ajv-formats";
import messageSchema from "../../schemas/message-1.0.json";
import reportSchema from "../../schemas/report-1.0.json";
import type { Envelope } from "./types/message";
import type { Report } from "./types/report";

const ajv = new Ajv2020({ allErrors: true, strict: false });
addFormats(ajv);
const validateMessage = ajv.compile(messageSchema);
const validateReport = ajv.compile(reportSchema);

export type Check = { valid: true } | { valid: false; reason: string };

export interface FeedItem {
  id: string;
  raw: Record<string, unknown>;
  message: Envelope | null; // null when the body is not a netcoop/1.x envelope
  check: Check;
}

export interface WireRecord {
  httpStatus: number | null; // null when Connectivity ran in-process
  error: string | null;
  sent: number; // messages in the request Bundle
  received: number; // messages in the reply Bundle
}

export interface RunItem {
  file: string;
  title: string;
  complaint?: string;
  report: Report;
  check: Check;
  sample: boolean; // true for examples/, which are schema samples and not runs
  isControl: boolean;
  trace?: FeedItem[]; // the message trace written by `netcoop run` / `netcoop demo`
  traceError?: string;
  wire?: WireRecord;
}

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

function checkReport(raw: Record<string, unknown>): Check {
  return validateReport(raw) ? { valid: true } : { valid: false, reason: validateReport.errors?.[0]?.message ?? "invalid" };
}

const stem = (path: string) => path.split("/").pop()!.replace(/\.[^.]+$/, "");
const titleOf = (name: string) => name.replace(/_/g, " ").replace(/\b(dns|dhcp)\b/g, (w) => w.toUpperCase())
  .replace(/(\d) (\d|x)\b/, "$1.$2").replace(/^./, (c) => c.toUpperCase());

// Real runs are git-ignored and empty on a fresh clone; `netcoop demo` writes them.
const runReports = import.meta.glob<Record<string, unknown>>("../../reports/*.json", { eager: true, import: "default" });
const runTraces = import.meta.glob<string>("../../traces/*.jsonl", { eager: true, query: "?raw", import: "default" });
const samples = import.meta.glob<Record<string, unknown>>("../../examples/report_*.json", { eager: true, import: "default" });

// Scenario names for the incident list. malformed_input.json is deliberately not valid JSON.
const incidentIds = import.meta.glob<string>(["../../scenarios/dev/*.json", "!../../scenarios/dev/malformed_input.json"], { eager: true, import: "incident_id" });
const complaints = import.meta.glob<string>(["../../scenarios/dev/*.json", "!../../scenarios/dev/malformed_input.json"], { eager: true, import: "complaint" });
const scenarioOf = new Map(Object.entries(incidentIds).map(([path, id]) => [id, { name: stem(path), complaint: complaints[path] }]));

function loadTrace(name: string): Pick<RunItem, "trace" | "traceError" | "wire"> {
  // Keyed by report file name: a --control report shares its incident_id with the normal run.
  const text = Object.entries(runTraces).find(([path]) => stem(path) === name)?.[1];
  if (!text) return {};
  try {
    const lines = text.split("\n").filter((line) => line.trim()).map((line) => JSON.parse(line) as Record<string, unknown>);
    const trace = lines.filter((rec) => rec.kind === "message").map(({ kind: _kind, ...raw }): FeedItem => { // `kind` tags the trace line, it is not part of the envelope
      const check = checkMessage(raw);
      return { id: String(raw.message_id), raw, message: check.valid ? (raw as unknown as Envelope) : null, check };
    });
    const rec = lines.find((line) => line.kind === "wire");
    const count = (body: unknown) => ((body as { messages?: unknown[] } | null)?.messages ?? []).length;
    const wire = rec && { httpStatus: (rec.http_status as number | null) ?? null, error: (rec.error as string | null) ?? null, sent: count(rec.request), received: count(rec.response) };
    return { trace, wire };
  } catch (exc) {
    return { traceError: `traces/${name}.jsonl could not be read: ${String(exc)}` };
  }
}

function runItem(path: string, raw: Record<string, unknown>): RunItem {
  const name = stem(path);
  const incident = String(raw.incident_id);
  const scenario = scenarioOf.get(incident);
  const isControl = name.endsWith("--control");
  return {
    file: `${name}.json`, report: raw as unknown as Report, check: checkReport(raw), sample: false, isControl,
    title: titleOf(scenario?.name ?? incident.replace(/^INC-/, "")) + (isControl ? " (control)" : ""),
    complaint: scenario?.complaint, ...loadTrace(name),
  };
}

// A control run is listed right after the run it is compared with.
const order = (path: string) => (path.endsWith("--control.json") ? path.replace("--control.json", ".json~") : path);
export const runs: RunItem[] = Object.entries(runReports).sort(([a], [b]) => order(a).localeCompare(order(b))).map(([path, raw]) => runItem(path, raw));

export const sampleReports: RunItem[] = Object.entries(samples).sort(([a], [b]) => a.localeCompare(b)).map(([path, raw]) => ({
  file: `${stem(path)}.json`, title: titleOf(stem(path).replace(/^report_/, "")), report: raw as unknown as Report,
  check: checkReport(raw), sample: true, isControl: false,
}));

export const controlTwin = (item: RunItem): RunItem | undefined =>
  runs.find((r) => r.isControl && r.report.incident_id === item.report.incident_id);
export const normalTwin = (item: RunItem): RunItem | undefined =>
  runs.find((r) => !r.isControl && r.report.incident_id === item.report.incident_id);
