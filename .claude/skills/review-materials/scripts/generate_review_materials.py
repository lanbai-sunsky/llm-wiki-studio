from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path


RAW_DIR = "原始资料"
WIKI_DIR = "已创建的Wiki"
REVIEW_DIR = "复习资料"
REVIEW_FOCUS_DIR = "复习资料/重点文档"
METADATA_DIR = ".llm-wiki/metadata"
REVIEW_DOC_NAME = "复习资料.md"
EVALUATION_DOC_NAME = "评测报告.md"


def read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        return default


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def page_type(page: dict) -> str:
    return str(page.get("type") or "")


def page_title(page: dict) -> str:
    title = str(page.get("title") or Path(str(page.get("path") or "")).stem or "未命名知识点")
    return normalize_title(title)


def normalize_title(title: str) -> str:
    title = re.sub(r"[\r\n\t]+", " ", title)
    title = re.sub(r"\s+", " ", title)
    title = title.strip(" -*#[]（）()：:|~")
    return title or "未命名知识点"


def split_terms(text: str) -> list[str]:
    text = re.sub(r"`([^`]+)`", r"\1", text)
    candidates = re.findall(r"[A-Za-z][A-Za-z0-9_./+-]{2,48}|[\u4e00-\u9fa5][\u4e00-\u9fa5A-Za-z0-9_./+-]{1,22}", text)
    stop = {
        "README",
        "Markdown",
        "Source",
        "Chunk",
        "暂无内容",
        "原始资料",
        "重点文档",
        "复习资料",
        "已创建的Wiki",
    }
    result: list[str] = []
    for candidate in candidates:
        title = normalize_title(candidate)
        if title in stop or title.isdigit() or len(title) < 2:
            continue
        if title not in result:
            result.append(title)
    return result


def terms_from_focus_docs(focus_docs: list[dict], focus_files: list[Path]) -> list[dict]:
    items: list[dict] = []
    for doc in focus_docs:
        text = " ".join(str(doc.get(key) or "") for key in ("fileName", "summary", "storedPath"))
        for title in split_terms(text)[:24]:
            items.append({"title": title, "source": "focus", "path": doc.get("storedPath", ""), "text": text})
    for path in focus_files:
        text = read_text(path)
        if not text:
            text = path.stem
        for title in split_terms(f"{path.name}\n{text}")[:40]:
            items.append({"title": title, "source": "focus", "path": str(path), "text": text[:1000]})
    return items


def terms_from_sources(source_docs: list[dict]) -> list[dict]:
    items: list[dict] = []
    for source in source_docs:
        chunks = source.get("chunks") or []
        text_parts = [str(source.get("fileName") or source.get("storedPath") or "")]
        for chunk in chunks[:6]:
            text_parts.append(str(chunk.get("section") or ""))
            text_parts.append(str(chunk.get("text") or "")[:1200])
        text = "\n".join(text_parts)
        for title in split_terms(text)[:24]:
            items.append({"title": title, "source": "raw", "path": source.get("storedPath", ""), "text": text[:1000]})
    return items


def terms_from_pages(pages: list[dict]) -> list[dict]:
    items = []
    for page in pages:
        if page_type(page) in {"scaffold-index", "source-attachment"}:
            continue
        items.append({"title": page_title(page), "source": "wiki", "path": page.get("path", ""), "text": str(page)})
    return items


def read_text(path: Path) -> str:
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


def list_focus_files(root: Path) -> list[Path]:
    focus_root = root / REVIEW_FOCUS_DIR
    if not focus_root.exists():
        return []
    return [path for path in focus_root.rglob("*") if path.is_file() and path.suffix.lower() in {".md", ".markdown", ".txt", ".canvas", ".base", ""}]


def importance_for(index: int, total: int, exam_target: str, item: dict) -> str:
    title = str(item.get("title") or "")
    text = " ".join(str(item.get(key) or "") for key in ("title", "text", "path", "source"))
    high_signal = any(
        signal in text
        for signal in (
            "必考",
            "重点",
            "核心",
            "关键",
            "基础",
            "考试",
            "易错",
            "流程",
            "机制",
            "原理",
            "掌握",
            "标注",
        )
    )
    if item.get("source") == "focus" or exam_target == "high" or high_signal:
        return "必考"
    if total <= 3:
        return "必考"
    return "必考" if index < max(1, int(total * 0.7)) and len(title) >= 2 else "有可能考"


