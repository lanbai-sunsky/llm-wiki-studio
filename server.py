from __future__ import annotations

import json
import mimetypes
import shutil
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import claude_cli_bridge as claude_bridge
import wiki_engine as engine


ROOT = Path(__file__).resolve().parent


class Handler(BaseHTTPRequestHandler):
    server_version = "LLMWikiStudio/1.0"

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_cors_headers()
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:
        try:
            parsed = urlparse(self.path)
            if parsed.path.startswith("/api/"):
                self.handle_api_get(parsed.path, parse_qs(parsed.query))
            elif parsed.path.startswith("/download/"):
                self.handle_download(parsed.path)
            else:
                self.serve_static(parsed.path)
        except Exception as exc:
            self.json({"error": str(exc), "trace": traceback.format_exc()}, status=500)

    def do_POST(self) -> None:
        try:
            parsed = urlparse(self.path)
            if parsed.path == "/api/workspaces":
                payload = self.read_json()
                workspace = engine.create_workspace(payload.get("name", "未命名知识库"), payload.get("description", ""))
                self.json(workspace)
                return
            if parsed.path == "/api/settings":
                payload = self.read_json()
                self.json(engine.public_settings(engine.save_settings(payload)))
                return
            if parsed.path.startswith("/api/workspaces/") and parsed.path.endswith("/upload"):
                workspace_id = parsed.path.split("/")[3]
                self.handle_upload(workspace_id)
                return
            if parsed.path.startswith("/api/workspaces/") and parsed.path.endswith("/review-focus"):
                workspace_id = parsed.path.split("/")[3]
                query = parse_qs(parsed.query)
                self.handle_review_focus_upload(workspace_id, replace=query.get("replace", ["0"])[0] == "1")
                return
            if parsed.path.startswith("/api/workspaces/") and parsed.path.endswith("/initialize"):
                workspace_id = parsed.path.split("/")[3]
                payload = self.read_json()
                run = engine.run_agent(workspace_id, "initialize", payload.get("params", {}))
                self.json(run)
                return
            if parsed.path.startswith("/api/workspaces/") and parsed.path.endswith("/console/start"):
                workspace_id = parsed.path.split("/")[3]
                payload = self.read_json()
                self.json(self.handle_console_start(workspace_id, reset=bool(payload.get("reset"))))
                return
            if parsed.path.startswith("/api/workspaces/") and parsed.path.endswith("/console/send"):
                workspace_id = parsed.path.split("/")[3]
                payload = self.read_json()
                self.json(self.handle_console_send(workspace_id, payload.get("message", "")))
                return
            if parsed.path.startswith("/api/workspaces/") and parsed.path.endswith("/console/reset"):
                workspace_id = parsed.path.split("/")[3]
                self.json(self.handle_console_reset(workspace_id))
                return
            if parsed.path.startswith("/api/workspaces/") and parsed.path.endswith("/open-in-obsidian"):
                workspace_id = parsed.path.split("/")[3]
                payload = self.read_json()
                self.json(engine.open_in_obsidian(workspace_id, payload.get("path", "")))
                return
            if parsed.path.startswith("/api/workspaces/") and parsed.path.endswith("/delete"):
                workspace_id = parsed.path.split("/")[3]
                self.json(engine.delete_workspace(workspace_id))
                return
            if parsed.path.startswith("/api/workspaces/") and parsed.path.endswith("/export"):
                workspace_id = parsed.path.split("/")[3]
                output = engine.export_workspace(workspace_id)
                self.json({"path": str(output), "downloadUrl": f"/download/{output.name}"})
                return
            self.json({"error": "Not found"}, status=404)
        except Exception as exc:
            self.json({"error": str(exc), "trace": traceback.format_exc()}, status=500)

    def do_PATCH(self) -> None:
        try:
            parsed = urlparse(self.path)
            if parsed.path.startswith("/api/workspaces/") and "/pages/" in parsed.path:
                parts = parsed.path.strip("/").split("/")
                workspace_id = parts[2]
                page_path = unquote("/".join(parts[4:]))
                payload = self.read_json()
                self.json(engine.save_page(workspace_id, page_path, payload.get("markdown", "")))
                return
            self.json({"error": "Not found"}, status=404)
        except Exception as exc:
            self.json({"error": str(exc), "trace": traceback.format_exc()}, status=500)

    def do_PUT(self) -> None:
        try:
            parsed = urlparse(self.path)
            if parsed.path == "/api/settings":
                payload = self.read_json()
                self.json(engine.public_settings(engine.save_settings(payload)))
                return
            self.json({"error": "Not found"}, status=404)
        except Exception as exc:
            self.json({"error": str(exc), "trace": traceback.format_exc()}, status=500)

    def do_DELETE(self) -> None:
        try:
            parsed = urlparse(self.path)
            parts = parsed.path.strip("/").split("/")
            if len(parts) == 3 and parts[0] == "api" and parts[1] == "workspaces":
                self.json(engine.delete_workspace(parts[2]))
                return
            self.json({"error": "Not found"}, status=404)
        except Exception as exc:
            self.json({"error": str(exc), "trace": traceback.format_exc()}, status=500)

    def handle_api_get(self, path: str, query: dict) -> None:
        if path == "/api/workspaces":
            self.json(engine.list_workspaces())
            return
        if path == "/api/settings":
            self.json(engine.public_settings())
            return
        if path == "/api/llm/test":
            self.json(engine.test_llm_connection())
            return
        parts = path.strip("/").split("/")
        if len(parts) >= 3 and parts[1] == "workspaces":
            workspace_id = parts[2]
            if len(parts) == 3:
                self.json(engine.workspace_detail(workspace_id))
                return
            if parts[3] == "sources":
                self.json(engine.list_sources(workspace_id))
                return
            if parts[3] == "pages":
                if len(parts) == 4:
                    self.json(engine.list_wiki_pages(workspace_id))
                    return
                page_path = unquote("/".join(parts[4:]))
                self.json(engine.read_page(workspace_id, page_path))
                return
        self.json({"error": "Not found"}, status=404)

    def handle_upload(self, workspace_id: str) -> None:
        content_type = self.headers.get("Content-Type", "")
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        if not content_type.startswith("multipart/form-data") or "boundary=" not in content_type:
            raise ValueError("Expected multipart/form-data.")
        boundary = content_type.split("boundary=", 1)[1].encode()
        files = parse_multipart_files(body, boundary, path_field="relativePaths")
        records = [engine.upload_source(workspace_id, filename, content) for filename, content in files]
        self.json({"uploaded": len(records), "sources": records})

    def handle_review_focus_upload(self, workspace_id: str, *, replace: bool = True) -> None:
        content_type = self.headers.get("Content-Type", "")
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        if not content_type.startswith("multipart/form-data") or "boundary=" not in content_type:
            raise ValueError("Expected multipart/form-data.")
        boundary = content_type.split("boundary=", 1)[1].encode()
        files = parse_multipart_files(body, boundary)
        records = engine.save_review_focus_documents(workspace_id, files, replace=replace)
        self.json({"uploaded": len(records), "documents": records})

    def handle_console_start(self, workspace_id: str, *, reset: bool = False) -> dict:
        workspace = engine.get_workspace(workspace_id)
        root = Path(workspace["path"])
        if reset:
            claude_bridge.reset_workspace_session(workspace_id)
        prompt = claude_bridge.build_console_bootstrap_prompt(workspace.get("name") or root.name, root)
        return claude_bridge.run_claude_session(
            workspace_id,
            workspace_root=root,
            workspace_name=workspace.get("name") or root.name,
            prompt=prompt,
            mode="start",
            reuse_session=not reset,
            stream=True,
            bootstrap=True,
        )

    def handle_console_send(self, workspace_id: str, message: str) -> dict:
        workspace = engine.get_workspace(workspace_id)
        root = Path(workspace["path"])
        return claude_bridge.run_claude_session(
            workspace_id,
            workspace_root=root,
            workspace_name=workspace.get("name") or root.name,
            prompt=message.strip(),
            mode="chat",
            reuse_session=True,
            stream=True,
        )

    def handle_console_reset(self, workspace_id: str) -> dict:
        claude_bridge.reset_workspace_session(workspace_id)
        workspace = engine.get_workspace(workspace_id)
        root = Path(workspace["path"])
        return {
            "ok": True,
            "workspaceId": workspace_id,
            "workspaceName": workspace.get("name") or root.name,
            "sessionId": "",
            "history": [],
            "text": "会话已重置。",
            "events": [],
        }

    def handle_download(self, path: str) -> None:
        filename = unquote(path.split("/", 2)[2])
        target = engine.EXPORTS_DIR / filename
        if not target.exists():
            self.send_error(404, "File not found")
            return
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Disposition", f'attachment; filename="{target.name}"')
        self.send_header("Content-Length", str(target.stat().st_size))
        self.end_headers()
        with target.open("rb") as handle:
            shutil.copyfileobj(handle, self.wfile)

    def serve_static(self, path: str) -> None:
        relative = "index.html" if path in {"", "/"} else path.lstrip("/")
        target = ROOT / relative
        if not target.exists() or target.is_dir():
            target = ROOT / "index.html"
        content_type = mimetypes.guess_type(target.name)[0] or "text/html"
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type + ("; charset=utf-8" if content_type.startswith("text/") else ""))
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def json(self, value, *, status: int = 200) -> None:
        data = json.dumps(value, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")


def parse_multipart_files(body: bytes, boundary: bytes, *, path_field: str | None = None) -> list[tuple[str, bytes]]:
    files = []
    relative_paths = []
    delimiter = b"--" + boundary
    for part in body.split(delimiter):
        part = part.strip(b"\r\n")
        if not part or part == b"--":
            continue
        headers, _, content = part.partition(b"\r\n\r\n")
        if not content:
            continue
        header_text = headers.decode("utf-8", errors="ignore")
        field_name = multipart_value(header_text, "name")
        if path_field and field_name == path_field:
            if content.endswith(b"\r\n"):
                content = content[:-2]
            relative_paths.append(content.decode("utf-8", errors="ignore"))
            continue
        if "filename=" not in header_text:
            continue
        filename = multipart_value(header_text, "filename") or "source.txt"
        if content.endswith(b"\r\n"):
            content = content[:-2]
        files.append((normalize_multipart_filename(filename), content))
    if relative_paths and len(relative_paths) == len(files):
        return [
            (normalize_multipart_filename(relative_path or filename), content)
            for (filename, content), relative_path in zip(files, relative_paths)
        ]
    return files


def multipart_value(header: str, key: str) -> str:
    marker = f'{key}="'
    if marker in header:
        return header.split(marker, 1)[1].split('"', 1)[0]
    return ""


def normalize_multipart_filename(filename: str) -> str:
    filename = filename.replace("\\", "/").strip("/")
    parts = [part for part in filename.split("/") if part not in {"", ".", ".."}]
    return "/".join(parts) or "source.txt"


def run(port: int = 8877) -> None:
    engine.ensure_dirs()
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"LLM Wiki Studio 已启动：http://127.0.0.1:{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    run(port=int(sys.argv[1]) if len(sys.argv) > 1 else 8877)
