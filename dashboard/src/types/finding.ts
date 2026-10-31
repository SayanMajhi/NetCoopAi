/* Generated from schemas/finding-1.0.json by `npm run gen:types`. Do not edit. */

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
export type Hypothesis = string;
export type Reason = string;
/**
 * Agent-local signal fit [0,1]
 */
export type Score = number | null;
/**
 * This interface was referenced by `FindingPayload`'s JSON-Schema
 * via the `definition` "Stance".
 */
export type Stance = "supports" | "contradicts";
export type TargetHypothesis = string;

export interface FindingPayload {
  consumed_message_ids?: ConsumedMessageIds;
  evidence_ids: EvidenceIds;
  finding_id: FindingId;
  hypothesis: Hypothesis;
  reason: Reason;
  score?: Score;
  stance: Stance;
  target_hypothesis: TargetHypothesis;
}
