"""用例自检服务:在不启动 Agent 的情况下,验证用例的环境准备与验收命令是否能跑通。

流程:还原 repo_snapshot → 执行 env_def.install_commands → 执行 acceptance.auto_commands
→ 返回每步的退出码与日志。
不写入 trace_events / case_executions,仅返回自检结果。
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from app.adapters.base import make_preexec_limits
from app.core.config import get_settings

_settings = get_settings()
# 与 executor 共用同一套超时口径,默认值统一在 config.py 维护
INSTALL_TIMEOUT = _settings.install_timeout_seconds
COMMAND_TIMEOUT = _settings.command_timeout_seconds


def _restore_files(workdir: str, files: dict[str, str]) -> None:
    root = Path(workdir)
    for relpath, content in files.items():
        p = (root / relpath).resolve()
        if not str(p).startswith(str(root.resolve())):
            raise ValueError(f"非法路径: {relpath}")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(str(content), encoding="utf-8")


def _normalize(item: Any) -> tuple[str, int]:
    if isinstance(item, str):
        return item, 0
    if isinstance(item, dict) and item.get("cmd"):
        expect = item.get("expect_exit_code")
        return str(item["cmd"]), int(expect) if expect is not None else 0
    raise ValueError(f"非法命令条目: {item!r}")


def _run(cmd: str, workdir: str, timeout: int) -> dict[str, Any]:
    try:
        proc = subprocess.run(  # noqa: S602
            cmd, shell=True, cwd=workdir, capture_output=True, text=True,
            timeout=timeout, check=False,
        )
        return {"command": cmd, "exit_code": proc.returncode,
                "stdout": (proc.stdout or "")[-2000:], "stderr": (proc.stderr or "")[-2000:]}
    except subprocess.TimeoutExpired:
        return {"command": cmd, "exit_code": -1, "stdout": "", "stderr": f"命令超时(>{timeout}s)"}


def run_self_check(testcase: Any) -> dict[str, Any]:
    """对一条用例执行自检,返回详细结果。"""
    snapshot = testcase.repo_snapshot or {}
    files = snapshot.get("files") or {}
    env_def = testcase.env_def or {}
    acceptance = testcase.acceptance or {}

    workdir = tempfile.mkdtemp(prefix=f"{_settings.temp_dir_prefix}selfcheck-")
    result: dict[str, Any] = {
        "passed": False, "stage": "init",
        "file_count": len(files),
        "install_logs": [], "acceptance_logs": [],
        "error": "",
    }
    try:
        # 1. 还原仓库
        _restore_files(workdir, files)

        # 2. 环境准备
        install_cmds = env_def.get("install_commands") or []
        for item in install_cmds:
            cmd, expect = _normalize(item)
            log = _run(cmd, workdir, INSTALL_TIMEOUT)
            log["expect_exit_code"] = expect
            result["install_logs"].append(log)
            if log["exit_code"] != expect:
                result["stage"] = "env_setup"
                result["error"] = f"环境准备命令失败(退出码 {log['exit_code']}): {cmd}"
                return result

        # 3. 自动化验收
        auto_cmds = acceptance.get("auto_commands") or []
        for item in auto_cmds:
            cmd, expect = _normalize(item)
            log = _run(cmd, workdir, COMMAND_TIMEOUT)
            log["expect_exit_code"] = expect
            result["acceptance_logs"].append(log)
            if log["exit_code"] != expect:
                result["stage"] = "acceptance"
                result["error"] = f"验收命令失败(退出码 {log['exit_code']}): {cmd}"
                return result

        result["passed"] = True
        result["stage"] = "done"
        return result
    except ValueError as e:
        result["stage"] = "restore"
        result["error"] = str(e)
        return result
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
