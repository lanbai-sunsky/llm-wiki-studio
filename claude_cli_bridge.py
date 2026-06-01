from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
CLI_STATE_PATH = DATA_DIR / "claude-sessions.json"
DEFAULT_MODEL = os.environ.get("CLAUDE_CODE_MODEL", "").strip()


def ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def load_state() -> dict:
    ensure_data_dir()
    if not CLI_STATE_PATH.exists():
        return {"workspaces": {}}
    try:
        data = json.loads(CLI_STATE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"workspaces": {}}
    if not isinstance(data, dict):
        return {"workspaces": {}}
    data.setdefault("workspaces", {})
    return data


def save_state(state: dict) -> None:
    ensure_data_dir()
    CLI_STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _find_claude_executable() -> list[str]:
    candidates = [
        shutil.which("claude.cmd"),
        shutil.which("claude"),
        shutil.which("claude.exe"),
    ]
    for candidate in candidates:
        if candidate:
            return [candidate]
    raise FileNotFoundError("找不到 Claude Code CLI，请先安装 claude 命令。")


def _session_dir(workspace_id: str) -> Path:
    path = DATA_DIR / "claude-sessions" / workspace_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def _history_path(workspace_id: str) -> Path:
    return _session_dir(workspace_id) / "history.jsonl"


def _append_history(workspace_id: str, role: str, text: str, *, command: str | None = None, session_id: str | None = None) -> None:
    record = {
        "id": uuid.uuid4().hex,
        "role": role,
        "text": text,
        "command": command,
        "sessionId": session_id,
    }
    with _history_path(workspace_id).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _read_history(workspace_id: str, limit: int = 200) -> list[dict]:
    path = _history_path(workspace_id)
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    entries: list[dict] = []
    for line in lines[-limit:]:
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def get_workspace_session(workspace_id: str) -> dict:
    state = load_state()
    return state["workspaces"].get(workspace_id, {})


def ensure_workspace_session(workspace_id: str, *, workspace_name: str | None = None) -> dict:
    state = load_state()
    workspaces = state.setdefault("workspaces", {})
    session = workspaces.get(workspace_id)
    if not session:
        now = now_iso()
        session = {
            "workspaceId": workspace_id,
            "workspaceName": workspace_name or workspace_id,
            "sessionId": "",
            "createdAt": now,
            "updatedAt": "",
            "lastCommand": "",
        }
        workspaces[workspace_id] = session
        save_state(state)
    elif workspace_name and session.get("workspaceName") != workspace_name:
        session["workspaceName"] = workspace_name
        save_state(state)
    return session


def update_workspace_session(workspace_id: str, **updates: Any) -> dict:
    state = load_state()
    session = state.setdefault("workspaces", {}).setdefault(workspace_id, {"workspaceId": workspace_id})
    if "updatedAt" not in updates:
        updates["updatedAt"] = now_iso()
    session.update({key: value for key, value in updates.items() if value is not None})
    save_state(state)
    return session


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _build_command(
    prompt: str,
    *,
    session_id: str | None,
    cwd: Path,
    add_dirs: list[Path],
    resume: bool = False,
    stream: bool = True,
) -> list[str]:
    command = _find_claude_executable()
    command.extend(["-p", "--verbose"])
    command.extend(["--output-format", "stream-json" if stream else "json"])
    if stream:
        command.append("--include-partial-messages")
    if DEFAULT_MODEL:
        command.extend(["--model", DEFAULT_MODEL])
    command.extend([
        "--append-system-prompt",
        (
            "你是在一个课程学习控制台里工作。页面只负责操控 Claude Code CLI；"
            "核心闭环是和用户对话学习，然后用 `learning-memory-save` skill 把用户新理解、例子、疑问和易错点保存到 `已创建的Wiki/`。"
            "当用户需要考试整理或复习输出时，用 `review-materials` skill 基于用户上传的重点标注文件和 `原始资料/` 生成 `复习资料/`；"
            "Wiki 只能作为可选补充，绝不能因为 Wiki 为空而拒绝生成。"
            "该 skill 只能在一个本次输出文件夹中写 `复习资料.md` 和 `评测报告.md` 两个文件，复习资料行格式必须是 `知识点名称~必考/有可能考`。"
            "不要把 Wiki 当作需要打分验收的最终产物；它是对话学习记忆层。"
            "当 `sources.json` 中存在 `chunkStrategy: chapter` 的长文档时，必须逐章处理：先读 sources.json 查看进度，再用 Read 工具的 offset/limit 参数只读取单章行范围，处理完用 `wiki_engine.py chapter-done` 标记。禁止一次性读取全文。"
        ),
    ])
    for directory in add_dirs:
        command.extend(["--add-dir", str(directory)])
    if session_id:
        if resume:
            command.extend(["--resume", session_id])
        else:
            command.extend(["--session-id", session_id])
    return command


def _parse_stream_chunk(line: str) -> tuple[str, str]:
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return "raw", line
    event = data.get("event", {})
    if data.get("type") == "assistant" and isinstance(data.get("message"), dict):
        content = data["message"].get("content") or []
        texts = [item.get("text", "") for item in content if isinstance(item, dict) and item.get("type") == "text"]
        if texts:
            return "assistant", "\n".join(texts)
    if event.get("type") == "content_block_delta":
        delta = event.get("delta", {})
        if delta.get("type") == "text_delta":
            return "delta", str(delta.get("text", ""))
        if delta.get("type") == "thinking_delta":
            return "thinking", str(delta.get("thinking", ""))
    if data.get("type") == "result":
        return "result", str(data.get("result", ""))
    return "event", line


