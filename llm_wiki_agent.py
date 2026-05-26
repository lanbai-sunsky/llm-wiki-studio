from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any, Literal, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph

import wiki_engine as engine_paths

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional dependency fallback
    load_dotenv = None

if load_dotenv:
    load_dotenv(Path(__file__).resolve().parent / ".env")


class WikiAgentState(TypedDict, total=False):
    root: str
    action: str
    params: dict
    agent_md: str
    index_md: str
    sources: list[dict]
    pages: list[dict]
    issues: list[dict]
    artifacts: list[dict]
    steps: list[dict]
    llm_enabled: bool
    llm_plan: dict
    fallback_reason: str
    repairs: list[dict]
    quality: dict
    semantic_quality: dict


def run_langgraph_agent(root: Path, action: str, params: dict | None = None) -> dict:
    graph = build_graph()
    initial: WikiAgentState = {"root": str(root), "action": action, "params": params or {}, "steps": []}
    result = graph.invoke(initial)
    return {
        "sources": result.get("sources", []),
        "pages": result.get("pages", []),
        "issues": result.get("issues", []),
        "artifacts": result.get("artifacts", []),
        "steps": result.get("steps", []),
        "llmEnabled": result.get("llm_enabled", False),
        "fallbackReason": result.get("fallback_reason", ""),
        "llmPlan": result.get("llm_plan", {}),
        "repairs": result.get("repairs", []),
        "quality": result.get("quality", {}),
        "semanticQuality": result.get("semantic_quality", {}),
    }


def chat_with_wiki_agent(root: Path, message: str) -> dict:
    context = build_chat_context(root)
    model = build_chat_model()
    if model is None:
        return {
            "reply": fallback_chat_reply(context, message),
            "llmEnabled": False,
            "sources": context["sourceFiles"],
            "pages": context["wikiPages"],
        }
    prompt = (
        "你是这个课程的 LLM Wiki Agent。请基于当前知识库状态回答用户问题。\n"
        f"边界：{engine_paths.RAW_DIR} 是事实来源，Wiki 是 Agent 维护的编译层；不要编造不存在的文件或来源。\n"
        "如果用户提出长期有价值的整理请求，可以建议写回 Wiki。\n\n"
        f"知识库状态 JSON：\n{json.dumps(context, ensure_ascii=False, indent=2)[:12000]}\n\n"
        f"用户问题：{message}"
    )
    try:
        response = model.invoke(
            [
                SystemMessage(content=agent_system_prompt(root, context.get("agentMd", ""))),
                HumanMessage(content=prompt),
            ]
        )
        reply = getattr(response, "content", str(response))
        return {
            "reply": reply,
            "llmEnabled": True,
            "sources": context["sourceFiles"],
            "pages": context["wikiPages"],
        }
    except Exception as exc:
        return {
            "reply": fallback_chat_reply(context, message) + f"\n\n模型调用失败，已使用本地回复：{exc}",
            "llmEnabled": False,
            "sources": context["sourceFiles"],
            "pages": context["wikiPages"],
        }


def build_chat_context(root: Path) -> dict:
    sources = read_json(root / engine_paths.METADATA_DIR / "sources.json", [])
    pages = read_json(root / engine_paths.METADATA_DIR / "pages.json", [])
    issues = read_json(root / engine_paths.METADATA_DIR / "lint.json", [])
    artifacts = read_json(root / engine_paths.METADATA_DIR / "review-artifacts.json", [])
    return {
        "agentMd": read_text(root / "AGENTS.md")[:6000],
        "indexMd": read_text(root / "index.md")[:6000],
        "sourceFiles": [
            {
                "id": source.get("id"),
                "fileName": source.get("fileName"),
                "storedPath": source.get("storedPath"),
                "kind": source.get("sourceKind"),
                "chunkCount": source.get("chunkCount", 0),
                "sections": [
                    {
                        "section": chunk.get("section"),
                        "chunkId": chunk.get("id"),
                        "lineStart": chunk.get("lineStart"),
                        "lineEnd": chunk.get("lineEnd"),
                    }
                    for chunk in source.get("chunks", [])[:8]
                ],
            }
            for source in sources
        ],
        "wikiPages": [
            {
                "title": page.get("title"),
                "type": page.get("type"),
                "path": page.get("path"),
                "sources": page.get("sourceRefs") or page.get("sourceIds", []),
            }
            for page in pages
        ],
        "issues": issues,
        "reviewArtifacts": artifacts,
    }


def fallback_chat_reply(context: dict, message: str) -> str:
    source_count = len(context["sourceFiles"])
    page_count = len(context["wikiPages"])
    issue_count = len(context["issues"])
    if page_count == 0:
        return (
            f"当前已经有 {source_count} 份原始资料，但还没有 Wiki 文件。\n"
            f"下一步建议先点击“初始化Wiki”创建骨架，再点击“生成Wiki”，我会读取 {engine_paths.RAW_DIR} 并生成知识正文。"
        )
    page_lines = "\n".join(f"- {page['title']}（{page['type']}）" for page in context["wikiPages"][:8])
    issue_line = f"当前有 {issue_count} 个待处理问题。" if issue_count else "当前没有记录中的待处理问题。"
    return (
        f"当前知识库包含 {source_count} 份原始资料、{page_count} 个 Wiki 文件。\n\n"
        f"主要 Wiki 文件：\n{page_lines}\n\n"
        f"{issue_line}\n\n"
        "你可以继续问我：哪些页面需要确认，或者某个 Wiki 文件来自哪些原始资料章节。"
    )


def build_graph():
    workflow = StateGraph(WikiAgentState)
    workflow.add_node("load_protocol", load_protocol)
    workflow.add_node("prepare_sources", prepare_sources)
    workflow.add_node("plan_with_llm", plan_with_llm)
    workflow.add_node("write_wiki", write_wiki)
    workflow.add_node("repair_wiki", repair_wiki)
    workflow.add_node("lint", lint)
    workflow.add_node("quality", quality)
    workflow.add_node("review", review)
    workflow.add_node("improve", improve)
    workflow.add_node("evaluate", evaluate)

    workflow.add_edge(START, "load_protocol")
    workflow.add_conditional_edges(
        "load_protocol",
        route_after_protocol,
        {"build": "prepare_sources", "lint": "lint", "review": "review", "improve": "improve", "evaluate": "evaluate"},
    )
    workflow.add_edge("prepare_sources", "plan_with_llm")
    workflow.add_edge("plan_with_llm", "write_wiki")
    workflow.add_edge("write_wiki", "repair_wiki")
    workflow.add_edge("repair_wiki", "lint")
    workflow.add_edge("lint", "quality")
    workflow.add_edge("quality", END)
    workflow.add_edge("review", "lint")
    workflow.add_edge("improve", "repair_wiki")
    workflow.add_edge("evaluate", END)
    return workflow.compile()


def route_after_protocol(state: WikiAgentState) -> Literal["build", "lint", "review", "improve", "evaluate"]:
    action = state.get("action", "build")
    if action == "review":
        return "review"
    if action == "improve":
        return "improve"
    if action == "evaluate":
        return "evaluate"
    if action == "lint":
        return "lint"
    return "build"


def load_protocol(state: WikiAgentState) -> WikiAgentState:
    root = Path(state["root"])
    state["agent_md"] = read_text(root / "AGENTS.md")
    state["index_md"] = read_text(root / "index.md")
    state["sources"] = read_json(root / engine_paths.METADATA_DIR / "sources.json", [])
    state["pages"] = read_json(root / engine_paths.METADATA_DIR / "pages.json", [])
    add_step(state, "读取工作协议", "已读取 AGENTS.md、index.md 和资料元数据。")
    return state


def prepare_sources(state: WikiAgentState) -> WikiAgentState:
    import wiki_engine as engine

    root = Path(state["root"])
    sources = engine.prepare_sources_for_wiki(root, state.get("sources", []))
    state["sources"] = sources
    add_step(state, "读取原始资料", f"已从 {engine_paths.RAW_DIR} 读取 {len(sources)} 份原始资料。")
    return state


