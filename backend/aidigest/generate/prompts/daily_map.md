# TASK — per-story takeaway (the map step)

Write the takeaway for ONE story for today's digest. Honor its tier exactly.

## Story

- Title: {title}
- Family: {family}
- Tier (HONOR THIS): {tier}
- Cross-source mentions: {mention_count}
- The reader's subfields: {subfields}

## Source material — write ONLY from this; do not invent beyond it

Every number, benchmark, mechanism, and attribution you write MUST appear in the
text below. If the source is only a headline (no body), state ONLY what the
headline itself says and say the details are not yet available — do NOT invent a
result, a number, or a mechanism to fill space.

{sources}

## Tier instruction (active tier = {tier})

{tier_instruction}

## Output

Return JSON with exactly these keys:

- `takeaway` — the summary, written to the active tier. BREAKTHROUGH = full depth
  with background/context (several sentences). NOTABLE = 2–4 sentences. MINOR =
  ONE line. Lead with the RESULT — name the concrete thing: the number/benchmark,
  the method, the capability delta, or the failure mode. No marketing adjectives,
  no vague "streamlines/enables/leverages" filler.
- `why_it_matters` — a SPECIFIC, mechanism-level insight, not a restatement of the
  takeaway. Say what it actually changes for the reader's subfields (Multi-Agent
  Systems; Efficient & Scalable NLP; RL for NLP; LLMs & Foundation Models;
  Optimization): what prior limitation it removes, what it now makes possible, or
  how it compares to the prior approach. BANNED as too generic: "provides a new
  baseline", "is relevant to", "streamlines pipelines", "showcases progress". If
  the real link is weak, say it is tangential in one honest clause rather than
  inflating it.
- `tags` — 1–3 tags chosen from the reader's subfields that ACTUALLY apply. Use the
  subfield names EXACTLY as written above (empty list if none genuinely apply).

Stay in voice: terse, dense, technical, honest — Karpathy / smol.ai.
