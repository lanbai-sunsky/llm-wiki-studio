from __future__ import annotations

import json
import re
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path


TARGET_ROOT = Path(r"C:\Users\28634\Desktop\LLM Wiki Studio TEST")
ORG = "datawhalechina"
REPOS = ["hello-agents", "self-llm", "happy-llm", "pumpkin-book", "llm-cookbook"]
SUPPORTED_SUFFIXES = {
    ".md",
    ".markdown",
    ".txt",
    ".pdf",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".svg",
    ".canvas",
    ".base",
}
MAX_FILES_PER_REPO = 28
MAX_FILE_BYTES = 8 * 1024 * 1024
LOCAL_IMAGE_SUFFIXES = {".avif", ".bmp", ".gif", ".jpeg", ".jpg", ".png", ".svg", ".webp"}
IMAGE_REF_PATTERNS = (
    re.compile(r"!\[[^\]]*\]\(([^)]+)\)"),
    re.compile(r"<img\s+[^>]*src=[\"']([^\"']+)[\"']", re.I),
)


@dataclass
class RepoInfo:
    name: str
    full_name: str
    stars: int
    description: str
    html_url: str
    default_branch: str
    license: str


def main() -> None:
    TARGET_ROOT.mkdir(parents=True, exist_ok=True)
    repos = [fetch_repo_info(name) for name in REPOS]
    write_overview(repos)
    for index, repo in enumerate(repos, start=1):
        print(f"[{index}/{len(repos)}] preparing {repo.full_name} ...")
        prepare_repo(index, repo)
    print(f"Prepared Datawhale test set at: {TARGET_ROOT}")


