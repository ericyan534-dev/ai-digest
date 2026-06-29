import { getDigest, getLatestDailySummary } from "@/lib/api";
import { isDaily } from "@/lib/types";
import { DailyView } from "@/components/DailyView";
import { EmptyState } from "@/components/EmptyState";

// Always render fresh — the daily digest changes nightly.
export const dynamic = "force-dynamic";

export default async function TodayPage(): Promise<JSX.Element> {
  const summary = await getLatestDailySummary();

  if (!summary) {
    return (
      <EmptyState
        title="No daily digest yet"
        body="Once the nightly pipeline runs, today's takeaways land here — grouped by Academia, Industry, and Community, with an honest quiet-day note when nothing major ships."
        hint="Start the backend, then run: AIDIGEST_LLM_MOCK=1 make daily"
        cta={{ href: "/archive", label: "Browse archive" }}
      />
    );
  }

  let digest;
  try {
    digest = await getDigest(summary.id);
  } catch {
    return (
      <EmptyState
        title="Could not load today's digest"
        body="The backend listed a digest but it could not be fetched. Check that the API is reachable."
        hint="Default API base: http://localhost:8000 (set NEXT_PUBLIC_API_BASE to override)"
      />
    );
  }

  if (!isDaily(digest)) {
    return (
      <EmptyState
        title="No daily digest yet"
        body="The latest entry is not a daily digest."
        cta={{ href: "/archive", label: "Browse archive" }}
      />
    );
  }

  return <DailyView digest={digest} />;
}
