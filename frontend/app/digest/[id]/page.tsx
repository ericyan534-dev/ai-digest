import Link from "next/link";
import { notFound } from "next/navigation";
import { getDigest, ApiError } from "@/lib/api";
import { isDaily } from "@/lib/types";
import { DailyView } from "@/components/DailyView";
import { WeeklyView } from "@/components/WeeklyView";
import { EmptyState } from "@/components/EmptyState";

export const dynamic = "force-dynamic";

export default async function DigestDetailPage({
  params,
}: {
  params: { id: string };
}): Promise<JSX.Element> {
  let digest;
  try {
    digest = await getDigest(decodeURIComponent(params.id));
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) {
      notFound();
    }
    return (
      <EmptyState
        title="Could not load this digest"
        body="The backend did not return the requested digest."
        cta={{ href: "/archive", label: "Back to archive" }}
      />
    );
  }

  return (
    <div>
      <div className="mb-4">
        <Link href="/archive" className="label-mono text-accent hover:underline">
          ← Archive
        </Link>
      </div>
      {isDaily(digest) ? <DailyView digest={digest} /> : <WeeklyView digest={digest} />}
    </div>
  );
}
