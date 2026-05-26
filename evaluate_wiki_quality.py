from __future__ import annotations

import json
import re
from pathlib import Path

import wiki_engine as engine


REPORT = Path("D:/05_Obsidian/courses/LLM Wiki Studio 5门课程Wiki评估报告.md")


def strip_frontmatter(text: str) -> str:
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            return parts[2].strip()
    return text.strip()


def score_course(metrics: dict) -> int:
    score = 100
    if metrics["issues"]:
        score -= min(18, metrics["issues"] * 3)
    if metrics["sourceCoverage"] < 0.15:
        score -= 20
    elif metrics["sourceCoverage"] < 0.3:
        score -= 10
    if metrics["avgCharsPerPage"] < 600:
        score -= 12
    elif metrics["avgCharsPerPage"] < 800:
        score -= 6
    if metrics["templateHitPages"]:
        score -= min(12, metrics["templateHitPages"] * 4)
    if metrics["badLinks"]:
        score -= min(12, len(metrics["badLinks"]) * 3)
    if metrics["pages"] < 8:
        score -= 8
    if metrics["refs"] / max(metrics["pages"], 1) < 3:
        score -= 10
    return max(0, min(100, score))


def grade(score: int) -> str:
    if score >= 85:
        return "优秀"
    if score >= 75:
        return "良好"
    if score >= 65:
        return "可用但需迭代"
    return "需要明显改进"


def collect_metrics() -> list[dict]:
    rows: list[dict] = []
    for workspace in engine.list_workspaces():
        root = Path(workspace["path"])
        sources = engine.read_json(root / engine.METADATA_DIR / "sources.json", [])
        pages = engine.read_json(root / engine.METADATA_DIR / "pages.json", [])
        issues = engine.read_json(root / engine.METADATA_DIR / "lint.json", [])
        graph = engine.read_json(root / engine.METADATA_DIR / "graph.json", {"nodes": [], "edges": []})
        raw_files = [path for path in (root / engine.RAW_DIR).rglob("*") if path.is_file()] if (root / engine.RAW_DIR).exists() else []
        wiki_files = [path for path in (root / engine.WIKI_DIR).rglob("*.md")] if (root / engine.WIKI_DIR).exists() else []
        titles = {page.get("title") for page in pages}
        total_chars = 0
        template_hit_pages: list[str] = []
        bad_links: list[str] = []
        excerpts: list[tuple[str, str, str]] = []
        source_ids_used: set[str] = set()
        refs = 0

        for page in pages:
            path = root / page.get("path", "")
            text = path.read_text(encoding="utf-8", errors="ignore") if path.exists() else ""
            body = strip_frontmatter(text)
            total_chars += len(body)
            refs += len(page.get("sourceRefs", []))
            for ref in page.get("sourceRefs", []):
                if ref.get("sourceId"):
                    source_ids_used.add(ref["sourceId"])
            if any(marker in text for marker in ["待补充", "需要进一步补全", "needs-review", "## 待确认"]):
                if "## 待确认\n无" not in text and "## 待确认\r\n无" not in text:
                    template_hit_pages.append(page.get("title", "未命名页面"))
            for link in re.findall(r"\[\[([^\]]+)\]\]", text):
                link_title = link.split("|", 1)[0].split("#", 1)[0]
                if link_title not in titles and "/" not in link and "\\" not in link:
                    bad_links.append(link)
            if len(excerpts) < 3:
                excerpts.append((page.get("title", "未命名页面"), page.get("type", "-"), re.sub(r"\s+", " ", body)[:700]))

        metrics = {
            "course": workspace["name"],
            "rawFiles": len(raw_files),
            "sources": len(sources),
            "sourceChunks": sum(source.get("chunkCount", 0) for source in sources),
            "wikiFiles": len(wiki_files),
            "pages": len(pages),
            "types": {kind: sum(1 for page in pages if page.get("type") == kind) for kind in sorted({page.get("type") for page in pages})},
            "issues": len(issues),
            "issueMessages": [issue.get("message") for issue in issues],
            "refs": refs,
            "sourceCoverage": round(len(source_ids_used) / len(sources), 3) if sources else 0,
            "avgCharsPerPage": round(total_chars / len(pages), 1) if pages else 0,
            "templateHitPages": len(template_hit_pages),
            "templateHitTitles": template_hit_pages,
            "badLinks": sorted(set(bad_links)),
            "graphNodes": len(graph.get("nodes", [])),
            "graphEdges": len(graph.get("edges", [])),
            "excerpts": excerpts,
        }
        metrics["score"] = score_course(metrics)
        metrics["grade"] = grade(metrics["score"])
        rows.append(metrics)
    return rows


