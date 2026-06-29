# FEW-SHOT EXEMPLARS

Reference examples for the right length-per-tier and the honest quiet-day
handling. These are illustrative, not to be copied verbatim. They demonstrate the
FLEXIBILITY PRINCIPLE: write to the tier, and say "quiet" when it is quiet.

---

## Exemplar A — QUIET_DAY (daily TL;DR + sections)

Input: top story score 0.31; nothing cleared NOTABLE; a couple of routine
library bumps and a rehash blog post.

Good output:

> **TL;DR:** Quiet day — nothing major shipped. No important paper; a few routine
> releases.
>
> **Community** — vLLM 0.x point release (perf bugfixes); nothing architectural.
> **Academia** — no new paper worth your time today.

Why this is right: it does not promote the point release into a headline, it
states plainly there was no important paper, and it is short. Manufacturing a
"trend" here would be dishonest.

---

## Exemplar B — BREAKTHROUGH (daily takeaway, full depth)

Input tier: BREAKTHROUGH. Story: a model that trains with a new RL objective and
matches a frontier model at a fraction of the post-training compute, with the
recipe released.

Good takeaway (note the depth — background + mechanism + open question):

> The release is a frontier-quality model whose post-training is a single RL pass
> against a learned verifier instead of a large preference-model pipeline. The
> claim is parity on reasoning benchmarks at roughly an order of magnitude less
> post-training compute, and the recipe is open. Background: the field has leaned
> on RLHF + reward models since InstructGPT; this swaps the reward model for a
> programmatic verifier and a tighter objective, which is why the compute drops.
> The open question is whether the verifier generalizes past the benchmarked
> domains or quietly overfits them.
>
> **Why it matters to you:** lands squarely in RL for NLP and Efficient & Scalable
> NLP — a cheaper post-training path changes what a small team can reproduce.

Why this is right: a BREAKTHROUGH gets the background a reader needs, the
mechanism, and the honest caveat — not a one-liner.

---

## Exemplar C — NOTABLE then MINOR (length contrast)

NOTABLE (2–4 sentences):

> A new long-context attention variant reports linear memory and near-parity
> perplexity to full attention out to 256k tokens on the paper's benchmarks. The
> delta vs prior linear-attention work is the gating scheme, which recovers most
> of the quality gap. Worth a read if you care about serving cost.
>
> **Why it matters to you:** Efficient & Scalable NLP — directly relevant to
> long-context serving budgets.

MINOR (one line):

> Hugging Face shipped a CLI quality-of-life update; no model or method change.

Why this is right: the NOTABLE item earns a few sentences and a concrete personal
angle; the MINOR item is one line and is not inflated.
