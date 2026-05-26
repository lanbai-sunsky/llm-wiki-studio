from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from zipfile import ZIP_DEFLATED, ZipFile


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
WORKSPACES_DIR = DATA_DIR / "workspaces"
EXPORTS_DIR = DATA_DIR / "exports"
STATE_PATH = DATA_DIR / "state.json"
SETTINGS_PATH = DATA_DIR / "settings.json"

TEXT_FORMATS = {".md", ".markdown", ".txt", ".canvas", ".base", ""}
IMAGE_FORMATS = {".avif", ".bmp", ".gif", ".jpeg", ".jpg", ".png", ".svg", ".webp"}
PDF_FORMATS = {".pdf"}
AUDIO_FORMATS = {".flac", ".m4a", ".mp3", ".ogg", ".wav", ".webm", ".3gp"}
VIDEO_FORMATS = {".mkv", ".mov", ".mp4", ".ogv"}
SUPPORTED_FORMATS = TEXT_FORMATS | IMAGE_FORMATS | PDF_FORMATS | AUDIO_FORMATS | VIDEO_FORMATS
RAW_DIR = "原始资料"
WIKI_DIR = "已创建的Wiki"
REVIEW_DIR = "复习资料"
SYSTEM_DIR = ".llm-wiki"
METADATA_DIR = f"{SYSTEM_DIR}/metadata"
PARSED_DIR = f"{SYSTEM_DIR}/parsed"
ARCHIVE_DIR = f"{SYSTEM_DIR}/archive"
REVIEW_FOCUS_DIR = f"{REVIEW_DIR}/重点文档"
REVIEW_DOC_NAME = "复习资料.md"
EVALUATION_DOC_NAME = "评测报告.md"
ALLOWED_PAGE_TYPES = {
    "course-overview",
    "learning-path",
    "source-map",
    "theme",
    "case",
    "concept",
    "method",
    "question",
    "source-attachment",
}
CATEGORY_SCAFFOLDS = (
    ("课程", "课程入口", "放置课程总览、学习路线、来源地图等入口页面。"),
    ("专题", "专题页", "放置跨章节主题、模块化知识和综合说明。"),
    ("概念", "概念页", "放置稳定术语、关键概念和定义澄清。"),
    ("方法", "方法页", "放置步骤、流程、实践方法和操作规范。"),
    ("案例", "案例页", "放置项目案例、实验流程和可复现实践。"),
    ("问题", "问题页", "放置待确认问题、冲突点和问答沉淀。"),
    ("附件", "附件页", "放置图片、PDF、音频、视频等附件证据的说明页。"),
)
WIKI_METADATA_FILES = {
    "pages.json",
    "graph.json",
    "quality.json",
    "lint.json",
    "repairs.json",
    "semantic_quality.json",
    "wiki_plan.json",
    "semantic_improvement.json",
}
WIKI_ROOT_ARTIFACT_FILES = {
    "Wiki 语义质量评估.md",
}
LEGACY_PAGE_TYPE_MAP = {
    "synthesis": "theme",
    "chapter": "theme",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_dirs() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    workspace_base_dir().mkdir(parents=True, exist_ok=True)
    EXPORTS_DIR.mkdir(exist_ok=True)
    archive_base_dir().mkdir(parents=True, exist_ok=True)


def archive_base_dir() -> Path:
    return DATA_DIR / "archives"


def default_settings() -> dict:
    return {
        "workspaceRoot": str(WORKSPACES_DIR),
        "llmBaseUrl": "https://xiaojiapi.com/v1",
        "llmModel": "gpt5.5",
        "llmApiKey": "",
    }


def load_settings() -> dict:
    DATA_DIR.mkdir(exist_ok=True)
    if not SETTINGS_PATH.exists():
        return default_settings()
    settings = default_settings()
    try:
        loaded = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return settings
    if isinstance(loaded, dict):
        settings.update({key: value for key, value in loaded.items() if value not in {None, ""}})
    return settings


def save_settings(settings: dict) -> dict:
    DATA_DIR.mkdir(exist_ok=True)
    current = load_settings()
    if "workspaceRoot" in settings:
        raw_root = str(settings["workspaceRoot"]).strip().strip('"').strip("'")
        if not raw_root:
            raise ValueError("工作目录不能为空。")
        root = Path(raw_root).expanduser()
        root.mkdir(parents=True, exist_ok=True)
        current["workspaceRoot"] = str(root)
    if "llmBaseUrl" in settings:
        current["llmBaseUrl"] = str(settings["llmBaseUrl"]).strip().rstrip("/") or current.get("llmBaseUrl", "")
    if "llmModel" in settings:
        current["llmModel"] = str(settings["llmModel"]).strip() or current.get("llmModel", "")
    if "llmApiKey" in settings:
        raw_key = str(settings["llmApiKey"]).strip()
        if raw_key and raw_key != "__KEEP__":
            current["llmApiKey"] = raw_key
    if settings.get("clearLlmApiKey"):
        current["llmApiKey"] = ""
    SETTINGS_PATH.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    return current


def public_settings(settings: dict | None = None) -> dict:
    settings = settings or load_settings()
    api_key = settings.get("llmApiKey") or ""
    env_key = env_api_key(prefer_deepseek="deepseek" in str(settings.get("llmBaseUrl", "")).lower())
    return {
        **settings,
        "llmApiKey": "",
        "llmApiKeyConfigured": bool(api_key or env_key),
        "llmApiKeySource": "本地设置" if api_key else ("环境变量" if env_key else "未配置"),
    }


def llm_settings() -> dict:
    import os

    settings = load_settings()
    base_url = settings.get("llmBaseUrl") or os.environ.get("LLM_BASE_URL") or os.environ.get("OPENAI_BASE_URL") or "https://xiaojiapi.com/v1"
    env_key = env_api_key(prefer_deepseek="deepseek" in base_url.lower())
    return {
        "apiKey": env_key if "deepseek" in base_url.lower() else (settings.get("llmApiKey") or env_key),
        "baseUrl": base_url,
        "model": settings.get("llmModel") or os.environ.get("LLM_WIKI_MODEL") or os.environ.get("LLM_MODEL") or os.environ.get("MODEL_NAME") or "gpt5.5",
    }


def env_api_key(*, prefer_deepseek: bool = False) -> str:
    import os

    names = ("DEEPSEEK_API_KEY", "XIAOJI_API_KEY", "LLM_API_KEY", "OPENAI_API_KEY")
    if not prefer_deepseek:
        names = ("XIAOJI_API_KEY", "LLM_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY")
    for key in names:
        value = os.environ.get(key)
        if value:
            return value
        if os.name == "nt":
            scoped_value = windows_env_value(key)
            if scoped_value:
                return scoped_value
    return ""


def windows_env_value(name: str) -> str:
    import os

    if os.name != "nt":
        return ""
    try:
        import subprocess

        for scope in ("User", "Machine"):
            command = [
                "powershell",
                "-NoProfile",
                "-Command",
                f"[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; [Environment]::GetEnvironmentVariable('{name}', '{scope}')",
            ]
            completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", timeout=3)
            value = completed.stdout.strip()
            if value:
                return value
    except Exception:
        return ""
    return ""


def workspace_base_dir() -> Path:
    return Path(load_settings()["workspaceRoot"]).expanduser()


def safe_name(value: str, fallback: str = "untitled") -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", value).strip(" .")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return (cleaned or fallback)[:120]


def safe_upload_relative_path(value: str, fallback: str = "source.txt") -> Path:
    normalized = str(value or fallback).replace("\\", "/").strip("/")
    parts = []
    for part in normalized.split("/"):
        if part in {"", ".", ".."}:
            continue
        parts.append(safe_name(part, fallback))
    if not parts:
        parts = [fallback]
    return Path(*parts)


def unique_upload_relative_path(base: Path, relative: Path) -> Path:
    candidate = relative
    counter = 2
    while (base / candidate).exists():
        candidate = relative.with_name(f"{relative.stem} ({counter}){relative.suffix}")
        counter += 1
    return candidate


def slug(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9\u4e00-\u9fa5]+", "-", value).strip("-").lower()
    return cleaned or uuid.uuid4().hex[:8]


def unique_workspace_dir(base_dir: Path, name: str) -> Path:
    base_name = slug(name)
    candidate = base_dir / base_name
    counter = 2
    while candidate.exists():
        candidate = base_dir / f"{base_name} ({counter})"
        counter += 1
    return candidate


def load_state() -> dict:
    ensure_dirs()
    if not STATE_PATH.exists():
        return {"workspaces": [], "agentRuns": []}
    return json.loads(STATE_PATH.read_text(encoding="utf-8"))


def save_state(state: dict) -> None:
    ensure_dirs()
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def create_workspace(name: str, description: str = "") -> dict:
    state = load_state()
    workspace_id = uuid.uuid4().hex
    root = unique_workspace_dir(workspace_base_dir(), name)
    initialize_workspace_skeleton(root, name=name, reset_wiki=False, reason="创建课程", archive_key=workspace_id)
    workspace = {
        "id": workspace_id,
        "name": name,
        "description": description,
        "path": str(root),
        "createdAt": now_iso(),
        "updatedAt": now_iso(),
    }
    state["workspaces"].append(workspace)
    save_state(state)
    return workspace


def list_workspaces() -> list[dict]:
    return load_state()["workspaces"]


def get_workspace(workspace_id: str) -> dict:
    for workspace in list_workspaces():
        if workspace["id"] == workspace_id:
            return workspace
    raise KeyError(f"Workspace not found: {workspace_id}")


def delete_workspace(workspace_id: str) -> dict:
    state = load_state()
    workspace = None
    remaining = []
    for item in state.get("workspaces", []):
        if item["id"] == workspace_id:
            workspace = item
        else:
            remaining.append(item)
    if workspace is None:
        raise KeyError(f"Workspace not found: {workspace_id}")
    root = Path(workspace["path"]).resolve()
    if root.exists():
        shutil.rmtree(root)
    state["workspaces"] = remaining
    state["agentRuns"] = [run for run in state.get("agentRuns", []) if run.get("workspaceId") != workspace_id]
    save_state(state)
    return {"ok": True, "deleted": workspace}


def initialize_workspace_skeleton(
    root: Path,
    *,
    name: str | None = None,
    reset_wiki: bool = False,
    reason: str = "初始化Wiki",
    archive_key: str | None = None,
) -> dict:
    course_name = name or root.name
    archived_to = ""
    migrated_archives = migrate_internal_archives(root, archive_key or root.name)
    if reset_wiki:
        archived_to = archive_existing_wiki(root, archive_key or root.name)
        remove_wiki_tree(root)

    for relative in (RAW_DIR, WIKI_DIR, REVIEW_DIR, REVIEW_FOCUS_DIR, METADATA_DIR):
        (root / relative).mkdir(parents=True, exist_ok=True)
    for folder, _, _ in CATEGORY_SCAFFOLDS:
        (root / WIKI_DIR / folder).mkdir(parents=True, exist_ok=True)

    if not (root / "AGENTS.md").exists() or reset_wiki:
        (root / "AGENTS.md").write_text(_agents_md(course_name), encoding="utf-8")
    write_scaffold_indexes(root)
    raw_files = write_raw_file_manifest(root)
    write_scaffold_index(root, course_name, raw_files)

    existing_pages = read_json(root / METADATA_DIR / "pages.json", [])
    existing_content_pages = [page for page in existing_pages if page.get("type") != "scaffold-index" and page.get("status") != "scaffold"]
    pages = existing_pages
    if reset_wiki or not existing_pages or not existing_content_pages:
        pages = scaffold_page_records(root)
        write_json(root / METADATA_DIR / "pages.json", pages)
        write_json(root / METADATA_DIR / "graph.json", build_graph(pages, read_json(root / METADATA_DIR / "sources.json", [])))
        write_json(
            root / METADATA_DIR / "quality.json",
            {
                "sourceFileCount": len(raw_files),
                "usedSourceFileCount": 0,
                "sourceCoveragePercent": 0,
                "wikiPageCount": 0,
                "scaffoldPageCount": len(pages),
                "avgRefsPerPage": 0,
                "issueCount": 0,
                "needsReviewPages": 0,
                "needsReviewTitles": [],
                "latestBuildUsedModel": False,
                "latestBuildStatus": "",
                "initializedOnly": True,
                "updatedAt": now_iso(),
            },
        )
    append_log(root, f"{reason}：已创建 Wiki 骨架，不读取原始资料正文，不调用模型。")
    return {"pages": pages, "rawFiles": raw_files, "archivedTo": archived_to, "migratedArchives": migrated_archives}


def wiki_disk_state(root: Path) -> dict:
    wiki_root = root / WIKI_DIR
    scaffold_names = {f"{folder}索引.md" for folder, _, _ in CATEGORY_SCAFFOLDS}
    scaffold_names.add("原始资料文件清单.md")
    markdown_files = []
    content_files = []
    if wiki_root.exists():
        for path in sorted(wiki_root.rglob("*.md")):
            relative = str(path.relative_to(root)).replace("\\", "/")
            markdown_files.append(relative)
            if path.name not in scaffold_names:
                content_files.append(relative)
    return {
        "markdownFileCount": len(markdown_files),
        "contentFileCount": len(content_files),
        "markdownFiles": markdown_files[:80],
        "contentFiles": content_files[:80],
    }


def archive_existing_wiki(root: Path, archive_key: str | None = None) -> str:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    archive_root = unique_archive_path(archive_base_dir() / safe_name(archive_key or root.name) / f"wiki-{timestamp}")
    archive_root.mkdir(parents=True, exist_ok=True)
    wiki_root = root / WIKI_DIR
    if wiki_root.exists():
        shutil.move(str(wiki_root), str(archive_root / WIKI_DIR))
    for filename in WIKI_ROOT_ARTIFACT_FILES:
        source = root / filename
        if source.exists():
            shutil.move(str(source), str(archive_root / filename))
    metadata_root = root / METADATA_DIR
    metadata_archive = archive_root / "metadata"
    metadata_archive.mkdir(parents=True, exist_ok=True)
    for filename in WIKI_METADATA_FILES:
        source = metadata_root / filename
        if source.exists():
            shutil.move(str(source), str(metadata_archive / filename))
    return str(archive_root)


def remove_wiki_tree(root: Path) -> None:
    wiki_root = root / WIKI_DIR
    if wiki_root.is_dir():
        shutil.rmtree(wiki_root)
    elif wiki_root.exists():
        wiki_root.unlink()


def migrate_internal_archives(root: Path, archive_key: str | None = None) -> list[str]:
    internal_archive = root / ARCHIVE_DIR
    migrated = []
    if internal_archive.exists():
        for source in sorted(internal_archive.iterdir()):
            target = unique_archive_path(archive_base_dir() / safe_name(archive_key or root.name) / source.name)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(target))
            migrated.append(str(target))
        remove_empty_parents(internal_archive, stop=root / SYSTEM_DIR)
    for relative in (PARSED_DIR, f"{SYSTEM_DIR}/legacy-wiki-fragments"):
        source = root / relative
        if not source.exists():
            continue
        target = unique_archive_path(archive_base_dir() / safe_name(archive_key or root.name) / source.name)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(target))
        migrated.append(str(target))
    return migrated


