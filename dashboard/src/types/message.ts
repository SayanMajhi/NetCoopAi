/* Generated from schemas/message-1.0.json by `npm run gen:types`. Do not edit. */

export type Envelope = {
  extra?: Extra;
  in_reply_to: InReplyTo;
  incident_id: IncidentId;
  message_id: MessageId;
  payload: Payload;
  protocol: Protocol;
  recipient: Recipient;
  sender: Sender;
  timestamp: Timestamp;
  type: MessageType;
};
/**
 * null only for a new incident or an unsolicited finding
 */
export type InReplyTo = string | null;
export type IncidentId = string;
/**
 * Deterministic, unique per incident
 */
export type MessageId = string;
/**
 * Protocol and version
 */
export type Protocol = string;
export type Recipient = string;
export type Sender = string;
/**
 * UTC, ISO 8601
 */
export type Timestamp = string;
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "MessageType".
 */
export type MessageType = "TASK" | "FINDING" | "QUERY" | "RESPONSE" | "DECISION" | "ERROR";
export type DecisionId = string;
export type Limitations = string[];
export type Confidence = number;
export type ContradictingFindingIds = string[];
export type Coverage = number;
export type Hypothesis = string;
export type SupportingFindingIds = string[];
export type Ranking = RankedCause[];
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "Status".
 */
export type Status = "diagnosed" | "probable" | "healthy" | "insufficient_evidence" | "invalid_input" | "failed";
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "ErrorCode".
 */
export type ErrorCode =
  | "SCHEMA_VERSION_UNSUPPORTED"
  | "MISSING_FIELD"
  | "INVALID_VALUE"
  | "MALFORMED_JSON"
  | "DUPLICATE_MESSAGE"
  | "UNKNOWN_EVIDENCE"
  | "UNKNOWN_RECIPIENT"
  | "AGENT_TIMEOUT"
  | "AGENT_FAILURE";
export type Detail = string;
export type FieldPath = string | null;
export type OffendingMessageId = string | null;
/**
 * Peer messages interpreted to produce this finding
 */
export type ConsumedMessageIds = string[];
/**
 * Canonical evidence IDs cited
 *
 * @minItems 1
 */
export type EvidenceIds = [string, ...string[]];
/**
 * e.g. F-PERF-01
 */
export type FindingId = string;
/**
 * <cause>@<device_or_link_id>
 */
export type Hypothesis1 = string;
export type Reason = string;
/**
 * Agent-local signal fit [0,1]
 */
export type Score = number | null;
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "Stance".
 */
export type Stance = "supports" | "contradicts";
export type TargetHypothesis = string;
/**
 * Canonical metric or service check name
 */
export type DesiredMeasurement = string;
export type QueryId = string;
export type Question = string;
export type RelatedHypotheses = string[];
/**
 * Device or link ID to check
 */
export type TargetId = string;
export type Answer = string;
export type EvidenceIds1 = string[];
/**
 * Query answered, if any
 */
export type QueryId1 = string | null;
/**
 * Capability requested, must match an agent card
 */
export type Capability = string;
/**
 * Evidence the agent may read
 */
export type EvidenceIds2 = string[];
export type Instructions = string;
/**
 * Peer FINDING message IDs forwarded for interpretation
 */
export type PeerMessageIds = string[];
/**
 * Device/link IDs in scope
 */
export type ScopeIds = string[];
/**
 * Deterministic task ID, e.g. INC-0001-T-PERF-01
 */
export type TaskId = string;

/**
 * Unknown optional fields preserved from compatible 1.x senders
 */
export interface Extra {
  [k: string]: unknown;
}
export interface Payload {
  [k: string]: unknown;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "DecisionPayload".
 */
export interface DecisionPayload {
  decision_id: DecisionId;
  limitations?: Limitations;
  ranking?: Ranking;
  status: Status;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "RankedCause".
 */
export interface RankedCause {
  confidence: Confidence;
  contradicting_finding_ids?: ContradictingFindingIds;
  coverage: Coverage;
  hypothesis: Hypothesis;
  supporting_finding_ids?: SupportingFindingIds;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "ErrorPayload".
 */
export interface ErrorPayload {
  code: ErrorCode;
  detail: Detail;
  field_path?: FieldPath;
  offending_message_id?: OffendingMessageId;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "FindingPayload".
 */
export interface FindingPayload {
  consumed_message_ids?: ConsumedMessageIds;
  evidence_ids: EvidenceIds;
  finding_id: FindingId;
  hypothesis: Hypothesis1;
  reason: Reason;
  score?: Score;
  stance: Stance;
  target_hypothesis: TargetHypothesis;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "QueryPayload".
 */
export interface QueryPayload {
  desired_measurement: DesiredMeasurement;
  query_id: QueryId;
  question: Question;
  related_hypotheses?: RelatedHypotheses;
  target_id: TargetId;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "ResponsePayload".
 */
export interface ResponsePayload {
  answer?: Answer;
  evidence_ids?: EvidenceIds1;
  query_id?: QueryId1;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "TaskPayload".
 */
export interface TaskPayload {
  capability: Capability;
  evidence_ids?: EvidenceIds2;
  instructions?: Instructions;
  peer_message_ids?: PeerMessageIds;
  scope_ids?: ScopeIds;
  task_id: TaskId;
}
