from __future__ import annotations

import json
import sys
from pathlib import Path

import wiki_engine as engine
from llm_wiki_agent import build_chat_model, parse_json_object, read_text
from langchain_core.messages import HumanMessage, SystemMessage


TARGETS = {
    "Hello_Agents": [
        "课程总览",
        "学习路线",
        "来源地图",
        "智能体核心范式",
        "项目案例总览",
    ],
    "Happy_LLM": [
        "课程总览",
        "学习路线",
        "来源地图",
        "Transformer 架构",
        "微调实践",
    ],
    "pumpkin-book": [
        "南瓜书项目总览",
        "学习路线与使用指南",
        "来源地图",
        "公式解析学习方法",
        "章节公式索引",
    ],
    "LLM_CookBook": [
        "课程总览",
        "学习路线",
        "来源地图",
        "RAG 应用路线",
        "提示工程实践路线",
    ],
}


def workspace_by_name(name: str) -> dict:
    for workspace in engine.list_workspaces():
        if workspace["name"] == name:
            return workspace
    raise KeyError(name)


def build_context(root: Path) -> dict:
    sources = engine.read_json(root / engine.METADATA_DIR / "sources.json", [])
    pages = engine.read_json(root / engine.METADATA_DIR / "pages.json", [])
    semantic = engine.read_json(root / engine.METADATA_DIR / "semantic_quality.json", {})
    source_summaries = []
    for source in sources[:60]:
        chunks = source.get("chunks", [])
        source_summaries.append(
            {
                "id": source.get("id"),
                "storedPath": source.get("storedPath"),
                "kind": source.get("sourceKind"),
                "sections": [
                    {
                        "chunkId": chunk.get("id"),
                        "section": chunk.get("section"),
                        "excerpt": " ".join(str(chunk.get("text", "")).split())[:600],
                    }
                    for chunk in chunks[:4]
                ],
            }
        )
    page_summaries = []
    for page in pages[:60]:
        path = root / page.get("path", "")
        page_summaries.append(
            {
                "title": page.get("title"),
                "type": page.get("type"),
                "path": page.get("path"),
                "sourceRefs": page.get("sourceRefs", [])[:8],
                "excerpt": " ".join(read_text(path).split())[:800],
            }
        )
    return {"sources": source_summaries, "pages": page_summaries, "semanticQuality": semantic}


def rewrite_course(course: str) -> dict:
    workspace = workspace_by_name(course)
    root = Path(workspace["path"])
    sources = engine.read_json(root / engine.METADATA_DIR / "sources.json", [])
    source_by_id = {source.get("id"): source for source in sources}
    pages = engine.read_json(root / engine.METADATA_DIR / "pages.json", [])
    model = build_chat_model()
    if model is None:
        raise RuntimeError("模型不可用。")
    targets = TARGETS.get(course, ["课程总览", "学习路线", "来源地图"])
    prompt = (
        "请只重写少数核心 Wiki 页面，目标是提升真实学习质量，不要堆模板。"
        "每页必须给出具体知识组织、学习判断、实践验收或案例拆解。"
        "不要复制长段原文，不要让来源地图制造虚高覆盖。"
        "只返回 JSON，不要 Markdown 代码块。格式："
        '{"pages":[{"title":"...","type":"course-overview|learning-path|source-map|theme|case|concept|method",'
        '"sourceIds":["..."],"sourceRefs":[{"sourceId":"...","chunkId":"...","section":"..."}],"markdown":"..."}]}'
        f"\n\n必须重写这些页面：{json.dumps(targets, ensure_ascii=False)}"
        f"\n\n上下文：{json.dumps(build_context(root), ensure_ascii=False, indent=2)[:26000]}"
    )
    response = model.invoke([SystemMessage(content=read_text(root / "AGENTS.md")[:6000]), HumanMessage(content=prompt)])
    plan = parse_json_object(getattr(response, "content", str(response)))
    if not isinstance(plan.get("pages"), list) or not plan["pages"]:
        raise RuntimeError("模型没有返回页面。")

    by_title = {page.get("title"): page for page in pages}
    changed = []
    for item in plan["pages"]:
        title = engine.safe_name(str(item.get("title") or "未命名页面"))
        page_type = engine.normalize_page_type(item.get("type"), title)
        old = by_title.get(title)
        if old:
            target = root / old["path"]
        else:
            target = root / engine.WIKI_DIR / "专题" / f"{title}.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        markdown = normalize_page(item, title, page_type)
        target.write_text(markdown, encoding="utf-8")
        source_ids = [sid for sid in item.get("sourceIds", []) if sid in source_by_id]
        refs = refs_for_item(item, source_by_id, source_ids)
        record = old or {
            "id": engine.uuid.uuid4().hex,
            "title": title,
            "path": str(target.relative_to(root)).replace("\\", "/"),
        }
        record.update(
            {
                "title": title,
                "type": page_type,
                "path": str(target.relative_to(root)).replace("\\", "/"),
                "sourceIds": source_ids or list(dict.fromkeys(ref.get("sourceId") for ref in refs if ref.get("sourceId"))),
                "sourceRefs": refs,
                "updatedAt": engine.now_iso(),
            }
        )
        if not old:
            pages.append(record)
        changed.append(title)

    engine.write_json(root / engine.METADATA_DIR / "pages.json", pages)
    engine.write_json(root / engine.METADATA_DIR / "graph.json", engine.build_graph(pages, sources))
    engine.enhance_wiki_quality(root, target_coverage=1.0)
    issues = engine.lint_workspace(root)
    pages = engine.read_json(root / engine.METADATA_DIR / "pages.json", [])
    engine.compute_wiki_quality(root, sources, pages, issues)
    engine.update_index(root, sources, pages)
    return {"course": course, "changed": changed, "pages": len(pages), "issues": len(issues)}


def normalize_page(item: dict, title: str, page_type: str) -> str:
    markdown = str(item.get("markdown") or "").strip()
    if markdown.startswith("---"):
        return markdown.rstrip() + "\n"
    source_ids = item.get("sourceIds", [])
    frontmatter = [
        "---",
        f"title: {title}",
        f"type: {page_type}",
        "status: source-backed",
        "sources:",
        *(f"  - {source_id}" for source_id in source_ids),
        "tags:",
        "  - wiki",
        "---",
        "",
    ]
    return "\n".join(frontmatter) + markdown.rstrip() + "\n"


def refs_for_item(item: dict, source_by_id: dict, source_ids: list[str]) -> list[dict]:
    requested = item.get("sourceRefs", [])
    refs = []
    chunk_by_id = {
        chunk.get("id"): (source, chunk)
        for source in source_by_id.values()
        for chunk in source.get("chunks", [])
        if chunk.get("id")
    }
    for ref in requested:
        if not isinstance(ref, dict):
            continue
        chunk_id = ref.get("chunkId")
        if chunk_id in chunk_by_id:
            source, chunk = chunk_by_id[chunk_id]
            refs.append(engine.chunk_source_ref(source, chunk))
            continue
        source = source_by_id.get(ref.get("sourceId"))
        if source:
            refs.extend(engine.source_refs_for_sources([source], max_refs=1))
    if refs:
        return refs[:30]
    return engine.source_refs_for_sources([source_by_id[sid] for sid in source_ids if sid in source_by_id], max_refs=18)


def main() -> int:
    course = sys.argv[1] if len(sys.argv) > 1 else "Hello_Agents"
    result = rewrite_course(course)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