def plan_with_llm(state: WikiAgentState) -> WikiAgentState:
    params = state.get("params") or {}
    require_llm_agent = state.get("action") == "build" and params.get("requireLlmAgent", True)
    model = build_chat_model()
    if model is None:
        state["llm_enabled"] = False
        state["fallback_reason"] = "未检测到可用模型配置，无法执行真正的 Agent 生成。请检查 API Key、Base URL 和模型名称。"
        add_step(state, "模型规划", state["fallback_reason"])
        if require_llm_agent:
            raise RuntimeError(state["fallback_reason"])
        return state

    state["llm_enabled"] = True
    prompt = build_planning_prompt(state)
    try:
        response = model.invoke(
            [
                SystemMessage(content=agent_system_prompt(Path(state["root"]), state.get("agent_md", ""))),
                HumanMessage(content=prompt),
            ]
        )
        raw = getattr(response, "content", str(response))
        state["llm_plan"] = normalize_wiki_plan(parse_json_plan(raw), state.get("sources", []))
        if not state["llm_plan"].get("pages"):
            raise ValueError("模型没有返回可执行的 Wiki 页面计划。")
        write_json(Path(state["root"]) / engine_paths.METADATA_DIR / "wiki_plan.json", state["llm_plan"])
        add_step(state, "模型规划", "LangChain 模型已基于 AGENTS.md 生成 Wiki 写作计划。")
    except Exception as exc:
        state["llm_enabled"] = False
        state["fallback_reason"] = f"模型 Agent 生成失败：{friendly_model_error(exc)}"
        state["raw_error"] = str(exc)
        state["llm_plan"] = {}
        add_step(state, "模型规划", state["fallback_reason"])
        if require_llm_agent:
            raise RuntimeError(state["fallback_reason"]) from exc
    return state


def repair_wiki(state: WikiAgentState) -> WikiAgentState:
    import wiki_engine as engine

    root = Path(state["root"])
    repairs = engine.repair_workspace(root)
    state["repairs"] = repairs
    if repairs:
        add_step(state, "修复 Wiki", f"已自动修复 {len(repairs)} 个结构问题。")
    else:
        add_step(state, "修复 Wiki", "未发现需要自动修复的结构问题。")
    return state


def friendly_model_error(exc: Exception) -> str:
    text = str(exc)
    lowered = text.lower()
    if "503" in text or "service temporarily unavailable" in lowered:
        return "模型服务暂时不可用，请稍后重试。"
    if "401" in text or "unauthorized" in lowered or "api key" in lowered:
        return "模型鉴权失败，请检查 API Key。"
    if "404" in text or "model" in lowered and "not found" in lowered:
        return "模型名称不可用，请检查 LLM_WIKI_MODEL 或 MODEL_NAME。"
    if "timeout" in lowered or "timed out" in lowered:
        return "模型请求超时，请稍后重试或缩小资料范围。"
    if "rate limit" in lowered or "429" in text:
        return "模型请求过于频繁，请稍后重试。"
    return "模型调用失败，请检查 API 配置或稍后重试。"


def agent_system_prompt(root: Path, agent_md: str, *, limit: int = 8000) -> str:
    overlay = f"""

## 当前执行补充协议：80 分语义质量闭环

- 当前课程：`{root.name}`。
- 原始资料是事实来源，只能读取，不能改写、移动、删除。
- 初始化Wiki只创建骨架、分类索引和文件清单，不读取原始资料正文，不调用模型写知识正文。
- 生成Wiki才读取原始资料并写入知识正文；优化时只能写 Wiki、索引、日志和隐藏元数据。
- 生成或优化 Wiki 后，必须用模型语义评估判断真实质量。80/100 以上才视为可交付。
- 结构覆盖率、来源引用数量、来源地图覆盖全部资料，都不能替代语义质量。
- 如果低于 80 分，先读 `semantic_quality.json` 里的 pageFindings、risks、nextActions，再定向重写少数关键页。
- 优先重写课程总览、学习路线、来源地图和低分核心页；不要批量生成碎片化概念桩或逐文件摘要。
- 不得使用通用模板刷篇幅。每个新增小节都必须和课程领域及原始资料章节直接相关。
- 改写后要再次评估；如果仍未达标，必须诚实记录剩余问题，不得声称已经达标。
"""
    return (agent_md.strip() + overlay).strip()[:limit]


def write_wiki(state: WikiAgentState) -> WikiAgentState:
    import wiki_engine as engine

    root = Path(state["root"])
    sources = state.get("sources", [])
    plan = state.get("llm_plan", {})
    if state.get("llm_enabled") and plan.get("pages"):
        pages = write_llm_pages(root, sources, plan)
    else:
        if (state.get("params") or {}).get("requireLlmAgent", state.get("action") == "build"):
            raise RuntimeError("真正的 Agent 生成没有成功生成 Wiki 计划，已停止写入。")
        pages = engine.build_wiki_pages(root, sources)
    engine.update_index(root, sources, pages)
    state["pages"] = pages
    add_step(state, "写入 Wiki", f"已写入 {len(pages)} 个 Wiki 页面。")
    return state


def lint(state: WikiAgentState) -> WikiAgentState:
    import wiki_engine as engine

    root = Path(state["root"])
    issues = engine.lint_workspace(root, options=state.get("params") or {})
    state["issues"] = issues
    add_step(state, "检查知识库", f"发现 {len(issues)} 个待处理问题。")
    return state


def quality(state: WikiAgentState) -> WikiAgentState:
    import wiki_engine as engine

    root = Path(state["root"])
    pages = engine.list_wiki_pages_by_root(root)
    sources = state.get("sources", [])
    issues = state.get("issues", [])
    quality_report = engine.compute_wiki_quality(root, sources, pages, issues)
    engine.update_index(root, sources, pages)
    state["pages"] = pages
    state["quality"] = quality_report
    add_step(
        state,
        "写入质量报告",
        f"来源覆盖率 {quality_report['sourceCoveragePercent']}%，平均每页 {quality_report['avgRefsPerPage']} 个来源引用。",
    )
    return state


def review(state: WikiAgentState) -> WikiAgentState:
    import wiki_engine as engine

    root = Path(state["root"])
    pages = engine.list_wiki_pages_by_root(root)
    options = state.get("params") or {}
    model = build_chat_model()
    if model is None:
        artifacts = engine.generate_review(root, pages, options=options)
        state["llm_enabled"] = False
        state["fallback_reason"] = "未检测到可用模型配置，已使用本地复习资料生成器。"
        add_step(state, "复习资料规划", state["fallback_reason"])
    else:
        try:
            artifacts = generate_review_with_llm(root, pages, options, model)
            state["llm_enabled"] = True
            add_step(state, "复习资料规划", "已通过模型结合重点文档与原始资料生成考试复习资料，Wiki 仅作可选补充。")
        except Exception as exc:
            artifacts = engine.generate_review(root, pages, options=options)
            state["llm_enabled"] = False
            state["fallback_reason"] = f"模型生成复习资料失败，已使用本地生成器：{exc}"
            add_step(state, "复习资料规划", state["fallback_reason"])
    state["pages"] = pages
    state["artifacts"] = artifacts
    add_step(state, "生成复习资料", f"已写入 {len(artifacts)} 份复习资料。")
    return state


def improve(state: WikiAgentState) -> WikiAgentState:
    root = Path(state["root"])
    model = build_chat_model()
    require_llm_agent = (state.get("params") or {}).get("requireLlmAgent", True)
    if model is None:
        message = "未检测到可用模型配置，无法执行 Wiki 语义优化。请检查 API Key、Base URL 和模型名称。"
        add_step(state, "优化 Wiki", message)
        if require_llm_agent:
            raise RuntimeError(message)
        return state
    try:
        result = improve_wiki_with_llm(root, model, state.get("params") or {})
        state["pages"] = result.get("pages", [])
        state["semantic_quality"] = result.get("semanticQuality", {})
        state["llm_enabled"] = True
        score = (state.get("semantic_quality") or {}).get("overallScore", 0)
        changed = "、".join(result.get("changedTitles", [])[:6]) or "无新增改写页面"
        status = "已达标" if score >= result.get("targetScore", 80) else "仍需继续迭代"
        add_step(state, "优化 Wiki", f"已完成语义评估驱动的定向优化：{status}，当前 {score}/100；改写页面：{changed}。")
    except Exception as exc:
        message = f"Wiki 语义优化失败：{friendly_model_error(exc)}"
        add_step(state, "优化 Wiki", message)
        if require_llm_agent:
            raise RuntimeError(message) from exc
    return state


