# TASK — extract metadata from a finished weekly editorial

The editorial below is already written and final. Do not rewrite it, do not add
facts, do not change its judgements. Just pull out its metadata.

## Week of

{week_of}

## The editorial

{editorial}

## The week's stories, with their REAL source links

{story_lines}

## What to return

- `title` — the editorial's own headline. If it starts with a `# ` line, use
  that text verbatim. Under 15 words, plain prose.
- `lede` — its opening one or two sentences, copied from the editorial.
- `shortlist` — the picks under "What I'd actually read this week", as entries.
- `on_my_radar` — the entries under "On my radar".

For every entry: `title`, `url`, `one_liner`, `family`
(family ∈ academia|industry|community|meta).

URLS ARE A HARD GATE. Use ONLY a URL that appears verbatim in the story list
above. If a pick has no URL there, leave `url` empty. Never invent, guess, or
adapt a link.

Return JSON with exactly these four keys. Both lists may be empty.
