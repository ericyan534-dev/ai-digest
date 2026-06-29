import { getDigest, getLatestDailySummary, listStories } from "@/lib/api";
import { isDaily } from "@/lib/types";
import type { Story, StorySummary } from "@/lib/types";
import { StoryDetail } from "@/components/StoryDetail";

export const dynamic = "force-dynamic";

/**
 * Story detail. The API exposes no single-story endpoint, so we resolve the id
 * against today's ranked stories (GET /api/stories) and the latest daily's
 * StorySummaries (which carry takeaway + why-it-matters + links).
 */
export default async function StoryPage({
  params,
}: {
  params: { id: string };
}): Promise<JSX.Element> {
  const storyId = decodeURIComponent(params.id);

  const [story, summary] = await Promise.all([
    findStory(storyId),
    findSummary(storyId),
  ]);

  return <StoryDetail storyId={storyId} story={story} summary={summary} />;
}

async function findStory(storyId: string): Promise<Story | null> {
  try {
    const stories = await listStories();
    return stories.find((s) => s.id === storyId) ?? null;
  } catch {
    return null;
  }
}

async function findSummary(storyId: string): Promise<StorySummary | null> {
  try {
    const latest = await getLatestDailySummary();
    if (!latest) return null;
    const digest = await getDigest(latest.id);
    if (!isDaily(digest)) return null;
    for (const section of digest.sections) {
      const match = section.summaries.find((s) => s.story_id === storyId);
      if (match) return match;
    }
    return null;
  } catch {
    return null;
  }
}
