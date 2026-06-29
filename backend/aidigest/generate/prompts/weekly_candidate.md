# TASK — weekly "Week at a Glance" editorial (candidate draft #{candidate_index})

Write a NYT-style narrative editorial covering the week in AI for one expert
reader. This is candidate draft {candidate_index} of {n_candidates}; use the
assigned lead angle below so the drafts differ and a judge can pick the best.

## Lead angle for THIS draft

{lead_angle}

## Week context

- Week of: {week_of}
- Overall tier across the week (HONOR THIS): {overall_tier}
- Quiet week: {quiet_week}
- The reader's subfields: {subfields}
- Primary venues: {venues}

## The week's stories (ranked, highest first; each tagged with its tier)

{story_blocks}

## How to write it

- Open with a strong lede built from the assigned lead angle. Then weave the
  stories into 2–4 themes — synthesis, not a list. Connect threads across
  academia, industry, and community. Name the through-line nobody else named.
- HONOR THE TIERS. A BREAKTHROUGH gets full depth and anchors a theme. NOTABLE
  items support themes. MINOR items are mentioned in passing or dropped. Do not
  inflate.
- HONESTY (hard gate): if it was a quiet week, say so — open by naming it a quiet
  week and keep it short. If no important paper landed, say that. Never
  manufacture importance to fill a column.
- Voice: fast-paced, dense, technical, plain, lightly opinionated, signal over
  fluff, short sentences, NO marketing adjectives. Expert reader.
- End the body with two short labeled lists inside the markdown:
  - **What I'd actually read this week** — 2–5 picks, each one line, that you
    would genuinely open. These should skew to the reader's subfields.
  - **On my radar** — 1–4 academia previews (arXiv/NeurIPS/ACL) worth watching.

## Output

Return JSON with exactly these keys:

- `title` — the editorial headline (no marketing adjectives).
- `lede` — the opening 1–2 sentences (also the strong narrative opening).
- `body_markdown` — the full editorial in markdown, INCLUDING the two labeled
  lists described above.
- `shortlist` — array of {{ "title", "url", "one_liner", "family" }} for "What
  I'd actually read this week" (family ∈ academia|industry|community|meta).
- `on_my_radar` — array of {{ "title", "url", "one_liner", "family" }} academia
  previews (family usually academia).
