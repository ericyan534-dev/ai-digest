import { DigestHeader } from "./DigestHeader";
import { Shortlist } from "./Shortlist";
import { QuietDayNotice } from "./QuietDayNotice";
import { FeedbackButtons } from "./FeedbackButtons";
import { Markdown } from "@/lib/markdown";
import type { WeeklyDigest } from "@/lib/types";

/** Full weekly editorial render, shared by /week and /digest/[id]. */
export function WeeklyView({ digest }: { digest: WeeklyDigest }): JSX.Element {
  return (
    <article className="mx-auto max-w-editorial">
      <DigestHeader
        kicker="Week at a Glance"
        title={digest.title}
        date={digest.week_of}
        tier={digest.overall_tier}
        quiet={digest.quiet_week}
        model={digest.model || undefined}
      />

      {digest.lede ? (
        <p className="mb-8 font-serif text-xl italic leading-body text-ink/90">{digest.lede}</p>
      ) : null}

      {digest.quiet_week ? <QuietDayNotice message={digest.lede} variant="week" /> : null}

      <div className="font-serif text-[1.05rem] leading-body text-ink/90">
        <Markdown source={digest.body_markdown} />
      </div>

      <div className="my-8 flex items-center justify-between border-y border-hairline py-4">
        <p className="label-mono text-muted">Was this week&apos;s read worth it?</p>
        <FeedbackButtons targetId={digest.id} targetKind="digest" label="weekly editorial" />
      </div>

      <div className="space-y-8">
        <Shortlist
          title="What I'd actually read this week"
          entries={digest.shortlist}
          emptyHint="Nothing rose above the noise this week."
        />
        <Shortlist
          title="On my radar — academia preview"
          entries={digest.on_my_radar}
          emptyHint="No academia previews flagged."
        />
      </div>

      {Object.keys(digest.eval_scores).length > 0 ? (
        <section aria-label="Editorial scores" className="mt-10 border-t border-hairline pt-6">
          <h2 className="label-mono mb-3 text-muted">Editorial rubric (judge)</h2>
          <dl className="grid grid-cols-2 gap-2 sm:grid-cols-5">
            {Object.entries(digest.eval_scores).map(([k, v]) => (
              <div key={k} className="rounded border border-hairline px-3 py-2 text-center">
                <dt className="label-mono text-muted">{k}</dt>
                <dd className="font-serif text-lg font-semibold">{Number(v).toFixed(1)}</dd>
              </div>
            ))}
          </dl>
        </section>
      ) : null}
    </article>
  );
}