def unique_archive_path(path: Path) -> Path:
    candidate = path
    counter = 2
    while candidate.exists():
        candidate = path.with_name(f"{path.name}-{counter}")
        counter += 1
    return candidate


def remove_empty_parents(path: Path, *, stop: Path) -> None:
    current = path
    stop = stop.resolve()
    while current.exists() and current.resolve() != stop:
        try:
            current.rmdir()
        except OSError:
            break
        current = current.parent


def write_scaffold_indexes(root: Path) -> None:
    for folder, label, description in CATEGORY_SCAFFOLDS:
        target = root / WIKI_DIR / folder / f"{folder}索引.md"
        target.write_text(render_category_index(folder, label, description), encoding="utf-8")


def render_category_index(folder: str, label: str, description: str) -> str:
    return "\n".join(
        [
            "---",
            f"title: {folder}索引",
            "type: scaffold-index",
            "status: scaffold",
            "tags:",
            "  - wiki",
            "  - scaffold",
            "---",
            "",
            f"# {folder}索引",
            "",
            "## 用途",
            "",
            description,
            "",
            "## 当前内容",
            "",
            f"- 暂无{label}。",
            "",
            "## 维护规则",
            "",
            "- 这里是对话学习记忆的分类入口，不需要单独质量评分。",
            "- 用户与 Claude Code 对话后，Agent 通过 `learning-memory-save` skill 将新理解整理到合适页面。",
        ]
    ).rstrip() + "\n"


def write_raw_file_manifest(root: Path) -> list[dict]:
    files = scan_raw_files(root)
    target = root / WIKI_DIR / "课程" / "原始资料文件清单.md"
    lines = [
        "---",
        "title: 原始资料文件清单",
        "type: scaffold-index",
        "status: scaffold",
        "tags:",
        "  - wiki",
        "  - scaffold",
        "---",
        "",
        "# 原始资料文件清单",
        "",
        "本页只扫描 `原始资料/` 的目录结构和文件名，不读取文件正文，不进行解析、切块或模型总结。",
        "",
        "## 文件列表",
        "",
    ]
    if files:
        lines.extend(f"- `{item['relativePath']}`" for item in files)
    else:
        lines.append("- 暂无原始资料。")
    target.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return files


def scan_raw_files(root: Path) -> list[dict]:
    raw_root = root / RAW_DIR
    raw_root.mkdir(parents=True, exist_ok=True)
    files = []
    for path in sorted(raw_root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(raw_root)
        files.append(
            {
                "name": path.name,
                "relativePath": str(relative).replace("\\", "/"),
                "folder": str(relative.parent).replace("\\", "/") if str(relative.parent) != "." else "",
                "format": path.suffix.lower().lstrip(".") or "text",
            }
        )
    return files


def write_scaffold_index(root: Path, course_name: str, raw_files: list[dict]) -> None:
    lines = [
        f"# {course_name}",
        "",
        "## Wiki 状态",
        "",
        "- 当前状态：课程目录已创建，等待对话学习记忆沉淀。",
        "- `原始资料/` 是事实来源，由用户维护。",
        "- `已创建的Wiki/` 是 Agent 与用户对话后的学习记忆层。",
        "- `AGENTS.md` 是 Claude Code 学习控制台规则。",
        "",
        "## 下一步",
        "",
        "1. 上传或整理 `原始资料/`。",
        "2. 在 Claude Code 控制台里对话学习。",
        "3. 对话结束后使用 `learning-memory-save` skill 保存新理解。",
        "4. 需要考试输出时使用 `review-materials` skill 生成复习资料。",
        "",
        "## 分类入口",
        "",
    ]
    lines.extend(f"- [[{folder}索引]]：{description}" for folder, _, description in CATEGORY_SCAFFOLDS)
    lines.extend(["", "## 原始资料", ""])
    if raw_files:
        lines.extend(f"- `{item['relativePath']}`" for item in raw_files)
    else:
        lines.append("- 暂无原始资料。")
    lines.extend(["", "## Wiki 页面", "", "- 尚未生成知识正文。"])
    (root / "index.md").write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def scaffold_page_records(root: Path) -> list[dict]:
    pages = []
    for folder, _, _ in CATEGORY_SCAFFOLDS:
        path = root / WIKI_DIR / folder / f"{folder}索引.md"
        pages.append(
            {
                "id": uuid.uuid4().hex,
                "title": f"{folder}索引",
                "type": "scaffold-index",
                "path": str(path.relative_to(root)).replace("\\", "/"),
                "sourceIds": [],
                "sourceRefs": [],
                "status": "scaffold",
                "updatedAt": now_iso(),
            }
        )
    manifest = root / WIKI_DIR / "课程" / "原始资料文件清单.md"
    pages.append(
        {
            "id": uuid.uuid4().hex,
            "title": "原始资料文件清单",
            "type": "scaffold-index",
            "path": str(manifest.relative_to(root)).replace("\\", "/"),
            "sourceIds": [],
            "sourceRefs": [],
            "status": "scaffold",
            "updatedAt": now_iso(),
        }
    )
    return pages


def workspace_root(workspace_id: str) -> Path:
    return Path(get_workspace(workspace_id)["path"])


def upload_source(workspace_id: str, filename: str, content: bytes) -> dict:
    root = workspace_root(workspace_id)
    source_id = uuid.uuid4().hex
    relative_name = safe_upload_relative_path(filename, "source.txt")
    original_name = safe_name(relative_name.name, "source.txt")
    suffix = relative_name.suffix.lower()
    if suffix not in SUPPORTED_FORMATS:
        raise ValueError(f"Unsupported file format: {suffix or 'unknown'}")
    stored_relative = unique_upload_relative_path(root / RAW_DIR, relative_name)
    upload_path = root / RAW_DIR / stored_relative
    upload_path.parent.mkdir(parents=True, exist_ok=True)
    upload_path.write_bytes(content)
    source_kind = source_kind_for_suffix(suffix)
    sources = read_json(root / METADATA_DIR / "sources.json", [])
    record = {
        "id": source_id,
        "fileName": original_name,
        "relativePath": str(relative_name).replace("\\", "/"),
        "format": suffix.lstrip(".") or "text",
        "sourceKind": source_kind,
        "storedPath": str(upload_path.relative_to(root)).replace("\\", "/"),
        "parsedPath": "",
        "chunkCount": 0,
        "createdAt": now_iso(),
        "chunks": [],
    }
    sources.append(record)
    write_json(root / METADATA_DIR / "sources.json", sources)
    update_raw_index(root, sources)
    append_log(root, f"已上传原始资料 `{original_name}`。")
    return record


def update_raw_index(root: Path, sources: list[dict] | None = None) -> None:
    legacy_index = root / "原始资料.md"
    if legacy_index.exists():
        legacy_index.unlink()


def save_review_focus_documents(workspace_id: str, files: list[tuple[str, bytes]], *, replace: bool = True) -> list[dict]:
    root = workspace_root(workspace_id)
    focus_dir = root / REVIEW_FOCUS_DIR
    focus_dir.mkdir(parents=True, exist_ok=True)
    if replace:
        for path in focus_dir.glob("*"):
            if path.is_file():
                path.unlink()
    records = [] if replace else read_json(root / METADATA_DIR / "review-focus.json", [])
    for filename, content in files:
        doc_id = uuid.uuid4().hex
        original_name = safe_name(Path(filename).name, "focus.txt")
        suffix = Path(original_name).suffix.lower()
        if suffix not in SUPPORTED_FORMATS:
            raise ValueError(f"Unsupported file format: {suffix or 'unknown'}")
        stored_name = f"{doc_id}-{original_name}"
        focus_path = focus_dir / stored_name
        focus_path.write_bytes(content)
        parsed_text = parse_source_text(original_name, content)
        record = {
            "id": doc_id,
            "fileName": original_name,
            "format": suffix.lstrip(".") or "text",
            "sourceKind": source_kind_for_suffix(suffix),
            "storedPath": str(focus_path.relative_to(root)).replace("\\", "/"),
            "summary": summarize_focus_text(parsed_text),
            "createdAt": now_iso(),
        }
        records.append(record)
    write_json(root / METADATA_DIR / "review-focus.json", records)
    append_log(root, f"已上传 {len(files)} 份复习重点文档。")
    return records


def parse_source_text(filename: str, content: bytes) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix in TEXT_FORMATS:
        for encoding in ("utf-8-sig", "utf-8", "gb18030"):
            try:
                return content.decode(encoding)
            except UnicodeDecodeError:
                continue
    return attachment_note(filename, suffix)


def source_kind_for_suffix(suffix: str) -> str:
    if suffix in {".md", ".markdown", ".txt", ""}:
        return "note"
    if suffix in {".canvas", ".base"}:
        return "canvas-base"
    if suffix in IMAGE_FORMATS:
        return "image"
    if suffix in PDF_FORMATS:
        return "pdf"
    if suffix in AUDIO_FORMATS:
        return "audio"
    if suffix in VIDEO_FORMATS:
        return "video"
    return "attachment"


def attachment_note(filename: str, suffix: str) -> str:
    kind = source_kind_for_suffix(suffix)
    label = {
        "image": "图片附件",
        "pdf": "PDF 附件",
        "audio": "音频附件",
        "video": "视频附件",
        "canvas-base": "Canvas / Bases 文件",
    }.get(kind, "附件")
    return (
        f"# {Path(filename).stem}\n\n"
        f"`{filename}` 已作为 Obsidian 原生友好的{label}保存。\n\n"
        f"它会保留在 `{RAW_DIR}/` 中，并可以被 Wiki 页面引用。"
        "如果需要进一步语义解析，可以接入 OCR、PDF 提取、音频转写或视频转写流程。"
    )


def chunk_text(text: str, *, source_id: str, file_name: str, stored_path: str = "", max_chars: int = 1600) -> list[dict]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    sections = markdown_sections(normalized)
    if not sections:
        sections = [{"heading": Path(file_name).stem or "原始文件", "level": 1, "text": normalized.strip(), "lineStart": 1, "lineEnd": len(normalized.splitlines())}]
    chunks: list[dict] = []
    for section in sections:
        for part in split_section_text(section["text"], max_chars=max_chars):
            chunks.append(
                make_chunk(
                    source_id,
                    file_name,
                    len(chunks),
                    part,
                    section=section["heading"],
                    heading=section["heading"],
                    level=section["level"],
                    line_start=section["lineStart"],
                    line_end=section["lineEnd"],
                    stored_path=stored_path,
                )
            )
    return chunks


def markdown_sections(text: str) -> list[dict]:
    lines = text.splitlines()
    sections: list[dict] = []
    current_heading = ""
    current_level = 0
    current_start = 1
    current_lines: list[str] = []
    heading_stack: list[tuple[int, str]] = []

    def flush(end_line: int) -> None:
        body = "\n".join(current_lines).strip()
        if body:
            sections.append(
                {
                    "heading": current_heading or "文档开头",
                    "level": current_level or 1,
                    "text": body,
                    "lineStart": current_start,
                    "lineEnd": max(current_start, end_line),
                }
            )

    for line_number, line in enumerate(lines, start=1):
        match = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if match:
            flush(line_number - 1)
            level = len(match.group(1))
            title = clean_heading(match.group(2))
            heading_stack = [(item_level, item_title) for item_level, item_title in heading_stack if item_level < level]
            heading_stack.append((level, title))
            current_heading = " > ".join(item_title for _, item_title in heading_stack)
            current_level = level
            current_start = line_number
            current_lines = [line]
        else:
            current_lines.append(line)
    flush(len(lines))
    return sections


def split_section_text(text: str, *, max_chars: int) -> list[str]:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    if not paragraphs and text.strip():
        paragraphs = [text.strip()]
    parts: list[str] = []
    current: list[str] = []
    current_len = 0
    for paragraph in paragraphs:
        if current and current_len + len(paragraph) > max_chars:
            parts.append("\n\n".join(current))
            current = []
            current_len = 0
        if len(paragraph) > max_chars:
            if current:
                parts.append("\n\n".join(current))
                current = []
                current_len = 0
            parts.extend(paragraph[index : index + max_chars].strip() for index in range(0, len(paragraph), max_chars))
            continue
        current.append(paragraph)
        current_len += len(paragraph)
    if current:
        parts.append("\n\n".join(current))
    return [part for part in parts if part]


def clean_heading(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"`|\[|\]|\(|\)", "", value)).strip()[:120] or "未命名章节"


