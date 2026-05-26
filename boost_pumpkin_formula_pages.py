from __future__ import annotations

import json
from pathlib import Path

import wiki_engine as engine


def body_has(text: str, heading: str) -> bool:
    return f"## {heading}" in text


def boost() -> dict:
    workspace = next(item for item in engine.list_workspaces() if item["name"] == "pumpkin-book")
    root = Path(workspace["path"])
    pages = engine.read_json(root / engine.METADATA_DIR / "pages.json", [])
    sources = engine.read_json(root / engine.METADATA_DIR / "sources.json", [])
    source_by_id = {source.get("id"): source for source in sources}
    changed = []
    for page in pages:
        title = page.get("title", "")
        if not (title.startswith("第") or title.startswith("chapter") or "公式" in title):
            continue
        path = root / page.get("path", "")
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        additions = []
        if not body_has(text, "前置知识"):
            additions.append(render_prerequisites(title))
        if not body_has(text, "公式拆解方法"):
            additions.append(render_formula_method(title, page, source_by_id))
        if not body_has(text, "易错点"):
            additions.append(render_pitfalls(title))
        if not body_has(text, "配合西瓜书阅读"):
            additions.append(render_xigua_reading(title))
        if additions:
            path.write_text(text.rstrip() + "\n\n" + "\n\n".join(additions).rstrip() + "\n", encoding="utf-8")
            changed.append(title)
    issues = engine.lint_workspace(root)
    engine.compute_wiki_quality(root, sources, pages, issues)
    engine.update_index(root, sources, pages)
    return {"changed": changed, "issues": len(issues), "pages": len(pages)}


def render_prerequisites(title: str) -> str:
    return "\n".join(
        [
            "## 前置知识",
            "",
            "- 先确认西瓜书对应章节的概念语境：公式服务于哪个学习任务、模型假设或优化目标。",
            "- 复习线性代数中的向量、矩阵、内积和范数；涉及概率章节时，先复习条件概率、贝叶斯公式和期望。",
            "- 不建议一开始逐符号死磕，应先知道公式要解决的问题，再看南瓜书如何补齐中间推导。",
        ]
    )


def render_formula_method(title: str, page: dict, source_by_id: dict[str, dict]) -> str:
    refs = page.get("sourceRefs", [])[:4]
    source_lines = []
    for ref in refs:
        source = source_by_id.get(ref.get("sourceId"), {})
        source_lines.append(f"- `{ref.get('storedPath') or source.get('storedPath')}` / {ref.get('section') or '原始文件'}")
    if not source_lines:
        source_lines.append("- 回到本页来源章节，找到对应公式的上下文。")
    return "\n".join(
        [
            "## 公式拆解方法",
            "",
            "1. **定位公式用途**：先问这个公式是在定义概念、转换形式，还是为了得到可优化目标。",
            "2. **拆符号**：把每个变量、下标、求和范围、矩阵维度单独写出来，避免在符号层面迷路。",
            "3. **补中间步**：遇到“显然”“可得”时，优先检查代数恒等变形、概率链式法则或优化条件。",
            "4. **回到模型意义**：推导完成后，用一句话解释它如何帮助理解模型，而不是只得到等式。",
            "",
            "本页应优先核对这些来源章节：",
            "",
            *source_lines,
        ]
    )


def render_pitfalls(title: str) -> str:
    return "\n".join(
        [
            "## 易错点",
            "",
            "- 把符号推导当成目标，忘记公式背后的建模问题。",
            "- 跳过变量定义，导致同一个符号在不同上下文中含义混淆。",
            "- 只看最终结论，不检查约束条件、独立性假设或优化目标是否改变。",
            "- 对长推导没有分块：应按“定义展开、代入、化简、解释”拆成若干小步。",
        ]
    )


def render_xigua_reading(title: str) -> str:
    return "\n".join(
        [
            "## 配合西瓜书阅读",
            "",
            "- 先读西瓜书正文，标出看不懂的公式编号或段落。",
            "- 再回到南瓜书对应章节查中间推导，不要脱离西瓜书原问题单独背推导。",
            "- 最后把推导压缩成一张自己的笔记：公式用途、关键变形、容易忘的条件。",
            "- 如果某一步仍不清楚，应在 Wiki 中新增待确认问题，而不是把整段原文复制进来。",
        ]
    )


if __name__ == "__main__":
    print(json.dumps(boost(), ensure_ascii=False, indent=2))