def run_claude_session(
    workspace_id: str,
    *,
    workspace_root: Path,
    workspace_name: str,
    prompt: str,
    mode: str = "chat",
    reuse_session: bool = True,
    stream: bool = True,
    bootstrap: bool = False,
) -> dict:
    session = ensure_workspace_session(workspace_id, workspace_name=workspace_name)
    session_id = session.get("sessionId") or ""
    command = _build_command(
        prompt,
        session_id=session_id if session_id else None,
        cwd=ROOT,
        add_dirs=[ROOT, workspace_root],
        resume=reuse_session and bool(session_id),
        stream=stream,
    )
    _append_history(workspace_id, "user", prompt, command=" ".join(command), session_id=session_id or None)
    events: list[dict] = []
    stdout_lines: list[str] = []
    stderr_lines: list[str] = []
    process = subprocess.Popen(
        command,
        cwd=str(workspace_root),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    assert process.stdin is not None
    process.stdin.write(prompt)
    process.stdin.close()

    stdout_lock = threading.Lock()
    stderr_lock = threading.Lock()

    def read_stdout() -> None:
        assert process.stdout is not None
        for raw_line in process.stdout:
            line = raw_line.rstrip("\r\n")
            if not line:
                continue
            stdout_lines.append(line)
            kind, payload = _parse_stream_chunk(line)
            events.append({"channel": "stdout", "kind": kind, "text": payload, "raw": line})

    def read_stderr() -> None:
        assert process.stderr is not None
        for raw_line in process.stderr:
            line = raw_line.rstrip("\r\n")
            if not line:
                continue
            stderr_lines.append(line)
            events.append({"channel": "stderr", "kind": "stderr", "text": line, "raw": line})

    stdout_thread = threading.Thread(target=read_stdout, daemon=True)
    stderr_thread = threading.Thread(target=read_stderr, daemon=True)
    stdout_thread.start()
    stderr_thread.start()
    return_code = process.wait()
    stdout_thread.join(timeout=2)
    stderr_thread.join(timeout=2)

    session_id_out = session_id
    result_text = ""
    parsed_result = None
    assistant_texts: list[str] = []
    for line in stdout_lines:
        try:
            candidate = json.loads(line)
        except json.JSONDecodeError:
            continue
        if candidate.get("type") == "result":
            parsed_result = candidate
        if candidate.get("type") == "assistant" and isinstance(candidate.get("message"), dict):
            msg = candidate["message"]
            content = msg.get("content") or []
            texts = [item.get("text", "") for item in content if isinstance(item, dict) and item.get("type") == "text"]
            if texts:
                assistant_texts.append("\n".join(texts).strip())
                parsed_result = candidate
    if parsed_result and parsed_result.get("session_id"):
        session_id_out = str(parsed_result["session_id"])
    if assistant_texts:
        result_text = assistant_texts[-1].strip()
    elif parsed_result and isinstance(parsed_result.get("result"), str):
        result_text = parsed_result["result"].strip()
    elif stdout_lines:
        result_text = stdout_lines[-1].strip()
    if not result_text:
        result_text = "Claude CLI 没有返回可见文本。"

    update_workspace_session(
        workspace_id,
        workspaceName=workspace_name,
        sessionId=session_id_out,
        lastCommand=prompt,
        lastMode=mode,
        lastBootstrap=bootstrap,
    )
    _append_history(workspace_id, "assistant", result_text, session_id=session_id_out)

    return {
        "workspaceId": workspace_id,
        "workspaceName": workspace_name,
        "sessionId": session_id_out,
        "mode": mode,
        "command": command,
        "returnCode": return_code,
        "text": result_text,
        "events": events[-400:],
        "history": _read_history(workspace_id),
        "stdout": stdout_lines[-200:],
        "stderr": stderr_lines[-50:],
        "ok": return_code == 0,
    }


def get_workspace_console(workspace_id: str) -> dict:
    session = get_workspace_session(workspace_id)
    return {
        "workspaceId": workspace_id,
        "sessionId": session.get("sessionId", ""),
        "workspaceName": session.get("workspaceName", ""),
        "lastCommand": session.get("lastCommand", ""),
        "lastMode": session.get("lastMode", ""),
        "lastBootstrap": session.get("lastBootstrap", False),
        "updatedAt": session.get("updatedAt", ""),
        "createdAt": session.get("createdAt", ""),
        "history": _read_history(workspace_id),
    }


def reset_workspace_session(workspace_id: str) -> None:
    state = load_state()
    if workspace_id in state.get("workspaces", {}):
        del state["workspaces"][workspace_id]
        save_state(state)
    shutil.rmtree(_session_dir(workspace_id), ignore_errors=True)


def build_console_bootstrap_prompt(workspace_name: str, workspace_root: Path) -> str:
    return (
        f"你现在在 `{workspace_name}` 的 Claude Code CLI 控制台中。"
        f"工作区路径是 `{workspace_root}`。"
        "先快速确认你能看到项目说明、课程目录和本项目的 skills。"
        "后续优先通过 `learning-memory-save` 保存对话学习记忆，"
        "并通过 `review-materials` 基于重点标注文件和原始资料生成复习资料。"
        "Wiki 只是可选补充，不能因为 Wiki 为空而拒绝生成。"
        "复习资料输出固定为一个文件夹中的 `复习资料.md` 和 `评测报告.md`，"
        "`复习资料.md` 只使用 `知识点名称~必考/有可能考` 行格式。"
        "只回复一条简短 READY，并说明这个工作方式。"
    )
