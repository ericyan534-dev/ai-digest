# TASK — polish the winning weekly editorial

A judge picked this draft as the best of {n_candidates}. Tighten it without
changing its facts, claims, or structure. Do not add information not present in
the draft.

## Judge rationale (what it liked / flagged)

{judge_rationale}

## The winning draft

{winning_draft}

## Polish instructions

- Tighten prose: cut filler, fuse short sentences only where it improves flow,
  delete any marketing adjective that slipped in.
- Keep the lede strong. Keep the two labeled lists ("What I'd actually read this
  week", "On my radar").
- Preserve the honesty: if the draft called it a quiet week, keep that. Do not
  add importance the draft did not claim.
- Do NOT introduce new facts, numbers, models, or attributions.

## Output

Return JSON with the SAME keys as the draft:
`title`, `lede`, `body_markdown`, `shortlist`, `on_my_radar`
(shortlist / on_my_radar entries = {{ "title", "url", "one_liner", "family" }}).