def improve_wiki_with_llm(root: Path, model, params: dict) -> dict:
    import wiki_engine as engine

    sources = read_json(root / engine_paths.METADATA_DIR / "sources.json", [])
    sources = engine.prepare_sources_for_wiki(root, sources)
    pages = read_json(root / engine_paths.METADATA_DIR / "pages.json", [])
    target_score = int(params.get("targetSemanticScore") or 80)
    max_rounds = max(1, min(int(params.get("maxSemanticImproveRounds") or 2), 3))
    force = bool(params.get("forceSemanticRewrite", False))

    semantic_quality = read_json(root / engine_paths.METADATA_DIR / "semantic_quality.json", {})
    if force or not semantic_quality.get("evaluatedByModel"):
        semantic_quality = evaluate_wiki_semantics(root, model, {**params, "strictMode": True})

    changed_titles: list[str] = []
    rounds: list[dict] = []
    initial_score = semantic_quality.get("overallScore", 0)
    if semantic_quality.get("overallScore", 0) >= target_score and not force:
        pages = engine.list_wiki_pages_by_root(root)
        result = {
            "targetScore": target_score,
            "initialScore": initial_score,
            "finalScore": semantic_quality.get("overallScore", 0),
            "changedTitles": changed_titles,
            "rounds": rounds,
            "semanticQuality": semantic_quality,
            "pages": pages,
            "status": "already-passed",
        }
        write_improvement_record(root, result)
        return result

    for round_index in range(max_rounds):
        context = build_improvement_context(root, sources, pages, semantic_quality, params, changed_titles)
        prompt = build_semantic_improvement_prompt(context, target_score=target_score, round_index=round_index)
        response = model.invoke(
            [
                SystemMessage(content=agent_system_prompt(root, read_text(root / "AGENTS.md"))),
                HumanMessage(content=prompt),
            ]
        )
        plan = parse_json_object(getattr(response, "content", str(response)))
        if not isinstance(plan.get("pages"), list) or not plan["pages"]:
            raise ValueError("模型没有返回可执行的定向优化页面。")

        pages, round_changed = write_improved_pages(root, sources, pages, plan, params)
        changed_titles.extend(title for title in round_changed if title not in changed_titles)
        issues = engine.lint_workspace(root, options=params)
        pages = engine.list_wiki_pages_by_root(root)
        quality_report = engine.compute_wiki_quality(root, sources, pages, issues)
        engine.update_index(root, sources, pages)
        semantic_quality = evaluate_wiki_semantics(root, model, {**params, "strictMode": True})
        rounds.append(
            {
                "round": round_index + 1,
                "changedTitles": round_changed,
                "score": semantic_quality.get("overallScore", 0),
                "quality": {
                    "sourceCoveragePercent": quality_report.get("sourceCoveragePercent"),
                    "issueCount": quality_report.get("issueCount"),
                },
                "notes": normalize_text_list(plan.get("notes", [])),
            }
        )
        if semantic_quality.get("overallScore", 0) >= target_score:
            break

    result = {
        "targetScore": target_score,
        "initialScore": initial_score,
        "finalScore": semantic_quality.get("overallScore", 0),
        "changedTitles": changed_titles,
        "rounds": rounds,
        "semanticQuality": semantic_quality,
        "pages": pages,
        "status": "passed" if semantic_quality.get("overallScore", 0) >= target_score else "needs-more-work",
    }
    write_improvement_record(root, result)
    return result


def build_improvement_context(
    root: Path,
    sources: list[dict],
    pages: list[dict],
    semantic_quality: dict,
    params: dict,
    changed_titles: list[str] | None = None,
) -> dict:
    target_titles = select_semantic_rewrite_targets(root, pages, semantic_quality)
    domain = infer_course_domain(root, sources, pages)
    source_context = []
    for source in sources[:70]:
        chunks = source.get("chunks", [])
        source_context.append(
            {
                "id": source.get("id"),
                "fileName": source.get("fileName"),
                "storedPath": source.get("storedPath"),
                "kind": source.get("sourceKind"),
                "sections": [
                    {
                        "chunkId": chunk.get("id"),
                        "section": chunk.get("section"),
                        "lineStart": chunk.get("lineStart"),
                        "lineEnd": chunk.get("lineEnd"),
                        "excerpt": clean_eval_excerpt(chunk.get("text", ""))[:850],
                    }
                    for chunk in chunks[:5]
                ],
            }
        )
    page_context = []
    target_set = set(target_titles)
    ordered_pages = sorted(
        pages,
        key=lambda page: (
            0 if page.get("title") in target_set else 1,
            0 if page.get("type") in {"course-overview", "learning-path", "source-map"} else 1,
            str(page.get("title") or ""),
        ),
    )
    for page in ordered_pages[:45]:
        path = root / page.get("path", "")
        page_context.append(
            {
                "title": page.get("title"),
                "type": page.get("type"),
                "path": page.get("path"),
                "sourceRefs": page.get("sourceRefs", [])[:8],
                "body": clean_eval_excerpt(strip_frontmatter(read_text(path)))[:1800],
            }
        )
    return {
        "params": params,
        "courseName": root.name,
        "domain": domain,
        "rewriteTargets": target_titles,
        "alreadyChangedTitles": changed_titles or [],
        "semanticQuality": semantic_quality,
        "strategy": semantic_rewrite_strategy(domain),
        "sources": source_context,
        "currentPages": page_context,
    }


def build_semantic_improvement_prompt(context: dict, *, target_score: int, round_index: int) -> str:
    return (
        "你是这个课程的 LLM Wiki 语义改稿 Agent。你的目标不是补模板，也不是制造来源覆盖率，而是把 Wiki 改到真正适合学习、导航和后续维护。\n"
        f"交付门槛：语义质量至少 {target_score}/100。当前是第 {round_index + 1} 轮定向优化。\n"
        "请先根据 semanticQuality.pageFindings、risks 和当前页面内容判断低分原因，然后只重写 rewriteTargets 指定的少数关键页；必要时可以补 1-3 个模块级页面，但不要生成碎片化概念桩。\n\n"
        "硬性要求：\n"
        "- 不得改写、移动、删除原始资料；只能写 Wiki 页面。\n"
        "- 不得用“学习目标/实践检查/典型问题/来源解读”等通用套话刷篇幅；每个小节必须和课程领域、原始资料章节直接相关。\n"
        "- 来源地图只能说明证据关系，不能把它当作语义覆盖的替代品。\n"
        "- 每个页面要有清晰主题、可执行学习顺序、关键判断、真实来源章节和 Obsidian 双链。\n"
        "- 数学/公式课程要讲前置知识、公式含义、推导阅读法和易错点；CookBook/应用课程要重建模块路线和实践链路；Agent 课程要讲角色、工具、记忆、编排和案例流程。\n"
        "- 如果仍无法达到 80 分，请在 notes 里诚实说明还缺哪些原始证据或需要人工判断的地方。\n\n"
        "只返回 JSON，不要 Markdown 代码块。格式：\n"
        "{\n"
        '  "pages": [\n'
        '    {"title": "...", "type": "course-overview|learning-path|theme|case|concept|method|source-map|question|source-attachment", "sourceIds": ["..."], "sourceRefs": [{"sourceId": "...", "chunkId": "...", "section": "..."}], "markdown": "..."}\n'
        "  ],\n"
        '  "notes": ["..."]\n'
        "}\n\n"
        f"上下文 JSON：\n{json.dumps(context, ensure_ascii=False, indent=2)[:32000]}"
    )


def select_semantic_rewrite_targets(root: Path, pages: list[dict], semantic_quality: dict) -> list[str]:
    existing_titles = [str(page.get("title") or "") for page in pages if page.get("title")]
    by_type = {page.get("type"): str(page.get("title") or "") for page in pages if page.get("title")}
    targets: list[str] = []

    for candidate in (by_type.get("course-overview"), "课程总览", "项目总览", f"{root.name} 总览"):
        add_existing_target(targets, candidate, existing_titles)
        if targets:
            break
    for candidate in (by_type.get("learning-path"), "学习路线", "学习路线与使用指南"):
        add_existing_target(targets, candidate, existing_titles)
        if any("学习" in title and ("路线" in title or "指南" in title) for title in targets):
            break
    for candidate in (by_type.get("source-map"), "来源地图", "资料地图", "章节公式索引"):
        add_existing_target(targets, candidate, existing_titles)
        if any("来源" in title or "资料" in title or "索引" in title for title in targets):
            break

    findings = semantic_quality.get("pageFindings") if isinstance(semantic_quality, dict) else []
    if isinstance(findings, list):
        for finding in sorted(findings, key=lambda item: item.get("score", 100) if isinstance(item, dict) else 100):
            if not isinstance(finding, dict):
                continue
            add_existing_target(targets, finding.get("title"), existing_titles)
            if len(targets) >= 8:
                break

    domain = infer_course_domain(root, [], pages)
    for title in domain_default_targets(domain, existing_titles):
        add_existing_target(targets, title, existing_titles)
        if len(targets) >= 8:
            break

    if not targets:
        targets = existing_titles[:5]
    return targets[:8]


