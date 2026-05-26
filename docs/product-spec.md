# Product Spec

## Positioning

LLM Wiki Studio is a GUI workbench for creating and maintaining LLM-powered Markdown wikis. The first vertical scenario is course knowledge management: students upload course materials, the system maintains a structured wiki, and review artifacts are generated from that wiki.

## Problem

Claude Code + Obsidian is effective for developers because they are comfortable with:

- file trees
- Markdown conventions
- command-line agent workflows
- prompt design
- manual review of generated changes

Non-developers need the same power wrapped in a visible workflow. They should not need to understand terminal commands or repository structure to benefit from a durable LLM-maintained knowledge base.

## Core User Flow

1. Create a wiki workspace.
2. Upload raw materials.
3. Run `Ingest` to parse sources.
4. Run `Build Wiki` to create or update wiki pages.
5. Review proposed changes and risk warnings.
6. Run `Lint` to find gaps, duplicate concepts, and source issues.
7. Generate review artifacts or export an Obsidian-compatible vault.

## Product Principles

- Markdown files are the durable asset.
- SQLite stores indexes, runs, and UI state, not the primary knowledge body.
- The agent never writes arbitrary paths.
- Model output is structured and validated before writing files.
- Users approve meaningful changes through the GUI.
- Every important claim should link back to a source chunk when possible.

## MVP Scope

Included:

- workspace dashboard
- source upload surface
- wiki page explorer
- agent run timeline
- lint issue queue
- review artifact generation panel
- Obsidian-compatible vault structure

Excluded:

- arbitrary shell execution
- general programming agent features
- real-time multi-user collaboration
- mobile app
- marketplace of agent skills
