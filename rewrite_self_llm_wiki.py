from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import wiki_engine as engine


ROOT = Path(r"D:\05_Obsidian\courses\self-llm")


def main() -> None:
    wiki_dir = ROOT / engine.WIKI_DIR
    metadata_dir = ROOT / engine.METADATA_DIR
    archive = ROOT / engine.SYSTEM_DIR / "legacy-wiki-fragments"
    if archive.exists():
        shutil.rmtree(archive)
    if wiki_dir.exists():
        archive.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(wiki_dir, archive)
        shutil.rmtree(wiki_dir)

    for folder in ("课程", "专题", "案例"):
        (wiki_dir / folder).mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc).isoformat()
    pages = write_pages(wiki_dir, now)
    metadata_dir.mkdir(parents=True, exist_ok=True)
    engine.write_json(metadata_dir / "pages.json", pages)
    engine.write_json(metadata_dir / "graph.json", graph_for_pages(pages))
    engine.write_json(metadata_dir / "lint.json", [])
    write_index()
    with (ROOT / "log.md").open("a", encoding="utf-8") as handle:
        handle.write(f"\n## {now}\n\n- 已重构 self-llm Wiki：删除碎片概念页，改为课程级导航结构。\n")
    print(f"rewritten self-llm wiki pages: {len(pages)}")


def write_pages(wiki_dir: Path, now: str) -> list[dict]:
    pages: list[dict] = []
    for spec in page_specs():
        folder = "案例" if spec["type"] == "案例" else ("专题" if spec["type"] == "专题" else "课程")
        path = wiki_dir / folder / f"{spec['title']}.md"
        path.write_text(render_page(spec["title"], spec["type"], spec["sources"], spec["body"]), encoding="utf-8")
        pages.append(
            {
                "id": spec["title"],
                "title": spec["title"],
                "type": spec["type"],
                "path": path.relative_to(ROOT).as_posix(),
                "sourceIds": spec["sources"],
                "sourceRefs": [
                    {
                        "sourceId": source,
                        "fileName": Path(source).name,
                        "chunkId": "",
                        "section": "页面级来源",
                    }
                    for source in spec["sources"]
                ],
                "updatedAt": now,
            }
        )
    return pages


def render_page(title: str, page_type: str, sources: list[str], body: str) -> str:
    source_lines = "\n".join(f"  - {source}" for source in sources)
    return f"""---
title: {title}
type: {page_type}
status: source-backed
sources:
{source_lines}
tags:
  - wiki
  - self-llm
---

# {title}

{body.strip()}
"""


