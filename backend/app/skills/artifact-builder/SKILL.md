---
name: artifact-builder
description: Create a self-contained Markdown document or HTML/CSS page (one-pager, cheat sheet, checklist, comparison table, playbook, interview guide) from the conversation and transcript sources, rendered in the in-app Artifact Viewer. Use when the user asks to make, create, export or design a document, page, table or visual.
route: artifact
version: 1.1.0
output: artifact
retrieval:
  top_k: 8
  top_k_local: 5
max_output_tokens: 3200
---

# Artifact builder

Produce one polished, self-contained artifact grounded in `<sources>` and the
conversation.

## Output contract (exactly this shape)
```
<artifact type="{{kind}}" title="Short descriptive title">
...the complete artifact...
</artifact>
```
After the closing tag, add a single short sentence for the user describing what
the artifact contains (do not repeat these instructions).

## If type is `html`
- One complete HTML document: `<!DOCTYPE html>`, `<head>` with a single inline
  `<style>`, and `<body>`.
- **No JavaScript, no external fonts, images, stylesheets or iframes.** The
  viewer blocks them; use system font stacks and CSS only.
- Responsive layout: `max-width` container, CSS grid/flex, readable at 360px.
- Clear hierarchy: title, short intro, sections or cards, and a final
  "Sources" list naming guest + episode for each `[S#]` used.
- Accessible: semantic tags (`header`, `main`, `section`, `table`), sufficient
  colour contrast, no text inside images.

## If type is `markdown`
- Use headings, short paragraphs, tables and checklists (`- [ ]`) where they help.
- End with a "Sources" section listing guest + episode for each `[S#]` used.

## Grounding
- Every claim carries an `[S#]` citation. Do not invent facts, metrics or names.
- If `<current_artifact>` is provided, the user wants a revision: return the
  **full updated artifact** (not a diff), keep the title unless asked otherwise.
