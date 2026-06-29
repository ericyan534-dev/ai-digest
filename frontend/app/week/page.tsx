import { getDigest, getLatestWeeklySummary } from "@/lib/api";
import { isWeekly } from "@/lib/types";
import { WeeklyView } from "@/components/WeeklyView";
import { EmptyState } from "@/components/EmptyState";

export const dynamic = "force-dynamic";

export default async function WeekPage(): Promise<JSX.Element> {
  const summary = await getLatestWeeklySummary();

  if (!summary) {
    return (
      <EmptyState
        title="No weekly editorial yet"
        body="The Week at a Glance is a long, NYT-style narrative built from the week's stories. It appears once the weekly pipeline runs."
        hint="Run: AIDIGEST_LLM_MOCK=1 make weekly"
        cta={{ href: "/", label: "Go to Today" }}
      />
    );
  }

  let digest;
  try {
    digest = await getDigest(summary.id);
  } catch {
    return (
      <EmptyState
        title="Could not load the weekly editorial"
        body="The backend listed a weekly digest but it could not be fetched."
      />
    );
  }

  if (!isWeekly(digest)) {
    return <EmptyState title="No weekly editorial yet" cta={{ href: "/", label: "Go to Today" }} />;
  }

  return <WeeklyView digest={digest} />;
}