def page_specs() -> list[dict]:
    return [
        {
            "title": "课程总览",
            "type": "课程",
            "sources": [
                "原始资料/raw-materials/README.md",
                "原始资料/raw-materials/examples/readme.md",
                "原始资料/raw-materials/models_mlx/README.md",
            ],
            "body": """
## 这个课程解决什么问题

Self-LLM 是一套面向国内学习者的开源大模型使用教程，重点不是讲模型原理，而是帮助学习者把开源模型真正跑起来、用起来、改起来。课程覆盖环境配置、本地部署、服务化调用、应用接入和微调实践。

## 推荐学习顺序

1. [[学习路线]]：先明确学习入口和阶段目标。
2. [[环境与硬件平台]]：根据自己的机器选择 Linux GPU、AMD、Ascend 或 Apple M 路线。
3. [[模型部署矩阵]]：选择一个模型完成下载、推理和服务部署。
4. [[微调与检索增强]]：进入 LoRA、Embedding 微调、RAG 和知识库助手。
5. [[案例库]]：通过 Chat-嬛嬛、天机、AMchat、数字生命等案例理解完整应用。

## Wiki 使用方式

- 如果你是初学者，优先阅读“课程”目录下的页面。
- 如果你已经有目标硬件或模型，直接进入“专题”目录。
- 如果你想做项目，直接从“案例”目录挑一个案例复现。

## 来源

- [[原始资料/raw-materials/README.md]]
- [[原始资料/raw-materials/examples/readme.md]]
- [[原始资料/raw-materials/models_mlx/README.md]]
""",
        },
        {
            "title": "学习路线",
            "type": "课程",
            "sources": ["原始资料/raw-materials/README.md"],
            "body": """
## 三阶段路线

### 第一阶段：能运行

目标是完成基础环境和第一个模型推理。README 中给出的学习建议是先学环境配置，再学模型部署使用，最后再学微调。初学者可以优先选择 Qwen、InternLM、MiniCPM 等资料较完整的模型。

### 第二阶段：能部署

目标是把模型从 Notebook 或脚本运行推进到服务化部署。重点关注 vLLM、Transformers、ModelScope、Docker、OpenAI 兼容 API 等部署方式。

### 第三阶段：能改造

目标是进入私域数据、微调、Embedding、RAG、LangChain 接入和应用开发。此阶段应该阅读 [[微调与检索增强]] 和 [[案例库]]。

## 不建议的学习方式

- 不要从所有模型列表逐个横向浏览，容易迷失。
- 不要一开始就做复杂微调，先保证推理链路跑通。
- 不要只复制命令，要记录硬件、驱动、Python、CUDA/ROCm/CANN 等版本。
""",
        },
        {
            "title": "环境与硬件平台",
            "type": "专题",
            "sources": [
                "原始资料/raw-materials/README.md",
                "原始资料/raw-materials/support_model_amd.md",
                "原始资料/raw-materials/support_model_Ascend.md",
                "原始资料/raw-materials/models_mlx/README.md",
            ],
            "body": """
## 平台分层

Self-LLM 的资料不是单一平台教程，而是多硬件路线集合。当前资料里比较明确的路线包括：

| 平台 | 适合对象 | 关键资料 |
| --- | --- | --- |
| Linux + CUDA | 通用部署、训练、微调 | `README.md` 和各模型目录 |
| AMD GPU / Ryzen AI | AMD 设备用户 | `support_model_amd.md` |
| Ascend NPU | 昇腾服务器或国产算力环境 | `support_model_Ascend.md` |
| Apple M 系列 | 本地轻量推理和 MLX 生态 | `models_mlx/README.md` |

## 环境选择建议

- 想最快体验：优先选 Apple MLX 或小参数量 Qwen/InternLM 示例。
- 想做服务：优先看 vLLM、Docker、OpenAI API 兼容服务。
- 想做国产硬件：先阅读 AMD 或 Ascend 专区，不要直接套 CUDA 教程。
- 想做微调：优先确认显存、batch size、训练框架和数据格式。

## 相关页面

- [[Apple MLX 专区]]
- [[模型部署矩阵]]
- [[微调与检索增强]]
""",
        },
        {
            "title": "模型部署矩阵",
            "type": "专题",
            "sources": [
                "原始资料/raw-materials/README.md",
                "原始资料/raw-materials/models/Step-3.5-Flash/01-Step-3.5-Flash-vLLM部署教程.md",
                "原始资料/raw-materials/models/DeepSeek-R1-Distill-Qwen/readme.md",
                "原始资料/raw-materials/models/Qwen2.5/readme.md",
                "原始资料/raw-materials/models/SpatialLM/readme.md",
            ],
            "body": """
## 部署资料的组织方式

课程中的模型资料主要按模型目录组织，每个目录通常对应一个模型或一个应用方向。Wiki 不应该把每个 README 生成为孤立页面，而应该把它们纳入部署矩阵。

| 模型 / 方向 | 重点能力 | 适合学习内容 | 来源 |
| --- | --- | --- | --- |
| Step-3.5-Flash | vLLM 推理、Docker 镜像、服务化部署 | 高吞吐推理、OpenAI 兼容 API | `models/Step-3.5-Flash/...` |
| DeepSeek-R1-Distill-Qwen | 推理模型体验 | 蒸馏模型、强化学习模型入口 | `models/DeepSeek-R1-Distill-Qwen/readme.md` |
| Qwen2.5 | 通用中文模型部署 | Qwen 系列生态 | `models/Qwen2.5/readme.md` |
| SpatialLM | 点云理解与目标检测 | 多模态/空间理解模型 | `models/SpatialLM/readme.md` |
| Yuan2.0 / Yuan2.0-M32 | 浪潮信息模型 | 国产模型部署 | `models/Yuan2.0*` |

## 部署页应该沉淀什么

- 环境：系统、Python、CUDA/ROCm/CANN、依赖版本。
- 模型：模型来源、下载方式、目录结构。
- 推理：Transformers 或 MLX 的最小推理脚本。
- 服务：vLLM、Gradio、OpenAI API 兼容服务。
- 常见问题：显存不足、驱动问题、依赖冲突、模型路径错误。
""",
        },
        {
            "title": "Apple MLX 专区",
            "type": "专题",
            "sources": [
                "原始资料/raw-materials/models_mlx/README.md",
                "原始资料/raw-materials/models_mlx/docs/MLX-LM_Intro.md",
                "原始资料/raw-materials/models_mlx/README_en.md",
                "原始资料/raw-materials/models_mlx/docs/figs/qwen3_res_mlx.png",
                "原始资料/raw-materials/models_mlx/docs/figs/qwen3_res_trm.png",
            ],
            "body": """
## 这个专区的定位

Apple MLX 专区面向 Apple Silicon 用户，目标是在本地用 MLX-LM 部署和使用大模型。相比通用 Transformers 路线，MLX 更贴近 Apple M 系列芯片的统一内存和本地推理体验。

## 主要内容

- 创建 `mlx-lm` Python 环境。
- 使用 `requirements.txt` 安装依赖。
- 通过 Gradio 应用完成模型下载与对话。
- 使用 Notebook 学习 Qwen3 在 MLX 与 Transformers 下的部署差异。
- 通过配置文件管理模型列表。

## 使用判断

如果你的机器是 MacBook / Mac Studio 等 Apple Silicon 设备，且目标是本地体验中小模型，优先阅读本专区。如果你需要 GPU 高吞吐服务，优先回到 [[模型部署矩阵]] 的 vLLM 路线。
""",
        },
        {
            "title": "微调与检索增强",
            "type": "专题",
            "sources": [
                "原始资料/raw-materials/models/BGE-M3-finetune-embedding-with-valid/README.md",
                "原始资料/raw-materials/models/InternLM/06-InternLM接入LangChain搭建知识库助手/readme.md",
                "原始资料/raw-materials/models/Qwen/07-Qwen-7B-Chat 接入langchain搭建知识库助手/readme.md",
                "原始资料/raw-materials/examples/数字生命/readme.md",
                "原始资料/raw-materials/examples/Chat-嬛嬛/readme.md",
            ],
            "body": """
## 这部分解决什么问题

部署模型只解决“能用”；微调和检索增强解决“为我的任务变好用”。Self-LLM 中这部分资料分散在模型目录和案例目录，需要在 Wiki 中合并阅读。

## 关键主题

### Embedding 微调

BGE-M3 微调资料重点讲查询和文档向量、对比学习、In-batch Negatives、温度参数、batch size 与检索评测。它适合想做代码检索、语义检索、RAG 检索底座的人阅读。

### LangChain / 知识库助手

InternLM 与 Qwen 目录里有接入 LangChain 搭建知识库助手的入口。这类资料适合用于理解模型部署后如何进入应用层。

### LoRA / 个性化微调

Chat-嬛嬛、数字生命等案例说明如何围绕角色语料、个人语料或领域语料制作数据集，并通过微调形成特定风格或能力。

## 学习建议

先完成一个模型部署，再做 RAG；先做小样本 LoRA 或 Embedding 微调，再尝试完整私域模型训练。
""",
        },
        {
            "title": "案例库",
            "type": "案例",
            "sources": [
                "原始资料/raw-materials/examples/readme.md",
                "原始资料/raw-materials/examples/Chat-嬛嬛/readme.md",
                "原始资料/raw-materials/examples/Tianji-天机/readme.md",
                "原始资料/raw-materials/examples/AMchat-高等数学/readme.md",
                "原始资料/raw-materials/examples/数字生命/readme.md",
            ],
            "body": """
## 案例库的作用

案例库不是单纯展示项目，而是帮助学习者把“模型部署”推进到“应用开发”。当前资料中比较核心的案例有：

| 案例 | 主题 | 可学到什么 |
| --- | --- | --- |
| Chat-嬛嬛 | 角色语气 LoRA 微调 | 角色语料、风格模仿、模型发布 |
| 天机 | 中国式社交场景应用 | Prompt、Agent、RAG、数据清洗、全栈应用 |
| AMchat | 高等数学问答模型 | 数学数据集、xtuner 微调、垂直领域问答 |
| 数字生命 | 个人风格数字人 | 个人语料、迁移复制、个性化表达 |

## 怎么选案例

- 想学微调：从 Chat-嬛嬛或 AMchat 开始。
- 想学应用：从天机开始。
- 想做个人知识/人格复刻：读数字生命。
- 想做检索底座：先读 [[微调与检索增强]] 中的 BGE-M3。
""",
        },
        {
            "title": "来源地图",
            "type": "课程",
            "sources": [
                "原始资料/raw-materials/README.md",
                "原始资料/raw-materials/support_model_amd.md",
                "原始资料/raw-materials/support_model_Ascend.md",
                "原始资料/raw-materials/models_mlx/README.md",
                "原始资料/raw-materials/examples/readme.md",
            ],
            "body": """
## Wiki 页面与原始资料关系

| Wiki 页面 | 主要来源 |
| --- | --- |
| [[课程总览]] | `README.md`、`examples/readme.md`、`models_mlx/README.md` |
| [[学习路线]] | `README.md` |
| [[环境与硬件平台]] | `README.md`、`support_model_amd.md`、`support_model_Ascend.md`、`models_mlx/README.md` |
| [[模型部署矩阵]] | `README.md`、各模型目录 README、Step-3.5-Flash vLLM 教程 |
| [[Apple MLX 专区]] | `models_mlx/README.md`、`MLX-LM_Intro.md`、Qwen3 结果图 |
| [[微调与检索增强]] | BGE-M3 微调、LangChain 知识库助手、Chat-嬛嬛、数字生命 |
| [[案例库]] | `examples/readme.md` 及案例子目录 |

## 为什么不保留旧碎片页

旧 Wiki 把“包括”“简介”“这是一个基于”等普通短语抽成概念页，既不能帮助阅读，也会污染来源图。新的结构只保留能承担学习导航职责的页面。
""",
        },
    ]