def select_items(
    pages: list[dict],
    source_docs: list[dict],
    focus_docs: list[dict],
    focus_files: list[Path],
    exam_target: str,
) -> list[tuple[str, str, dict]]:
    candidates = terms_from_focus_docs(focus_docs, focus_files)
    candidates.extend(terms_from_sources(source_docs))
    candidates.extend(terms_from_pages(pages))
    seen: set[str] = set()
    selected: list[dict] = []
    for item in candidates:
        title = normalize_title(str(item.get("title") or ""))
        key = re.sub(r"\s+", "", title).lower()
        if key in seen:
            continue
        seen.add(key)
        selected.append({**item, "title": title})
    coverage = 1.0 if exam_target == "high" else 0.8
    limit = max(1, int(len(selected) * coverage)) if selected else 0
    selected = selected[:limit]
    total = len(selected)
    return [(str(item["title"]), importance_for(index, total, exam_target, item), item) for index, item in enumerate(selected)]


def render_review_doc(items: list[tuple[str, str, dict]]) -> str:
    if not items:
        return "暂无可用原始资料或重点标注~有可能考\n"
    return "\n".join(f"{title}~{marker}" for title, marker, _page in items) + "\n"


def validate_review_lines(text: str) -> tuple[bool, list[str]]:
    invalid = []
    for index, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        if not re.fullmatch(r"[^~\r\n]+~(必考|有可能考)", line.strip()):
            invalid.append(f"第 {index} 行格式错误：{line}")
    return not invalid, invalid


def data_source_label(focus_docs: list[dict], source_docs: list[dict]) -> str:
    if focus_docs and source_docs:
        return "重点文档 + 原始资料"
    if focus_docs:
        return "重点文档"
    if source_docs:
        return "原始资料"
    return "未检测到可用资料"


def status_line(status: str, note: str) -> str:
    return f"| {status[0]} | {status[1]} | {note} |"


def render_evaluation_doc(
    items: list[tuple[str, str, dict]],
    review_text: str,
    focus_docs: list[dict],
    source_docs: list[dict],
) -> str:
    valid_format, invalid_lines = validate_review_lines(review_text)
    total = len(items) if items else 1 if review_text.strip() else 0
    must_count = sum(1 for _title, marker, _page in items if marker == "必考")
    possible_count = sum(1 for _title, marker, _page in items if marker == "有可能考")
    unique_count = len({title for title, _marker, _page in items})
    focus_count = sum(1 for _title, _marker, item in items if item.get("source") == "focus")
    raw_count = sum(1 for _title, _marker, item in items if item.get("source") == "raw")
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
        f"- 数据来源：{data_source_label(focus_docs, source_docs)}",
        "",
        "## 指标",
        "",
        "| 指标 | 结果 | 说明 |",
        "|---|---|---|",
    ]
    lines.extend(f"| {name} | {status} | {note} |" for name, status, note in statuses)
    lines.extend(
        [
            "",
            "## 结论",
            "",
            f"整体判断：{overall}",
            f"需要修正：{'无' if overall == 'pass' else '请先修正 fail 指标后再使用。'}",
            "",
        ]
    )
    return "\n".join(lines)


def make_output_dir(review_root: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = review_root / f"复习资料-{stamp}"
    output_dir = base
    suffix = 1
    while output_dir.exists():
        suffix += 1
        output_dir = Path(f"{base}-{suffix}")
    output_dir.mkdir(parents=True, exist_ok=False)
    return output_dir


def generate(root: Path, exam_target: str = "pass") -> list[dict]:
    metadata_root = root / METADATA_DIR
    pages = read_json(metadata_root / "pages.json", [])
    source_docs = read_json(metadata_root / "sources.json", [])
    focus_docs = read_json(metadata_root / "review-focus.json", [])
    focus_files = list_focus_files(root)
    items = select_items(pages, source_docs, focus_docs, focus_files, exam_target)
    review_text = render_review_doc(items)
    evaluation_text = render_evaluation_doc(items, review_text, focus_docs, source_docs)

    output_dir = make_output_dir(root / REVIEW_DIR)
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
        metadata_root / "review-artifacts.json",
        [{"title": output_dir.name, "path": str(output_dir.relative_to(root)).replace("\\", "/"), "files": result}],
    )
    return result


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    exam_target = sys.argv[2] if len(sys.argv) > 2 else "pass"
    if not (root / RAW_DIR).exists() and not (root / METADATA_DIR / "sources.json").exists():
        print(f"Not an LLM Wiki Studio workspace or missing raw materials metadata: {root}", file=sys.stderr)
        return 2
    result = generate(root, exam_target=exam_target)
    print(json.dumps({"ok": True, "artifacts": result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