def add_existing_target(targets: list[str], candidate: Any, existing_titles: list[str]) -> None:
    title = str(candidate or "").strip()
    if not title:
        return
    if title in existing_titles and title not in targets:
        targets.append(title)
        return
    lowered = title.lower()
    for existing in existing_titles:
        if (lowered and lowered in existing.lower()) or (existing.lower() and existing.lower() in lowered):
            if existing not in targets:
                targets.append(existing)
            return


def infer_course_domain(root: Path, sources: list[dict], pages: list[dict]) -> str:
    haystack_parts = [root.name]
    haystack_parts.extend(str(source.get("fileName") or source.get("storedPath") or "") for source in sources[:50])
    haystack_parts.extend(str(page.get("title") or "") for page in pages[:80])
    haystack = " ".join(haystack_parts).lower()
    if any(keyword in haystack for keyword in ("cookbook", "rag", "prompt", "提示工程", "应用", "实战", "langchain")):
        return "cookbook"
    if any(keyword in haystack for keyword in ("pumpkin", "南瓜书", "公式", "推导", "西瓜书", "机器学习")):
        return "math-formula"
    if any(keyword in haystack for keyword in ("agent", "agents", "mcp", "autogen", "agentscope", "智能体")):
        return "agent"
    if any(keyword in haystack for keyword in ("llm", "transformer", "微调", "大模型", "self_llm", "happy_llm")):
        return "llm-theory"
    return "general"


def domain_default_targets(domain: str, existing_titles: list[str]) -> list[str]:
    keyword_map = {
        "agent": ("智能体", "Agent", "核心范式", "项目案例", "工具", "记忆", "编排"),
        "llm-theory": ("Transformer", "微调", "大模型", "训练", "推理", "部署", "RAG"),
        "math-formula": ("公式", "推导", "前置知识", "易错", "章节", "索引"),
        "cookbook": ("RAG", "提示", "Prompt", "实践", "应用", "评估", "模块"),
        "general": ("核心", "方法", "实践", "案例", "专题"),
    }
    keywords = keyword_map.get(domain, keyword_map["general"])
    matched = []
    for title in existing_titles:
        if any(keyword.lower() in title.lower() for keyword in keywords):
            matched.append(title)
    return matched[:5]


def semantic_rewrite_strategy(domain: str) -> dict:
    strategies = {
        "agent": {
            "focus": ["课程总览", "学习路线", "来源地图", "智能体核心范式", "项目案例总览"],
            "rewriteRules": [
                "解释角色、工具、记忆、规划、反思、多 Agent 协作之间的关系。",
                "案例页必须写清输入、Agent 执行动作、输出和验证方式。",
                "避免只罗列框架名，要沉淀可迁移的智能体设计判断。",
            ],
        },
        "llm-theory": {
            "focus": ["课程总览", "学习路线", "来源地图", "Transformer 架构", "微调实践"],
            "rewriteRules": [
                "把模型结构、训练/微调、推理和应用串成学习路线。",
                "核心概念页要给出用途、边界、常见误解和实践检查点。",
                "理论页和实践页之间要有明确双链。",
            ],
        },
        "math-formula": {
            "focus": ["项目总览", "学习路线与使用指南", "来源地图", "公式解析学习方法", "章节公式索引"],
            "rewriteRules": [
                "为公式密集页面补充前置知识、符号含义、推导阅读法和易错点。",
                "不要把公式截图或章节名堆成摘要，要说明公式解决什么问题。",
                "保留与教材章节的来源关系，便于回到原文复查。",
            ],
        },
        "cookbook": {
            "focus": ["课程总览", "学习路线", "来源地图", "RAG 应用路线", "提示工程实践路线"],
            "rewriteRules": [
                "按任务模块重建 Wiki，而不是给每个短文件建一个碎片页。",
                "每个模块页要包含适用场景、输入输出、操作步骤、验证方式和常见失败点。",
                "优先连接原始资料与可执行实践链路，少写泛泛的概念介绍。",
            ],
        },
        "general": {
            "focus": ["课程总览", "学习路线", "来源地图", "核心主题", "实践路线"],
            "rewriteRules": [
                "围绕学习者的理解路径组织页面。",
                "每页只承担一个稳定主题，并连接来源证据。",
                "避免用通用模板制造看似完整的页面。",
            ],
        },
    }
    return strategies.get(domain, strategies["general"])


def write_improvement_record(root: Path, result: dict) -> None:
    public_result = {key: value for key, value in result.items() if key != "pages"}
    write_json(root / engine_paths.METADATA_DIR / "semantic_improvement.json", public_result)
    lines = [
        "# Wiki 语义优化记录",
        "",
        f"- 目标分：{result.get('targetScore', 80)}/100",
        f"- 初始分：{result.get('initialScore', 0)}/100",
        f"- 当前分：{result.get('finalScore', 0)}/100",
        f"- 状态：{semantic_improvement_status_label(result.get('status', ''))}",
        "",
        "## 改写页面",
        "",
    ]
    changed = result.get("changedTitles") or []
    lines.extend(f"- {title}" for title in changed) if changed else lines.append("- 本次没有新增改写页面。")
    lines.extend(["", "## 迭代记录", ""])
    rounds = result.get("rounds") or []
    if rounds:
        for item in rounds:
            lines.extend(
                [
                    f"### 第 {item.get('round')} 轮：{item.get('score', 0)}/100",
                    "",
                    f"- 改写：{'、'.join(item.get('changedTitles') or []) or '无'}",
                    f"- 结构覆盖：{(item.get('quality') or {}).get('sourceCoveragePercent', '-')}%",
                    f"- 结构问题：{(item.get('quality') or {}).get('issueCount', '-')}",
                    "",
                ]
            )
            notes = item.get("notes") or []
            if notes:
                lines.extend(f"- {note}" for note in notes)
                lines.append("")
    else:
        lines.append("- Wiki 已达到目标分，未触发改写。")
    (root / "Wiki 语义优化记录.md").write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def semantic_improvement_status_label(status: str) -> str:
    return {
        "already-passed": "已达标，未触发改写",
        "passed": "已达标",
        "needs-more-work": "仍需继续迭代",
    }.get(status, status or "未知")


def write_improved_pages(root: Path, sources: list[dict], old_pages: list[dict], plan: dict, params: dict) -> tuple[list[dict], list[str]]:
    import wiki_engine as engine

    source_by_id = {source.get("id"): source for source in sources}
    pages: list[dict] = [dict(page) for page in old_pages]
    by_title = {page.get("title"): page for page in pages}
    changed_titles: list[str] = []
    for page in plan.get("pages", []):
        if not isinstance(page, dict):
            continue
        title = engine.safe_name(str(page.get("title") or "未命名页面"))
        page_type = engine.normalize_page_type(str(page.get("type") or ""), title)
        existing = by_title.get(title)
        target = root / existing.get("path", "") if existing and existing.get("path") else root / engine.WIKI_DIR / page_directory_for_type(page_type) / f"{title}.md"
        markdown = normalize_improved_markdown(page, title, page_type)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(markdown, encoding="utf-8")
        source_ids = [source_id for source_id in page.get("sourceIds", []) if source_id in source_by_id]
        refs = source_refs_for_page(source_by_id, page, source_ids)
        if not source_ids:
            source_ids = list(dict.fromkeys(ref.get("sourceId") for ref in refs if ref.get("sourceId")))
        record = existing or {
            "id": uuid.uuid4().hex,
            "title": title,
        }
        record.update(
            {
                "title": title,
                "type": page_type,
                "path": str(target.relative_to(root)).replace("\\", "/"),
                "sourceIds": source_ids,
                "sourceRefs": refs,
                "updatedAt": engine.now_iso(),
            }
        )
        if existing is None:
            pages.append(record)
            by_title[title] = record
        changed_titles.append(title)
    if params.get("createSourceBriefs", False):
        pages = add_source_brief_pages(root, sources, pages)
    engine.write_json(root / engine.METADATA_DIR / "pages.json", pages)
    engine.write_json(root / engine.METADATA_DIR / "graph.json", engine.build_graph(pages, sources))
    engine.enhance_wiki_quality(root, target_coverage=1.0)
    return engine.read_json(root / engine.METADATA_DIR / "pages.json", pages), changed_titles