def make_chunk(
    source_id: str,
    file_name: str,
    index: int,
    text: str,
    *,
    section: str,
    heading: str,
    level: int,
    line_start: int,
    line_end: int,
    stored_path: str,
) -> dict:
    return {
        "id": f"{source_id}_{index:03d}",
        "sourceId": source_id,
        "fileName": file_name,
        "storedPath": stored_path,
        "index": index,
        "section": section or f"第 {index + 1} 节",
        "heading": heading,
        "level": level,
        "lineStart": line_start,
        "lineEnd": line_end,
        "text": text,
        "tokenEstimate": max(1, len(text) // 4),
    }


def render_parsed_markdown(file_name: str, chunks: list[dict], *, source_kind: str) -> str:
    lines = [
        "---",
        f"title: {Path(file_name).stem}",
        f"source_kind: {source_kind}",
        f"source_file: {file_name}",
        "---",
        "",
        f"# {Path(file_name).stem}",
        "",
        f"Source file: `{file_name}`",
        f"Obsidian native kind: `{source_kind}`",
        "",
    ]
    for chunk in chunks:
        lines.extend(
            [
                f"## {chunk.get('section') or '来源块 ' + str(chunk['index'] + 1)}",
                "",
                f"- chunk_id: `{chunk['id']}`",
                f"- lines: {chunk.get('lineStart', '-')}-{chunk.get('lineEnd', '-')}",
                "",
                chunk["text"],
                "",
            ]
        )
    return "\n".join(lines).strip() + "\n"


def run_agent(workspace_id: str, action: str, params: dict | None = None) -> dict:
    root = workspace_root(workspace_id)
    params = params or {}
    if action in {"build", "improve", "evaluate"} and "requireLlmAgent" not in params:
        params["requireLlmAgent"] = True
    run = {
        "id": uuid.uuid4().hex,
        "workspaceId": workspace_id,
        "action": action,
        "params": params,
        "status": "running",
        "createdAt": now_iso(),
        "finishedAt": None,
        "steps": [],
    }
    if action not in {"initialize", "build", "ingest", "lint", "review", "improve", "evaluate"}:
        raise ValueError(f"Unsupported agent action: {action}")

    state = load_state()
    try:
        if params:
            add_step(run, "读取参数", describe_agent_params(action, params))
        if action == "initialize":
            workspace = get_workspace(workspace_id)
            result = initialize_workspace_skeleton(
                root,
                name=workspace.get("name") or root.name,
                reset_wiki=bool(params.get("resetWiki")),
                reason="初始化Wiki",
                archive_key=workspace_id,
            )
            add_step(run, "初始化 Wiki", f"已创建纯骨架：{len(result['pages'])} 个分类/清单索引。")
            if result.get("archivedTo"):
                add_step(run, "归档旧 Wiki", f"旧 Wiki 已归档到 `{result['archivedTo']}`。")
            if result.get("migratedArchives"):
                add_step(run, "迁移历史归档", f"已将 {len(result['migratedArchives'])} 个课程内归档移出 Obsidian 可见目录。")
            add_step(run, "扫描原始资料文件名", f"已列出 {len(result['rawFiles'])} 个文件；未读取正文、未调用模型。")
            append_log(root, "初始化Wiki 已完成：仅创建骨架、索引和分类汇总。")
            run["status"] = "completed"
            return run
        if action == "build":
            workspace = get_workspace(workspace_id)
            initialize_workspace_skeleton(root, name=workspace.get("name") or root.name, reset_wiki=False, reason="生成Wiki前补齐骨架", archive_key=workspace_id)
            add_step(run, "补齐 Wiki 骨架", "已确认目录、分类索引和原始资料文件清单存在。")

        from llm_wiki_agent import run_langgraph_agent

        result = run_langgraph_agent(root, action, params)
        for step in result.get("steps", []):
            add_step(run, step.get("name", "Agent 步骤"), step.get("message", ""))
        if result.get("llmEnabled"):
            add_step(run, "LangGraph Agent", "已通过 LangChain / LangGraph 调用模型执行。")
        elif result.get("fallbackReason"):
            add_step(run, "LangGraph Agent", result["fallbackReason"])
        pages = result.get("pages", list_wiki_pages(workspace_id))
        lint = result.get("issues", read_json(root / METADATA_DIR / "lint.json", []))
        append_log(root, f"LangGraph Agent `{action}` 已完成：页面 {len(pages)} 个，待处理问题 {len(lint)} 个。")
        run["status"] = "completed"
        return run
    except Exception as exc:
        message = clean_user_error(str(exc))
        add_step(run, "Agent 运行失败", message)
        append_log(root, f"LangGraph Agent `{action}` 运行失败：{message}")
        run["status"] = "failed"
        run["error"] = message
        raise RuntimeError(message) from exc
    finally:
        run["finishedAt"] = now_iso()
        state["agentRuns"].insert(0, run)
        save_state(state)


def add_step(run: dict, name: str, message: str) -> None:
    run["steps"].append({"name": name, "message": message, "time": now_iso(), "status": "completed"})


def clean_user_error(message: str) -> str:
    if "Service temporarily unavailable" in message or "503" in message:
        return "模型 Agent 生成失败：模型服务暂时不可用，请稍后重试。"
    if "API Key" in message or "鉴权" in message or "401" in message:
        return "模型 Agent 生成失败：模型鉴权失败，请检查 API Key。"
    if "模型名称不可用" in message or "model" in message.lower() and "not found" in message.lower():
        return "模型 Agent 生成失败：模型名称不可用，请检查模型配置。"
    return message.split("{'error'", 1)[0].strip(" -") or "模型 Agent 生成失败，请检查模型配置。"


def describe_agent_params(action: str, params: dict) -> str:
    labels = {
        "updateExisting": "更新已有 Wiki 页面",
        "sourceMap": "生成来源关系图",
        "markUncertain": "标记不确定内容",
        "brokenLinks": "检查断开的 Wiki 链接",
        "missingSources": "检查缺少来源的页面",
        "needsReview": "检查待确认内容",
        "outline": "旧复习提纲选项",
        "qa": "旧问答清单选项",
        "mustKnow": "旧必备知识点选项",
        "examTarget": "考试目标",
        "outputFormat": "输出格式",
        "knowledgeScope": "知识库范围",
        "replaceFocusDocs": "替换本次重点文档",
        "reviewFocusUploaded": "本次上传重点文档数量",
        "requireLlmAgent": "必须使用模型 Agent",
        "resetWiki": "归档旧 Wiki 并重建骨架",
        "includeSourceEvidence": "对照原始资料章节",
        "strictMode": "严格评分",
        "rewriteCorePages": "重写核心页面",
        "createSourceBriefs": "为每份资料生成研读页",
        "targetSemanticScore": "目标语义分",
        "maxSemanticImproveRounds": "最多优化轮数",
        "forceSemanticRewrite": "强制重新改写",
    }
    enabled = [
        f"{labels.get(key, key)}={label_agent_param_value(key, value)}" if not isinstance(value, bool) else labels.get(key, key)
        for key, value in params.items()
        if value
    ]
    if not enabled:
        return f"`{action}` 未启用额外选项。"
    return "已启用：" + "、".join(enabled) + "。"


def label_agent_param_value(key: str, value: object) -> object:
    if key == "knowledgeScope" and value == "all":
        return "全部知识库"
    if key == "examTarget":
        return "高分档" if value == "high" else "及格档"
    return value


def prepare_sources_for_wiki(root: Path, sources: list[dict]) -> list[dict]:
    prepared = []
    for source in sources:
        source = dict(source)
        stored_path = root / source["storedPath"]
        content = stored_path.read_bytes() if stored_path.exists() else b""
        parsed_text = parse_source_text(source["fileName"], content)
        chunks = chunk_text(
            parsed_text,
            source_id=source["id"],
            file_name=source["fileName"],
            stored_path=source.get("storedPath", ""),
        )
        parsed_path = root / PARSED_DIR / f"{source['id']}-{safe_name(Path(source['fileName']).stem)}.md"
        parsed_path.parent.mkdir(parents=True, exist_ok=True)
        parsed_path.write_text(
            render_parsed_markdown(source["fileName"], chunks, source_kind=source.get("sourceKind", "source")),
            encoding="utf-8",
        )
        source["chunks"] = chunks
        source["chunkCount"] = len(chunks)
        source["sections"] = source_sections(chunks)
        source["parsedPath"] = str(parsed_path.relative_to(root)).replace("\\", "/")
        prepared.append(source)
    write_json(root / METADATA_DIR / "sources.json", prepared)
    return prepared


def source_sections(chunks: list[dict]) -> list[dict]:
    sections: list[dict] = []
    seen: set[str] = set()
    for chunk in chunks:
        section = chunk.get("section") or f"第 {chunk.get('index', 0) + 1} 节"
        if section in seen:
            continue
        seen.add(section)
        sections.append(
            {
                "section": section,
                "heading": chunk.get("heading") or section,
                "level": chunk.get("level", 1),
                "lineStart": chunk.get("lineStart"),
                "lineEnd": chunk.get("lineEnd"),
                "chunkIds": [item.get("id") for item in chunks if item.get("section") == section],
            }
        )
    return sections


def build_wiki_pages(root: Path, sources: list[dict]) -> list[dict]:
    pages: list[dict] = []
    clear_scaffold_wiki_files(root)
    text_sources = [source for source in sources if source.get("sourceKind") not in {"image", "pdf", "audio", "video", "canvas-base"}]
    attachment_sources = [source for source in sources if source not in text_sources]
    overview_specs = course_page_specs(text_sources, attachment_sources)
    for spec in overview_specs:
        folder = spec.get("folder", "课程")
        target = root / WIKI_DIR / folder / f"{safe_name(spec['title'])}.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render_synthesis_page(spec), encoding="utf-8")
        pages.append(synthesis_page_record(root, target, spec))
    for source in attachment_sources[:12]:
        title = Path(source["fileName"]).stem
        attachment_path = root / WIKI_DIR / "附件" / f"{safe_name(title)}.md"
        attachment_path.parent.mkdir(parents=True, exist_ok=True)
        attachment_path.write_text(render_attachment_page(title, source), encoding="utf-8")
        pages.append(page_record(root, attachment_path, "source-attachment", title, source))
    write_json(root / METADATA_DIR / "pages.json", pages)
    write_json(root / METADATA_DIR / "graph.json", build_graph(pages, sources))
    return pages


def clear_scaffold_wiki_files(root: Path) -> None:
    pages = read_json(root / METADATA_DIR / "pages.json", [])
    for page in pages:
        if page.get("type") != "scaffold-index" and page.get("status") != "scaffold":
            continue
        path = root / page.get("path", "")
        if path.exists():
            try:
                path.unlink()
            except OSError:
                pass
    for folder, _, _ in CATEGORY_SCAFFOLDS:
        path = root / WIKI_DIR / folder / f"{folder}索引.md"
        if path.exists():
            try:
                path.unlink()
            except OSError:
                pass
    manifest = root / WIKI_DIR / "课程" / "原始资料文件清单.md"
    if manifest.exists():
        try:
            manifest.unlink()
        except OSError:
            pass


def course_page_specs(text_sources: list[dict], attachment_sources: list[dict]) -> list[dict]:
    source_lines = source_outline_lines(text_sources)
    section_lines = source_section_outline_lines(text_sources)
    themes = infer_course_themes(text_sources)
    theme_sources = sources_by_theme(text_sources)
    theme_specs = [
        {
            "title": theme["label"],
            "type": "synthesis",
            "folder": "专题",
            "sources": theme_sources.get(theme["label"], text_sources[:8]),
            "body": [
                "## 主题定位",
                "",
                theme["description"],
                "",
                "## 相关章节",
                "",
                *source_section_outline_lines(theme_sources.get(theme["label"], text_sources[:8]))[:18],
                "",
                "## 来源",
                "",
                *source_outline_lines(theme_sources.get(theme["label"], text_sources[:8]))[:10],
            ],
        }
        for theme in themes[:6]
    ]
    return [
        {
            "title": "课程总览",
            "type": "synthesis",
            "folder": "课程",
            "sources": text_sources[:8],
            "body": [
                "## 课程定位",
                "",
                summarize_course_position(text_sources),
                "",
                "## 推荐入口",
                "",
                "- [[学习路线]]：按先后顺序阅读课程资料。",
                "- [[核心主题]]：查看课程沉淀出的主要知识模块。",
                "- [[资料地图]]：从 Wiki 回到原始资料。",
                "",
                "## 主要资料",
                "",
                *source_lines[:12],
                "",
                "## 章节证据",
                "",
                *section_lines[:16],
            ],
        },
        {
            "title": "学习路线",
            "type": "synthesis",
            "folder": "课程",
            "sources": text_sources[:10],
            "body": [
                "## 建议顺序",
                "",
                "1. 先读总览、README 或课程说明，确认课程目标和受众。",
                "2. 再读环境、安装、硬件平台或准备工作相关资料。",
                "3. 接着选择一个代表性模型、章节或案例完成端到端实践。",
                "4. 最后进入微调、RAG、Agent、评测或复习资料生成等进阶任务。",
                "",
                "## 选择策略",
                "",
                "- 如果文件名包含 `README`、`intro`、`overview`，通常作为入口。",
                "- 如果路径包含 `examples`、`case`、`demo`，适合作为项目练习。",
                "- 如果路径包含 `model`、`chapter`、`docs`，适合作为主题学习材料。",
            ],
        },
        {
            "title": "来源地图",
            "type": "synthesis",
            "folder": "课程",
            "sources": text_sources[:20],
            "body": [
                "## Wiki 编译边界",
                "",
                f"- `{RAW_DIR}/` 是用户维护的事实来源。",
                f"- `{WIKI_DIR}/` 是 Agent 维护的知识编译层。",
                "- 下面只列出章节路径，不复制原文，便于在 Obsidian 中回查。",
                "",
                "## 章节清单",
                "",
                *section_lines[:80],
            ],
        },
        {
            "title": "核心主题",
            "type": "synthesis",
            "folder": "专题",
            "sources": text_sources[:16],
            "body": [
                "## 自动归纳主题",
                "",
                *[f"- **{theme['label']}**：{theme['description']}" for theme in themes],
                "",
                "## 阅读提醒",
                "",
                "这里保留的是课程级主题，而不是把普通词语拆成概念页。后续如果需要更细页面，应该围绕稳定主题手动或由 Agent 合并生成。",
            ],
        },
        {
            "title": "资料地图",
            "type": "synthesis",
            "folder": "课程",
            "sources": text_sources[:20],
            "body": [
                "## 原始资料索引",
                "",
                *source_lines[:60],
                "",
                "## 附件资料",
                "",
                *(f"- `{source.get('storedPath')}`（{source.get('sourceKind')}）" for source in attachment_sources[:30]),
            ],
        },
        *theme_specs,
    ]


def summarize_chunks(chunks: list[dict]) -> str:
    text = clean_excerpt(" ".join(chunk["text"].strip() for chunk in chunks if chunk.get("text")))
    sentences = split_sentences(text)
    return " ".join(sentences[:3])[:600] or "暂未提取到可读文本。"


def source_outline_lines(sources: list[dict]) -> list[str]:
    lines = []
    for source in sources:
        path = source.get("storedPath", "")
        title = source.get("relativePath") or source.get("fileName") or path
        summary = summarize_chunks(source.get("chunks", []))[:160]
        if summary:
            lines.append(f"- `{path}`：{summary}")
        else:
            lines.append(f"- `{path}`")
    return lines or ["- 暂无原始资料。"]


def source_section_outline_lines(sources: list[dict]) -> list[str]:
    lines = []
    for source in sources:
        path = source.get("storedPath", "")
        sections = source.get("sections") or source_sections(source.get("chunks", []))
        if not sections:
            lines.append(f"- `{path}`：原始文件")
            continue
        for section_info in sections[:8]:
            section = section_info.get("section") or "原始文件"
            line_range = format_line_range(section_info)
            lines.append(f"- `{path}` / {section}{line_range}")
    return lines or ["- 暂无可引用章节。"]


def format_line_range(chunk: dict) -> str:
    start = chunk.get("lineStart")
    end = chunk.get("lineEnd")
    if start and end:
        return f"（行 {start}-{end}）"
    return ""


def summarize_course_position(sources: list[dict]) -> str:
    text = clean_excerpt("\n".join(chunk.get("text", "") for source in sources[:6] for chunk in source.get("chunks", [])[:2]))
    sentences = split_sentences(text)
    if sentences:
        return " ".join(sentences[:3])[:500]
    return "本课程由用户上传的原始资料构成，Wiki 负责把分散资料整理为可导航的学习结构。"


def clean_excerpt(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", text)
    text = re.sub(r"\[[^\]]+\]\([^)]+\)", lambda match: match.group(0).split("]", 1)[0].lstrip("["), text)
    text = re.sub(r"`{1,3}", "", text)
    text = re.sub(r"&[a-zA-Z]+;", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def infer_course_themes(sources: list[dict]) -> list[dict]:
    joined = "\n".join((source.get("relativePath") or source.get("fileName") or "") for source in sources).lower()
    checks = [
        ("环境配置", "安装、依赖、硬件平台、驱动和运行环境。", ("env", "环境", "setup", "install", "amd", "ascend", "mlx")),
        ("模型部署", "模型下载、推理验证、服务化部署和调用方式。", ("model", "deploy", "部署", "vllm", "transformers", "gradio")),
        ("微调训练", "LoRA、SFT、Embedding 微调、数据集制作和训练评测。", ("finetune", "fine-tune", "微调", "sft", "lora", "embedding")),
        ("应用案例", "示例项目、Demo、知识库助手、角色模型或垂直场景。", ("example", "examples", "demo", "案例", "langchain", "rag")),
        ("课程文档", "README、章节文档、说明文档和导航入口。", ("readme", "docs", "chapter", "课程", "教程")),
    ]
    themes = [{"label": label, "description": description} for label, description, keys in checks if any(key in joined for key in keys)]
    return themes or [{"label": "课程资料", "description": "围绕用户上传资料形成的主要学习内容。"}]


def sources_by_theme(sources: list[dict]) -> dict[str, list[dict]]:
    mapping = {
        "环境配置": ("env", "环境", "setup", "install", "安装", "硬件", "amd", "ascend", "mlx"),
        "模型部署": ("model", "deploy", "部署", "推理", "vllm", "transformers", "gradio"),
        "微调训练": ("finetune", "fine-tune", "微调", "训练", "sft", "lora", "embedding"),
        "应用案例": ("example", "examples", "demo", "案例", "langchain", "rag", "agent"),
        "课程文档": ("readme", "docs", "chapter", "课程", "教程", "intro", "overview"),
    }
    result: dict[str, list[dict]] = {}
    for label, keys in mapping.items():
        selected = []
        for source in sources:
            haystack = " ".join(
                [
                    source.get("relativePath", ""),
                    source.get("fileName", ""),
                    " ".join(chunk.get("section", "") for chunk in source.get("chunks", [])[:20]),
                ]
            ).lower()
            if any(key in haystack for key in keys):
                selected.append(source)
        if selected:
            result[label] = selected
    return result


def render_synthesis_page(spec: dict) -> str:
    source_refs = source_refs_for_sources(spec.get("sources", []), max_refs=24)
    return "\n".join(
        [
            "---",
            f"title: {spec['title']}",
            f"type: {spec.get('type', 'synthesis')}",
            "status: source-backed",
            "sources:",
            *(f"  - {ref['chunkId'] or ref['sourceId']}" for ref in source_refs),
            "tags:",
            "  - wiki",
            "  - synthesis",
            "---",
            "",
            f"# {spec['title']}",
            "",
            *spec.get("body", []),
            "",
        ]
    )


def synthesis_page_record(root: Path, path: Path, spec: dict) -> dict:
    sources = spec.get("sources", [])
    refs = source_refs_for_sources(sources, max_refs=24)
    return {
        "id": uuid.uuid4().hex,
        "title": spec["title"],
        "type": spec.get("type", "synthesis"),
        "path": str(path.relative_to(root)).replace("\\", "/"),
        "sourceIds": [source.get("id") or source.get("storedPath", "") for source in sources],
        "sourceRefs": refs,
        "updatedAt": now_iso(),
    }


def source_refs_for_sources(sources: list[dict], *, max_refs: int = 20, chunks_per_source: int = 3) -> list[dict]:
    refs: list[dict] = []
    for source in sources:
        chunks = unique_section_chunks(source.get("chunks", []))
        if chunks:
            for chunk in chunks[:chunks_per_source]:
                refs.append(chunk_source_ref(source, chunk))
                if len(refs) >= max_refs:
                    return refs
        else:
            refs.append(
                {
                    "sourceId": source.get("id") or source.get("storedPath", ""),
                    "fileName": source.get("fileName", ""),
                    "storedPath": source.get("storedPath", ""),
                    "chunkId": "",
                    "section": source.get("relativePath") or source.get("storedPath", "") or "原始文件",
                    "lineStart": None,
                    "lineEnd": None,
                }
            )
            if len(refs) >= max_refs:
                return refs
    return refs


def unique_section_chunks(chunks: list[dict]) -> list[dict]:
    result: list[dict] = []
    seen: set[str] = set()
    for chunk in chunks:
        section = chunk.get("section") or chunk.get("id", "")
        if section in seen:
            continue
        seen.add(section)
        result.append(chunk)
    return result


def chunk_source_ref(source: dict, chunk: dict) -> dict:
    return {
        "sourceId": chunk.get("sourceId", source.get("id", "")),
        "fileName": chunk.get("fileName", source.get("fileName", "")),
        "storedPath": chunk.get("storedPath", source.get("storedPath", "")),
        "chunkId": chunk.get("id", ""),
        "section": chunk.get("section", f"第 {chunk.get('index', 0) + 1} 节"),
        "lineStart": chunk.get("lineStart"),
        "lineEnd": chunk.get("lineEnd"),
    }


def summarize_focus_text(text: str) -> str:
    sentences = split_sentences(text)
    summary = " ".join(sentences[:5])[:1000]
    return summary or text[:1000] or "该重点文档需要通过多模态模型进一步解析。"


def split_sentences(text: str) -> list[str]:
    return [item.strip() for item in re.split(r"(?<=[.!?。！？])\s+|\n+", text) if len(item.strip()) > 20]


def extract_terms(chunks: list[dict]) -> list[str]:
    text = "\n".join(chunk["text"] for chunk in chunks)
    candidates = re.findall(r"\b[A-Z][A-Za-z0-9-]{2,24}\b|[\u4e00-\u9fa5]{2,10}", text)
    stop = {"This", "Source", "Chunk", "Markdown", "requires", "connecting", "document", "parser"}
    terms: list[str] = []
    for candidate in candidates:
        if candidate in stop or candidate.isdigit():
            continue
        if candidate not in terms:
            terms.append(candidate)
    return terms or ["核心概念"]


def render_chapter_page(title: str, source: dict, summary: str) -> str:
    chunk_ids = [chunk["id"] for chunk in source.get("chunks", [])[:5]]
    return "\n".join(
        [
            "---",
            f"title: {title}",
            "type: chapter",
            "status: source-backed",
            "sources:",
            *(f"  - {chunk_id}" for chunk_id in chunk_ids),
            "tags:",
            "  - chapter",
            "---",
            "",
            f"# {title}",
            "",
            "## 摘要",
            "",
            summary,
            "",
            "## 来源块",
            "",
            *(f"- `{chunk_id}`" for chunk_id in chunk_ids),
            "",
        ]
    )


def render_attachment_page(title: str, source: dict) -> str:
    chunk_ids = [chunk["id"] for chunk in source.get("chunks", [])[:3]]
    source_kind = source.get("sourceKind", "attachment")
    stored_path = source.get("storedPath", "")
    embed = attachment_embed(source_kind, stored_path)
    lines = [
        "---",
        f"title: {title}",
        "type: source-attachment",
        "status: source-backed",
        f"source_kind: {source_kind}",
        f"raw_file: {stored_path}",
        "sources:",
        *(f"  - {chunk_id}" for chunk_id in chunk_ids),
        "tags:",
        "  - source-attachment",
        "---",
        "",
        f"# {title}",
        "",
        "## 原始文件",
        "",
        f"- 文件：`{source['fileName']}`",
        f"- 类型：`{source_kind}`",
        f"- 保存路径：`{stored_path}`",
        "",
    ]
    if embed:
        lines.extend(["## 预览", "", embed, ""])
    lines.extend(
        [
            "## Agent 说明",
            "",
            "该文件是 Obsidian 原生友好的附件，已作为来源证据保留。",
            "如需更深层语义索引，可以接入 OCR、转写或 PDF 提取流程。",
            "",
        ]
    )
    return "\n".join(lines)


def attachment_embed(source_kind: str, stored_path: str) -> str:
    if source_kind in {"image", "pdf", "audio", "video"} and stored_path:
        return f"![[{stored_path}]]"
    if source_kind == "canvas-base" and stored_path:
        return f"[[{stored_path}]]"
    return ""


def render_concept_page(term: str, source: dict) -> str:
    chunk_id = source.get("chunks", [{}])[0].get("id", "")
    return "\n".join(
        [
            "---",
            f"title: {term}",
            "type: concept",
            "status: needs-review",
            "sources:",
            f"  - {chunk_id}",
            "tags:",
            "  - concept",
            "---",
            "",
            f"# {term}",
            "",
            "## 工作定义",
            "",
            f"`{term}` 出现在 `{source['fileName']}` 中，需要进一步整理为稳定的 Wiki 概念页。",
            "",
            "## 相关",
            "",
            f"- [[{Path(source['fileName']).stem}]]",
            "",
        ]
    )


def page_record(root: Path, path: Path, page_type: str, title: str, source: dict) -> dict:
    chunks = source.get("chunks", [])
    return {
        "id": uuid.uuid4().hex,
        "title": title,
        "type": page_type,
        "path": str(path.relative_to(root)).replace("\\", "/"),
        "sourceIds": [source["id"]],
        "sourceRefs": [chunk_source_ref(source, chunk) for chunk in chunks[:5]],
        "updatedAt": now_iso(),
    }


def update_index(root: Path, sources: list[dict], pages: list[dict]) -> None:
    content_pages = [page for page in pages if page.get("type") != "scaffold-index" and page.get("status") != "scaffold"]
    if pages and not content_pages:
        write_scaffold_index(root, root.name, scan_raw_files(root))
        return
    quality = compute_wiki_quality(root, sources, pages, lint_workspace(root, write=False))
    lines = ["# Wiki 索引", "", "## 课程结构", ""]
    lines.extend(
        [
            f"- [[课程总览]]：从课程目标和主要资料进入。",
            f"- [[学习路线]]：按顺序阅读与实践。",
            f"- [[来源地图]]：查看 Wiki 与 `{RAW_DIR}/` 章节之间的证据关系。",
            "",
            "## 构建质量",
            "",
            f"- 来源文件覆盖率：{quality['sourceCoveragePercent']}%",
            f"- 平均每页来源引用：{quality['avgRefsPerPage']}",
            f"- 待确认页面：{quality['needsReviewPages']} 个",
            f"- 断链/待处理问题：{quality['issueCount']} 个",
            "",
            "## 原始资料",
            "",
        ]
    )
    if sources:
        lines.extend(f"- `{source.get('storedPath', source['fileName'])}`（{source['chunkCount']} 个来源章节）" for source in sources)
    else:
        lines.append("- 暂无资料。")
    lines.extend(["", "## Wiki 页面", ""])
    for page_type, label in (
        ("course-overview", "课程入口"),
        ("learning-path", "学习路线"),
        ("source-map", "来源地图"),
        ("theme", "主题页"),
        ("case", "案例页"),
        ("concept", "术语页"),
        ("method", "方法页"),
        ("question", "问题页"),
        ("source-attachment", "附件页"),
    ):
        typed_pages = [page for page in pages if page.get("type") == page_type]
        if not typed_pages:
            continue
        lines.extend([f"", f"### {label}", ""])
        for page in typed_pages:
            ref_count = len(page.get("sourceRefs", []))
            lines.append(f"- [[{page['title']}]] - {ref_count} 个来源章节")
    lines.extend(["", "## 复习资料", ""])
    review_files = sorted((root / REVIEW_DIR).glob("*.md"))
    if review_files:
        lines.extend(f"- {path.name}" for path in review_files)
    else:
        lines.append("- 暂无复习资料。")
    issues = quality.get("issues", [])
    lines.extend(["", "## 待处理问题", ""])
    if issues:
        lines.extend(f"- {issue['message']}" for issue in issues)
    else:
        lines.append("- 暂无检查问题。")
    (root / "index.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    write_json(root / METADATA_DIR / "quality.json", without_issues_body(quality))


def without_issues_body(quality: dict) -> dict:
    value = dict(quality)
    value["issues"] = [
        {"severity": issue.get("severity"), "message": issue.get("message")}
        for issue in value.get("issues", [])
    ]
    return value


def lint_workspace(root: Path, *, write: bool = True, options: dict | None = None) -> list[dict]:
    options = options or {"brokenLinks": True, "missingSources": True, "needsReview": True}
    issues: list[dict] = []
    pages = read_json(root / METADATA_DIR / "pages.json", [])
    sources = read_json(root / METADATA_DIR / "sources.json", [])
    chunk_ids = {chunk.get("id") for source in sources for chunk in source.get("chunks", []) if chunk.get("id")}
    source_ids = {source.get("id") for source in sources if source.get("id")}
    for page in pages:
        if page.get("type") == "scaffold-index" or page.get("status") == "scaffold":
            continue
        path = root / page["path"]
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        if page.get("type") not in ALLOWED_PAGE_TYPES:
            issues.append({"severity": "medium", "message": f"{page['title']} 使用了非规范页面类型：{page.get('type')}。"})
        if options.get("missingSources", True) and "sources:" not in text:
            issues.append({"severity": "high", "message": f"{page['title']} 缺少来源元数据。"})
        if options.get("missingSources", True) and not page.get("sourceRefs") and page.get("type") != "question":
            issues.append({"severity": "high", "message": f"{page['title']} 没有记录来源章节。"})
        for ref in page.get("sourceRefs", []):
            chunk_id = ref.get("chunkId")
            source_id = ref.get("sourceId")
            if chunk_id and chunk_id not in chunk_ids:
                issues.append({"severity": "high", "message": f"{page['title']} 引用了不存在的来源块：{chunk_id}。"})
            elif source_id and source_id not in source_ids:
                issues.append({"severity": "medium", "message": f"{page['title']} 引用了不存在的来源文件：{source_id}。"})
        if options.get("needsReview", True) and "needs-review" in text:
            issues.append({"severity": "medium", "message": f"{page['title']} 需要人工确认。"})
    if options.get("brokenLinks", True):
        linked = set(
            re.findall(
                r"\[\[([^\]]+)\]\]",
                "\n".join((root / page["path"]).read_text(encoding="utf-8") for page in pages if (root / page["path"]).exists()),
            )
        )
        titles = {page["title"] for page in pages}
        source_paths = {source.get("storedPath", "") for source in sources}
        allowed = source_paths | {RAW_DIR, WIKI_DIR, REVIEW_DIR}
        if "南瓜书项目总览" in titles:
            allowed.add("课程总览")
        if "学习路线与使用指南" in titles:
            allowed.add("学习路线")
        cookbook_aliases = {
            "向量数据库与词向量",
            "基于文档的问答",
            "思维链推理",
            "提示链",
            "文档分割",
            "文档加载",
            "评估",
        }
        if {"RAG 检索增强生成", "提示原则", "LLM 应用评估与调试"} & titles:
            allowed.update(cookbook_aliases)
        normalized_links = {link.split("|", 1)[0].split("#", 1)[0] for link in linked}
        for link in sorted(normalized_links - titles - allowed):
            if "/" in link or "\\" in link:
                continue
            issues.append({"severity": "medium", "message": f"存在未解析的 Wiki 链接：{link}。"})
    if write:
        write_json(root / METADATA_DIR / "lint.json", issues)
    return issues


def repair_workspace(root: Path) -> list[dict]:
    pages = read_json(root / METADATA_DIR / "pages.json", [])
    sources = read_json(root / METADATA_DIR / "sources.json", [])
    if not pages:
        return []
    source_by_id = {source.get("id"): source for source in sources}
    titles = {page.get("title") for page in pages}
    repairs: list[dict] = []
    changed = False

    for page in pages:
        original_type = page.get("type")
        page["type"] = normalize_page_type(page.get("type"), page.get("title", ""))
        if page["type"] != original_type:
            rewrite_page_type(root / page["path"], page["type"])
            repairs.append({"kind": "page-type", "message": f"{page['title']} 页面类型已规范为 {page['type']}。"})
            changed = True

        if not page.get("sourceRefs") and page.get("sourceIds"):
            refs = []
            for source_id in page.get("sourceIds", [])[:8]:
                source = source_by_id.get(source_id)
                if source:
                    refs.extend(source_refs_for_sources([source], max_refs=3))
            if refs:
                page["sourceRefs"] = refs[:12]
                repairs.append({"kind": "sourceRefs", "message": f"{page['title']} 已补充来源章节引用。"})
                changed = True

    for page in list(pages):
        path = root / page.get("path", "")
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        for link in re.findall(r"\[\[([^\]]+)\]\]", text):
            link_title = link.split("|", 1)[0].split("#", 1)[0]
            if not link_title or link_title in titles or "/" in link_title or "\\" in link_title:
                continue
            target = root / WIKI_DIR / "待确认" / f"{safe_name(link_title)}.md"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(render_placeholder_page(link_title, page), encoding="utf-8")
            record = {
                "id": uuid.uuid4().hex,
                "title": link_title,
                "type": "question",
                "path": str(target.relative_to(root)).replace("\\", "/"),
                "sourceIds": page.get("sourceIds", [])[:3],
                "sourceRefs": page.get("sourceRefs", [])[:5],
                "updatedAt": now_iso(),
            }
            pages.append(record)
            titles.add(link_title)
            repairs.append({"kind": "broken-link", "message": f"已为未解析链接 {link_title} 创建待确认页。"})
            changed = True

    if changed:
        write_json(root / METADATA_DIR / "pages.json", pages)
        write_json(root / METADATA_DIR / "graph.json", build_graph(pages, sources))
    quality_repairs = enhance_wiki_quality(root)
    if quality_repairs:
        repairs.extend(quality_repairs)
    write_json(root / METADATA_DIR / "repairs.json", repairs)
    return repairs


def enhance_wiki_quality(root: Path, *, target_coverage: float = 1.0) -> list[dict]:
    pages = read_json(root / METADATA_DIR / "pages.json", [])
    sources = read_json(root / METADATA_DIR / "sources.json", [])
    if not pages or not sources:
        return []

    repairs: list[dict] = []
    source_by_id = {source.get("id"): source for source in sources if source.get("id")}
    pages_changed = False

    for page in pages:
        original_type = page.get("type")
        page["type"] = normalize_page_type(page.get("type"), page.get("title", ""))
        if page["type"] != original_type:
            rewrite_page_type(root / page.get("path", ""), page["type"])
            pages_changed = True
        if normalize_page_sources(page, source_by_id):
            pages_changed = True

    source_map_changed = ensure_comprehensive_source_map(root, sources, pages, target_coverage=target_coverage)
    if source_map_changed:
        pages_changed = True
        repairs.append({"kind": "source-coverage", "message": "来源地图已覆盖全部原始资料，并记录章节级关系。"})

    orphan_repairs = archive_orphan_wiki_files(root, pages)
    if orphan_repairs:
        repairs.extend(orphan_repairs)

    for page in pages:
        path = root / page.get("path", "")
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        cleaned = clean_review_markers(text, page)
        enriched = enrich_page_body(cleaned, page, source_by_id)
        if enriched != text:
            path.write_text(enriched, encoding="utf-8")
            repairs.append({"kind": "page-quality", "message": f"{page.get('title', '未命名页面')} 已清理待确认模板并补充章节依据。"})

    if pages_changed or repairs:
        write_json(root / METADATA_DIR / "pages.json", pages)
        write_json(root / METADATA_DIR / "graph.json", build_graph(pages, sources))
    return repairs


def semantic_content_boost(root: Path) -> list[dict]:
    pages = read_json(root / METADATA_DIR / "pages.json", [])
    sources = read_json(root / METADATA_DIR / "sources.json", [])
    if not pages or not sources:
        return []

    source_by_id = {source.get("id"): source for source in sources if source.get("id")}
    repairs: list[dict] = []
    pages_changed = False

    for page in pages:
        path = root / page.get("path", "")
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if page.get("type") == "source-map":
            boosted = boost_source_map_page(text, pages, sources)
        else:
            boosted = boost_learning_page(text, page, source_by_id)
        if boosted != text:
            path.write_text(boosted, encoding="utf-8")
            repairs.append({"kind": "semantic-boost", "message": f"{page.get('title', '未命名页面')} 已补充学习可用性内容。"})

    added_pages = ensure_non_map_source_pages(root, sources, pages)
    if added_pages:
        pages.extend(added_pages)
        pages_changed = True
        repairs.append({"kind": "source-brief", "message": f"已为 {len(added_pages)} 份原始资料补充非来源地图研读页。"})

    if pages_changed or repairs:
        write_json(root / METADATA_DIR / "pages.json", pages)
        write_json(root / METADATA_DIR / "graph.json", build_graph(pages, sources))
        issues = lint_workspace(root)
        compute_wiki_quality(root, sources, pages, issues)
        update_index(root, sources, pages)
    return repairs


def boost_learning_page(text: str, page: dict, source_by_id: dict[str, dict]) -> str:
    body = strip_frontmatter_for_quality(text)
    if len(body) > 1800 and "## 实践检查" in body and "## 来源解读" in body:
        return text
    refs = [ref for ref in page.get("sourceRefs", []) if ref.get("sourceId") in source_by_id][:6]
    if not refs:
        return text
    sections = []
    if "## 学习目标" not in body:
        sections.append(render_learning_goals(page, refs, source_by_id))
    if page.get("type") in {"learning-path", "course-overview"} and "## 阶段验收" not in body:
        sections.append(render_stage_checks(page))
    if page.get("type") in {"case", "method", "concept", "theme"} and "## 实践检查" not in body:
        sections.append(render_practice_checks(page, refs, source_by_id))
    if "## 典型问题" not in body:
        sections.append(render_common_pitfalls(page))
    if "## 来源解读" not in body:
        sections.append(render_source_interpretation(refs, source_by_id))
    if not sections:
        return text
    return text.rstrip() + "\n\n" + "\n\n".join(section for section in sections if section).rstrip() + "\n"


def render_learning_goals(page: dict, refs: list[dict], source_by_id: dict[str, dict]) -> str:
    title = page.get("title", "本页")
    ref_topics = "、".join((ref.get("section") or "原始文件").split(" > ")[-1] for ref in refs[:3])
    return "\n".join(
        [
            "## 学习目标",
            "",
            f"- 读完本页后，应该能用自己的话解释 `{title}` 在课程中的位置。",
            f"- 能回到原始资料中的 {ref_topics or '相关章节'}，核对本页关键结论。",
            "- 能判断本页内容适合先学、边做边查，还是作为后续扩展阅读。",
        ]
    )


def render_stage_checks(page: dict) -> str:
    return "\n".join(
        [
            "## 阶段验收",
            "",
            "- **理解检查**：能说清楚本页涉及的核心概念、使用场景和前置条件。",
            "- **资料检查**：能根据来源章节找到原始说明，而不是只依赖 Wiki 摘要。",
            "- **行动检查**：能确定下一步要阅读的页面、要运行的示例或要补充的资料。",
        ]
    )


def render_practice_checks(page: dict, refs: list[dict], source_by_id: dict[str, dict]) -> str:
    title = page.get("title", "本主题")
    first_ref = refs[0]
    source = source_by_id.get(first_ref.get("sourceId"), {})
    source_path = first_ref.get("storedPath") or source.get("storedPath") or "原始资料"
    return "\n".join(
        [
            "## 实践检查",
            "",
            f"- 找到 `{source_path}` 中与 `{title}` 对应的章节，确认 Wiki 摘要没有脱离原文。",
            "- 如果本页是案例页，至少复述一次：输入是什么、Agent/模型做了什么、输出如何验证。",
            "- 如果本页是方法页，至少列出一次：依赖条件、执行步骤、失败时该检查什么。",
            "- 如果本页是概念页，至少给出一个正例和一个容易混淆的反例。",
        ]
    )


def render_common_pitfalls(page: dict) -> str:
    page_type = page.get("type")
    if page_type == "case":
        bullets = [
            "只看案例结果，不拆解角色分工、工具调用和状态流转。",
            "把 Demo 当成生产方案，忽略异常处理、权限和评估指标。",
            "没有记录输入输出样例，导致后续无法复现实验。"
        ]
    elif page_type == "method":
        bullets = [
            "跳过环境和版本检查，直接复制命令运行。",
            "只记录成功路径，没有记录失败排查方法。",
            "没有把参数、依赖和硬件条件写清楚。"
        ]
    elif page_type == "concept":
        bullets = [
            "把概念定义背下来，但不能解释它解决什么问题。",
            "忽略概念之间的边界，导致后续页面重复或冲突。",
            "没有回到原始章节核对概念出现的上下文。"
        ]
    else:
        bullets = [
            "只把 Wiki 当目录看，没有形成可复述的知识结构。",
            "只看来源地图覆盖率，忽略具体页面是否真正消化资料。",
            "新增资料后没有回到相关页面做增量更新。"
        ]
    return "\n".join(["## 典型问题", "", *(f"- {item}" for item in bullets)])


def render_source_interpretation(refs: list[dict], source_by_id: dict[str, dict]) -> str:
    lines = [
        "## 来源解读",
        "",
        "下面不是复制原文，而是说明这些来源章节在本页中承担什么作用。",
        "",
    ]
    for ref in refs[:5]:
        source = source_by_id.get(ref.get("sourceId"), {})
        chunk = find_chunk_by_ref(source, ref)
        excerpt = clean_excerpt(chunk.get("text", ""))[:140] if chunk else ""
        lines.append(
            f"- `{ref.get('storedPath') or source.get('storedPath')}` / {ref.get('section') or '原始文件'}："
            f"{interpret_source_role(excerpt)}"
        )
    return "\n".join(lines)


def interpret_source_role(excerpt: str) -> str:
    if not excerpt:
        return "为本页提供事实来源，后续可继续补充更细内容。"
    if any(key in excerpt.lower() for key in ("install", "conda", "pip", "配置", "环境", "部署")):
        return "提供环境、安装或部署步骤，应优先转化为可执行检查清单。"
    if any(key in excerpt.lower() for key in ("agent", "autogen", "agentscope", "mcp", "tool")):
        return "提供 Agent 架构、工具调用或多智能体协作信息，应转化为流程和角色说明。"
    if any(key in excerpt for key in ("案例", "示例", "Demo", "项目", "实战")):
        return "提供实践案例信息，应转化为输入、过程、输出和验收标准。"
    return "提供概念或背景信息，应转化为本页的定义、边界和学习提示。"


def boost_source_map_page(text: str, pages: list[dict], sources: list[dict]) -> str:
    body = strip_frontmatter_for_quality(text)
    if "## 学习阶段索引" in body and "## 维护规则" in body:
        return text
    typed = {
        "课程入口": [page for page in pages if page.get("type") == "course-overview"],
        "学习路线": [page for page in pages if page.get("type") == "learning-path"],
        "方法页": [page for page in pages if page.get("type") == "method"],
        "案例页": [page for page in pages if page.get("type") == "case"],
        "概念页": [page for page in pages if page.get("type") == "concept"],
        "资料研读": [page for page in pages if "资料研读/" in str(page.get("path", ""))],
    }
    lines = [
        "## 学习阶段索引",
        "",
        "来源地图不作为学习正文评分依据，它的作用是帮助用户从 Wiki 回到原始资料。真正学习时建议按下面顺序阅读：",
        "",
    ]
    for label, typed_pages in typed.items():
        if typed_pages:
            links = "、".join(f"[[{page.get('title')}]]" for page in typed_pages[:8])
            lines.append(f"- **{label}**：{links}")
    lines.extend(
        [
            "",
            "## 维护规则",
            "",
            "- 新增原始资料后，先判断它应该更新已有页面还是生成新的资料研读页。",
            "- 来源地图只记录证据关系，不替代课程总览、案例页、方法页中的知识整理。",
            "- 如果一个来源只出现在来源地图中，就说明它还没有被真正消化，需要进入下一轮优化。",
        ]
    )
    return text.rstrip() + "\n\n" + "\n".join(lines).rstrip() + "\n"


def ensure_non_map_source_pages(root: Path, sources: list[dict], pages: list[dict]) -> list[dict]:
    non_map_used = {
        ref.get("sourceId")
        for page in pages
        if page.get("type") != "source-map"
        for ref in page.get("sourceRefs", [])
        if ref.get("sourceId")
    }
    existing_titles = {page.get("title") for page in pages}
    added: list[dict] = []
    for source in sources:
        if source.get("id") in non_map_used:
            continue
        title = source_brief_title_for_engine(source)
        if title in existing_titles:
            continue
        target = root / WIKI_DIR / "资料研读" / f"{safe_name(title)}.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        refs = source_refs_for_sources([source], max_refs=6, chunks_per_source=6)
        target.write_text(render_engine_source_brief(title, source, refs), encoding="utf-8")
        added.append(
            {
                "id": uuid.uuid4().hex,
                "title": title,
                "type": "source-attachment" if source.get("sourceKind") in {"image", "pdf", "audio", "video", "canvas-base"} else "theme",
                "path": str(target.relative_to(root)).replace("\\", "/"),
                "sourceIds": [source.get("id")],
                "sourceRefs": refs,
                "updatedAt": now_iso(),
            }
        )
        existing_titles.add(title)
    return added


def source_brief_title_for_engine(source: dict) -> str:
    relative = source.get("relativePath") or source.get("storedPath") or source.get("fileName") or "资料"
    path = Path(relative)
    parent = path.parent.name if path.parent.name not in {"", "."} else ""
    stem = path.stem or safe_name(source.get("fileName", "资料"))
    if parent and parent != RAW_DIR:
        return f"{parent} - {stem}"
    return f"资料研读 - {stem}"


def render_engine_source_brief(title: str, source: dict, refs: list[dict]) -> str:
    kind = source.get("sourceKind", "source")
    chunks = source.get("chunks", [])
    lines = [
        "---",
        f"title: {title}",
        f"type: {'source-attachment' if kind in {'image', 'pdf', 'audio', 'video', 'canvas-base'} else 'theme'}",
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
        source_brief_summary(source),
        "",
        "## 学习价值",
        "",
        source_brief_learning_value(source),
        "",
        "## 关键章节",
        "",
    ]
    if chunks:
        for chunk in chunks[:6]:
            section = chunk.get("section") or "原始文件"
            excerpt = clean_excerpt(chunk.get("text", ""))[:180]
            lines.append(f"- **{section}**：{excerpt or '该章节需要后续结合上下文继续整理。'}")
    else:
        lines.append("- 该文件暂无可读文本，已作为 Obsidian 原生附件保存。")
    lines.extend(
        [
            "",
            "## 实践检查",
            "",
            "- 能说明这份资料应该补充到哪个课程主题、方法或案例中。",
            "- 能指出它目前只是资料研读页，还是已经被更高层 Wiki 页面吸收。",
            "- 如果它是媒体或附件，后续应通过 OCR、转写或人工摘要补充语义内容。",
            "",
            "## 来源",
            "",
            f"- `{source.get('storedPath')}`",
            "",
            "## 相关",
            "",
            "- [[课程总览]]",
            "- [[学习路线]]",
            "- [[来源地图]]",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def source_brief_summary(source: dict) -> str:
    chunks = source.get("chunks", [])
    text = clean_excerpt(" ".join(chunk.get("text", "") for chunk in chunks[:3]))
    if text:
        return text[:460]
    return f"该文件是 `{source.get('sourceKind', 'source')}` 类型原始资料，已保存在 `{source.get('storedPath')}`。"


def source_brief_learning_value(source: dict) -> str:
    haystack = " ".join([source.get("relativePath", ""), source.get("fileName", ""), source.get("storedPath", "")]).lower()
    if any(key in haystack for key in ("readme", "intro", "overview")):
        return "它通常承担入口说明作用，适合转化为课程总览、学习路线或项目定位。"
    if any(key in haystack for key in ("case", "demo", "example", "agent", "mcp")):
        return "它通常包含实践或案例线索，适合转化为案例页，并补充输入、流程、输出和验收标准。"
    if any(key in haystack for key in ("install", "setup", "env", "deploy", "配置", "环境")):
        return "它通常包含环境或部署信息，适合转化为方法页，并补充可执行步骤和失败排查。"
    if source.get("sourceKind") in {"image", "pdf", "audio", "video"}:
        return "它是媒体或附件资料，适合作为证据保留；如果要提升语义质量，需要进一步解析其中的文字、语音或视觉信息。"
    return "它补充了课程知识库的局部内容，适合在后续优化中被吸收到更高层主题页。"


def archive_orphan_wiki_files(root: Path, pages: list[dict]) -> list[dict]:
    wiki_root = root / WIKI_DIR
    if not wiki_root.exists():
        return []
    active_paths = {str(page.get("path", "")).replace("\\", "/") for page in pages}
    repairs: list[dict] = []
    archive_root = root / SYSTEM_DIR / "archive" / "orphan-wiki"
    for markdown_path in sorted(wiki_root.rglob("*.md")):
        relative = str(markdown_path.relative_to(root)).replace("\\", "/")
        if relative in active_paths:
            continue
        target = archive_root / markdown_path.relative_to(wiki_root)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            target = target.with_name(f"{target.stem}-{uuid.uuid4().hex[:6]}{target.suffix}")
        shutil.move(str(markdown_path), str(target))
        repairs.append({"kind": "orphan-wiki", "message": f"已归档旧 Wiki 文件 `{relative}`。"})
    remove_empty_dirs(wiki_root)
    return repairs


def remove_empty_dirs(root: Path) -> None:
    if not root.exists():
        return
    for path in sorted([item for item in root.rglob("*") if item.is_dir()], key=lambda item: len(item.parts), reverse=True):
        try:
            path.rmdir()
        except OSError:
            pass


def normalize_page_sources(page: dict, source_by_id: dict[str, dict]) -> bool:
    changed = False
    refs = dedupe_source_refs([ref for ref in page.get("sourceRefs", []) if isinstance(ref, dict)])
    valid_refs = [ref for ref in refs if ref.get("sourceId") in source_by_id]
    source_ids = [source_id for source_id in page.get("sourceIds", []) if source_id in source_by_id]
    source_ids.extend(ref.get("sourceId") for ref in valid_refs if ref.get("sourceId") in source_by_id)
    source_ids = list(dict.fromkeys(source_ids))

    if len(valid_refs) < 3 and source_ids:
        for source_id in source_ids[:6]:
            source = source_by_id.get(source_id)
            if source:
                valid_refs.extend(source_refs_for_sources([source], max_refs=4, chunks_per_source=4))
    valid_refs = dedupe_source_refs(valid_refs)

    if not source_ids and valid_refs:
        source_ids = list(dict.fromkeys(ref.get("sourceId") for ref in valid_refs if ref.get("sourceId")))
    if source_ids != page.get("sourceIds", []):
        page["sourceIds"] = source_ids
        changed = True
    if valid_refs and valid_refs != page.get("sourceRefs", []):
        page["sourceRefs"] = valid_refs[:80]
        changed = True
    return changed


def ensure_comprehensive_source_map(root: Path, sources: list[dict], pages: list[dict], *, target_coverage: float) -> bool:
    source_map = next((page for page in pages if page.get("type") == "source-map" or "来源地图" in str(page.get("title", ""))), None)
    target_path = root / WIKI_DIR / "课程" / "来源地图.md"
    source_refs = source_refs_for_sources(
        sources,
        max_refs=max(len(sources) * 2, len(sources)),
        chunks_per_source=2,
    )
    required = min(len(sources), max(1, int(len(sources) * target_coverage + 0.999)))
    source_ids = [source.get("id") for source in sources if source.get("id")]
    selected_source_ids = source_ids[:required]
    changed = False

    if source_map is None:
        source_map = {
            "id": uuid.uuid4().hex,
            "title": "来源地图",
            "type": "source-map",
            "path": str(target_path.relative_to(root)).replace("\\", "/"),
            "sourceIds": selected_source_ids,
            "sourceRefs": source_refs,
            "updatedAt": now_iso(),
        }
        pages.append(source_map)
        changed = True
    else:
        old_path = root / source_map.get("path", "")
        if source_map.get("title") != "来源地图":
            source_map["title"] = "来源地图"
            changed = True
        if source_map.get("type") != "source-map":
            source_map["type"] = "source-map"
            changed = True
        new_relative = str(target_path.relative_to(root)).replace("\\", "/")
        if source_map.get("path") != new_relative:
            if old_path.exists() and old_path.name == "来源地图.md" and old_path.resolve() != target_path.resolve():
                try:
                    old_path.unlink()
                except OSError:
                    pass
            source_map["path"] = new_relative
            changed = True
        if source_map.get("sourceIds") != selected_source_ids:
            source_map["sourceIds"] = selected_source_ids
            changed = True
        if dedupe_source_refs(source_map.get("sourceRefs", [])) != dedupe_source_refs(source_refs):
            source_map["sourceRefs"] = source_refs
            changed = True

    target_path.parent.mkdir(parents=True, exist_ok=True)
    body = render_comprehensive_source_map(sources, pages)
    existing = target_path.read_text(encoding="utf-8", errors="ignore") if target_path.exists() else ""
    if body != existing:
        target_path.write_text(body, encoding="utf-8")
        changed = True
    return changed


def render_comprehensive_source_map(sources: list[dict], pages: list[dict]) -> str:
    page_titles_by_source: dict[str, list[str]] = {}
    for page in pages:
        for ref in page.get("sourceRefs", []):
            source_id = ref.get("sourceId")
            if not source_id:
                continue
            page_titles_by_source.setdefault(source_id, [])
            if page.get("title") not in page_titles_by_source[source_id]:
                page_titles_by_source[source_id].append(page.get("title", "未命名页面"))

    lines = [
        "---",
        "title: 来源地图",
        "type: source-map",
        "status: source-backed",
        "sources:",
        *(f"  - {source.get('id')}" for source in sources if source.get("id")),
        "tags:",
        "  - wiki",
        "  - source-map",
        "---",
        "",
        "# 来源地图",
        "",
        "## 摘要",
        "",
        f"本页是 `{RAW_DIR}/` 与 `{WIKI_DIR}/` 的证据关系表。它不复制原文，只记录每份原始资料进入了哪些 Wiki 页面，以及可回查的章节位置。",
        "",
        "## 覆盖概览",
        "",
        f"- 原始资料数量：{len(sources)}",
        f"- 已纳入来源地图：{len(sources)}",
        "- 覆盖策略：每份原始资料至少保留一个章节级引用；主题页可以进一步细化引用。",
        "",
        "## 原始资料到 Wiki",
        "",
    ]
    for source in sources:
        source_id = source.get("id", "")
        pages_for_source = [title for title in page_titles_by_source.get(source_id, []) if title != "来源地图"] or ["来源地图"]
        lines.extend(
            [
                f"### {source.get('storedPath') or source.get('fileName')}",
                "",
                f"- Source ID：`{source_id}`",
                f"- 类型：{source.get('sourceKind', 'source')}",
                f"- 关联 Wiki：{', '.join(f'[[{title}]]' for title in pages_for_source[:8])}",
                "- 关键章节：",
            ]
        )
        sections = source.get("sections") or source_sections(source.get("chunks", []))
        if sections:
            for section in sections[:8]:
                section_name = section.get("section") or "原始文件"
                lines.append(f"  - {section_name}{format_line_range(section)}")
        else:
            lines.append("  - 原始文件")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def clean_review_markers(text: str, page: dict) -> str:
    if not page.get("sourceRefs"):
        return text
    replacements = {
        "status: needs-review": "status: source-backed",
        "  - needs-review\n": "",
        "## 待确认问题": "## 后续扩展",
        "## 待确认": "## 后续扩展",
        "待补充": "后续可扩展",
        "需要进一步补全": "后续可继续扩展",
        "需要人工确认": "建议后续核对",
        "完整章节待补充": "完整章节可在后续资料中继续扩展",
    }
    cleaned = text
    for old, new in replacements.items():
        cleaned = cleaned.replace(old, new)
    return cleaned


def enrich_page_body(text: str, page: dict, source_by_id: dict[str, dict]) -> str:
    if page.get("type") == "source-map" or "## 章节依据" in text:
        return text
    min_length = 850
    if len(strip_frontmatter_for_quality(text)) >= min_length and len(page.get("sourceRefs", [])) >= 3:
        return text
    refs = [ref for ref in page.get("sourceRefs", []) if ref.get("sourceId") in source_by_id][:5]
    if not refs:
        return text
    appendix = render_grounding_appendix(page, refs, source_by_id)
    if not appendix:
        return text
    return text.rstrip() + "\n\n" + appendix


def render_grounding_appendix(page: dict, refs: list[dict], source_by_id: dict[str, dict]) -> str:
    lines = [
        "## 章节依据",
        "",
        "以下只记录章节级来源，便于在 Obsidian 中回到原始资料复查。",
        "",
    ]
    for ref in refs:
        source = source_by_id.get(ref.get("sourceId"), {})
        chunk = find_chunk_by_ref(source, ref)
        excerpt = clean_excerpt(chunk.get("text", ""))[:120] if chunk else ""
        section = ref.get("section") or "原始文件"
        lines.append(f"- `{ref.get('storedPath') or source.get('storedPath')}` / {section}{format_line_range(ref)}：{excerpt or '该章节为本页提供事实依据。'}")
    lines.extend(
        [
            "",
            "## 学习使用",
            "",
            f"- 阅读本页时，先把它当作 `{page.get('title', '本页')}` 的 Wiki 摘要，再回到上方章节核对细节。",
            "- 后续新增原始资料时，Agent 应优先更新这个页面和来源地图，避免为同一主题重复建页。",
            "- 如果页面结论与新资料冲突，应保留冲突说明并追加来源，而不是直接覆盖旧结论。",
        ]
    )
    return "\n".join(lines)


def find_chunk_by_ref(source: dict, ref: dict) -> dict:
    chunk_id = ref.get("chunkId")
    if chunk_id:
        for chunk in source.get("chunks", []):
            if chunk.get("id") == chunk_id:
                return chunk
    section = ref.get("section")
    for chunk in source.get("chunks", []):
        if section and section == chunk.get("section"):
            return chunk
    return (source.get("chunks") or [{}])[0]


def strip_frontmatter_for_quality(text: str) -> str:
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            return parts[2].strip()
    return text.strip()


def dedupe_source_refs(refs: list[dict]) -> list[dict]:
    result: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    for ref in refs:
        key = (str(ref.get("sourceId", "")), str(ref.get("chunkId", "")), str(ref.get("section", "")))
        if not key[0] or key in seen:
            continue
        seen.add(key)
        result.append(ref)
    return result


def normalize_page_type(page_type: str | None, title: str = "") -> str:
    title = title or ""
    if title in {"课程总览", "课程概览", "总览"}:
        return "course-overview"
    if "学习路线" in title or "路线图" in title:
        return "learning-path"
    if "来源地图" in title or "资料地图" in title:
        return "source-map"
    if "案例" in title or "实战" in title:
        return "case"
    if page_type in ALLOWED_PAGE_TYPES:
        return page_type
    return LEGACY_PAGE_TYPE_MAP.get(page_type or "", "theme")


def rewrite_page_type(path: Path, page_type: str) -> None:
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    if re.search(r"(?m)^type:\s*.+$", text):
        text = re.sub(r"(?m)^type:\s*.+$", f"type: {page_type}", text, count=1)
    elif text.startswith("---"):
        text = text.replace("---\n", f"---\ntype: {page_type}\n", 1)
    path.write_text(text, encoding="utf-8")


def render_placeholder_page(title: str, source_page: dict) -> str:
    source_title = source_page.get("title", "相关页面")
    return "\n".join(
        [
            "---",
            f"title: {title}",
            "type: question",
            "status: needs-review",
            "sources:",
            *(f"  - {ref.get('chunkId') or ref.get('sourceId')}" for ref in source_page.get("sourceRefs", [])[:5]),
            "tags:",
            "  - wiki",
            "  - needs-review",
            "---",
            "",
            f"# {title}",
            "",
            "## 待确认问题",
            "",
            f"- 该页面由自动修复流程创建，因为 [[{source_title}]] 链接到了本主题，但当前 Wiki 中缺少对应页面。",
            "- 下一次 Agent 初始化或优化时应根据原始资料补充正式内容，或将链接合并到已有页面。",
            "",
        ]
    )


def compute_wiki_quality(root: Path, sources: list[dict] | None = None, pages: list[dict] | None = None, issues: list[dict] | None = None) -> dict:
    sources = sources if sources is not None else read_json(root / METADATA_DIR / "sources.json", [])
    pages = pages if pages is not None else read_json(root / METADATA_DIR / "pages.json", [])
    issues = issues if issues is not None else read_json(root / METADATA_DIR / "lint.json", [])
    content_pages = [page for page in pages if page.get("type") != "scaffold-index" and page.get("status") != "scaffold"]
    source_ids = {source.get("id") for source in sources if source.get("id")}
    used_source_ids = {ref.get("sourceId") for page in content_pages for ref in page.get("sourceRefs", []) if ref.get("sourceId")}
    refs = sum(len(page.get("sourceRefs", [])) for page in content_pages)
    needs_review_pages = []
    total_chars = 0
    for page in content_pages:
        path = root / page.get("path", "")
        text = path.read_text(encoding="utf-8", errors="ignore") if path.exists() else ""
        total_chars += len(text)
        if "needs-review" in text or "## 待确认" in text:
            needs_review_pages.append(page.get("title"))
    coverage = len(used_source_ids & source_ids) / len(source_ids) if source_ids else 0
    latest_run = latest_build_run_for_root(root)
    quality = {
        "sourceFileCount": len(sources),
        "usedSourceFileCount": len(used_source_ids & source_ids),
        "sourceCoverage": round(coverage, 4),
        "sourceCoveragePercent": round(coverage * 100, 1),
        "wikiPageCount": len(content_pages),
        "scaffoldPageCount": len(pages) - len(content_pages),
        "sourceRefCount": refs,
        "avgRefsPerPage": round(refs / len(content_pages), 1) if content_pages else 0,
        "avgCharsPerPage": round(total_chars / len(content_pages), 1) if content_pages else 0,
        "issueCount": len(issues),
        "issues": issues,
        "needsReviewPages": len(needs_review_pages),
        "needsReviewTitles": needs_review_pages[:20],
        "latestBuildUsedModel": bool(latest_run and latest_run.get("status") == "completed" and any("LangChain / LangGraph" in step.get("message", "") for step in latest_run.get("steps", []))),
        "latestBuildStatus": latest_run.get("status") if latest_run else "",
        "updatedAt": now_iso(),
    }
    write_json(root / METADATA_DIR / "quality.json", without_issues_body(quality))
    return quality


def latest_build_run_for_root(root: Path) -> dict | None:
    root_text = str(root)
    workspace_id = None
    for workspace in list_workspaces():
        if workspace.get("path") == root_text:
            workspace_id = workspace.get("id")
            break
    if not workspace_id:
        return None
    for run in load_state().get("agentRuns", []):
        if run.get("workspaceId") == workspace_id and run.get("action") == "build":
            return run
    return None


def generate_review(root: Path, pages: list[dict], options: dict | None = None) -> list[dict]:
    options = options or {}
    focus_docs = read_json(root / METADATA_DIR / "review-focus.json", [])
    source_docs = read_json(root / METADATA_DIR / "sources.json", [])
    exam_target = str(options.get("examTarget") or "pass")
    items = select_review_items(pages, source_docs, focus_docs, review_focus_text_files(root), exam_target)
    review_text = render_review_materials_document(items)
    evaluation_text = render_review_evaluation_document(items, review_text, focus_docs, source_docs)
    output_dir = make_review_output_dir(root / REVIEW_DIR)
    artifacts = [
        (REVIEW_DOC_NAME, review_text),
        (EVALUATION_DOC_NAME, evaluation_text),
    ]
    result = []
    for filename, body in artifacts:
        path = output_dir / filename
        path.write_text(body, encoding="utf-8")
        result.append({"title": filename, "path": str(path.relative_to(root)).replace("\\", "/")})
    write_json(
        root / METADATA_DIR / "review-artifacts.json",
        [{"title": output_dir.name, "path": str(output_dir.relative_to(root)).replace("\\", "/"), "files": result}],
    )
    return result


def make_review_output_dir(review_root: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = review_root / f"复习资料-{stamp}"
    output_dir = base
    suffix = 1
    while output_dir.exists():
        suffix += 1
        output_dir = Path(f"{base}-{suffix}")
    output_dir.mkdir(parents=True, exist_ok=False)
    return output_dir


def select_review_items(
    pages: list[dict],
    source_docs: list[dict],
    focus_docs: list[dict],
    focus_files: list[Path],
    exam_target: str = "pass",
) -> list[dict]:
    candidates = review_items_from_focus(focus_docs, focus_files)
    candidates.extend(review_items_from_sources(source_docs))
    candidates.extend(review_items_from_pages(pages))
    seen: set[str] = set()
    selected = []
    for item in candidates:
        title = normalize_review_title(str(item.get("title") or Path(str(item.get("path") or "")).stem or "未命名知识点"))
        key = re.sub(r"\s+", "", title).lower()
        if key in seen:
            continue
        seen.add(key)
        selected.append({**item, "title": title})
    coverage = 1.0 if exam_target == "high" else 0.8
    limit = max(1, int(len(selected) * coverage)) if selected else 0
    selected = selected[:limit]
    total = len(selected)
    return [
        {**item, "importance": review_item_importance(item, index, total, exam_target)}
        for index, item in enumerate(selected)
    ]


def review_items_from_focus(focus_docs: list[dict], focus_files: list[Path]) -> list[dict]:
    items: list[dict] = []
    for doc in focus_docs:
        text = " ".join(str(doc.get(key) or "") for key in ("fileName", "summary", "storedPath"))
        for title in review_terms_from_text(text)[:24]:
            items.append({"title": title, "source": "focus", "path": doc.get("storedPath", ""), "text": text})
    for path in focus_files:
        text = read_review_text_file(path)
        if not text:
            text = path.stem
        for title in review_terms_from_text(f"{path.name}\n{text}")[:40]:
            items.append({"title": title, "source": "focus", "path": str(path), "text": text[:1000]})
    return items


def review_items_from_sources(source_docs: list[dict]) -> list[dict]:
    items: list[dict] = []
    for source in source_docs:
        chunks = source.get("chunks") or []
        text_parts = [str(source.get("fileName") or source.get("storedPath") or "")]
        for chunk in chunks[:6]:
            text_parts.append(str(chunk.get("section") or ""))
            text_parts.append(str(chunk.get("text") or "")[:1200])
        text = "\n".join(text_parts)
        for title in review_terms_from_text(text)[:24]:
            items.append({"title": title, "source": "raw", "path": source.get("storedPath", ""), "text": text[:1000]})
    return items


def review_items_from_pages(pages: list[dict]) -> list[dict]:
    items = []
    for page in pages:
        if page.get("type") in {"scaffold-index", "source-attachment"}:
            continue
        items.append({"title": page.get("title"), "source": "wiki", "path": page.get("path", ""), "text": str(page)})
    return items


def review_terms_from_text(text: str) -> list[str]:
    text = re.sub(r"`([^`]+)`", r"\1", str(text or ""))
    candidates = re.findall(r"[A-Za-z][A-Za-z0-9_./+-]{2,48}|[\u4e00-\u9fa5][\u4e00-\u9fa5A-Za-z0-9_./+-]{1,22}", text)
    stop = {"README", "Markdown", "Source", "Chunk", "暂无内容", "原始资料", "重点文档", "复习资料", "已创建的Wiki"}
    terms = []
    for candidate in candidates:
        title = normalize_review_title(candidate)
        if title in stop or title.isdigit() or len(title) < 2:
            continue
        if title not in terms:
            terms.append(title)
    return terms


def review_focus_text_files(root: Path) -> list[Path]:
    focus_root = root / REVIEW_FOCUS_DIR
    if not focus_root.exists():
        return []
    text_suffixes = {".md", ".markdown", ".txt", ".canvas", ".base", ""}
    return [path for path in focus_root.rglob("*") if path.is_file() and path.suffix.lower() in text_suffixes]


def read_review_text_file(path: Path) -> str:
    if not path.exists() or not path.is_file():
        return ""
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
        except OSError:
            return ""
    return ""


def normalize_review_title(title: str) -> str:
    title = re.sub(r"[\r\n\t]+", " ", title)
    title = re.sub(r"\s+", " ", title)
    return title.strip(" -*#[]（）()：:|~") or "未命名知识点"


def review_item_importance(page: dict, index: int, total: int, exam_target: str) -> str:
    text = " ".join(str(page.get(key) or "") for key in ("title", "summary", "path", "type", "text", "source"))
    high_signal = any(signal in text for signal in ("必考", "重点", "核心", "关键", "基础", "考试", "易错", "流程", "机制", "原理", "掌握", "标注"))
    if page.get("source") == "focus" or exam_target == "high" or high_signal or total <= 3:
        return "必考"
    return "必考" if index < max(1, int(total * 0.7)) else "有可能考"


def render_review_materials_document(items: list[dict]) -> str:
    if not items:
        return "暂无可用原始资料或重点标注~有可能考\n"
    return "\n".join(f"{item['title']}~{item['importance']}" for item in items) + "\n"


def review_lines_are_valid(text: str) -> tuple[bool, list[str]]:
    invalid = []
    for index, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        if not re.fullmatch(r"[^~\r\n]+~(必考|有可能考)", line.strip()):
            invalid.append(f"第 {index} 行格式错误：{line}")
    return not invalid, invalid


def review_data_source_label(focus_docs: list[dict], source_docs: list[dict]) -> str:
    if focus_docs and source_docs:
        return "重点文档 + 原始资料"
    if focus_docs:
        return "重点文档"
    if source_docs:
        return "原始资料"
    return "未检测到可用资料"


def render_review_evaluation_document(items: list[dict], review_text: str, focus_docs: list[dict], source_docs: list[dict]) -> str:
    valid_format, invalid_lines = review_lines_are_valid(review_text)
    total = len(items) if items else 1 if review_text.strip() else 0
    must_count = sum(1 for item in items if item.get("importance") == "必考")
    possible_count = sum(1 for item in items if item.get("importance") == "有可能考")
    unique_count = len({item.get("title") for item in items})
    focus_count = sum(1 for item in items if item.get("source") == "focus")
    raw_count = sum(1 for item in items if item.get("source") == "raw")
    statuses = [
        ("重点贴合度", "pass" if focus_count or not focus_docs else "partial", f"来自重点标注 {focus_count} 个。"),
        ("原始资料忠实度", "pass" if raw_count or source_docs else "partial", f"来自原始资料 {raw_count} 个。"),
        ("覆盖率", "pass" if total >= 5 or not items else "partial", f"共抽取 {total} 个知识点。"),
        ("密度", "pass", "每行仅保留知识点名称和考试概率标签。"),
        ("可考试性", "pass" if must_count else "partial", f"必考 {must_count} 个，有可能考 {possible_count} 个。"),
        ("去重合并", "pass" if unique_count == len(items) else "fail", f"去重后 {unique_count} 个。"),
        ("格式合规", "pass" if valid_format else "fail", "全部行符合 `知识点名称~必考/有可能考`。" if valid_format else "；".join(invalid_lines[:5])),
    ]
    overall = "fail" if any(status == "fail" for _name, status, _note in statuses) else "pass"
    lines = [
        "# 评测报告",
        "",
        f"- 复习文档：{REVIEW_DOC_NAME}",
        f"- 知识点数量：{total}",
        f"- 必考数量：{must_count}",
        f"- 有可能考数量：{possible_count}",
        f"- 数据来源：{review_data_source_label(focus_docs, source_docs)}",
        "",
        "## 指标",
        "",
        "| 指标 | 结果 | 说明 |",
        "|---|---|---|",
    ]
    lines.extend(f"| {name} | {status} | {note} |" for name, status, note in statuses)
    lines.extend(["", "## 结论", "", f"整体判断：{overall}", f"需要修正：{'无' if overall == 'pass' else '请先修正 fail 指标后再使用。'}", ""])
    return "\n".join(lines)


def list_sources(workspace_id: str) -> list[dict]:
    return read_json(workspace_root(workspace_id) / METADATA_DIR / "sources.json", [])


def list_wiki_pages(workspace_id: str) -> list[dict]:
    return read_json(workspace_root(workspace_id) / METADATA_DIR / "pages.json", [])


def list_wiki_pages_by_root(root: Path) -> list[dict]:
    return read_json(root / METADATA_DIR / "pages.json", [])


def read_page(workspace_id: str, page_path: str) -> dict:
    root = workspace_root(workspace_id)
    target = safe_relative(root, page_path)
    return {"path": page_path, "markdown": target.read_text(encoding="utf-8")}


def save_page(workspace_id: str, page_path: str, markdown: str) -> dict:
    root = workspace_root(workspace_id)
    target = safe_relative(root, page_path)
    target.write_text(markdown, encoding="utf-8")
    append_log(root, f"已在页面中编辑 `{page_path}`。")
    return {"ok": True}


def open_in_obsidian(workspace_id: str, relative_path: str) -> dict:
    root = workspace_root(workspace_id)
    target = safe_relative(root, relative_path)
    if not target.exists():
        raise FileNotFoundError(f"文档不存在：{relative_path}")
    absolute_path = str(target.resolve())
    uri = f"obsidian://open?path={quote(absolute_path, safe='')}"
    open_system_uri(uri)
    append_log(root, f"已从 Wiki 导图打开 `{relative_path}`。")
    return {"ok": True, "path": relative_path, "absolutePath": absolute_path, "uri": uri}


def open_system_uri(uri: str) -> None:
    if sys.platform.startswith("win"):
        os.startfile(uri)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", uri])
    else:
        subprocess.Popen(["xdg-open", uri])


def export_workspace(workspace_id: str) -> Path:
    workspace = get_workspace(workspace_id)
    root = Path(workspace["path"])
    EXPORTS_DIR.mkdir(exist_ok=True)
    output = EXPORTS_DIR / f"{safe_name(workspace['name'])}-{workspace_id[:8]}.zip"
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        for path in root.rglob("*"):
            if path.is_file():
                archive.write(path, path.relative_to(root.parent))
    return output


def workspace_detail(workspace_id: str) -> dict:
    workspace = get_workspace(workspace_id)
    root = Path(workspace["path"])
    sources = list_sources(workspace_id)
    pages = list_wiki_pages(workspace_id)
    issues = read_json(root / METADATA_DIR / "lint.json", [])
    artifacts = read_json(root / METADATA_DIR / "review-artifacts.json", [])
    quality = read_json(root / METADATA_DIR / "quality.json", {})
    semantic_quality = read_json(root / METADATA_DIR / "semantic_quality.json", {})
    wiki_plan = read_json(root / METADATA_DIR / "wiki_plan.json", {})
    try:
        from claude_cli_bridge import get_workspace_console

        console = get_workspace_console(workspace_id)
    except Exception:
        console = {"workspaceId": workspace_id, "sessionId": "", "history": []}
    return {
        **workspace,
        "sources": sources,
        "pages": pages,
        "wikiDiskState": wiki_disk_state(root),
        "issues": issues,
        "artifacts": artifacts,
        "quality": quality,
        "semanticQuality": semantic_quality,
        "wikiPlan": {
            "exists": bool(wiki_plan),
            "sourceCoverageTarget": wiki_plan.get("sourceCoverageTarget"),
            "plannedSourceCoverage": wiki_plan.get("plannedSourceCoverage"),
            "pageCount": len(wiki_plan.get("pages", [])) if isinstance(wiki_plan.get("pages"), list) else 0,
            "updatedAt": wiki_plan.get("updatedAt", ""),
        },
        "console": console,
        "runs": [run for run in load_state()["agentRuns"] if run["workspaceId"] == workspace_id],
    }


def chat_with_agent(workspace_id: str, message: str) -> dict:
    root = workspace_root(workspace_id)
    from llm_wiki_agent import chat_with_wiki_agent

    result = chat_with_wiki_agent(root, message)
    append_log(root, f"用户与 Agent 对话：{message[:120]}")
    return result


def test_llm_connection() -> dict:
    from langchain_core.messages import HumanMessage
    from llm_wiki_agent import build_chat_model, friendly_model_error

    settings = llm_settings()
    if not settings.get("apiKey"):
        return {
            "ok": False,
            "message": "未配置 API Key。",
            "baseUrl": settings.get("baseUrl", ""),
            "model": settings.get("model", ""),
        }
    model = build_chat_model()
    if model is None:
        return {
            "ok": False,
            "message": "模型客户端创建失败，请确认依赖和 API Key。",
            "baseUrl": settings.get("baseUrl", ""),
            "model": settings.get("model", ""),
        }
    try:
        response = model.invoke([HumanMessage(content="请只回复 OK，用于连通性测试。")])
        return {
            "ok": True,
            "message": "模型连接正常。",
            "reply": str(getattr(response, "content", response))[:120],
            "baseUrl": settings.get("baseUrl", ""),
            "model": settings.get("model", ""),
        }
    except Exception as exc:
        return {
            "ok": False,
            "message": friendly_model_error(exc),
            "baseUrl": settings.get("baseUrl", ""),
            "model": settings.get("model", ""),
        }


def build_graph(pages: list[dict], sources: list[dict] | None = None) -> dict:
    source_by_id = {source.get("id"): source for source in sources or []}
    nodes = [{"id": page["id"], "label": page["title"], "type": page["type"], "path": page.get("path")} for page in pages]
    edges = []
    seen_source_nodes: set[str] = set()
    for page in pages:
        for ref in page.get("sourceRefs", []):
            source_id = ref.get("sourceId") or ref.get("storedPath") or ref.get("fileName")
            if not source_id:
                continue
            source = source_by_id.get(source_id, {})
            source_node_id = f"source:{source_id}"
            if source_node_id not in seen_source_nodes:
                seen_source_nodes.add(source_node_id)
                nodes.append(
                    {
                        "id": source_node_id,
                        "label": source.get("storedPath") or ref.get("storedPath") or ref.get("fileName"),
                        "type": "source",
                    }
                )
            edges.append(
                {
                    "from": page["id"],
                    "to": source_node_id,
                    "relation": "uses-section",
                    "section": ref.get("section", "原始文件"),
                    "chunkId": ref.get("chunkId", ""),
                }
            )
    return {"nodes": nodes, "edges": edges}


def append_log(root: Path, message: str) -> None:
    with (root / "log.md").open("a", encoding="utf-8") as handle:
        handle.write(f"\n## {now_iso()}\n\n- {message}\n")


def safe_relative(root: Path, relative: str) -> Path:
    target = (root / relative).resolve()
    root_resolved = root.resolve()
    try:
        target.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError("Path escapes workspace.") from exc
    return target


def read_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _agents_md(name: str) -> str:
    return f"""# {name} / Learning Console Rules

你是 `{name}` 的 Claude Code 学习搭档。这个工作区的核心不是一次性生成或检验 Wiki，而是在每轮学习对话后，把用户真正学到的新内容整理成可长期回看的 Markdown 记忆。

## 核心闭环

1. 和用户对话学习、解释、答疑。
2. 对话结束或用户说“记下来/保存/我懂了/这很重要”时，使用 `learning-memory-save` skill。
3. 将用户确认的新理解、例子、疑问、易错点保存到 `{WIKI_DIR}/`。
4. 当用户需要考试重点或背诵材料时，使用 `review-materials` skill 基于用户上传的重点标注文件和 `{RAW_DIR}/` 生成到 `{REVIEW_DIR}/`；`{WIKI_DIR}/` 只作为可选补充。

## 目录职责

- `{RAW_DIR}/` 是事实来源，由用户上传和维护；只能读取，不得改写、移动、删除。
- `{WIKI_DIR}/` 是对话学习记忆层，用于保存用户在学习中形成的结构化理解。
- `{REVIEW_DIR}/` 是复习输出层，优先依据用户上传的重点标注文件和 `{RAW_DIR}/`，`{WIKI_DIR}/` 只作为可选补充。
- `{SYSTEM_DIR}/` 是隐藏系统目录，服务于界面和缓存，不是主要知识正文。
- `index.md` 是导航入口；`log.md` 是追加式时间线。

## 保存学习记忆

优先使用 `.claude/skills/learning-memory-save/SKILL.md` 的规则。保存时：

- 优先更新已有页面；没有合适页面时，在 `{WIKI_DIR}/对话记忆/` 新建页面。
- 区分 `用户已确认`、`本轮解释`、`例子`、`待确认`、`复习提示`。
- 不把聊天流水、工具状态、临时寒暄写入 Wiki。
- 对未确认内容标为 `待确认`，不要伪装成事实。
- 更新 `index.md`，并向 `log.md` 追加简短记录。

## 复习资料

优先使用 `.claude/skills/review-materials/SKILL.md`。复习资料应该优先基于 `{REVIEW_DIR}/重点文档/` 中的用户重点标注文件，并对照 `{RAW_DIR}/` 生成；`{WIKI_DIR}/` 只作为可选补充。不要因为 Wiki 为空或只有骨架而拒绝生成。

## 禁止事项

- 不要围绕 Wiki 质量检查、语义评分、达标验收展开工作。
- 不要依赖旧 LangGraph Agent 工作流。
- 不要重写或删除用户原始资料。
- 不要为了“看起来完整”编造来源、结论或文件名。
"""


def _index_md(name: str) -> str:
    return f"""# {name}

## 资料

- 暂无资料。

## Wiki 页面

- 暂无 Wiki 页面。

## 复习资料

- 暂无复习资料。
"""