def graph_for_pages(pages: list[dict]) -> dict:
    return {
        "nodes": [{"id": page["id"], "label": page["title"], "type": page["type"]} for page in pages],
        "edges": [
            {"from": "课程总览", "to": "学习路线", "relation": "导航"},
            {"from": "课程总览", "to": "环境与硬件平台", "relation": "导航"},
            {"from": "课程总览", "to": "模型部署矩阵", "relation": "导航"},
            {"from": "环境与硬件平台", "to": "Apple MLX 专区", "relation": "平台专题"},
            {"from": "模型部署矩阵", "to": "微调与检索增强", "relation": "进阶"},
            {"from": "微调与检索增强", "to": "案例库", "relation": "应用"},
            {"from": "来源地图", "to": "课程总览", "relation": "来源说明"},
        ],
    }


def write_index() -> None:
    index = """# Self_LLM Wiki 索引

## 推荐阅读

1. [[课程总览]]
2. [[学习路线]]
3. [[环境与硬件平台]]
4. [[模型部署矩阵]]
5. [[微调与检索增强]]
6. [[案例库]]

## 课程页面

- [[课程总览]]：Self-LLM 的定位、范围和阅读入口。
- [[学习路线]]：从能运行、能部署到能改造的学习顺序。
- [[来源地图]]：Wiki 页面和原始资料之间的对应关系。

## 专题页面

- [[环境与硬件平台]]：Linux/CUDA、AMD、Ascend、Apple M 的路线选择。
- [[模型部署矩阵]]：模型部署资料如何阅读和选择。
- [[Apple MLX 专区]]：Apple Silicon 本地推理路线。
- [[微调与检索增强]]：Embedding 微调、RAG、LangChain 和 LoRA 案例。

## 案例页面

- [[案例库]]：Chat-嬛嬛、天机、AMchat、数字生命等应用案例。

## 复习资料

- 暂无复习资料。

## 待处理问题

- 暂无检查问题。
"""
    (ROOT / "index.md").write_text(index, encoding="utf-8")


if __name__ == "__main__":
    main()
