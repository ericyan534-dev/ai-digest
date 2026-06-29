import Link from "next/link";
import { FamilyChip } from "./FamilyChip";
import { FeedbackButtons } from "./FeedbackButtons";
import { hostname, formatShortDate } from "@/lib/format";
import { tierLabel } from "@/lib/families";
import type { Story, StorySummary } from "@/lib/types";

interface StoryDetailProps {
  storyId: string;
  story: Story | null;
  summary: StorySummary | null;
}

/**
 * Story detail: prefers the daily StorySummary (takeaway + why-it-matters +
 * links) and augments with the ranked Story metrics when available.
 */
export function StoryDetail({ storyId, story, summary }: StoryDetailProps): JSX.Element {
  const title = summary?.title || story?.title || storyId;
  const family = summary?.family || story?.family;
  const tier = summary?.tier || story?.tier;
  const links = summary?.links ?? [];
  const tags = summary?.tags ?? [];

  return (
    <article className="mx-auto max-w-editorial">
      <div className="mb-4">
        <Link href="/" className="label-mono text-accent hover:underline">
          ← Today
        </Link>
      </div>

      <header className="mb-6 border-b border-hairline pb-5">
        <div className="mb-3 flex flex-wrap items-center gap-2">
          {family ? <FamilyChip family={family} /> : null}
          {tier ? (
            <span
              className={[
                "label-mono rounded-sm px-1.5 py-0.5",
                tier === "breakthrough" ? "bg-accent/10 text-accent" : "text-muted",
              ].join(" ")}
            >
              {tierLabel(tier)}
            </span>
          ) : null}
          {tags.map((t) => (
            <span key={t} className="label-mono text-muted">
              #{t}
            </span>
          ))}
        </div>
        <h1 className="font-serif text-3xl font-bold leading-tight tracking-tight">{title}</h1>
      </header>

      {summary?.takeaway ? (
        <p className="mb-6 font-serif text-lg leading-body text-ink/90">{summary.takeaway}</p>
      ) : (
        <p className="mb-6 leading-body text-muted">
          No takeaway is attached to this story yet — it may be older than the latest daily digest.
        </p>
      )}

      {summary?.why_it_matters ? (
        <div className="mb-8 border-l-2 border-accent pl-4">
          <p className="label-mono mb-1 text-accent">Why you care</p>
          <p className="leading-body text-ink/85">{summary.why_it_matters}</p>
        </div>
      ) : null}

      {story ? (
        <section aria-label="Signals" className="mb-8 border-y border-hairline py-4">
          <h2 className="label-mono mb-3 text-muted">Ranking signals</h2>
          <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Metric term="Importance" value={story.importance} />
            <Metric term="Personal" value={story.personal} />
            <Metric term="Final rank" value={story.final_rank} />
            <Metric term="Mentions" value={story.mention_count} integer />
          </dl>
          <p className="label-mono mt-3 text-muted">
            clustered {formatShortDate(story.created_at)} · {story.item_ids.length} item
            {story.item_ids.length === 1 ? "" : "s"}
          </p>
        </section>
      ) : null}

      {links.length > 0 ? (
        <section aria-label="Sources" className="mb-8">
          <h2 className="label-mono mb-3 text-accent">Sources</h2>
          <ul className="space-y-2">
            {links.map((link) => (
              <li key={link}>
                <a
                  href={link}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="font-serif text-base text-ink hover:text-accent"
                >
                  {hostname(link)} ↗
                </a>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      <div className="flex items-center justify-between border-t border-hairline pt-5">
        <p className="label-mono text-muted">Rate this story</p>
        <FeedbackButtons targetId={storyId} targetKind="story" label={title} />
      </div>
    </article>
  );
}

function Metric({
  term,
  value,
  integer,
}: {
  term: string;
  value: number;
  integer?: boolean;
}): JSX.Element {
  return (
    <div className="rounded border border-hairline px-3 py-2 text-center">
      <dt className="label-mono text-muted">{term}</dt>
      <dd className="font-serif text-lg font-semibold">
        {integer ? value : value.toFixed(2)}
      </dd>
    </div>
  );
}
