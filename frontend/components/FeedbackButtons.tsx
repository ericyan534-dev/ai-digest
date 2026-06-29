"use client";

import { useState } from "react";
import { postFeedback } from "@/lib/api";
import type { FeedbackTargetKind } from "@/lib/types";

interface FeedbackButtonsProps {
  targetId: string;
  targetKind?: FeedbackTargetKind;
  /** Accessible label fragment, e.g. the story title. */
  label?: string;
}

type Vote = "up" | "down" | null;
type Status = "idle" | "saving" | "saved" | "error";

/**
 * Per-story 👍/👎. Optimistic: the active state flips immediately, then POSTs to
 * /api/feedback. On error it reverts and announces via an aria-live region.
 */
export function FeedbackButtons({
  targetId,
  targetKind = "story",
  label,
}: FeedbackButtonsProps): JSX.Element {
  const [vote, setVote] = useState<Vote>(null);
  const [status, setStatus] = useState<Status>("idle");

  async function send(signal: "up" | "down") {
    const previous = vote;
    const next: Vote = vote === signal ? null : signal;
    setVote(next);
    setStatus("saving");

    // Toggling off does not need a new server signal; just reset UI.
    if (next === null) {
      setStatus("idle");
      return;
    }

    try {
      await postFeedback({
        target_id: targetId,
        target_kind: targetKind,
        signal,
        value: signal === "up" ? 1.0 : -1.0,
      });
      setStatus("saved");
    } catch {
      setVote(previous);
      setStatus("error");
    }
  }

  const suffix = label ? `: ${label}` : "";

  return (
    <div className="flex items-center gap-1.5" role="group" aria-label={`Feedback${suffix}`}>
      <button
        type="button"
        onClick={() => send("up")}
        aria-pressed={vote === "up"}
        aria-label={`Thumbs up${suffix}`}
        className={[
          "rounded-full border px-2 py-1 text-sm transition-colors",
          vote === "up"
            ? "border-accent bg-accent/10 text-accent"
            : "border-hairline text-muted hover:border-ink/40 hover:text-ink",
        ].join(" ")}
      >
        <span aria-hidden>👍</span>
      </button>
      <button
        type="button"
        onClick={() => send("down")}
        aria-pressed={vote === "down"}
        aria-label={`Thumbs down${suffix}`}
        className={[
          "rounded-full border px-2 py-1 text-sm transition-colors",
          vote === "down"
            ? "border-ink bg-ink/5 text-ink"
            : "border-hairline text-muted hover:border-ink/40 hover:text-ink",
        ].join(" ")}
      >
        <span aria-hidden>👎</span>
      </button>
      <span className="sr-only" role="status" aria-live="polite">
        {status === "saved"
          ? "Feedback saved"
          : status === "error"
            ? "Could not save feedback"
            : ""}
      </span>
    </div>
  );
}
