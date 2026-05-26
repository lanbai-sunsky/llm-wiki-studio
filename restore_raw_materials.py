from __future__ import annotations

import shutil
import sys
from pathlib import Path

import wiki_engine as engine


def main() -> None:
    if len(sys.argv) < 3:
        raise SystemExit("Usage: restore_raw_materials.py <course-root> <source-materials-dir>")
    course_root = Path(sys.argv[1]).resolve()
    source_dir = Path(sys.argv[2]).resolve()
    if not course_root.exists():
        raise SystemExit(f"Course root does not exist: {course_root}")
    if not source_dir.exists():
        raise SystemExit(f"Source materials dir does not exist: {source_dir}")
    restore(course_root, source_dir)
    print(f"Restored raw materials: {course_root / engine.RAW_DIR}")


def restore(course_root: Path, source_dir: Path) -> None:
    raw_dir = course_root / engine.RAW_DIR
    backup_dir = course_root / engine.SYSTEM_DIR / "legacy-flat-raw"
    if raw_dir.exists():
        if backup_dir.exists():
            shutil.rmtree(backup_dir)
        backup_dir.parent.mkdir(parents=True, exist_ok=True)
        raw_dir.rename(backup_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)

    records = []
    for file_path in sorted(source_dir.rglob("*")):
        if not file_path.is_file():
            continue
        relative = file_path.relative_to(source_dir)
        target = raw_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        content = file_path.read_bytes()
        target.write_bytes(content)
        suffix = target.suffix.lower()
        if suffix not in engine.SUPPORTED_FORMATS:
            continue
        source_id = stable_source_id(str(relative).replace("\\", "/"))
        records.append(
            {
                "id": source_id,
                "fileName": target.name,
                "relativePath": str(relative).replace("\\", "/"),
                "format": suffix.lstrip(".") or "text",
                "sourceKind": engine.source_kind_for_suffix(suffix),
                "storedPath": str(target.relative_to(course_root)).replace("\\", "/"),
                "parsedPath": "",
                "chunkCount": 0,
                "createdAt": engine.now_iso(),
                "chunks": [],
            }
        )
    engine.write_json(course_root / engine.METADATA_DIR / "sources.json", records)
    engine.write_json(course_root / engine.METADATA_DIR / "pages.json", [])
    engine.write_json(course_root / engine.METADATA_DIR / "graph.json", {"nodes": [], "edges": []})
    refresh_agent_schema(course_root)
    engine.update_index(course_root, records, [])
    engine.append_log(course_root, f"已恢复 `{engine.RAW_DIR}` 的原始文件夹结构，共 {len(records)} 个文件。")


def stable_source_id(value: str) -> str:
    import hashlib

    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:32]


def refresh_agent_schema(course_root: Path) -> None:
    name = course_root.name
    state_path = engine.STATE_PATH
    state = engine.load_state() if state_path.exists() else {"workspaces": []}
    for workspace in state.get("workspaces", []):
        if Path(workspace.get("path", "")).resolve() == course_root:
            name = workspace.get("name") or name
            break
    (course_root / "AGENTS.md").write_text(engine._agents_md(name), encoding="utf-8")


if __name__ == "__main__":
    main()
