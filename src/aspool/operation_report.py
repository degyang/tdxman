"""Shared public-CLI execution and persisted operation receipt handling."""

import json
import subprocess
from pathlib import Path


def run_command(executable, command, *, cwd, env, capture=False):
    args = [str(executable), *command]
    if capture:
        return subprocess.run(args, cwd=cwd, env=env, text=True, capture_output=True, check=True)
    lines = []
    with subprocess.Popen(
        args,
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    ) as process:
        assert process.stdout is not None
        for line in process.stdout:
            print(line, end="", flush=True)
            lines.append(line)
        return subprocess.CompletedProcess(args, process.wait(), stdout="".join(lines))


def operation_outcome(completed, *, require_report=False):
    """Preserve partial source failures even when the public command exits zero."""
    path = None
    for line in (completed.stdout or "").splitlines():
        if "报告：" in line:
            path = Path(line.rsplit("报告：", 1)[1].strip())
    if path is None:
        if require_report and completed.returncode == 0:
            raise ValueError("Operation completed without a persisted source report")
        return {"status": "ok" if completed.returncode == 0 else "failed"}
    report = json.loads(path.read_text())
    counts = {
        key: len(report.get(key) or [])
        for key in (
            "failed",
            "factor_failed",
            "missing",
            "invalid",
            "empty",
            "stale",
            "factor_unavailable",
            "remaining_block",
        )
    }
    source_status = report.get("status")
    if (
        source_status in ("failed", "aborted_source_failure", "running")
        or counts["remaining_block"]
    ):
        status = "failed"
    elif source_status in ("partial", "completed_with_missing") or any(
        counts[key] for key in ("failed", "factor_failed", "missing", "invalid", "empty", "stale")
    ):
        status = "partial"
    elif source_status == "ok" and completed.returncode == 0:
        status = "ok"
    else:
        status = "failed"
    return {
        "status": status,
        "source_status": source_status,
        "source_report": str(path),
        "source_counts": counts,
    }