def add_source_brief_pages(root: Path, sources: list[dict], pages: list[dict]) -> list[dict]:
    import wiki_engine as engine

    used = {ref.get("sourceId") for page in pages for ref in page.get("sourceRefs", []) if ref.get("sourceId")}
    for source in sources:
        if source.get("id") in used:
            continue
        title = source_brief_title(source)
        target = root / engine.WIKI_DIR / "资料研读" / f"{engine.safe_name(title)}.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        refs = engine.source_refs_for_sources([source], max_refs=5, chunks_per_source=5)
        target.write_text(render_source_brief_page(title, source, refs), encoding="utf-8")
        pages.append(
            {
                "id": uuid.uuid4().hex,
                "title": title,
                "type": "source-attachment" if source.get("sourceKind") in {"image", "pdf", "audio", "video", "canvas-base"} else "theme",
                "path": str(target.relative_to(root)).replace("\\", "/"),
                "sourceIds": [source.get("id")],
                "sourceRefs": refs,
                "updatedAt": engine.now_iso(),
            }
        )
    return pages


def source_brief_title(source: dict) -> str:
    stem = Path(source.get("relativePath") or source.get("fileName") or source.get("storedPath") or "资料").stem
    parent = Path(source.get("relativePath") or source.get("storedPath") or "").parent.name
    if parent and parent not in {".", engine_paths.RAW_DIR}:
        return f"{parent} - {stem}"
    return f"资料研读 - {stem}"


def render_source_brief_page(title: str, source: dict, refs: list[dict]) -> str:
    chunks = source.get("chunks", [])
    lines = [
        "---",
        f"title: {title}",
        "type: theme",
        "status: source-backed",
        "sources:",
        *(f"  - {ref.get('chunkId') or ref.get('sourceId')}" for ref in refs),
        "tags:",
        "  - wiki",
        "  - source-brief",
        "---",
        "",
        f"# {title}",
        "",
        "## 摘要",
        "",
        summarize_source_for_brief(source),
        "",
        "## 学习要点",
        "",
    ]
    for chunk in chunks[:5]:
        section = chunk.get("section") or "原始文件"
        excerpt = clean_eval_excerpt(chunk.get("text", ""))[:180]
        lines.append(f"- **{section}**：{excerpt or '该章节保留为原始资料证据，后续可继续细化。'}")
    lines.extend(
        [
            "",
            "## 来源",
            "",
            f"- `{source.get('storedPath')}`",
            "",
            "## 相关",
            "",
            "- [[课程总览]]",
            "- [[来源地图]]",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def summarize_source_for_brief(source: dict) -> str:
    chunks = source.get("chunks", [])
    text = clean_eval_excerpt(" ".join(chunk.get("text", "") for chunk in chunks[:3]))
    if text:
        return text[:420]
    kind = source.get("sourceKind", "资料")
    return f"该文件是 `{kind}` 类型原始资料，已保留在 `{source.get('storedPath')}`，可作为后续 Wiki 细化的证据。"


def page_directory_for_type(page_type: str) -> str:
    return {
        "course-overview": "课程",
        "learning-path": "课程",
        "source-map": "课程",
        "method": "方法",
        "case": "案例",
        "concept": "概念",
        "question": "问题",
        "source-attachment": "附件",
    }.get(page_type, "专题")


def normalize_improved_markdown(page: dict, title: str, page_type: str) -> str:
    markdown = str(page.get("markdown") or "").strip()
    if markdown.startswith("---"):
        return markdown.rstrip() + "\n"
    source_ids = page.get("sourceIds") or []
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
    body = markdown or f"# {title}\n\n## 摘要\n\n该页面由优化 Agent 创建，后续可继续扩展。"
    return "\n".join(frontmatter) + body.rstrip() + "\n"


def evaluate(state: WikiAgentState) -> WikiAgentState:
    root = Path(state["root"])
    model = build_chat_model()
    require_llm_agent = (state.get("params") or {}).get("requireLlmAgent", True)
    if model is None:
        message = "未检测到可用模型配置，无法执行语义质量评估。请检查 API Key、Base URL 和模型名称。"
        add_step(state, "语义评估", message)
        if require_llm_agent:
            raise RuntimeError(message)
        state["semantic_quality"] = {}
        return state
    try:
        report = evaluate_wiki_semantics(root, model, state.get("params") or {})
        state["semantic_quality"] = report
        state["llm_enabled"] = True
        add_step(state, "语义评估", f"模型已完成语义评估：{report.get('overallScore', 0)}/100。")
    except Exception as exc:
        message = f"语义质量评估失败：{friendly_model_error(exc)}"
        add_step(state, "语义评估", message)
        if require_llm_agent:
            raise RuntimeError(message) from exc
        state["semantic_quality"] = {}
    return state


def evaluate_wiki_semantics(root: Path, model, params: dict) -> dict:
    context = build_semantic_eval_context(root, params)
    prompt = (
        "你是一个严格的 LLM Wiki 质量评测者。请评估当前课程 Wiki 是否真的适合学习和长期维护，而不只是来源引用数量好看。\n"
        "请按 0-100 分打分，80 分以上才算可交付。评分维度：\n"
        "1. factualGrounding：结论是否被原始资料章节支撑。\n"
        "2. sourceCoverageDepth：是否覆盖关键资料，而不是只把来源堆到来源地图。\n"
        "3. wikiStructure：页面是否主题清晰、链接和入口可导航。\n"
        "4. learningUsefulness：对非开发者/学习者是否有实际帮助。\n"
        "5. maintainability：是否便于后续增量更新、冲突标记和复查。\n"
        "请特别惩罚：空泛总结、只列目录不解释、来源地图虚高覆盖、页面与来源不匹配、过度机械追加的章节依据。\n"
        "只返回 JSON，不要 Markdown 代码块。格式：\n"
        "{\n"
        '  "overallScore": 0,\n'
        '  "grade": "优秀|良好|可用但需迭代|需要明显改进",\n'
        '  "dimensions": {\n'
        '    "factualGrounding": {"score": 0, "comment": "..."},\n'
        '    "sourceCoverageDepth": {"score": 0, "comment": "..."},\n'
        '    "wikiStructure": {"score": 0, "comment": "..."},\n'
        '    "learningUsefulness": {"score": 0, "comment": "..."},\n'
        '    "maintainability": {"score": 0, "comment": "..."}\n'
        "  },\n"
        '  "pageFindings": [{"title": "...", "score": 0, "issue": "...", "suggestion": "..."}],\n'
        '  "strengths": ["..."],\n'
        '  "risks": ["..."],\n'
        '  "nextActions": ["..."]\n'
        "}\n\n"
        f"评估上下文 JSON：\n{json.dumps(context, ensure_ascii=False, indent=2)[:26000]}"
    )
    response = model.invoke(
        [
            SystemMessage(content=agent_system_prompt(root, read_text(root / "AGENTS.md"))),
            HumanMessage(content=prompt),
        ]
    )
    raw_report = parse_json_object(getattr(response, "content", str(response)))
    if not raw_report:
        raise ValueError("模型没有返回可解析的语义评估 JSON。")
    report = normalize_semantic_quality_report(raw_report, context)
    write_json(root / engine_paths.METADATA_DIR / "semantic_quality.json", report)
    write_semantic_quality_markdown(root, report)
    return report


def build_semantic_eval_context(root: Path, params: dict) -> dict:
    sources = read_json(root / engine_paths.METADATA_DIR / "sources.json", [])
    pages = read_json(root / engine_paths.METADATA_DIR / "pages.json", [])
    quality = read_json(root / engine_paths.METADATA_DIR / "quality.json", {})
    semantic_pages = []
    for page in pages[:40]:
        path = root / page.get("path", "")
        text = read_text(path)
        refs = page.get("sourceRefs", [])
        evidence = []
        for ref in refs[:6]:
            source = next((item for item in sources if item.get("id") == ref.get("sourceId")), {})
            chunk = next((item for item in source.get("chunks", []) if item.get("id") == ref.get("chunkId")), {})
            evidence.append(
                {
                    "storedPath": ref.get("storedPath") or source.get("storedPath"),
                    "section": ref.get("section"),
                    "lineStart": ref.get("lineStart"),
                    "lineEnd": ref.get("lineEnd"),
                    "excerpt": clean_eval_excerpt(chunk.get("text", ""))[:500],
                }
            )
        semantic_pages.append(
            {
                "title": page.get("title"),
                "type": page.get("type"),
                "path": page.get("path"),
                "sourceRefCount": len(refs),
                "bodyExcerpt": clean_eval_excerpt(strip_frontmatter(read_text(path)))[:2200],
                "evidence": evidence,
            }
        )
    return {
        "courseName": root.name,
        "strictMode": bool(params.get("strictMode", True)),
        "includeSourceEvidence": bool(params.get("includeSourceEvidence", True)),
        "structuralQuality": quality,
        "sourceFiles": [
            {
                "id": source.get("id"),
                "storedPath": source.get("storedPath"),
                "kind": source.get("sourceKind"),
                "chunkCount": source.get("chunkCount", 0),
                "sections": [section.get("section") for section in (source.get("sections") or [])[:8]],
            }
            for source in sources[:80]
        ],
        "wikiPages": semantic_pages,
    }


def normalize_semantic_quality_report(report: dict, context: dict) -> dict:
    dimensions = report.get("dimensions") if isinstance(report.get("dimensions"), dict) else {}
    normalized_dimensions = {}
    for key, label in (
        ("factualGrounding", "事实支撑"),
        ("sourceCoverageDepth", "覆盖深度"),
        ("wikiStructure", "Wiki 结构"),
        ("learningUsefulness", "学习可用性"),
        ("maintainability", "可维护性"),
    ):
        item = dimensions.get(key, {})
        if not isinstance(item, dict):
            item = {}
        normalized_dimensions[key] = {
            "label": label,
            "score": clamp_score(item.get("score", 0)),
            "comment": str(item.get("comment") or "模型未给出说明。")[:500],
        }
    dimension_scores = [item["score"] for item in normalized_dimensions.values()]
    overall = clamp_score(report.get("overallScore", round(sum(dimension_scores) / len(dimension_scores)) if dimension_scores else 0))
    return {
        "overallScore": overall,
        "grade": str(report.get("grade") or semantic_grade(overall)),
        "dimensions": normalized_dimensions,
        "pageFindings": normalize_page_findings(report.get("pageFindings", []), context),
        "strengths": normalize_text_list(report.get("strengths", [])),
        "risks": normalize_text_list(report.get("risks", [])),
        "nextActions": normalize_text_list(report.get("nextActions", [])),
        "evaluatedByModel": True,
        "updatedAt": engine_paths.now_iso(),
    }


def normalize_page_findings(items: Any, context: dict) -> list[dict]:
    if not isinstance(items, list):
        return []
    known_titles = {page.get("title") for page in context.get("wikiPages", [])}
    findings = []
    for item in items[:30]:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "")
        findings.append(
            {
                "title": title if title in known_titles or title else "未命名页面",
                "score": clamp_score(item.get("score", 0)),
                "issue": str(item.get("issue") or "未指出明显问题。")[:500],
                "suggestion": str(item.get("suggestion") or "保持后续迭代。")[:500],
            }
        )
    return findings


def normalize_text_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item)[:500] for item in value[:12] if str(item).strip()]