def prepare_repo(index: int, repo: RepoInfo) -> None:
    course_root = TARGET_ROOT / f"{index:02d}-{repo.name}"
    if course_root.exists():
        shutil.rmtree(course_root)
    raw_dir = course_root / "raw-materials"
    focus_dir = course_root / "focus-documents"
    evaluation_dir = course_root / "evaluation"
    raw_dir.mkdir(parents=True)
    focus_dir.mkdir(parents=True)
    evaluation_dir.mkdir(parents=True)

    tree = fetch_tree(repo)
    tree_by_path = {item.get("path", ""): item for item in tree}
    candidates = select_files(tree)
    downloaded = []
    downloaded_paths = set()
    for item in candidates[:MAX_FILES_PER_REPO]:
        if download_tree_item(repo, raw_dir, item, downloaded_paths):
            downloaded.append(item)

    image_dependencies = collect_image_dependencies(raw_dir, tree_by_path, downloaded_paths)
    for item in image_dependencies:
        if download_tree_item(repo, raw_dir, item, downloaded_paths):
            downloaded.append(item)

    focus_text = render_focus_document(repo, downloaded)
    (focus_dir / "考试重点说明.md").write_text(focus_text, encoding="utf-8")
    (evaluation_dir / "Agent评分说明.md").write_text(render_evaluation_prompt(repo), encoding="utf-8")
    (course_root / "README.md").write_text(render_course_readme(index, repo, downloaded), encoding="utf-8")
    (course_root / "repo-metadata.json").write_text(
        json.dumps(
            {
                "name": repo.full_name,
                "stars": repo.stars,
                "description": repo.description,
                "url": repo.html_url,
                "defaultBranch": repo.default_branch,
                "license": repo.license,
                "selectedFiles": [item["path"] for item in downloaded],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def fetch_repo_info(name: str) -> RepoInfo:
    data = github_json(f"https://api.github.com/repos/{ORG}/{name}")
    license_data = data.get("license") or {}
    return RepoInfo(
        name=name,
        full_name=data["full_name"],
        stars=int(data.get("stargazers_count", 0)),
        description=data.get("description") or "",
        html_url=data["html_url"],
        default_branch=data["default_branch"],
        license=license_data.get("spdx_id") or "",
    )


def fetch_tree(repo: RepoInfo) -> list[dict]:
    data = github_json(
        f"https://api.github.com/repos/{repo.full_name}/git/trees/{repo.default_branch}?recursive=1"
    )
    return [item for item in data.get("tree", []) if item.get("type") == "blob"]


def select_files(tree: list[dict]) -> list[dict]:
    eligible = []
    for item in tree:
        path = item.get("path", "")
        suffix = Path(path).suffix.lower()
        size = int(item.get("size") or 0)
        if suffix not in SUPPORTED_SUFFIXES:
            continue
        if size <= 0 or size > MAX_FILE_BYTES:
            continue
        if should_skip_path(path):
            continue
        item = dict(item)
        item["score"] = score_path(path, size)
        eligible.append(item)
    eligible.sort(key=lambda value: (-value["score"], len(value["path"]), value["path"].lower()))
    return eligible


def download_tree_item(repo: RepoInfo, raw_dir: Path, item: dict, downloaded_paths: set[str]) -> bool:
    path = item["path"]
    if path in downloaded_paths:
        return False
    target = raw_dir / safe_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        download_file(repo, path, target)
    except Exception as exc:  # pragma: no cover - network preparation helper
        print(f"  skip {path}: {exc}")
        return False
    downloaded_paths.add(path)
    time.sleep(0.08)
    return True


def collect_image_dependencies(raw_dir: Path, tree_by_path: dict[str, dict], downloaded_paths: set[str]) -> list[dict]:
    dependencies: list[dict] = []
    seen = set(downloaded_paths)
    for markdown_path in sorted(raw_dir.rglob("*.md")):
        source_path = markdown_path.relative_to(raw_dir).as_posix()
        text = markdown_path.read_text(encoding="utf-8", errors="ignore")
        for reference in extract_local_image_references(text):
            repo_path = normalize_repo_reference(source_path, reference)
            candidates = [repo_path]
            if source_path.lower() == "readme.md":
                candidates.append(normalize_repo_reference("docs/README.md", reference))
            if repo_path.endswith("/images/7.1-1.png"):
                candidates.append("docs/images/6-images/7.1-1.png")
            for candidate in candidates:
                item = tree_by_path.get(candidate)
                if item and candidate not in seen:
                    dependencies.append(item)
                    seen.add(candidate)
                    break
    return dependencies


def extract_local_image_references(text: str) -> list[str]:
    references = []
    for pattern in IMAGE_REF_PATTERNS:
        for match in pattern.finditer(text):
            target = match.group(1).strip()
            if target.startswith(("http://", "https://", "data:", "#")):
                continue
            target = urllib.parse.unquote(target.split("#", 1)[0].split("?", 1)[0]).strip()
            if not target:
                continue
            suffix = Path(target).suffix.lower()
            if suffix in LOCAL_IMAGE_SUFFIXES:
                references.append(target)
    return references


def normalize_repo_reference(markdown_repo_path: str, reference: str) -> str:
    base = Path(markdown_repo_path).parent
    parts = []
    for part in (base / reference).as_posix().split("/"):
        if part in {"", "."}:
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    return "/".join(parts)


def should_skip_path(path: str) -> bool:
    lowered = path.lower()
    skip_parts = {
        ".github/",
        "__pycache__/",
        ".git/",
        "node_modules/",
        "site-packages/",
        ".ipynb_checkpoints/",
    }
    return any(part in lowered for part in skip_parts)


def score_path(path: str, size: int) -> int:
    lowered = path.lower()
    name = Path(path).name.lower()
    score = 0
    if name in {"readme.md", "readme_cn.md", "index.md"}:
        score += 120
    if lowered.startswith(("docs/", "document/", "docs_cn/", "notebook/", "chapter", "content/")):
        score += 60
    if re.search(r"chapter|第|chap|task|教程|课程|notebook|docs|docs", lowered):
        score += 35
    if Path(path).suffix.lower() == ".md":
        score += 30
    if Path(path).suffix.lower() == ".pdf":
        score += 22
    if Path(path).suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".svg"}:
        score += 10
    if size < 20_000:
        score += 6
    return score


def download_file(repo: RepoInfo, path: str, target: Path) -> None:
    raw_url = f"https://raw.githubusercontent.com/{repo.full_name}/{repo.default_branch}/{quote_path(path)}"
    request = urllib.request.Request(raw_url, headers={"User-Agent": "LLM-Wiki-Studio-Test-Prep"})
    with urllib.request.urlopen(request, timeout=60) as response:
        content = response.read(MAX_FILE_BYTES + 1)
    if len(content) > MAX_FILE_BYTES:
        raise ValueError("file is larger than limit")
    target.write_bytes(content)


def github_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "LLM-Wiki-Studio-Test-Prep"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"GitHub request failed: {exc.code} {url}") from exc


def quote_path(path: str) -> str:
    return "/".join(urllib.parse.quote(part) for part in path.split("/"))


def safe_path(path: str) -> Path:
    safe_parts = []
    for part in Path(path).parts:
        cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", part).strip(" .")
        safe_parts.append(cleaned or "untitled")
    return Path(*safe_parts)


def render_focus_document(repo: RepoInfo, files: list[dict]) -> str:
    lines = [
        f"# {repo.name} 考试重点说明",
        "",
        "## 考试目标",
        "",
        "请基于本课程原始资料和已构建 Wiki，生成适合期末复习的知识清单。",
        "",
        "## 覆盖要求",
        "",
        "- 及格档：覆盖课程中最核心、最高频、最基础的 80% 知识。",
        "- 高分档：覆盖课程结构、关键概念、实践流程、易混点和综合应用。",
        "",
        "## 推荐重点",
        "",
        "- 优先识别课程章节结构。",
        "- 优先保留定义、方法、流程、公式、案例、实践步骤。",
        "- 对不确定或资料证据不足的内容标记为有可能考。",
        "- 默认输出格式：知识点名称~必考/有可能考。",
        "",
        "## 本次抽样资料",
        "",
    ]
    lines.extend(f"- {item['path']}" for item in files)
    return "\n".join(lines) + "\n"


def render_evaluation_prompt(repo: RepoInfo) -> str:
    return f"""# Agent 评分说明

你是 LLM Wiki Studio 的评测 Agent。请读取本课程的原始资料、生成后的 Wiki、复习资料和来源关系图，对本次构建结果打分。

## 被测课程

- 仓库：{repo.full_name}
- Stars：{repo.stars}
- 地址：{repo.html_url}
- 简介：{repo.description}

## 评分维度

每项 0-10 分，最后给出总分和改进建议。

1. Wiki 结构完整性：是否能从资料中沉淀出章节、概念、方法或实践页面。
2. 来源忠实度：Wiki 和复习资料是否能追溯到原始资料章节，是否避免编造。
3. 重点覆盖率：复习资料是否覆盖核心知识，及格档和高分档区分是否合理。
4. 可读性：非开发者是否能理解页面和复习资料。
5. Agent 可用性：对话回答是否能基于知识库回答，并指出来源或不确定性。

## 输出格式

```md
# 评测结果

- 总分：x/50
- 结论：通过/需要迭代/不可用

## 分项评分

- Wiki 结构完整性：x/10，理由...
- 来源忠实度：x/10，理由...
- 重点覆盖率：x/10，理由...
- 可读性：x/10，理由...
- Agent 可用性：x/10，理由...

## 主要问题

- ...

## 下一轮优化建议

- ...
```
"""


def render_course_readme(index: int, repo: RepoInfo, files: list[dict]) -> str:
    return f"""# 测试课程 {index:02d}: {repo.name}

来源：{repo.html_url}

Stars：{repo.stars}

简介：{repo.description}

## 使用方法

1. 在 LLM Wiki Studio 中创建课程：`{repo.name}`。
2. 上传 `raw-materials/` 中的文件作为原始资料。
3. 在「与 Agent 对话」中点击「构建Wiki」。
4. 进入「生成复习资料」，上传 `focus-documents/考试重点说明.md`。
5. 运行生成后，用 `evaluation/Agent评分说明.md` 作为评测提示词，让 Agent 对结果打分。

## 抽样文件数量

{len(files)} 个。
"""


def write_overview(repos: list[RepoInfo]) -> None:
    lines = [
        "# LLM Wiki Studio Datawhale 测试集",
        "",
        "本测试集按 GitHub 当前 star 数选取 DatawhaleChina 前 5 个教程仓库。",
        "",
        "## 仓库列表",
        "",
    ]
    for index, repo in enumerate(repos, start=1):
        lines.append(f"{index}. {repo.full_name} - {repo.stars} stars - {repo.html_url}")
    lines.extend(
        [
            "",
            "## 测试方式",
            "",
            "每个课程目录包含：",
            "",
            "- `raw-materials/`：上传到 LLM Wiki Studio 的原始资料。",
            "- `focus-documents/`：生成复习资料时上传的重点文档。",
            "- `evaluation/Agent评分说明.md`：让 Agent 作为评测者打分的统一提示词。",
            "",
        ]
    )
    (TARGET_ROOT / "README.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
