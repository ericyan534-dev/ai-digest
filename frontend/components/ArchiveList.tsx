"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { formatShortDate } from "@/lib/format";
import { tierLabel } from "@/lib/families";
import type { DigestKind, DigestSummary } from "@/lib/types";

type KindFilter = "all" | DigestKind;

/**
 * Searchable, filterable archive list. Search matches title + date; the
 * kind toggle filters daily/weekly. All client-side over the fetched rows.
 */
export function ArchiveList({ rows }: { rows: DigestSummary[] }): JSX.Element {
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState<KindFilter>("all");

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return rows.filter((row) => {
      if (kind !== "all" && row.kind !== kind) return false;
      if (!q) return true;
      return (
        row.title.toLowerCase().includes(q) ||
        row.date.toLowerCase().includes(q) ||
        row.id.toLowerCase().includes(q)
      );
    });
  }, [rows, query, kind]);

  return (
    <div>
      <div className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="relative w-full sm:max-w-sm">
          <label htmlFor="archive-search" className="sr-only">
            Search archive
          </label>
          <input
            id="archive-search"
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search by title or date…"
            className="w-full rounded border border-hairline bg-white/40 px-3 py-2 font-mono text-sm text-ink placeholder:text-muted/70 focus:border-accent focus:outline-none"
          />
        </div>

        <div role="group" aria-label="Filter by kind" className="flex items-center gap-1">
          {(["all", "daily", "weekly"] as KindFilter[]).map((k) => (
            <button
              key={k}
              type="button"
              aria-pressed={kind === k}
              onClick={() => setKind(k)}
              className={[
                "label-mono rounded-full border px-2.5 py-1 transition-colors",
                kind === k
                  ? "border-accent bg-accent/10 text-accent"
                  : "border-hairline text-muted hover:border-ink/40 hover:text-ink",
              ].join(" ")}
            >
              {k}
            </button>
          ))}
        </div>
      </div>

      <p className="label-mono mb-3 text-muted" aria-live="polite">
        {filtered.length} {filtered.length === 1 ? "entry" : "entries"}
      </p>

      {filtered.length === 0 ? (
        <p className="my-10 text-center text-sm leading-body text-muted">
          No digests match your search.
        </p>
      ) : (
        <ul className="divide-y divide-hairline border-y border-hairline">
          {filtered.map((row) => (
            <li key={row.id}>
              <Link
                href={`/digest/${encodeURIComponent(row.id)}`}
                className="group flex flex-col gap-2 py-4 sm:flex-row sm:items-baseline sm:gap-4"
              >
                <div className="flex shrink-0 items-center gap-2 sm:w-44">
                  <span className="label-mono text-accent">{row.kind}</span>
                  <time className="label-mono" dateTime={row.date}>
                    {formatShortDate(row.date)}
                  </time>
                </div>
                <div className="flex-1">
                  <p className="font-serif text-base leading-snug group-hover:text-accent">
                    {row.title || "(untitled)"}
                  </p>
                  <div className="mt-1 flex flex-wrap items-center gap-2">
                    <span
                      className={[
                        "label-mono",
                        row.quiet || row.tier === "quiet_day" ? "text-muted" : "text-ink/60",
                      ].join(" ")}
                    >
                      {tierLabel(row.tier)}
                    </span>
                    {row.quiet ? <span className="label-mono text-muted">· quiet</span> : null}
                  </div>
                </div>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
