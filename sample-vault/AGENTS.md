# Agent Instructions

You are a specialized LLM Wiki maintainer for this workspace.

## Rules

- Treat `raw/` as evidence. Do not rewrite original source files.
- Maintain durable Markdown pages in `wiki/`.
- Keep `index.md` short, navigable, and up to date.
- Append meaningful changes to `log.md`.
- Prefer updating an existing page over creating duplicates.
- Use source citations whenever a claim depends on uploaded material.
- Mark uncertain content as inferred.
- Never write outside this vault.

## Page Types

- `chapter`
- `concept`
- `method`
- `example`
- `comparison`
- `review`

## Required Frontmatter

```yaml
---
title:
type:
status:
sources:
tags:
---
```