def build_report(rows: list[dict]) -> str:
    lines = [
        "# LLM Wiki Studio 5门课程 Wiki 评估报告",
        "",
        "## 总体结论",
        "",
        "这 5 个课程的 Wiki 已经确认是通过 LangChain / LangGraph 调用模型 Agent 生成的，不再是本地 fallback 脚本。整体上，它们已经具备“可导航的课程 Wiki 雏形”：有页面、来源引用、索引和基本图谱。但距离 Karpathy LLM Wiki 中强调的“长期维护、可追溯、可增量演化的知识层”还有差距，主要问题集中在来源覆盖率、链接规范、页面深度和待确认内容处理。",
        "",
        "| 课程 | 评分 | 等级 | Wiki页 | 来源文件覆盖 | 平均页长 | Lint问题 | 来源引用 |",
        "|---|---:|---|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row['course']} | {row['score']} | {row['grade']} | {row['pages']} | "
            f"{row['sourceCoverage'] * 100:.1f}% | {row['avgCharsPerPage']} | {row['issues']} | {row['refs']} |"
        )
    lines.extend(["", "## 分课程评估", ""])

    for row in rows:
        lines.extend(
            [
                f"### {row['course']}：{row['grade']}（{row['score']}/100）",
                "",
                f"- 原始文件：{row['rawFiles']}；登记来源：{row['sources']}；来源章节块：{row['sourceChunks']}。",
                f"- Wiki 页面：{row['pages']}；实际 Markdown 文件：{row['wikiFiles']}；页面类型：{json.dumps(row['types'], ensure_ascii=False)}。",
                f"- 来源引用：{row['refs']}；来源文件覆盖率：{row['sourceCoverage'] * 100:.1f}%；图谱：{row['graphNodes']} 个节点 / {row['graphEdges']} 条边。",
                f"- 平均页面长度：{row['avgCharsPerPage']} 字符；待补全/待确认页面：{row['templateHitPages']}。",
                "",
            ]
        )
        strengths: list[str] = []
        weaknesses: list[str] = []
        if row["issues"] == 0:
            strengths.append("lint 结果干净，基础链接和来源元数据较稳定。")
        if row["refs"] / max(row["pages"], 1) >= 6:
            strengths.append("每个页面平均来源引用较多，可追溯性较好。")
        if row["avgCharsPerPage"] >= 850:
            strengths.append("页面正文相对充分，不只是标题式目录。")
        if row["sourceCoverage"] >= 0.3:
            strengths.append("来源文件覆盖率在当前样本中较好。")
        if not strengths:
            strengths.append("已经生成了可作为 Obsidian 导航入口的基础 Wiki 页面。")
        if row["issues"]:
            weaknesses.append("仍存在 lint 问题：" + "；".join(row["issueMessages"][:5]))
        if row["sourceCoverage"] < 0.3:
            weaknesses.append("来源覆盖率偏低，Agent 主要使用了少数入口文件，很多原始资料尚未被综合进 Wiki。")
        if row["avgCharsPerPage"] < 700:
            weaknesses.append("页面内容偏薄，更像课程索引或摘要，不足以承担稳定知识层。")
        if row["templateHitPages"]:
            weaknesses.append("存在待确认或模板残留页面：" + "、".join(row["templateHitTitles"][:5]))
        if row["badLinks"]:
            weaknesses.append("存在未解析链接：" + "、".join(row["badLinks"][:8]))
        lines.extend(["**优点**", ""])
        lines.extend(f"- {item}" for item in strengths)
        lines.extend(["", "**问题**", ""])
        lines.extend(f"- {item}" for item in weaknesses)
        lines.extend(["", "**内容抽样**", ""])
        for title, page_type, excerpt in row["excerpts"]:
            lines.append(f"- `{title}`（{page_type}）：{excerpt[:260]}")
        lines.append("")

    lines.extend(
        [
            "## 共性问题",
            "",
            "1. Agent 能生成 Wiki，但规划阶段仍偏“少数页面总结”，不是完整课程知识编译。大多数课程只生成 8-10 页，面对 20+ 个原始文件时覆盖不足。",
            "2. 来源引用存在，但很多页面引用的是文件级或少量 chunk，缺少“章节级证据如何支持具体断言”的细粒度映射。",
            "3. 有些页面混用了 `chapter`、`concept`、`synthesis` 类型，页面类型语义不稳定；例如“来源地图”不应是 source-attachment。",
            "4. 仍有未解析 Wiki 链接和待确认内容，说明 Agent 写完后缺少自动修复回路。",
            "5. 当前更像第一版课程知识库草稿，还不是可以长期自动维护的 LLM Wiki。",
            "",
            "## 建议的下一轮重构",
            "",
            "1. 构建前先让 Agent 输出 `wiki_plan.json`，明确每个原始资料会进入哪个 Wiki 页面，避免只看 README。",
            "2. 构建分两轮：第一轮生成课程骨架，第二轮逐页面补充缺失来源，直到来源覆盖率达到设定阈值。",
            "3. 将 lint 从“发现问题”升级为“自动修复问题”：断链要创建页面或改链接，needs-review 要收敛为明确问题列表。",
            "4. 固定页面类型：课程入口、学习路线、主题页、案例页、术语页、来源地图，禁止 Agent 随意把来源地图写成附件页。",
            "5. 前端显示一个构建质量面板：来源覆盖率、待确认数、断链数、平均来源引用数，让用户知道 Wiki 是否真正可用。",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    rows = collect_metrics()
    REPORT.write_text(build_report(rows), encoding="utf-8")
    print(json.dumps([{key: row[key] for key in ("course", "score", "grade", "pages", "sourceCoverage", "issues", "refs", "avgCharsPerPage")} for row in rows], ensure_ascii=False, indent=2))
    print(REPORT)


if __name__ == "__main__":
    main()
