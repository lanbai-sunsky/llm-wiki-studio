---
name: learning-memory-save
description: Use when a learning conversation finishes or the user says to remember, save, record, 写入Wiki, 记下来, 我懂了, 这很重要, or asks Claude Code to preserve new understanding from the dialogue. Extracts durable learning points, examples, questions, mistakes, and user-confirmed insights, then writes them into 已创建的Wiki/ without touching 原始资料/.
---

# Learning Memory Save

Use this skill at the end of a tutoring or study conversation, or whenever the user asks to save what they learned.

## Goal

Turn dialogue into durable Markdown learning memory under `已创建的Wiki/`.

## What To Save

Save only durable material:

- User-confirmed understanding.
- A clearer explanation reached during the conversation.
- Examples, analogies, formulas, workflows, or comparisons that helped the user learn.
- Questions that remain unresolved.
- Mistakes, confusions, or易错点 worth reviewing later.
- Links between concepts that should be preserved.

Do not save transient chat filler, greetings, tool status, or unsupported claims.

## Workflow

1. Confirm the current directory is an LLM Wiki Studio course workspace.
2. Read `index.md` and scan `已创建的Wiki/` to find the best target page.
3. Prefer updating an existing page. Create a new page only when no suitable page exists.
4. Keep `原始资料/` read-only.
5. Write concise Markdown sections with clear headings.
6. Distinguish:
   - `用户已确认`
   - `本轮解释`
   - `例子`
   - `待确认`
   - `复习提示`
7. Update `index.md` when creating a new page.
8. Append one short line to `log.md` describing what was saved.
9. Run the quality self-check below before replying.

## Page Rules

Use `已创建的Wiki/对话记忆/` for conversation-derived notes unless a more specific existing page clearly fits.

Suggested frontmatter:

```yaml
---
title: 页面标题
type: dialogue-memory
status: user-confirmed
sources:
  - conversation
tags:
  - wiki
  - learning-memory
---
```

If a point is not confirmed by the user, mark it as `待确认` instead of presenting it as fact.

## Deterministic Helper

For a stable append-only save, run:

```powershell
python .claude/skills/learning-memory-save/scripts/save_learning_memory.py . --title "主题" --content "要保存的学习内容"
```

If `python` is unavailable on Windows:

```powershell
py -3 .claude/skills/learning-memory-save/scripts/save_learning_memory.py . --title "主题" --content "要保存的学习内容"
```

The helper writes to `已创建的Wiki/对话记忆/主题.md`, updates `index.md`, and appends `log.md`.

## Quality Self-Check

After saving, evaluate the saved Wiki memory with this lightweight checklist. Do not create a separate report file unless the user asks; include the result briefly in your final response.

Use `pass`, `partial`, or `fail` for each item:

- **保存准确率**: The saved content matches what the user actually learned or confirmed in the conversation.
- **可检索性**: The title, target page, and `index.md` entry make the memory easy to find later.
- **去流水化**: The page saves durable conclusions, examples, questions, or mistakes, not chat filler or tool status.
- **合并质量**: Existing related pages were updated when appropriate; duplicate pages were avoided.
- **待确认标记**: Unconfirmed or uncertain content is clearly marked as `待确认`.
- **链接价值**: The saved memory links to relevant existing Wiki pages or has a clear place in the index.

Overall judgment:

```text
Wiki 记忆质量 = 准确保存 + 可检索 + 去流水化 + 可持续更新
```

If any item is `fail`, fix the saved Markdown before responding.

## Exit Criteria

- Say which Wiki file was created or updated.
- Mention whether the saved content is user-confirmed or待确认.
- Include the short quality self-check summary.
- Keep the final response short; the saved Markdown is the source of truth.