def clamp_score(value: Any) -> int:
    try:
        score = int(round(float(value)))
    except (TypeError, ValueError):
        return 0
    return max(0, min(100, score))


def semantic_grade(score: int) -> str:
    if score >= 85:
        return "优秀"
    if score >= 80:
        return "良好"
    if score >= 65:
        return "可用但需迭代"
    return "需要明显改进"


def write_semantic_quality_markdown(root: Path, report: dict) -> None:
    lines = [
        "# Wiki 语义质量评估",
        "",
        f"- 总分：{report.get('overallScore', 0)}/100",
        f"- 等级：{report.get('grade', '-')}",
        f"- 评估时间：{report.get('updatedAt', '')}",
        f"- 模型评估：{'是' if report.get('evaluatedByModel') else '否'}",
        "",
        "## 维度评分",
        "",
    ]
    for item in report.get("dimensions", {}).values():
        lines.extend([f"### {item.get('label')}：{item.get('score')}/100", "", item.get("comment", ""), ""])
    for section, title in (("strengths", "优点"), ("risks", "风险"), ("nextActions", "建议动作")):
        lines.extend([f"## {title}", ""])
        values = report.get(section) or []
        lines.extend(f"- {item}" for item in values) if values else lines.append("- 暂无。")
        lines.append("")
    lines.extend(["## 页面发现", ""])
    findings = report.get("pageFindings") or []
    if findings:
        for item in findings:
            lines.extend(
                [
                    f"### {item.get('title')}：{item.get('score')}/100",
                    "",
                    f"- 问题：{item.get('issue')}",
                    f"- 建议：{item.get('suggestion')}",
                    "",
                ]
            )
    else:
        lines.append("- 暂无逐页发现。")
    (root / "Wiki 语义质量评估.md").write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def clean_eval_excerpt(text: str) -> str:
    return " ".join(str(text or "").replace("\r\n", "\n").replace("\r", "\n").split())


def strip_frontmatter(text: str) -> str:
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            return parts[2].strip()
    return text.strip()


def generate_review_with_llm(root: Path, pages: list[dict], options: dict, model) -> list[dict]:
    import wiki_engine as engine

    focus_docs = read_json(root / engine_paths.METADATA_DIR / "review-focus.json", [])
    context = build_review_context(root, pages, focus_docs, options)
    prompt = (
        "请根据用户上传的重点标注文件和原始资料生成考试复习资料。\n"
        "重点文档是最高优先级：如果重点文档是图片、PDF、音频或视频，请把它作为多模态重点依据；当前上下文会提供文件名、类型、路径和可得摘要。\n"
        "原始资料是事实来源：必须用原始资料或来源元数据核对、扩展重点标注中的知识点。\n"
        "Wiki 只是可选补充：如果 Wiki 为空、只有骨架或没有正文，也必须继续生成，不能要求先生成 Wiki。\n"
        "考试目标规则：examTarget=pass 表示覆盖 80% 重点知识；examTarget=high 表示覆盖 100% 知识。\n"
        "最终只允许生成一个复习文档和一个评测报告。复习文档中的每一行必须严格是 `知识点名称~必考` 或 `知识点名称~有可能考`，不要大纲、问答、表格或解释。\n"
        "输出必须是 JSON，不要 Markdown 代码块。格式：\n"
        "{\n"
        '  "items": ["知识点名称~必考", "知识点名称~有可能考"],\n'
        '  "evaluationNotes": {"coverage": "...", "traceability": "..."}\n'
        "}\n\n"
        f"上下文 JSON：\n{json.dumps(context, ensure_ascii=False, indent=2)[:18000]}"
    )
    response = model.invoke(
        [
            SystemMessage(content=agent_system_prompt(root, read_text(root / "AGENTS.md"), limit=10000)),
            HumanMessage(content=prompt),
        ]
    )
    plan = parse_json_object(getattr(response, "content", str(response)))
    if not plan:
        raise ValueError("模型没有返回可解析的 JSON。")
    return write_review_plan(root, plan, options, focus_docs, context["sourceFiles"])


def build_review_context(root: Path, pages: list[dict], focus_docs: list[dict], options: dict) -> dict:
    source_files = build_review_source_context(root)
    wiki_pages = []
    for page in pages[:80]:
        path = root / page.get("path", "")
        wiki_pages.append(
            {
                "title": page.get("title"),
                "type": page.get("type"),
                "path": page.get("path"),
                "sourceRefs": page.get("sourceRefs", []),
                "excerpt": read_text(path)[:1800],
            }
        )
    return {
        "examTarget": options.get("examTarget") or "pass",
        "outputFormat": options.get("outputFormat") or "知识点名称~必考/有可能考",
        "knowledgeScope": options.get("knowledgeScope") or "all",
        "knowledgeScopeLabel": "重点文档优先：重点标注文件 + 原始资料；Wiki 仅作可选补充",
        "focusDocuments": focus_docs,
        "sourceFiles": source_files,
        "wikiPages": wiki_pages,
    }


def build_review_source_context(root: Path) -> list[dict]:
    sources = read_json(root / engine_paths.METADATA_DIR / "sources.json", [])
    source_files = []
    for source in sources[:80]:
        chunks = source.get("chunks") or []
        parsed_path = source.get("parsedPath") or ""
        excerpt = read_text(root / parsed_path)[:1200] if parsed_path else ""
        if not excerpt:
            excerpt_lines = []
            for chunk in chunks[:3]:
                default_section = f"第 {chunk.get('index', 0) + 1} 节"
                section = chunk.get("section", default_section)
                excerpt_lines.append(f"{section}：{chunk.get('text', '')[:500]}")
            excerpt = "\n".join(excerpt_lines)[:1200]
        source_files.append(
            {
                "id": source.get("id"),
                "fileName": source.get("fileName"),
                "kind": source.get("sourceKind"),
                "format": source.get("format"),
                "storedPath": source.get("storedPath"),
                "parsedPath": parsed_path,
                "chunkCount": source.get("chunkCount", 0),
                "sections": [
                    {
                        "chunkId": chunk.get("id"),
                        "section": chunk.get("section", f"第 {chunk.get('index', 0) + 1} 节"),
                        "lineStart": chunk.get("lineStart"),
                        "lineEnd": chunk.get("lineEnd"),
                    }
                    for chunk in chunks[:8]
                ],
                "excerpt": excerpt,
            }
        )
    return source_files


