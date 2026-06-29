import { listDigests } from "@/lib/api";
import { ArchiveList } from "@/components/ArchiveList";
import { EmptyState } from "@/components/EmptyState";
import type { DigestSummary } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function ArchivePage(): Promise<JSX.Element> {
  let rows: DigestSummary[] = [];
  let failed = false;
  try {
    rows = await listDigests({ limit: 100 });
  } catch {
    failed = true;
  }

  return (
    <div className="mx-auto max-w-wide">
      <header className="mb-8 border-b border-hairline pb-4">
        <p className="label-mono text-accent">Archive</p>
        <h1 className="mt-2 font-serif text-3xl font-bold tracking-tight">Past digests</h1>
        <p className="mt-2 leading-body text-muted">
          Every daily and weekly, newest first. Search by headline or date.
        </p>
      </header>

      {failed ? (
        <EmptyState
          title="Could not reach the archive"
          body="The backend did not respond. Check that the API is running."
          hint="Default API base: http://localhost:8000"
        />
      ) : rows.length === 0 ? (
        <EmptyState
          title="No digests yet"
          body="Once the pipeline produces a daily or weekly digest, it will appear here."
          hint="Run: AIDIGEST_LLM_MOCK=1 make daily weekly"
          cta={{ href: "/", label: "Go to Today" }}
        />
      ) : (
        <ArchiveList rows={rows} />
      )}
    </div>
  );
}
