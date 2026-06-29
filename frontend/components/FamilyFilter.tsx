"use client";

import { FAMILIES, SUBFIELDS } from "@/lib/families";
import type { Family } from "@/lib/types";

export interface FilterState {
  families: Set<Family>;
  subfields: Set<string>;
}

interface FamilyFilterProps {
  state: FilterState;
  onChange: (next: FilterState) => void;
  /** Subfield tags actually present in the current digest (optional narrowing). */
  availableSubfields?: string[];
}

function toggle<T>(set: Set<T>, value: T): Set<T> {
  const next = new Set(set);
  if (next.has(value)) next.delete(value);
  else next.add(value);
  return next;
}

/**
 * Filter chips by family and subfield. Empty selection = show everything.
 * Active chips use the single accent. Each chip is a real toggle button with
 * aria-pressed for screen readers.
 */
export function FamilyFilter({
  state,
  onChange,
  availableSubfields,
}: FamilyFilterProps): JSX.Element {
  const subfields =
    availableSubfields && availableSubfields.length > 0
      ? SUBFIELDS.filter((s) => availableSubfields.includes(s))
      : SUBFIELDS;

  const anyActive = state.families.size > 0 || state.subfields.size > 0;

  return (
    <div className="flex flex-col gap-3">
      <fieldset className="flex flex-wrap items-center gap-2">
        <legend className="label-mono mr-1 inline text-muted">Family</legend>
        {FAMILIES.map((f) => {
          const active = state.families.has(f.key);
          return (
            <button
              key={f.key}
              type="button"
              aria-pressed={active}
              onClick={() => onChange({ ...state, families: toggle(state.families, f.key) })}
              className={chipClass(active)}
            >
              <span aria-hidden className="mr-1">
                {f.emoji}
              </span>
              {f.label}
            </button>
          );
        })}
      </fieldset>

      <fieldset className="flex flex-wrap items-center gap-2">
        <legend className="label-mono mr-1 inline text-muted">Subfield</legend>
        {subfields.map((s) => {
          const active = state.subfields.has(s);
          return (
            <button
              key={s}
              type="button"
              aria-pressed={active}
              onClick={() => onChange({ ...state, subfields: toggle(state.subfields, s) })}
              className={chipClass(active)}
            >
              {s}
            </button>
          );
        })}
        {anyActive ? (
          <button
            type="button"
            onClick={() => onChange({ families: new Set(), subfields: new Set() })}
            className="label-mono ml-1 text-muted underline underline-offset-2 hover:text-ink"
          >
            clear
          </button>
        ) : null}
      </fieldset>
    </div>
  );
}

function chipClass(active: boolean): string {
  return [
    "label-mono rounded-full border px-2.5 py-1 transition-colors",
    active
      ? "border-accent bg-accent/10 text-accent"
      : "border-hairline text-muted hover:border-ink/40 hover:text-ink",
  ].join(" ");
}
