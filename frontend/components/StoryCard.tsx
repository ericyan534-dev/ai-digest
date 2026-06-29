import Link from "next/link";
import { FamilyChip } from "./FamilyChip";
import { FeedbackButtons } from "./FeedbackButtons";
import { ExpandToggle } from "./ExpandToggle";
import { hostname } from "@/lib/format";
import { isFullDepth, tierLabel } from "@/lib/families";
import type { StorySummary } from "@/lib/types";

/**
 * One story in the daily digest: serif headline, family chip + tier, the
 * takeaway, a mono "WHY YOU CARE" tag with the personal angle, source links,
 * 👍/👎, and an expand affordance for full-depth BREAKTHROUGH items.
 */
export function StoryCard({ summary }: { summary: StorySummary }): JSX.Element {
  const fullDepth = isFullDepth(summary.tier);
  const primaryLink = summary.links[0];

  return (
    <article className="border-b border-hairline py-6 first:pt-0 last:border-b-0">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <FamilyChip family={summary.family} />
        <span
          className={[
            "label-mono rounded-sm px-1.5 py-0.5",
            summary.tier === "breakthrough"
              ? "bg-accent/10 text-accent"
              : "text-muted",
          ].join(" ")}
        >
          {tierLabel(summary.tier)}
        </span>
        {summary.tags.map((tag) => (
          <span key={tag} className="label-mono text-muted">
            #{tag}
          </span>
        ))}
      </div>

      <h3 className="font-serif text-xl font-semibold leading-snug">
        <Link
          href={`/story/${encodeURIComponent(summary.story_id)}`}
          className="hover:text-accent"
        >
          {summary.title}
        </Link>
      </h3>

      <p className="mt-2 leading-body text-ink/90">{summary.takeaway}</p>

      {summary.why_it_matters ? (
        <div className="mt-3 border-l-2 border-accent pl-3">
          <p className="label-mono mb-1 text-accent">Why you care</p>
          <p className="text-sm leading-body text-ink/85">{summary.why_it_matters}</p>
        </div>
      ) : null}

      {fullDepth && summary.takeaway.length > 0 ? (
        <ExpandToggle collapsedLabel="Background & context" expandedLabel="Hide context">
          <div className="mt-2 rounded-md bg-hairline/30 p-3 text-sm leading-body text-ink/85">
            <p>
              This is a <strong>breakthrough</strong>-tier story — covered at full depth so it
              isn&apos;t buried. Follow the links below for primary sources; the takeaway above is
              the signal, the rest is context.
            </p>
            {summary.links.length > 1 ? (
              <ul className="mt-2 space-y-1">
                {summary.links.map((link) => (
                  <li key={link}>
                    <a
                      href={link}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-accent underline decoration-hairline underline-offset-2 hover:decoration-accent"
                    >
                      {hostname(link)}
                    </a>
                  </li>
                ))}
              </ul>
            ) : null}
          </div>
        </ExpandToggle>
      ) : null}

      <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-3">
          {primaryLink ? (
            <a
              href={primaryLink}
              target="_blank"
              rel="noopener noreferrer"
              className="label-mono text-accent hover:underline"
            >
              {hostname(primaryLink)} ↗
            </a>
          ) : null}
          {summary.links.length > 1 ? (
            <span className="label-mono text-muted">+{summary.links.length - 1} more</span>
          ) : null}
        </div>
        <FeedbackButtons targetId={summary.story_id} targetKind="story" label={summary.title} />
      </div>
    </article>
  );
}
