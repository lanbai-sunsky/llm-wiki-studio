from __future__ import annotations

import json
from pathlib import Path

import wiki_engine as engine
from langchain_core.messages import HumanMessage, SystemMessage
from llm_wiki_agent import build_chat_model, parse_json_object, read_text


TARGETS = ["提示工程（Prompt Engineering）", "基于 ChatGPT API 的问答系统开发", "使用 LangChain 开发 LLM 应用程序", "个人数据访问与 RAG"]


def rewrite() -> dict:
    workspace = next(item for item in engine.list_workspaces() if item["name"] == "LLM_CookBook")
    root = Path(workspace["path"])
    pages = engine.read_json(root / engine.METADATA_DIR / "pages.json", [])
    sources = engine.read_json(root / engine.METADATA_DIR / "sources.json", [])
    model = build_chat_model()
    if model is None:
        raise RuntimeError("模型不可用")
    page_context = []
    for page in pages:
        if page.get("title") in TARGETS or page.get("title") in {"提示原则", "RAG 检索增强生成", "LLM 应用评估与调试"}:
            page_context.append(
                {
                    "title": page.get("title"),
                    "type": page.get("type"),
                    "sourceRefs": page.get("sourceRefs", [])[:10],
                    "body": " ".join(read_text(root / page.get("path", "")).split())[:1600],
                }
            )
    source_context = []
    for source in sources:
        hay = (source.get("storedPath", "") + " " + source.get("fileName", "")).lower()
        if any(key in hay for key in ["c1", "c2", "c3", "c4", "prompt", "rag", "langchain", "qa"]):
            source_context.append(
                {
                    "id": source.get("id"),
                    "storedPath": source.get("storedPath"),
                    "sections": [
                        {
                            "chunkId": chunk.get("id"),
                            "section": chunk.get("section"),
                            "excerpt": " ".join(chunk.get("text", "").split())[:900],
                        }
                        for chunk in source.get("chunks", [])[:5]
                    ],
                }
            )
    prompt = (
        "请重写 LLM_CookBook 的四个核心专题页，使它们能独立承担学习材料，而不是目录。"
        "每页必须包含：核心问题、关键机制、最小示例、实践步骤、常见陷阱、和相关概念链接。"
        "不要复制长段原文，不要写泛泛总结。只返回 JSON："
        '{"pages":[{"title":"...","type":"theme","sourceIds":["..."],"sourceRefs":[{"sourceId":"...","chunkId":"...","section":"..."}],"markdown":"..."}]}'
        f"\n\n目标页：{json.dumps(TARGETS, ensure_ascii=False)}"
        f"\n\n当前页面：{json.dumps(page_context, ensure_ascii=False, indent=2)}"
        f"\n\n来源：{json.dumps(source_context, ensure_ascii=False, indent=2)[:24000]}"
    )
    response = model.invoke([SystemMessage(content=read_text(root / "AGENTS.md")[:6000]), HumanMessage(content=prompt)])
    plan = parse_json_object(getattr(response, "content", str(response)))
    if not isinstance(plan.get("pages"), list):
        raise RuntimeError("模型没有返回页面")
    by_title = {page.get("title"): page for page in pages}
    source_by_id = {source.get("id"): source for source in sources}
    changed = []
    for item in plan["pages"]:
        title = engine.safe_name(item.get("title") or "")
        if title not in by_title:
            continue
        page = by_title[title]
        path = root / page["path"]
        markdown = item.get("markdown", "").strip()
        if not markdown.startswith("---"):
            markdown = "\n".join(
                [
                    "---",
                    f"title: {title}",
                    "type: theme",
                    "status: source-backed",
                    "sources:",
                    *(f"  - {sid}" for sid in item.get("sourceIds", [])),
                    "tags:",
                    "  - wiki",
                    "---",
                    "",
                    markdown,
                ]
            )
        path.write_text(markdown.rstrip() + "\n", encoding="utf-8")
        refs = refs_for_item(item, source_by_id)
        page["sourceRefs"] = refs or page.get("sourceRefs", [])
        page["sourceIds"] = list(dict.fromkeys(ref.get("sourceId") for ref in page["sourceRefs"] if ref.get("sourceId")))
        page["updatedAt"] = engine.now_iso()
        changed.append(title)
    engine.write_json(root / engine.METADATA_DIR / "pages.json", pages)
    engine.write_json(root / engine.METADATA_DIR / "graph.json", engine.build_graph(pages, sources))
    issues = engine.lint_workspace(root)
    engine.compute_wiki_quality(root, sources, pages, issues)
    engine.update_index(root, sources, pages)
    return {"changed": changed, "issues": len(issues), "pages": len(pages)}


def refs_for_item(item: dict, source_by_id: dict) -> list[dict]:
    refs = []
    chunk_by_id = {
        chunk.get("id"): (source, chunk)
        for source in source_by_id.values()
        for chunk in source.get("chunks", [])
        if chunk.get("id")
    }
    for ref in item.get("sourceRefs", []):
        chunk_id = ref.get("chunkId") if isinstance(ref, dict) else ""
        if chunk_id in chunk_by_id:
            source, chunk = chunk_by_id[chunk_id]
            refs.append(engine.chunk_source_ref(source, chunk))
    if refs:
        return refs[:24]
    ids = [sid for sid in item.get("sourceIds", []) if sid in source_by_id]
    return engine.source_refs_for_sources([source_by_id[sid] for sid in ids], max_refs=18)


if __name__ == "__main__":
    print(json.dumps(rewrite(), ensure_ascii=False, indent=2))