def parse_json_object(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:].strip()
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end >= start:
        text = text[start : end + 1]
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def write_review_plan(root: Path, plan: dict, options: dict, focus_docs: list[dict], source_docs: list[dict]) -> list[dict]:
    items = normalize_llm_review_items(plan.get("items") or plan.get("examFocus") or [])
    if not items:
        items = ["暂无可用原始资料或重点标注~有可能考"]
    review_text = "\n".join(items) + "\n"
    evaluation_text = render_llm_review_evaluation(items, review_text, focus_docs, source_docs, plan.get("evaluationNotes") or {})
    output_dir = engine_paths.make_review_output_dir(root / engine_paths.REVIEW_DIR)
    artifacts = [
        (engine_paths.REVIEW_DOC_NAME, review_text),
        (engine_paths.EVALUATION_DOC_NAME, evaluation_text),
    ]
    result = []
    for filename, body in artifacts:
        path = output_dir / filename
        path.write_text(body, encoding="utf-8")
        result.append({"title": filename, "path": str(path.relative_to(root)).replace("\\", "/")})
    write_json(
        root / engine_paths.METADATA_DIR / "review-artifacts.json",
        [{"title": output_dir.name, "path": str(output_dir.relative_to(root)).replace("\\", "/"), "files": result}],
    )
    return result


def normalize_llm_review_items(raw_items: Any) -> list[str]:
    if not isinstance(raw_items, list):
        return []
    result = []
    seen: set[str] = set()
    for raw in raw_items:
        text = str(raw).strip().lstrip("-*0123456789.、 ")
        if "~" not in text:
            continue
        name, marker = text.rsplit("~", 1)
        name = engine_paths.normalize_review_title(name)
        marker = marker.strip()
        if marker not in {"必考", "有可能考"}:
            marker = "必考" if "必" in marker else "有可能考"
        key = name.lower().replace(" ", "")
        if key in seen:
            continue
        seen.add(key)
        result.append(f"{name}~{marker}")
    return result


def render_llm_review_evaluation(
    items: list[str],
    review_text: str,
    focus_docs: list[dict],
    source_docs: list[dict],
    notes: dict,
) -> str:
    valid_format, invalid_lines = engine_paths.review_lines_are_valid(review_text)
    must_count = sum(1 for item in items if item.endswith("~必考"))
    possible_count = sum(1 for item in items if item.endswith("~有可能考"))
    unique_count = len({item.rsplit("~", 1)[0] for item in items})
    statuses = [
        ("覆盖率", "pass" if len(items) >= 5 else "partial", str(notes.get("coverage") or f"共抽取 {len(items)} 个知识点。")),
        ("密度", "pass", "每行仅保留知识点名称和考试概率标签。"),
        ("可考试性", "pass" if must_count else "partial", f"必考 {must_count} 个，有可能考 {possible_count} 个。"),
        ("原始资料忠实度", "pass" if source_docs or focus_docs else "partial", str(notes.get("traceability") or "已基于当前上下文生成，可回到重点文档和原始资料核对。")),
        ("去重合并", "pass" if unique_count == len(items) else "fail", f"去重后 {unique_count} 个。"),
        ("格式合规", "pass" if valid_format else "fail", "全部行符合 `知识点名称~必考/有可能考`。" if valid_format else "；".join(invalid_lines[:5])),
    ]
    overall = "fail" if any(status == "fail" for _name, status, _note in statuses) else "pass"
    lines = [
        "# 评测报告",
        "",
        f"- 复习文档：{engine_paths.REVIEW_DOC_NAME}",
        f"- 知识点数量：{len(items)}",
        f"- 必考数量：{must_count}",
        f"- 有可能考数量：{possible_count}",
        f"- 数据来源：{engine_paths.review_data_source_label(focus_docs, source_docs)}",
        "",
        "## 指标",
        "",
        "| 指标 | 结果 | 说明 |",
        "|---|---|---|",
    ]
    lines.extend(f"| {name} | {status} | {note} |" for name, status, note in statuses)
    lines.extend(["", "## 结论", "", f"整体判断：{overall}", f"需要修正：{'无' if overall == 'pass' else '请先修正 fail 指标后再使用。'}", ""])
    return "\n".join(lines)


def build_chat_model():
    settings = engine_paths.llm_settings()
    api_key = settings.get("apiKey")
    if not api_key:
        return None
    try:
        from langchain_openai import ChatOpenAI
    except ImportError:
        return None
    return ChatOpenAI(model=settings.get("model"), api_key=api_key, base_url=settings.get("baseUrl"), temperature=0.2)


def build_planning_prompt(state: WikiAgentState) -> str:
    sources = state.get("sources", [])
    source_summaries = []
    for source in sources[:40]:
        chunks = source.get("chunks", [])
        excerpt = "\n\n".join(chunk.get("text", "")[:1200] for chunk in chunks[:3])
        source_summaries.append(
            {
                "id": source.get("id"),
                "fileName": source.get("fileName"),
                "storedPath": source.get("storedPath"),
                "sourceKind": source.get("sourceKind"),
                "sections": [
                    {
                        "chunkId": chunk.get("id"),
                        "section": chunk.get("section"),
                        "lineStart": chunk.get("lineStart"),
                        "lineEnd": chunk.get("lineEnd"),
                    }
                    for chunk in chunks[:10]
                ],
                "excerpt": excerpt,
            }
        )
    return (
        "请基于 AGENTS.md 的规则，为这些原始资料规划并编写 Wiki 页面。\n"
        "目标不是少数 README 摘要，而是可长期维护的 LLM Wiki 编译层。请尽量覆盖至少 70% 的来源文件；如果来源不足 10 个，尽量覆盖全部来源。\n"
        "请生成课程级、主题级、案例级的 Wiki，而不是逐文件摘要；禁止把普通短语、泛词、半截句子生成为概念页。\n"
        "优先规划 8-16 个高质量页面，必须包含：课程总览、学习路线、来源地图；并根据资料增加主题页、案例页、方法页、术语页。\n"
        "页面类型只能使用：course-overview, learning-path, source-map, theme, case, concept, method, question, source-attachment。\n"
        "每个页面必须说明它使用了哪些原始资料章节；sourceIds 只写真实来源 id，sourceRefs 必须尽量写 chunkId。\n"
        "只返回 JSON，不要返回 Markdown 代码块。JSON 格式：\n"
        "{\n"
        '  "sourceCoverageTarget": 0.7,\n'
        '  "uncoveredSourceIds": ["..."],\n'
        '  "questionsForUser": ["..."],\n'
        '  "pages": [\n'
        '    {"title": "...", "type": "course-overview|learning-path|source-map|theme|case|concept|method|question|source-attachment", "path": "课程/课程总览.md", "purpose": "...", "status": "source-backed|needs-review|contested", "sourceIds": ["..."], "sourceRefs": [{"sourceId": "...", "chunkId": "...", "section": "..."}], "markdown": "..."}\n'
        "  ]\n"
        "}\n\n"
        "要求：页面必须有 YAML frontmatter；尽量引用来源章节；不要编造来源；不确定内容写 needs-review；concept 只用于稳定术语，不用于普通词；来源地图必须使用 type: source-map。\n\n"
        f"现有 index.md：\n{state.get('index_md', '')[:4000]}\n\n"
        f"资料摘要 JSON：\n{json.dumps(source_summaries, ensure_ascii=False, indent=2)}"
    )


def parse_json_plan(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:].strip()
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end >= start:
        text = text[start : end + 1]
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    pages = data.get("pages")
    if not isinstance(pages, list):
        return {}
    return {
        "sourceCoverageTarget": data.get("sourceCoverageTarget", 0.7),
        "uncoveredSourceIds": data.get("uncoveredSourceIds", []),
        "questionsForUser": data.get("questionsForUser", []),
        "pages": [page for page in pages if isinstance(page, dict)],
    }


