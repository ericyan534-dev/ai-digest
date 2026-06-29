import { familyMeta } from "@/lib/families";
import type { Family } from "@/lib/types";

/** A small mono chip identifying a story's family (Academia / Industry / …). */
export function FamilyChip({ family }: { family: Family }): JSX.Element {
  const meta = familyMeta(family);
  return (
    <span className="label-mono inline-flex items-center gap-1 rounded-sm border border-hairline px-1.5 py-0.5 text-ink/70">
      <span aria-hidden>{meta.emoji}</span>
      <span>{meta.label}</span>
    </span>
  );
}
