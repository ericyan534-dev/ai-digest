import { FamilyChip } from "./FamilyChip";
import { hostname } from "@/lib/format";
import type { WeeklyShortlistEntry } from "@/lib/types";

interface ShortlistProps {
  title: string;
  entries: WeeklyShortlistEntry[];
  emptyHint?: string;
}

/** "What I'd actually read this week" / "On my radar" lists for the weekly. */
export function Shortlist({ title, entries, emptyHint }: ShortlistProps): JSX.Element {
  return (
    <section aria-label={title} className="border-t border-hairline pt-6">
      <h2 className="label-mono mb-4 text-accent">{title}</h2>
      {entries.length === 0 ? (
        <p className="text-sm leading-body text-muted">{emptyHint || "Nothing flagged."}</p>
      ) : (
        <ol className="space-y-4">
          {entries.map((entry, i) => (
            <li key={`${entry.title}-${i}`} className="flex gap-3">
              <span className="label-mono mt-1 shrink-0 text-muted">
                {String(i + 1).padStart(2, "0")}
              </span>
              <div>
                <div className="mb-1 flex flex-wrap items-center gap-2">
                  <FamilyChip family={entry.family} />
                </div>
                <p className="font-serif text-base font-semibold leading-snug">
                  {entry.url ? (
                    <a
                      href={entry.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="hover:text-accent"
                    >
                      {entry.title}
                    </a>
                  ) : (
                    entry.title
                  )}
                </p>
                <p className="mt-1 text-sm leading-body text-ink/80">{entry.one_liner}</p>
                {entry.url ? (
                  <a
                    href={entry.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="label-mono mt-1 inline-block text-accent hover:underline"
                  >
                    {hostname(entry.url)} ↗
                  </a>
                ) : null}
              </div>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
