from __future__ import annotations

import json
from pathlib import Path

import wiki_engine as engine


PAGES = [
    {
        "title": "向量数据库与词向量",
        "type": "concept",
        "keywords": ["vector", "embedding", "chroma", "向量", "词向量", "数据库"],
        "definition": "向量数据库用于保存文本、图片或其他对象的向量表示，词向量/Embedding 则把语义相近的内容映射到距离更近的向量空间。它们是 RAG 能够按语义检索资料的基础。",
        "example": "把课程文档切分成片段后，为每个片段生成 embedding 并写入 Chroma；用户提问时，将问题也转成向量，再取回最相近的文档片段。",
        "pitfall": "只搭建向量库却不评估召回结果，可能导致模型拿到无关资料，回答看似流畅但事实错误。",
    },
    {
        "title": "基于文档的问答",
        "type": "method",
        "keywords": ["qa", "question", "answer", "文档", "问答", "rag"],
        "definition": "基于文档的问答不是让模型凭记忆回答，而是先检索相关文档，再让模型基于检索片段生成答案，并尽量保留来源依据。",
        "example": "用户问“某节课讲了什么评估方法”，系统检索课程文档中的评估章节，把片段和问题一起交给模型，再生成带引用的回答。",
        "pitfall": "把整个文档直接塞进 Prompt，既浪费上下文窗口，也容易让模型忽略真正相关的段落。",
    },
    {
        "title": "思维链推理",
        "type": "concept",
        "keywords": ["chain", "thought", "reason", "思维链", "推理"],
        "definition": "思维链推理强调让模型分步骤处理复杂任务。实际应用中，更重要的是设计可验证的中间步骤，而不是要求模型暴露冗长推理文本。",
        "example": "把“分析客户反馈”拆成：识别问题类型、提取证据、判断严重程度、给出处理建议。",
        "pitfall": "误以为写上“逐步思考”就一定更好；如果没有检查点和输出约束，模型可能只是生成更长的错误解释。",
    },
    {
        "title": "提示链",
        "type": "method",
        "keywords": ["chain", "prompt", "提示链", "链式"],
        "definition": "提示链把一个复杂任务拆成多个 Prompt 节点，每个节点产出中间结果，后续节点继续加工，从而提高可控性和可调试性。",
        "example": "先让模型抽取文档要点，再让模型按要点生成大纲，最后根据大纲写成复习资料。",
        "pitfall": "链条过长但没有中间结果校验，会把前一步错误逐级放大。",
    },
    {
        "title": "文档分割",
        "type": "method",
        "keywords": ["split", "chunk", "文档分割", "切分"],
        "definition": "文档分割决定 RAG 中每个检索片段的粒度。好的分割应保留语义完整性，同时避免片段过长导致召回不准。",
        "example": "按 Markdown 标题、段落或代码块切分课程资料，并保留章节名、行号等元数据，方便回答时回链到原文。",
        "pitfall": "固定按字符数硬切，可能把一个概念、公式或代码示例切断。",
    },
    {
        "title": "文档加载",
        "type": "method",
        "keywords": ["loader", "load", "document", "文档加载"],
        "definition": "文档加载负责把 PDF、Markdown、网页、CSV 等来源转成统一的 Document 对象，并保留来源路径、标题和元数据。",
        "example": "加载一组 Markdown 课程文件时，每个 Document 应包含 page_content 和 metadata，其中 metadata 记录原始路径与章节。",
        "pitfall": "只提取正文却丢失文件路径和章节信息，后续就无法做来源追踪。",
    },
    {
        "title": "评估",
        "type": "method",
        "keywords": ["eval", "evaluate", "assessment", "评估", "调试"],
        "definition": "评估用于判断 LLM 应用是否稳定可靠，常见维度包括事实正确性、格式遵循、召回质量、延迟、成本和失败案例。",
        "example": "为问答系统准备一组测试问题，检查答案是否引用正确文档、是否遗漏关键事实、是否符合指定格式。",
        "pitfall": "只凭几次人工试用判断效果，缺少可重复测试集，导致上线后问题难以复现。",
    },
]


