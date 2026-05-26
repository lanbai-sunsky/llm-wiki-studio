from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import wiki_engine as engine


def main() -> None:
    original_data_dir = engine.DATA_DIR
    original_workspaces_dir = engine.WORKSPACES_DIR
    original_exports_dir = engine.EXPORTS_DIR
    original_state_path = engine.STATE_PATH
    temp_dir = Path(tempfile.mkdtemp(prefix="llm-wiki-studio-smoke-"))
    try:
        engine.DATA_DIR = temp_dir / "data"
        engine.WORKSPACES_DIR = engine.DATA_DIR / "workspaces"
        engine.EXPORTS_DIR = engine.DATA_DIR / "exports"
        engine.STATE_PATH = engine.DATA_DIR / "state.json"
        engine.SETTINGS_PATH = engine.DATA_DIR / "settings.json"
        custom_root = temp_dir / "custom-root"
        assert not custom_root.exists()
        settings = engine.save_settings({"workspaceRoot": str(custom_root)})
        assert Path(settings["workspaceRoot"]) == custom_root
        assert custom_root.exists()
        workspace = engine.create_workspace("Algorithms", "Smoke test workspace")
        assert Path(workspace["path"]).is_relative_to(custom_root)
        source = engine.upload_source(
            workspace["id"],
            "week1/stack-notes.md",
            b"# Stack\n\nStack is a LIFO data structure.\n\nQueue is a FIFO data structure.",
        )
        assert source["chunkCount"] == 0
        assert source["chunks"] == []
        assert source["parsedPath"] == ""
        assert source["sourceKind"] == "note"
        assert source["relativePath"] == "week1/stack-notes.md"
        assert source["storedPath"] == f"{engine.RAW_DIR}/week1/stack-notes.md"
        assert (Path(workspace["path"]) / source["storedPath"]).exists()
        init_run = engine.run_agent(workspace["id"], "initialize", {})
        assert init_run["status"] == "completed"
        init_detail = engine.workspace_detail(workspace["id"])
        assert init_detail["sources"][0]["chunkCount"] == 0
        assert init_detail["sources"][0]["parsedPath"] == ""
        assert all(page["type"] == "scaffold-index" for page in init_detail["pages"])
        raw_manifest = Path(workspace["path"]) / engine.WIKI_DIR / "课程" / "原始资料文件清单.md"
        assert raw_manifest.exists()
        assert "week1/stack-notes.md" in raw_manifest.read_text(encoding="utf-8")
        assert not any((Path(workspace["path"]) / engine.PARSED_DIR).glob("*.md"))
        for filename, content, expected_kind in (
            ("diagram.png", b"\x89PNG\r\n\x1a\n", "image"),
            ("slides.pdf", b"%PDF-1.7", "pdf"),
            ("lecture.mp3", b"ID3", "audio"),
            ("demo.mp4", b"\x00\x00\x00\x18ftypmp42", "video"),
            ("board.canvas", b'{"nodes":[],"edges":[]}', "canvas-base"),
            ("table.base", b'{"views":[]}', "canvas-base"),
        ):
            uploaded = engine.upload_source(workspace["id"], filename, content)
            assert uploaded["sourceKind"] == expected_kind
        run = engine.run_agent(workspace["id"], "build", {"requireLlmAgent": False})
        assert run["status"] == "completed"
        detail = engine.workspace_detail(workspace["id"])
        assert detail["sources"][0]["chunkCount"] >= 1
        assert detail["sources"][0]["parsedPath"]
        assert detail["pages"]
        assert any(page["type"] != "scaffold-index" for page in detail["pages"])
        assert any(page["type"] == "source-attachment" for page in detail["pages"])
        workspace_root = Path(workspace["path"])
        stale_wiki_file = workspace_root / engine.WIKI_DIR / "专题" / "旧残留页面.md"
        stale_wiki_file.write_text("# 旧残留页面\n\n这应该在初始化重置后消失。\n", encoding="utf-8")
        reset_run = engine.run_agent(workspace["id"], "initialize", {"resetWiki": True})
        assert reset_run["status"] == "completed"
        assert not stale_wiki_file.exists()
        reset_detail = engine.workspace_detail(workspace["id"])
        assert reset_detail["wikiDiskState"]["contentFileCount"] == 0
        assert all(page["type"] == "scaffold-index" for page in reset_detail["pages"])
        visible_wiki_markdown = sorted((workspace_root / engine.WIKI_DIR).rglob("*.md"))
        assert len(visible_wiki_markdown) == len(engine.CATEGORY_SCAFFOLDS) + 1
        assert not (workspace_root / engine.PARSED_DIR).exists()
        assert not (workspace_root / engine.ARCHIVE_DIR).exists()
        assert not (workspace_root / engine.SYSTEM_DIR / "legacy-wiki-fragments").exists()
        assert any((engine.archive_base_dir() / workspace["id"]).glob("wiki-*/已创建的Wiki"))
        run = engine.run_agent(workspace["id"], "build", {"requireLlmAgent": False})
        assert run["status"] == "completed"
        detail = engine.workspace_detail(workspace["id"])
        assert any(page["type"] != "scaffold-index" for page in detail["pages"])
        focus_docs = engine.save_review_focus_documents(
            workspace["id"],
            [("exam-focus.md", b"# Focus\n\nStack is required for the final exam.")],
        )
        assert focus_docs
        review_run = engine.run_agent(
            workspace["id"],
            "review",
            {"examTarget": "pass", "outputFormat": "知识点名称~必考/有可能考", "knowledgeScope": "all", "requireLlmAgent": False},
        )
        assert review_run["status"] == "completed"
        detail = engine.workspace_detail(workspace["id"])
        assert detail["artifacts"]
        review_folder = Path(workspace["path"]) / detail["artifacts"][0]["path"]
        assert review_folder.is_dir()
        review_output = review_folder / "复习资料.md"
        evaluation_output = review_folder / "评测报告.md"
        assert review_output.exists()
        assert evaluation_output.exists()
        focus_text = review_output.read_text(encoding="utf-8")
        assert "~" in focus_text
        assert all(line.endswith("~必考") or line.endswith("~有可能考") for line in focus_text.splitlines() if line.strip())
        assert "评测报告" in evaluation_output.read_text(encoding="utf-8")
        assert (Path(workspace["path"]) / engine.METADATA_DIR / "sources.json").exists()
        assert (Path(workspace["path"]) / engine.PARSED_DIR).exists()
        for visible_internal in ("raw", "metadata", "wiki", "review"):
            assert not (Path(workspace["path"]) / visible_internal).exists()
        output = engine.export_workspace(workspace["id"])
        assert output.exists()
        delete_result = engine.delete_workspace(workspace["id"])
        assert delete_result["ok"] is True
        assert not Path(workspace["path"]).exists()
        assert workspace["id"] not in {item["id"] for item in engine.list_workspaces()}
        assert all(run["workspaceId"] != workspace["id"] for run in engine.load_state()["agentRuns"])
        print("smoke_test: PASS")
    finally:
        engine.DATA_DIR = original_data_dir
        engine.WORKSPACES_DIR = original_workspaces_dir
        engine.EXPORTS_DIR = original_exports_dir
        engine.STATE_PATH = original_state_path
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
