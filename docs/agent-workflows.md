# Agent Workflows

## Ingest

Input:

- uploaded files

Output:

- parsed markdown
- source chunks
- source metadata

Responsibilities:

- preserve source names
- preserve pages or slide numbers when possible
- extract images into a controlled assets directory
- produce chunk ids for citation

## Build Wiki

Input:

- parsed sources
- existing wiki pages
- `AGENTS.md`

Output:

- proposed wiki page writes
- updated index entries
- warnings

Responsibilities:

- create new pages only when needed
- update existing pages instead of duplicating concepts
- cite source chunks
- separate facts from inference

## Update Index

Input:

- current wiki page index
- file tree

Output:

- updated `index.md`

Responsibilities:

- summarize pages
- group pages by type
- keep navigation short and useful

## Lint

Input:

- wiki pages
- source metadata
- links

Output:

- issue list

Checks:

- pages without source citations
- duplicated concepts
- broken links
- orphan pages
- missing index entries
- stale review artifacts

## Generate Review

Input:

- wiki pages
- concepts
- links
- user review goal

Output:

- review outline
- Q&A list
- must-know list

Rule:

Review artifacts must be generated from the wiki layer first. Raw sources are used only as supporting evidence.