def boost() -> dict:
    workspace = next(item for item in engine.list_workspaces() if item["name"] == "LLM_CookBook")
    root = Path(workspace["path"])
    sources = engine.read_json(root / engine.METADATA_DIR / "sources.json", [])
    pages = engine.read_json(root / engine.METADATA_DIR / "pages.json", [])
    page_by_title = {page.get("title"): page for page in pages}
    changed = []

    for spec in PAGES:
        refs = refs_for_spec(sources, spec)
        target = root / engine.WIKI_DIR / ("方法" if spec["type"] == "method" else "概念") / f"{engine.safe_name(spec['title'])}.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render_page(spec, refs), encoding="utf-8")
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

    enrich_overview(root, pages)
    engine.write_json(root / engine.METADATA_DIR / "pages.json", pages)
    engine.write_json(root / engine.METADATA_DIR / "graph.json", engine.build_graph(pages, sources))
    issues = engine.lint_workspace(root)
    engine.compute_wiki_quality(root, sources, pages, issues)
    engine.update_index(root, sources, pages)
    return {"changed": changed, "pages": len(pages), "issues": len(issues)}


def refs_for_spec(sources: list[dict], spec: dict) -> list[dict]:
    matched = []
    for source in sources:
        text = " ".join(
            [
                source.get("storedPath", ""),
                source.get("fileName", ""),
                " ".join(chunk.get("text", "")[:1200] for chunk in source.get("chunks", [])[:4]),
            ]
        ).lower()
        if any(keyword.lower() in text for keyword in spec["keywords"]):
            matched.append(source)
    return engine.source_refs_for_sources(matched[:4] or sources[:3], max_refs=10, chunks_per_source=3)


def render_page(spec: dict, refs: list[dict]) -> str:
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
            spec["definition"],
            "",
            "## 最小例子",
            "",
            spec["example"],
            "",
            "## 在课程中的位置",
            "",
            course_position(spec["title"]),
            "",
            "## 常见陷阱",
            "",
            f"- {spec['pitfall']}",
            "- 忽略输入、输出、检查标准和失败处理，会让概念停留在名词层面。",
            "",
            "## 实践检查",
            "",
            "- 能用自己的任务举一个例子。",
            "- 能指出它关联到课程中的哪条路线。",
            "- 能设计一个最小测试来判断它是否有效。",
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


def course_position(title: str) -> str:
    if title in {"文档加载", "文档分割", "向量数据库与词向量", "基于文档的问答"}:
        return "它属于 RAG 应用路线，是把个人资料接入问答系统的关键环节。"
    if title in {"思维链推理", "提示链"}:
        return "它属于提示工程实践路线，用于把复杂任务拆成可控步骤。"
    if title == "评估":
        return "它贯穿所有 LLM 应用开发阶段，用于判断结果是否可靠、可复现、可维护。"
    return "它是 LLM 应用开发中的基础概念，应与具体案例一起学习。"


def enrich_overview(root: Path, pages: list[dict]) -> None:
    overview = next((page for page in pages if page.get("title") == "课程总览"), None)
    if not overview:
        return
    path = root / overview.get("path", "")
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8", errors="ignore")
    if "## 核心概念导航" in text:
        return
    section = "\n".join(
        [
            "## 核心概念导航",
            "",
            "- [[提示原则]]、[[迭代式 Prompt 开发]]、[[思维链推理]]、[[提示链]]：用于掌握提示工程的设计和调试。",
            "- [[文档加载]]、[[文档分割]]、[[向量数据库与词向量]]、[[基于文档的问答]]：用于掌握 RAG 应用的资料处理链路。",
            "- [[工具调用与 Agent]]：用于理解模型如何调用外部工具完成任务。",
            "- [[评估]]、[[LLM 应用评估与调试]]：用于把 demo 变成可测试、可维护的应用。",
        ]
    )
    path.write_text(text.rstrip() + "\n\n" + section + "\n", encoding="utf-8")


if __name__ == "__main__":
    print(json.dumps(boost(), ensure_ascii=False, indent=2))
