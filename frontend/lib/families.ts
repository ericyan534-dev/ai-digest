/** Family + tier presentation metadata (labels, emoji, subfield catalogue). */

import type { Family, ImportanceTier } from "./types";

export interface FamilyMeta {
  key: Family;
  label: string;
  emoji: string;
}

export const FAMILIES: FamilyMeta[] = [
  { key: "academia", label: "Academia", emoji: "🎓" },
  { key: "industry", label: "Industry", emoji: "🏭" },
  { key: "community", label: "Community", emoji: "💬" },
  { key: "meta", label: "Meta", emoji: "🧭" },
];

const FAMILY_BY_KEY: Record<Family, FamilyMeta> = FAMILIES.reduce(
  (acc, f) => {
    acc[f.key] = f;
    return acc;
  },
  {} as Record<Family, FamilyMeta>,
);

export function familyMeta(family: Family): FamilyMeta {
  return FAMILY_BY_KEY[family] ?? { key: family, label: family, emoji: "•" };
}

export function familyLabel(family: Family): string {
  return familyMeta(family).label;
}

/** The user's tracked subfields (used by the filter chips). */
export const SUBFIELDS: string[] = [
  "Multi-Agent Systems",
  "Efficient & Scalable NLP",
  "RL for NLP",
  "LLMs & Foundation Models",
  "Optimization",
];

export interface TierMeta {
  key: ImportanceTier;
  label: string;
}

const TIER_LABELS: Record<ImportanceTier, string> = {
  breakthrough: "Breakthrough",
  notable: "Notable",
  minor: "Minor",
  quiet_day: "Quiet day",
};

export function tierLabel(tier: ImportanceTier): string {
  return TIER_LABELS[tier] ?? tier;
}

/** Breakthrough items get full-depth expand/collapse. */
export function isFullDepth(tier: ImportanceTier): boolean {
  return tier === "breakthrough";
}
