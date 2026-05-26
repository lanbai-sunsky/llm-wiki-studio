from __future__ import annotations

import json
from pathlib import Path

import wiki_engine as engine


CONCEPTS = [
    {
        "title": "提示原则",
        "type": "concept",
        "keywords": ["prompt", "提示", "principle", "原则"],
        "summary": "提示原则用于把模糊任务转化为模型可执行的明确指令，核心是清晰、具体、给足上下文，并通过迭代逐步修正输出。",
        "example": "把“总结这段话”改写为“用三条要点总结下面文本，每条不超过30字，并保留关键数字”。",
        "pitfall": "只写愿望式提示，不说明输出格式、约束条件和判断标准。",
    },
    {
        "title": "迭代式 Prompt 开发",
        "type": "method",
        "keywords": ["iterative", "迭代", "prompt"],
        "summary": "Prompt 开发不是一次写对，而是观察模型输出、定位偏差、补充上下文或约束，再重复测试。",
        "example": "先让模型生成摘要，若过长则增加长度限制；若遗漏重点，则加入必须覆盖的字段。",
        "pitfall": "把一次失败归因于模型能力，而不是拆解提示中的歧义和缺失条件。",
    },
    {
        "title": "Token 与上下文窗口",
        "type": "concept",
        "keywords": ["token", "tokens", "context", "上下文"],
        "summary": "Token 是模型处理文本的基本单位，上下文窗口决定一次调用中能放入多少输入、历史和资料。",
        "example": "做 RAG 时不能把整本资料直接塞进 Prompt，而要检索相关片段，控制上下文长度。",
        "pitfall": "只按字符数估算输入长度，忽略中英文、代码和表格都会影响 token 消耗。",
    },
    {
        "title": "对话格式与角色消息",
        "type": "concept",
        "keywords": ["chat", "message", "system", "assistant", "user"],
        "summary": "对话模型通常通过 system、user、assistant 等角色消息组织输入，不同角色承担不同指令优先级和上下文功能。",
        "example": "system 里写长期行为规则，user 里写本次任务，assistant 历史用于维持多轮上下文。",
        "pitfall": "把长期规则和临时任务混在一个用户消息里，导致后续维护困难。",
    },
    {
        "title": "RAG 检索增强生成",
        "type": "concept",
        "keywords": ["rag", "retrieval", "检索", "向量", "embedding"],
        "summary": "RAG 通过检索外部资料片段补充模型上下文，让回答基于可追溯的私有或课程资料。",
        "example": "用户提问后先检索相关文档片段，再把片段和问题一起交给模型生成答案。",
        "pitfall": "只关注向量数据库搭建，忽略分块策略、召回质量和答案引用。",
    },
    {
        "title": "工具调用与 Agent",
        "type": "concept",
        "keywords": ["agent", "tool", "function", "tools"],
        "summary": "工具调用让模型不只生成文本，还能选择外部函数完成搜索、计算、文件处理或 API 操作。",
        "example": "模型判断需要查天气时调用 weather 工具，再基于工具结果回答用户。",
        "pitfall": "把工具调用当自动化魔法，缺少权限边界、参数校验和失败回退。",
    },
    {
        "title": "LLM 应用评估与调试",
        "type": "method",
        "keywords": ["evaluate", "evaluation", "debug", "评估", "调试"],
        "summary": "评估与调试用于判断 LLM 应用是否稳定，包括任务成功率、事实性、格式遵循、延迟和成本。",
        "example": "为问答系统准备一组标准问题，检查回答是否引用正确资料、是否符合格式要求。",
        "pitfall": "只看几个手工 demo 感觉不错，没有建立可重复的测试集。",
    },
]


def boost() -> dict:
    workspace = next(item for item in engine.list_workspaces() if item["name"] == "LLM_CookBook")
    root = Path(workspace["path"])
    sources = engine.read_json(root / engine.METADATA_DIR / "sources.json", [])
    pages = engine.read_json(root / engine.METADATA_DIR / "pages.json", [])
    page_by_title = {page.get("title"): page for page in pages}
    changed = []
    for spec in CONCEPTS:
        matched = match_sources(sources, spec["keywords"])
        refs = engine.source_refs_for_sources(matched[:4] or sources[:2], max_refs=10, chunks_per_source=3)
        target = root / engine.WIKI_DIR / ("方法" if spec["type"] == "method" else "概念") / f"{engine.safe_name(spec['title'])}.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render_concept(spec, refs), encoding="utf-8")
        record = page_by_title.get(spec["title"])
        if record is None:
            record = {"id": engine.uuid.uuid4().hex, "title": spec["title"]}
            pages.append(record)
        record.update(
            {
                "title": spec["title"],
                "type": spec["type"],
                "path": str(target.relative_to(root)).replace("\\", "/"),
                "sourceIds": list(dict.fromkeys(ref.get("sourceId") for ref in refs if ref.get("sourceId"))),
                "sourceRefs": refs,
                "updatedAt": engine.now_iso(),
            }
        )
        changed.append(spec["title"])
    engine.write_json(root / engine.METADATA_DIR / "pages.json", pages)
    engine.write_json(root / engine.METADATA_DIR / "graph.json", engine.build_graph(pages, sources))
    issues = engine.lint_workspace(root)
    engine.compute_wiki_quality(root, sources, pages, issues)
    engine.update_index(root, sources, pages)
    return {"changed": changed, "pages": len(pages), "issues": len(issues)}


def match_sources(sources: list[dict], keywords: list[str]) -> list[dict]:
    result = []
    for source in sources:
        haystack = " ".join(
            [
                source.get("fileName", ""),
                source.get("storedPath", ""),
                " ".join(chunk.get("text", "")[:1000] for chunk in source.get("chunks", [])[:3]),
            ]
        ).lower()
        if any(keyword.lower() in haystack for keyword in keywords):
            result.append(source)
    return result


def render_concept(spec: dict, refs: list[dict]) -> str:
    return "\n".join(
        [
            "---",
            f"title: {spec['title']}",
            f"type: {spec['type']}",
            "status: source-backed",
            "sources:",
            *(f"  - {ref.get('chunkId') or ref.get('sourceId')}" for ref in refs),
            "tags:",
            "  - wiki",
            "  - llm-cookbook",
            "---",
            "",
            f"# {spec['title']}",
            "",
            "## 定义",
            "",
            spec["summary"],
            "",
            "## 为什么重要",
            "",
            "这个概念决定了 LLM 应用能否从一次性演示走向可维护系统。学习时不要只记住名词，而要能说明它在输入组织、模型调用、结果验证或系统集成中解决了什么问题。",
            "",
            "## 最小例子",
            "",
            spec["example"],
            "",
            "## 常见陷阱",
            "",
            f"- {spec['pitfall']}",
            "- 没有把概念落实到输入、输出、检查标准或失败处理上。",
            "",
            "## 实践检查",
            "",
            "- 能用一个自己的任务改写出对应的 Prompt、检索流程或工具调用流程。",
            "- 能指出本概念在课程中的对应章节，并回到原始资料核对实现细节。",
            "- 能列出至少一个失败案例，并说明如何调试。",
            "",
            "## 来源",
            "",
            *(f"- `{ref.get('storedPath')}` / {ref.get('section') or '原始文件'}" for ref in refs[:6]),
            "",
            "## 相关",
            "",
            "- [[课程总览]]",
            "- [[学习路线]]",
            "- [[提示工程实践路线]]",
            "- [[RAG 应用路线]]",
        ]
    ).rstrip() + "\n"


if __name__ == "__main__":
    print(json.dumps(boost(), ensure_ascii=False, indent=2))
