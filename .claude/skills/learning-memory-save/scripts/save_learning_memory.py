from __future__ import annotations

import argparse
import re
from datetime import datetime
from pathlib import Path


WIKI_DIR = "已创建的Wiki"
MEMORY_DIR = "对话记忆"


def safe_name(value: str, fallback: str = "学习记忆") -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", value).strip(" .")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return (cleaned or fallback)[:120]


def ensure_workspace(root: Path) -> None:
    if not (root / WIKI_DIR).exists():
        raise SystemExit(f"Missing {WIKI_DIR}/ in workspace: {root}")


def upsert_index(root: Path, title: str, relative_path: str) -> None:
    index_path = root / "index.md"
    link = f"- [[{title}]]：{relative_path}"
    if index_path.exists():
        text = index_path.read_text(encoding="utf-8")
    else:
        text = "# 课程索引\n"
    if "## 对话记忆" not in text:
        text = text.rstrip() + "\n\n## 对话记忆\n\n"
    if link not in text:
        text = text.rstrip() + "\n" + link + "\n"
    index_path.write_text(text, encoding="utf-8")


def append_log(root: Path, title: str, relative_path: str) -> None:
    log_path = root / "log.md"
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    line = f"- {now} 保存学习记忆：[[{title}]]（{relative_path}）\n"
    if log_path.exists():
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(line)
    else:
        log_path.write_text("# 日志\n\n" + line, encoding="utf-8")


def append_memory(root: Path, title: str, content: str, status: str = "user-confirmed") -> Path:
    ensure_workspace(root)
    target_dir = root / WIKI_DIR / MEMORY_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{safe_name(title)}.md"
    relative_path = str(target.relative_to(root)).replace("\\", "/")
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    if not target.exists():
        header = "\n".join(
            [
                "---",
                f"title: {title}",
                "type: dialogue-memory",
                f"status: {status}",
                "sources:",
                "  - conversation",
                "tags:",
                "  - wiki",
                "  - learning-memory",
                "---",
                "",
                f"# {title}",
                "",
            ]
        )
        target.write_text(header, encoding="utf-8")

    section = "\n".join(
        [
            "",
            f"## {now}",
            "",
            content.strip(),
            "",
        ]
    )
    with target.open("a", encoding="utf-8") as handle:
        handle.write(section)

    upsert_index(root, title, relative_path)
    append_log(root, title, relative_path)
    return target


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("workspace", nargs="?", default=".")
    parser.add_argument("--title", required=True)
    parser.add_argument("--content", required=True)
    parser.add_argument("--status", default="user-confirmed")
    args = parser.parse_args()

    root = Path(args.workspace).resolve()
    target = append_memory(root, args.title, args.content, status=args.status)
    print(str(target))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
