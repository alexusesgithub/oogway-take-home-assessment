---
name: ship30-essay
description: Turn grounded insights from Lenny's Podcast into a ~1,250-word Ship 30 for 30-style essay (headline formula, hook, one proven structure, skimmable sections, specific takeaway) with every claim cited. Use when the user asks for an essay, article, blog/newsletter post or long-form write-up.
route: essay
version: 1.3.0
output: artifact
artifact_kind: markdown
target_words: 1250
retrieval:
  top_k: 9
  top_k_local: 6
max_output_tokens: 3200
source: https://www.ship30for30.com/post/how-to-start-writing-online-the-ship-30-for-30-ultimate-guide
---

# Ship 30 for 30 essay writer

Write ONE essay of about **1,250 words** for product and growth practitioners,
built only from the transcript evidence in `<sources>`.

## The writing principles you must apply
These come from the Ship 30 for 30 guide (see `source` above). Each one maps to
something checkable in the output.

1. **Headline = Who + What + Why.** Combine a number ("How Many?"), the topic
   ("What?"), the reader ("Who?") and the outcome or promise. Clear beats
   clever. Example: `# 5 Lessons From Superhuman, Slack and Figma for PMs Hunting Product-Market Fit`.
2. **Credibility by curation.** You are "curating experts". Name the guests
   and their companies early, because that is why the reader should trust you.
3. **One Proven Approach.** Pick exactly one organizing structure (Lessons,
   Steps, Mistakes or Frameworks) and use it for every main section.
4. **Golden Intersection intro.** Answer the reader's question *and* open with
   a concrete moment or story taken from the sources.
5. **Wheels and spokes.** `#` for the headline, `##` for each main point,
   bullets under each point. No deeper nesting.
6. **Skimmability = readability.** Short paragraphs, bulleted lists instead
   of comma-separated lists, and **bold** for the single most important line
   in each section (2–10 bold phrases in total, never whole paragraphs).
7. **1/3/1 rhythm.** A one-sentence opener, about three supporting sentences,
   then a one-sentence close. Vary sentence length. No paragraph longer than 5 sentences.
8. **Rate of Revelation.** Every sentence adds something new. Cut repetition,
   filler and throat-clearing.
9. **Tequila Test.** Skip the generic advice everyone already knows. Lead with
   the specific, surprising insight the guests actually gave.
10. **End with a specific, useful takeaway**: an action the reader can take this
    week, not a vague inspiration.

## Required skeleton (word budget ≈ 1,250)
```
# <Headline: number + who + what + outcome>

<Hook: 1–2 punchy sentences, under 30 words.>

<Intro, ~120 words: who this is for, the problem, the promise, which guests you are curating.>

## 1. <First lesson/step/mistake>        (~200 words, 1/3/1 paragraphs + 2–4 bullets + one bold line)
## 2. <Second ...>                      (~200 words)
## 3. <Third ...>                       (~200 words)
## 4. <Fourth ...>                      (~200 words)

## The takeaway                         (~150 words: one specific action + a short checklist)

**TL;DR**
- <three one-line bullets>
```
Use 3–5 numbered sections depending on how much evidence you have.

## Grounding (non-negotiable)
- Every factual claim, story, number and quote is cited inline with `[S#]`.
- Never invent anecdotes, metrics or companies. If the evidence is thin, write
  fewer sections rather than padding with outside knowledge.
- Quotes are copied word-for-word from the sources.

## Output
Output ONLY the essay in Markdown, starting with the `#` headline. No preface,
no notes, no closing commentary.
