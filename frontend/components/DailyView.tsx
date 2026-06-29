"use client";

import { useMemo, useState } from "react";
import { DigestHeader } from "./DigestHeader";
import { StoryCard } from "./StoryCard";
import { FamilyFilter, type FilterState } from "./FamilyFilter";
import { TuneFeed } from "./TuneFeed";
import { QuietDayNotice } from "./QuietDayNotice";
import { FeedbackButtons } from "./FeedbackButtons";
import { familyMeta } from "@/lib/families";
import type { DailyDigest, StorySummary } from "@/lib/types";

function matchesFilter(summary: StorySummary, filter: FilterState): boolean {
  const familyOk = filter.families.size === 0 || filter.families.has(summary.family);
  const subfieldOk =
    filter.subfields.size === 0 ||
    summary.tags.some((tag) => filter.subfields.has(tag));
  return familyOk && subfieldOk;
}

/**
 * Today view: masthead + tune box + filter chips + family-grouped story sections.
 * Filtering is client-side over the already-fetched digest (no refetch).
 */
export function DailyView({ digest }: { digest: DailyDigest }): JSX.Element {
  const [filter, setFilter] = useState<FilterState>({
    families: new Set(),
    subfields: new Set(),
  });

  const availableSubfields = useMemo(() => {
    const tags = new Set<string>();
    for (const section of digest.sections) {
      for (const s of section.summaries) {
        for (const tag of s.tags) tags.add(tag);
      }
    }
    return Array.from(tags);
  }, [digest]);

  const filteredSections = useMemo(
    () =>
      digest.sections
        .map((section) => ({
          ...section,
          summaries: section.summaries.filter((s) => matchesFilter(s, filter)),
        }))
        .filter((section) => section.summaries.length > 0),
    [digest, filter],
  );

  const totalShown = filteredSections.reduce((n, s) => n + s.summaries.length, 0);
  const hasAnyStories = digest.sections.some((s) => s.summaries.length > 0);

  return (
    <div className="mx-auto max-w-editorial">
      <DigestHeader
        kicker="Daily Digest"
        title={digest.tldr}
        date={digest.date}
        tier={digest.overall_tier}
        quiet={digest.quiet_day}
        model={digest.model || undefined}
      />

      <div className="mb-6">
        <TuneFeed />
      </div>

      {hasAnyStories ? (
        <div className="mb-6 border-y border-hairline py-4">
          <FamilyFilter
            state={filter}
            onChange={setFilter}
            availableSubfields={availableSubfields}
          />
        </div>
      ) : null}

      {digest.quiet_day && !hasAnyStories ? (
        <QuietDayNotice message={digest.tldr} variant="day" />
      ) : null}

      {hasAnyStories && totalShown === 0 ? (
        <p className="my-8 text-center text-sm leading-body text-muted">
          No stories match the current filters.{" "}
          <button
            type="button"
            onClick={() => setFilter({ families: new Set(), subfields: new Set() })}
            className="text-accent underline underline-offset-2"
          >
            Clear filters
          </button>
        </p>
      ) : null}

      {filteredSections.map((section) => {
        const meta = familyMeta(section.family);
        return (
          <section key={section.family} aria-label={section.heading} className="mb-10">
            <div className="mb-3 flex items-center justify-between border-b-2 border-ink pb-1">
              <h2 className="font-mono text-sm font-semibold uppercase tracking-[0.14em]">
                <span aria-hidden className="mr-1.5">
                  {meta.emoji}
                </span>
                {section.heading || meta.label}
              </h2>
              <FeedbackButtons
                targetId={`${digest.id}:${section.family}`}
                targetKind="digest_section"
                label={`${meta.label} section`}
              />
            </div>
            <div>
              {section.summaries.map((summary) => (
                <StoryCard key={summary.story_id} summary={summary} />
              ))}
            </div>
          </section>
        );
      })}
    </div>
  );
}
