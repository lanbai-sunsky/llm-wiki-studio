---
name: review-materials
description: Use when the user asks Claude Code to generate 复习资料, exam review notes, high-yield topic lists, 必考/有可能考 lists, or a review quality report for an LLM Wiki Studio course workspace. Primary inputs are user-uploaded focus/annotation documents in 复习资料/重点文档/ or .llm-wiki/metadata/review-focus.json plus 原始资料/ evidence. Wiki is optional supporting context and must never block review generation.
---

# Review Materials

Use this skill inside an LLM Wiki Studio course workspace when the user asks to generate review materials, exam prep notes, high-yield knowledge lists, or must-test / possible-test topic lists.

## Source Priority

Generate review materials from these sources in this order:

1. **User focus / annotation documents**: `复习资料/重点文档/` and `.llm-wiki/metadata/review-focus.json`. Treat them as the user’s exam emphasis and selection signal.
2. **Raw course materials**: `原始资料/` and `.llm-wiki/metadata/sources.json`. Use them as the factual evidence base.
3. **Wiki**: `已创建的Wiki/` and `.llm-wiki/metadata/pages.json`. Use only as optional supporting context or for names the user has already organized.

Do not refuse or delay review generation just because Wiki pages are empty, skeletal, or missing. Wiki is not a required intermediate product for this skill.

## Goal

Turn the user’s focus annotations plus raw course materials into one compact, exam-oriented review document and one quality evaluation report.

## Required Output Contract

Always create exactly one new output folder under `复习资料/`, named like:

```text
复习资料/复习资料-YYYYMMDD-HHMMSS/
```

Inside that folder, create exactly two markdown files:

```text
复习资料.md
评测报告.md
```

Do not create separate outline, Q&A, must-know, exam-focus, summary, or extra markdown files. If older files exist elsewhere, leave them alone unless the user explicitly asks to clean them.

## 复习资料.md Format

The review document must be a plain line list. Each non-empty content line must use exactly this format:

```text
知识点名称~必考
知识点名称~有可能考
```

Rules:

- Use only `必考` or `有可能考` after `~`.
- Do not add bullets, numbering, headings, tables, explanations, answers, paragraphs, or YAML frontmatter.
- Keep the knowledge point name concise but specific enough to review directly.
- Prefer topics explicitly mentioned in focus/annotation documents.
- Expand focus items by checking raw materials for the exact concept, method, comparison, procedure, or exam trap.
- Use Wiki names only when they improve wording; do not require Wiki content.
- Mark an item `必考` when it appears in focus annotations, is repeated or emphasized in raw materials, is prerequisite knowledge, or connects multiple important topics.
- Mark an item `有可能考` when it is useful but secondary, contextual, niche, or evidence is thinner.
- Deduplicate near-identical points and merge overlapping names before writing.

Example:

```text
Transformer 自注意力机制~必考
位置编码的作用~有可能考
RAG 检索增强生成流程~必考
```

## Workflow

1. Confirm the current directory is a course workspace containing `原始资料/` and `复习资料/`.
2. Inspect `复习资料/重点文档/` and `.llm-wiki/metadata/review-focus.json` first. Extract explicit exam emphasis, highlighted concepts, teacher hints, likely question areas, and “重点/必考/易错/掌握” signals.
3. Inspect `原始资料/` and `.llm-wiki/metadata/sources.json` to verify and expand those focus signals into concise knowledge point names.
4. Inspect `已创建的Wiki/` only as optional support. If Wiki is empty, continue normally.
5. Select and normalize knowledge points into `知识点名称~必考/有可能考` lines.
6. Create the timestamped output folder and write only `复习资料.md` and `评测报告.md`.
7. Run the quality evaluation below. If any item is `fail`, revise `复习资料.md` before final reply.

## Deterministic Fallback

For a fast, stable generator, run:

```powershell
python .claude/skills/review-materials/scripts/generate_review_materials.py .
```

If `python` is unavailable on Windows, try:

```powershell
py -3 .claude/skills/review-materials/scripts/generate_review_materials.py .
```

The script reads optional `.llm-wiki/metadata/review-focus.json`, `.llm-wiki/metadata/sources.json`, `.llm-wiki/metadata/pages.json`, plus visible focus files when available, and writes one timestamped folder containing `复习资料.md` and `评测报告.md`.

After running the script, improve `复习资料.md` manually by reading focus documents and the relevant raw material excerpts if metadata is thin.

## 评测报告.md Format

Write a concise markdown report with this structure:

```markdown
# 评测报告

- 复习文档：复习资料.md
- 知识点数量：N
- 必考数量：N
- 有可能考数量：N
- 数据来源：重点文档 + 原始资料 / 重点文档 + 原始资料 + Wiki / 原始资料 / 原始资料 + Wiki

## 指标

| 指标 | 结果 | 说明 |
|---|---|---|
| 重点贴合度 | pass/partial/fail | ... |
| 原始资料忠实度 | pass/partial/fail | ... |
| 覆盖率 | pass/partial/fail | ... |
| 密度 | pass/partial/fail | ... |
| 可考试性 | pass/partial/fail | ... |
| 去重合并 | pass/partial/fail | ... |
| 格式合规 | pass/partial/fail | ... |

## 结论

整体判断：pass/partial/fail
需要修正：...
```

Evaluation rules:

- **重点贴合度**: The output follows the user’s uploaded focus/annotation documents when present.
- **原始资料忠实度**: Important items can be checked against raw course materials or source metadata.
- **覆盖率**: Core focus signals and important source topics are represented.
- **密度**: Each line is short, reviewable, and has no filler.
- **可考试性**: Items are exam-facing and clearly split into `必考` / `有可能考`.
- **去重合并**: Overlapping points are merged and near-duplicates removed.
- **格式合规**: Every review line exactly matches `知识点名称~必考` or `知识点名称~有可能考`.

## Exit Criteria

- List the two files created or updated.
- State the output folder.
- State whether you used focus documents, raw materials, and/or Wiki.
- Never say Wiki must be generated first.
- Do not mention or create legacy multi-file outputs.
