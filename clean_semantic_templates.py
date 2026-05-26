from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import wiki_engine as engine


TEMPLATE_HEADINGS = {
    "学习目标",
    "阶段验收",
    "实践检查",
    "典型问题",
    "来源解读",
    "学习使用",
}


def strip_template_sections(text: str) -> str:
    lines = text.splitlines()
    result: list[str] = []
    skipping = False
    for line in lines:
        match = re.match(r"^(##)\s+(.+?)\s*$", line)
        if match and match.group(2).strip() in TEMPLATE_HEADINGS:
            skipping = True
            continue
        if skipping and re.match(r"^##\s+", line):
            skipping = False
        if not skipping:
            result.append(line)
    cleaned = "\n".join(result).rstrip() + "\n"
    cleaned = re.sub(r"\n{4,}", "\n\n\n", cleaned)
    return cleaned


def clean_course(course: str) -> dict:
    workspace = next(item for item in engine.list_workspaces() if item["name"] == course)
    root = Path(workspace["path"])
    pages = engine.read_json(root / engine.METADATA_DIR / "pages.json", [])
    changed = []
    for page in pages:
        path = root / page.get("path", "")
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        cleaned = strip_template_sections(text)
        if cleaned != text:
            path.write_text(cleaned, encoding="utf-8")
            changed.append(page.get("title"))
    sources = engine.read_json(root / engine.METADATA_DIR / "sources.json", [])
    issues = engine.lint_workspace(root)
    engine.compute_wiki_quality(root, sources, pages, issues)
    engine.update_index(root, sources, pages)
    return {"course": course, "changed": changed, "issues": len(issues), "pages": len(pages)}


def main() -> int:
    course = sys.argv[1]
    print(json.dumps(clean_course(course), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
