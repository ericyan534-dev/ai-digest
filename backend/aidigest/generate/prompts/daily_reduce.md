# TASK — the day's TL;DR (the reduce step)

You have the per-story takeaways for today. Write ONE line that captures the day.

## Day context

- Date: {date}
- Overall tier (HONOR THIS): {overall_tier}
- Quiet day: {quiet_day}
- Number of stories that cleared the bar: {n_stories}

## The stories (ranked, highest first)

{story_lines}

## Instruction

- If `Quiet day` is True OR overall tier is QUIET_DAY: the TL;DR MUST say so
  plainly — e.g. "Quiet day — nothing major shipped." Do not manufacture a
  headline. If there was no important paper, say so.
- If overall tier is BREAKTHROUGH: lead the TL;DR with that one thing; it is the
  story of the day.
- Otherwise: name the 1–2 threads that actually moved. One sentence. Dense, plain,
  no marketing adjectives.

## Output

Return JSON with key `tldr` — a single line (no markdown, no list).
