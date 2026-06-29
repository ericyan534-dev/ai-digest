"use client";

import { useId, useState } from "react";
import { postTune } from "@/lib/api";
import type { TunedProfile } from "@/lib/types";

type Status = "idle" | "saving" | "done" | "error";

const PLACEHOLDER =
  "less agent-framework drama, more kernel/systems papers, keep the Karpathy takes";

/**
 * "Tune my feed" — a natural-language steering box that POSTs /api/tune and
 * shows a confirmation of what the server adjusted (subfields / mutes / weights).
 */
export function TuneFeed(): JSX.Element {
  const [instruction, setInstruction] = useState("");
  const [status, setStatus] = useState<Status>("idle");
  const [profile, setProfile] = useState<TunedProfile | null>(null);
  const [error, setError] = useState<string>("");
  const fieldId = useId();

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    const text = instruction.trim();
    if (!text) return;
    setStatus("saving");
    setError("");
    try {
      const res = await postTune({ instruction: text });
      setProfile(res.profile);
      setStatus("done");
      setInstruction("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not tune the feed.");
      setStatus("error");
    }
  }

  return (
    <section
      aria-labelledby={`${fieldId}-label`}
      className="rounded-md border border-hairline bg-paper p-4"
    >
      <h2 id={`${fieldId}-label`} className="label-mono mb-1 text-accent">
        Tune my feed
      </h2>
      <p className="mb-3 text-sm leading-body text-muted">
        Steer the feed in plain language. It adjusts your subfields, mutes, and ranking weights.
      </p>

      <form onSubmit={onSubmit} className="flex flex-col gap-2 sm:flex-row">
        <label htmlFor={fieldId} className="sr-only">
          Tune instruction
        </label>
        <input
          id={fieldId}
          type="text"
          value={instruction}
          onChange={(e) => setInstruction(e.target.value)}
          placeholder={PLACEHOLDER}
          className="flex-1 rounded border border-hairline bg-white/40 px-3 py-2 font-mono text-sm text-ink placeholder:text-muted/70 focus:border-accent focus:outline-none"
        />
        <button
          type="submit"
          disabled={status === "saving" || instruction.trim().length === 0}
          className="rounded bg-accent px-4 py-2 font-mono text-xs uppercase tracking-[0.12em] text-paper transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {status === "saving" ? "Tuning…" : "Tune"}
        </button>
      </form>

      <div role="status" aria-live="polite" className="mt-3 text-sm">
        {status === "done" && profile ? <TuneSummary profile={profile} /> : null}
        {status === "error" ? <p className="text-accent">{error}</p> : null}
      </div>
    </section>
  );
}

function TuneSummary({ profile }: { profile: TunedProfile }): JSX.Element {
  const ranking = profile.ranking;
  return (
    <div className="rounded border border-hairline bg-hairline/20 p-3">
      <p className="label-mono mb-2 text-ink/70">Feed updated</p>
      <dl className="space-y-1 text-sm">
        {profile.subfields && profile.subfields.length > 0 ? (
          <Row term="Subfields" value={profile.subfields.join(", ")} />
        ) : null}
        {profile.mutes && profile.mutes.length > 0 ? (
          <Row term="Muted" value={profile.mutes.join(", ")} />
        ) : null}
        {ranking ? (
          <Row
            term="Ranking"
            value={`α ${fmt(ranking.alpha)} · β ${fmt(ranking.beta)} · γ ${fmt(ranking.gamma)}`}
          />
        ) : null}
      </dl>
    </div>
  );
}

function Row({ term, value }: { term: string; value: string }): JSX.Element {
  return (
    <div className="flex gap-2">
      <dt className="label-mono shrink-0 text-muted">{term}</dt>
      <dd className="leading-body text-ink/90">{value}</dd>
    </div>
  );
}

function fmt(n: number | undefined): string {
  return typeof n === "number" ? n.toFixed(2) : "—";
}