def normalize_wiki_plan(plan: dict, sources: list[dict]) -> dict:
    import wiki_engine as engine

    source_ids = {source.get("id") for source in sources}
    pages = []
    covered = set()
    for page in plan.get("pages", []):
        page = dict(page)
        title = str(page.get("title") or "未命名页面")
        page["title"] = title
        page["type"] = engine.normalize_page_type(str(page.get("type") or ""), title)
        page["sourceIds"] = [source_id for source_id in page.get("sourceIds", []) if source_id in source_ids]
        page["sourceRefs"] = [
            ref
            for ref in page.get("sourceRefs", [])
            if isinstance(ref, dict) and (ref.get("sourceId") in source_ids or ref.get("chunkId"))
        ]
        covered.update(page["sourceIds"])
        pages.append(page)
    uncovered = [source.get("id") for source in sources if source.get("id") not in covered]
    return {
        "courseName": "",
        "sourceCoverageTarget": plan.get("sourceCoverageTarget", 0.7),
        "sourceFileCount": len(sources),
        "plannedSourceCoverage": round(len(covered) / len(sources), 4) if sources else 0,
        "uncoveredSourceIds": uncovered,
        "questionsForUser": plan.get("questionsForUser", []),
        "pages": pages,
        "updatedAt": engine.now_iso(),
    }


def write_llm_pages(root: Path, sources: list[dict], plan: dict) -> list[dict]:
    import wiki_engine as engine

    engine.clear_scaffold_wiki_files(root)
    pages = []
    source_by_id = {source["id"]: source for source in sources}
    for page in plan.get("pages", []):
        title = engine.safe_name(str(page.get("title") or "未命名页面"))
        page_type = engine.normalize_page_type(str(page.get("type") or ""), title)
        directory = page_directory(page_type, title, str(page.get("path") or ""))
        target = root / engine.WIKI_DIR / directory / f"{title}.md"
        markdown = normalize_markdown_page(page, title, page_type)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(markdown, encoding="utf-8")
        source_ids = [sid for sid in page.get("sourceIds", []) if sid in source_by_id]
        source = source_by_id[source_ids[0]] if source_ids else (sources[0] if sources else {"id": ""})
        record = engine.page_record(root, target, page_type, title, source)
        record["sourceIds"] = source_ids or record["sourceIds"]
        record["sourceRefs"] = source_refs_for_page(source_by_id, page, record["sourceIds"])
        pages.append(record)
    pages = ensure_minimum_source_coverage(root, sources, pages)
    engine.write_json(root / engine.METADATA_DIR / "pages.json", pages)
    engine.write_json(root / engine.METADATA_DIR / "graph.json", engine.build_graph(pages, sources))
    return pages


def page_directory(page_type: str, title: str, planned_path: str = "") -> str:
    if planned_path:
        planned = planned_path.replace("\\", "/").strip("/")
        if "/" in planned:
            return planned.rsplit("/", 1)[0]
    if page_type == "source-attachment":
        return "附件"
    if page_type == "source-map":
        return "课程"
    if page_type in {"theme", "concept", "method", "case"} and any(keyword in title for keyword in ("主题", "模型", "环境", "部署", "微调", "RAG", "案例", "Agent")):
        return "专题"
    if page_type == "question":
        return "问题"
    return "课程"


def ensure_minimum_source_coverage(root: Path, sources: list[dict], pages: list[dict], target: float = 0.7) -> list[dict]:
    import wiki_engine as engine

    if not sources:
        return pages
    used = {ref.get("sourceId") for page in pages for ref in page.get("sourceRefs", []) if ref.get("sourceId")}
    required = min(len(sources), max(1, int(len(sources) * target + 0.999)))
    missing = [source for source in sources if source.get("id") not in used]
    if len(used) >= required or not missing:
        return pages
    source_map = next((page for page in pages if page.get("type") == "source-map"), None)
    if source_map is None:
        target_path = root / engine.WIKI_DIR / "课程" / "来源地图.md"
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(render_source_map_page(missing[:required], title="来源地图"), encoding="utf-8")
        source_map = {
            "id": uuid.uuid4().hex,
            "title": "来源地图",
            "type": "source-map",
            "path": str(target_path.relative_to(root)).replace("\\", "/"),
            "sourceIds": [],
            "sourceRefs": [],
            "updatedAt": engine.now_iso(),
        }
        pages.append(source_map)
    needed = required - len(used)
    add_sources = missing[:needed]
    source_map["sourceIds"] = list(dict.fromkeys([*source_map.get("sourceIds", []), *(source.get("id") for source in add_sources)]))
    source_map["sourceRefs"] = [*source_map.get("sourceRefs", []), *engine.source_refs_for_sources(add_sources, max_refs=max(needed * 2, 12), chunks_per_source=2)]
    path = root / source_map["path"]
    path.write_text(render_source_map_page(add_sources, existing=path.read_text(encoding="utf-8", errors="ignore") if path.exists() else ""), encoding="utf-8")
    return pages


def render_source_map_page(sources: list[dict], *, title: str = "来源地图", existing: str = "") -> str:
    body = [
        f"# {title}",
        "",
        "## 摘要",
        "",
        "本页维护原始资料与 Wiki 页面之间的覆盖关系。它不是附件页，而是 Agent 用来追踪来源覆盖率的索引页。",
        "",
        "## 追加覆盖来源",
        "",
    ]
    for source in sources:
        sections = source.get("sections") or []
        section_names = "、".join(section.get("section", "") for section in sections[:6]) or "原始文件"
        body.append(f"- `{source.get('storedPath')}`：{section_names}")
    frontmatter = [
        "---",
        f"title: {title}",
        "type: source-map",
        "status: source-backed",
        "sources:",
        *(f"  - {source.get('id')}" for source in sources[:20]),
        "tags:",
        "  - wiki",
        "  - source-map",
        "---",
        "",
    ]
    if existing and "## 追加覆盖来源" in existing:
        return existing.rstrip() + "\n" + "\n".join(body[6:]) + "\n"
    return "\n".join(frontmatter + body) + "\n"


def normalize_markdown_page(page: dict, title: str, page_type: str) -> str:
    markdown = str(page.get("markdown") or "").strip()
    if markdown.startswith("---"):
        return markdown + "\n"
    status = page.get("status") or "needs-review"
    source_ids = page.get("sourceIds") or []
    frontmatter = [
        "---",
        f"title: {title}",
        f"type: {page_type}",
        f"status: {status}",
        "sources:",
        *(f"  - {source_id}" for source_id in source_ids),
        "tags:",
        "  - wiki",
        "---",
        "",
    ]
    body = markdown or f"# {title}\n\n## 待确认\n\n该页面由 Agent 规划，但内容需要进一步补全。"
    return "\n".join(frontmatter) + body + "\n"


def source_refs_for_page(source_by_id: dict[str, dict], page: dict, source_ids: list[str]) -> list[dict]:
    requested_refs = page.get("sourceRefs")
    if isinstance(requested_refs, list):
        refs = []
        chunk_by_id = {
            chunk.get("id"): (source, chunk)
            for source in source_by_id.values()
            for chunk in source.get("chunks", [])
            if chunk.get("id")
        }
        for item in requested_refs:
            if not isinstance(item, dict):
                continue
            source_id = item.get("sourceId")
            chunk_id = item.get("chunkId")
            if chunk_id in chunk_by_id:
                source, chunk = chunk_by_id[chunk_id]
                refs.append(engine_paths.chunk_source_ref(source, chunk))
                continue
            source = source_by_id.get(source_id)
            if not source:
                continue
            matching = [
                chunk
                for chunk in source.get("chunks", [])
                if item.get("section") and item.get("section") in (chunk.get("section") or "")
            ]
            if matching:
                refs.append(engine_paths.chunk_source_ref(source, matching[0]))
            else:
                refs.extend(engine_paths.source_refs_for_sources([source], max_refs=1))
        if refs:
            return refs[:30]
    return source_refs_for_ids(source_by_id, source_ids)


def source_refs_for_ids(source_by_id: dict[str, dict], source_ids: list[str]) -> list[dict]:
    refs = []
    for source_id in source_ids:
        source = source_by_id.get(source_id)
        if not source:
            continue
        chunks = source.get("chunks", [])
        if chunks:
            refs.extend(engine_paths.chunk_source_ref(source, chunk) for chunk in engine_paths.unique_section_chunks(chunks)[:5])
        else:
            refs.append(
                {
                    "sourceId": source_id,
                    "fileName": source.get("fileName", ""),
                    "storedPath": source.get("storedPath", ""),
                    "chunkId": "",
                    "section": "原始文件",
                    "lineStart": None,
                    "lineEnd": None,
                }
            )
    return refs


def add_step(state: WikiAgentState, name: str, message: str) -> None:
    state.setdefault("steps", []).append({"name": name, "message": message})


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def read_json(path: Path, default: Any):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
