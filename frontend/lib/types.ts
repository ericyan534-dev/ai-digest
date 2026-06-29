/**
 * Wire types — mirror `aidigest.models` as serialized by `model_dump(mode="json")`.
 * Source of truth: ../API_CONTRACT.md. Keep field names identical to the backend.
 */

export type Family = "academia" | "industry" | "community" | "meta";

export type ImportanceTier = "breakthrough" | "notable" | "minor" | "quiet_day";

export type DigestKind = "daily" | "weekly";

export type FeedbackSignal = "up" | "down" | "click" | "dwell" | "nl_instruction";

export type FeedbackTargetKind = "item" | "story" | "digest_section" | "digest";

/** GET /api/health */
export interface Health {
  status: string;
  db: "ok" | "down";
  llm_mock: boolean;
  version: string;
}

/** A row from GET /api/digests (archive list). */
export interface DigestSummary {
  id: string;
  kind: DigestKind;
  date: string; // ISO date (daily) or week start (weekly's week_of via date field)
  tier: ImportanceTier;
  quiet: boolean;
  title: string; // daily tldr OR weekly title
  created_at: string;
}

/** One per-story takeaway inside a daily section. */
export interface StorySummary {
  story_id: string;
  title: string;
  family: Family;
  tier: ImportanceTier;
  takeaway: string;
  why_it_matters: string;
  links: string[];
  tags: string[];
  score: number;
}

export interface DigestSection {
  family: Family;
  heading: string;
  summaries: StorySummary[];
}

export interface DailyDigest {
  id: string;
  kind: "daily";
  date: string;
  tldr: string;
  overall_tier: ImportanceTier;
  quiet_day: boolean;
  sections: DigestSection[];
  story_ids: string[];
  model: string;
  cost_usd: number;
  eval_scores: Record<string, number>;
  created_at: string;
}

export interface WeeklyShortlistEntry {
  title: string;
  url: string | null;
  one_liner: string;
  family: Family;
}

export interface WeeklyDigest {
  id: string;
  kind: "weekly";
  week_of: string;
  title: string;
  lede: string;
  body_markdown: string;
  overall_tier: ImportanceTier;
  quiet_week: boolean;
  shortlist: WeeklyShortlistEntry[];
  on_my_radar: WeeklyShortlistEntry[];
  story_ids: string[];
  candidate_count: number;
  winning_candidate: number;
  model: string;
  judge_model: string;
  cost_usd: number;
  eval_scores: Record<string, number>;
  created_at: string;
}

export type AnyDigest = DailyDigest | WeeklyDigest;

/** GET /api/stories */
export interface Story {
  id: string;
  title: string;
  family: Family;
  item_ids: string[];
  representative_item_id: string | null;
  embedding: null; // never shipped over the wire
  importance: number;
  personal: number;
  final_rank: number;
  tier: ImportanceTier;
  mention_count: number;
  created_at: string;
}

/** POST /api/feedback request body. */
export interface FeedbackRequest {
  target_id: string;
  target_kind: FeedbackTargetKind;
  signal: FeedbackSignal;
  value: number;
  text?: string | null;
}

export interface FeedbackResponse {
  ok: boolean;
  id: number;
}

/** POST /api/tune. */
export interface TuneRequest {
  instruction: string;
}

export interface TuneResponse {
  ok: boolean;
  profile: TunedProfile;
}

export interface TunedProfile {
  subfields?: string[];
  mutes?: string[];
  ranking?: { alpha?: number; beta?: number; gamma?: number };
  [key: string]: unknown;
}

export function isDaily(d: AnyDigest): d is DailyDigest {
  return d.kind === "daily";
}

export function isWeekly(d: AnyDigest): d is WeeklyDigest {
  return d.kind === "weekly";
}
