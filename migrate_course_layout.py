from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import wiki_engine as engine


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("Usage: migrate_course_layout.py <course-root>")
    root = Path(sys.argv[1]).resolve()
    if not root.exists():
        raise SystemExit(f"Course root does not exist: {root}")
    migrate(root)
    print(f"Migrated: {root}")


def migrate(root: Path) -> None:
    raw_uploads = root / "raw" / "uploads"
    raw_parsed = root / "raw" / "parsed"
    metadata = root / "metadata"
    wiki = root / "wiki"
    review = root / "review"

    raw_target = root / engine.RAW_DIR
    wiki_target = root / engine.WIKI_DIR
    review_target = root / engine.REVIEW_DIR
    metadata_target = root / engine.METADATA_DIR
    parsed_target = root / engine.PARSED_DIR

    for target in [raw_target, wiki_target, review_target, metadata_target, parsed_target]:
        target.mkdir(parents=True, exist_ok=True)

    sources = read_json(metadata / "sources.json")
    moved_uploads = move_raw_uploads(raw_uploads, raw_target, sources)
    move_children(wiki, wiki_target, replace=True)
    move_children(review, review_target, replace=True)
    move_children(metadata, metadata_target, replace=True)
    move_children(raw_parsed, parsed_target, replace=True)

    rewrite_sources(root, moved_uploads, sources)
    rewrite_pages(root)
    rewrite_review_artifacts(root)
    engine.update_raw_index(root)
    remove_legacy_raw_index(root)

    remove_empty_dirs([raw_uploads, raw_parsed, root / "raw", metadata, wiki, review])


def move_children(source_dir: Path, target_dir: Path, *, replace: bool = False) -> dict[str, Path]:
    moved: dict[str, Path] = {}
    if not source_dir.exists():
        return moved
    for item in source_dir.iterdir():
        destination = target_dir / item.name
        if destination.exists():
            if replace:
                if destination.is_dir():
                    shutil.rmtree(destination)
                else:
                    destination.unlink()
            else:
                destination = unique_path(destination)
        item.rename(destination)
        moved[item.name] = destination
    return moved


def move_raw_uploads(source_dir: Path, target_dir: Path, sources: list[dict]) -> dict[str, Path]:
    moved: dict[str, Path] = {}
    if not source_dir.exists():
        return moved
    for source in sources:
        stored_path = source.get("storedPath", "")
        if not stored_path.startswith("raw/uploads/"):
            continue
        current = source_dir / Path(stored_path).name
        if not current.exists():
            continue
        relative = engine.safe_upload_relative_path(source.get("relativePath") or current.name, current.name)
        destination = unique_path(target_dir / relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        current.rename(destination)
        moved[stored_path] = destination
        moved[current.name] = destination
    for item in source_dir.iterdir():
        destination = unique_path(target_dir / item.name)
        item.rename(destination)
        moved[f"raw/uploads/{item.name}"] = destination
        moved[item.name] = destination
    return moved


def unique_path(path: Path) -> Path:
    candidate = path
    counter = 2
    while candidate.exists():
        candidate = path.with_name(f"{path.stem} ({counter}){path.suffix}")
        counter += 1
    return candidate


def rewrite_sources(root: Path, moved_uploads: dict[str, Path], fallback_sources: list[dict] | None = None) -> None:
    sources_path = root / engine.METADATA_DIR / "sources.json"
    if sources_path.exists():
        sources = json.loads(sources_path.read_text(encoding="utf-8"))
    else:
        sources = fallback_sources or []
    if not sources:
        return
    for source in sources:
        stored_path = source.get("storedPath", "")
        old_name = Path(stored_path).name
        moved_path = moved_uploads.get(stored_path) or moved_uploads.get(old_name)
        if moved_path:
            source["storedPath"] = str(moved_path.relative_to(root)).replace("\\", "/")
        elif stored_path.startswith("raw/uploads/"):
            relative = engine.safe_upload_relative_path(source.get("relativePath") or old_name, old_name)
            relative_text = str(relative).replace("\\", "/")
            source["storedPath"] = f"{engine.RAW_DIR}/{relative_text}"
        parsed_path = source.get("parsedPath", "")
        if parsed_path:
            source["parsedPath"] = f"{engine.PARSED_DIR}/{Path(parsed_path).name}"
    sources_path.write_text(json.dumps(sources, ensure_ascii=False, indent=2), encoding="utf-8")


def rewrite_pages(root: Path) -> None:
    pages_path = root / engine.METADATA_DIR / "pages.json"
    if not pages_path.exists():
        return
    pages = json.loads(pages_path.read_text(encoding="utf-8"))
    for page in pages:
        path = page.get("path", "")
        if path.startswith("wiki/"):
            page["path"] = f"{engine.WIKI_DIR}/{path[len('wiki/'):]}"
    pages_path.write_text(json.dumps(pages, ensure_ascii=False, indent=2), encoding="utf-8")


def rewrite_review_artifacts(root: Path) -> None:
    artifacts_path = root / engine.METADATA_DIR / "review-artifacts.json"
    if not artifacts_path.exists():
        return
    artifacts = json.loads(artifacts_path.read_text(encoding="utf-8"))
    for artifact in artifacts:
        path = artifact.get("path", "")
        if path.startswith("review/"):
            artifact["path"] = f"{engine.REVIEW_DIR}/{path[len('review/'):]}"
    artifacts_path.write_text(json.dumps(artifacts, ensure_ascii=False, indent=2), encoding="utf-8")


def remove_legacy_raw_index(root: Path) -> None:
    path = root / "原始资料.md"
    if path.exists():
        path.unlink()


def read_json(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return value if isinstance(value, list) else []


def remove_empty_dirs(paths: list[Path]) -> None:
    for path in paths:
        try:
            if path.exists() and path.is_dir() and not any(path.iterdir()):
                path.rmdir()
        except OSError:
            pass


if __name__ == "__main__":
    main()
