# ai-digest — Content Quality Goals (the bar = smol.ai / AINews)

The digest is judged against these goals. Subagent validators score each digest
on them; a digest is **acceptable only if it passes 1–4 with no FAIL**.

## G1. Source balance (REQUIRED — user explicitly requested)
- Every digest (when data exists) MUST represent **academia + industry + community**.
- Not 100% one family. Academia (arXiv/conferences) and Industry (lab posts /
  products, like smol.ai covers) are first-class — academia is the differentiator.
- Diagnostic baseline (96h window): ingestion yields ~232 academia, ~156 community,
  ~10 meta, ~8 industry — so the data is there; selection must surface it.

## G2. Notability / curation (the smol.ai bar)
- Every item is worth a busy AI researcher's attention: significant papers in the
  reader's subfields, real lab/industry announcements, or **substantive, discussed**
  community threads.
- DROP: low-traction "Show HN" self-promo, random GitHub repos with no discussion,
  trivial tools, personal projects, off-topic posts.
- Good examples (keep): "Where every major LLM stands politically", "Why does
  everyone hate AI (Krugman)", "OpenAI+Broadcom inference chip", "RL without
  ground-truth solutions improves LLMs". Bad (drop): unknown one-off GitHub repos.

## G3. AI-relevance + personalization
- 100% AI/ML. Weighted to the reader's subfields: Multi-Agent Systems, Efficient &
  Scalable NLP, RL for NLP, LLMs & Foundation Models, Optimization.

## G4. Density + voice
- Each item: dense, technical, plain takeaway + a real "why it matters to you" tied
  to a subfield. Karpathy / smol.ai / LeCun / Ng voice. No marketing fluff.

## G5. Honest flexibility (already enforced; keep)
- Quiet day → honest TL;DR + a brief, balanced roundup. Big day → full depth on the
  breakthrough. Never manufacture importance.

## G6. Format
- Clean sections by family (🎓 Academia / 🏭 Industry / 💬 Community / 🗞️ Meta),
  scannable, links present.

## G7. Structure (smol.ai shape) — added 2026-06-26
- **3-tier day**: QUIET (trend recaps only) / NOTABLE (lead the top items) / BREAKTHROUGH
  (full-depth lead on the major release). A real breakthrough day (e.g. Jun 09 Claude
  Fable 5, importance ~0.92) MUST be detected as BREAKTHROUGH and lead with depth.
- **Top Stories lead**: the day's biggest items (breakthrough + notable) lead the
  digest in a single cross-family "⚡ Top Stories" section at the TOP, ordered by
  newsworthiness (a real release outranks a marketing essay) — a breakthrough is
  NEVER buried inside a family section.
- **Trends, not lists**: academia is a RESEARCH-TRENDS paragraph + arXiv links (NOT a
  wall of per-paper entries) unless a paper is itself a breakthrough. Each family
  section below Top Stories = a synthesis trend-recap intro (with a concrete number
  when the sources have one) + brief links for that family's non-lead items.
  Distinct papers must NOT be merged under one fabricated title (cluster by arXiv id).
- **Community is ALWAYS present** (a "pulse" recap) when community items exist — never
  cut entirely.
- Full per-item depth is RESERVED for genuine breakthroughs/notable items.

## Acceptance
PASS requires: G1 (≥2 families incl. ≥1 of academia/industry present when available),
G2 (no low-signal junk in the top items), G3 (no non-AI), G4 (dense, not filler),
G7 (correct day-tier + trend-summary structure; academia not over-long; community present).
