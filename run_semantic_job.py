from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

import wiki_engine as engine


JOB_DIR = Path(__file__).resolve().parent / "data" / "semantic-jobs"


def write_status(course: str, status: dict) -> None:
    JOB_DIR.mkdir(parents=True, exist_ok=True)
    (JOB_DIR / f"{safe_job_name(course)}.json").write_text(
        json.dumps(status, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def safe_job_name(value: str) -> str:
    return "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in value)


def find_workspace(course: str) -> dict:
    for workspace in engine.list_workspaces():
        if workspace["name"] == course:
            return workspace
    raise KeyError(f"课程不存在：{course}")


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: run_semantic_job.py <course-name>")
        return 2
    course = sys.argv[1]
    status = {"course": course, "status": "running", "stage": "starting", "score": None, "grade": None}
    write_status(course, status)
    try:
        workspace = find_workspace(course)
        status.update({"stage": "improve"})
        write_status(course, status)
        improve_run = engine.run_agent(
            workspace["id"],
            "improve",
            {"requireLlmAgent": True, "rewriteCorePages": True, "createSourceBriefs": True},
        )
        status.update({"stage": "evaluate", "improveStatus": improve_run["status"]})
        write_status(course, status)
        evaluate_run = engine.run_agent(
            workspace["id"],
            "evaluate",
            {"requireLlmAgent": True, "includeSourceEvidence": True, "strictMode": True},
        )
        detail = engine.workspace_detail(workspace["id"])
        semantic = detail.get("semanticQuality") or {}
        status.update(
            {
                "status": "completed",
                "stage": "done",
                "evaluateStatus": evaluate_run["status"],
                "score": semantic.get("overallScore"),
                "grade": semantic.get("grade"),
                "pages": len(detail.get("pages", [])),
                "risks": (semantic.get("risks") or [])[:3],
                "nextActions": (semantic.get("nextActions") or [])[:3],
            }
        )
        write_status(course, status)
        print(json.dumps(status, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        status.update(
            {
                "status": "failed",
                "stage": status.get("stage", "unknown"),
                "error": str(exc),
                "trace": traceback.format_exc(),
            }
        )
        write_status(course, status)
        print(json.dumps(status, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
