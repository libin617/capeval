"""沙箱执行器(FR-03/FR-04):消费 queued Run,逐 case 在临时目录中执行并落轨迹。

执行粒度:Run 内 case 串行;同 Agent 的串行约束由调度器(scheduler)保证。
单 case 流程:还原 repo_snapshot → env_def 环境准备 → 逐轮 send_message/fetch_events
→ acceptance.auto_commands 自动化验收 → 汇总 resource_usage。
失败策略(PRD 第 5 章):单 case 失败不阻断;失败占比 ≥ fail_threshold 时 Run 判 failed。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import time
import traceback
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.adapters.base import AdapterAuthError, BaseAdapter, SessionHandle, make_preexec_limits
from app.adapters.registry import get_adapter
from app.core.database import SessionLocal
from app.core.security import decrypt_credential
from app.models import Agent, CaseExecution, EvaluationRun, TestCase, TraceEvent
from app.services import attribution, notify, review, scoring

PAYLOAD_MAX_CHARS = 8000
COMMAND_TIMEOUT_SECONDS = 120
INSTALL_TIMEOUT_SECONDS = 600  # 依赖安装(如 npm install / mvn install)通常耗时较长


class CaseFailed(Exception):
    """单 case 执行失败(可预期原因),不阻断同 Run 其他 case。kind 用于区分可通知的失败类别。"""

    def __init__(self, message: str, *, kind: str = "generic") -> None:
        super().__init__(message)
        self.kind = kind


def _truncate_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    raw = json.dumps(payload, ensure_ascii=False, default=str)
    if len(raw) <= PAYLOAD_MAX_CHARS:
        return payload, False
    shrunk = {k: (str(v)[:PAYLOAD_MAX_CHARS] + "…[truncated]") for k, v in payload.items()}
    return shrunk, True


class _CaseContext:
    """单 case 执行上下文:负责事件落库与资源累计。"""

    def __init__(self, db: Session, execution: CaseExecution, cost_params: dict | None = None) -> None:
        self.db = db
        self.execution = execution
        self.seq = 0
        self.usage: dict[str, Any] = {"input_tokens": 0, "output_tokens": 0, "cost": 0.0}
        self.cost_params = cost_params or {}

    def emit(self, event_type: str, payload: dict[str, Any]) -> None:
        self.seq += 1
        if event_type == "resource":
            tin = int(payload.get("input_tokens", 0) or 0)
            tout = int(payload.get("output_tokens", 0) or 0)
            cost = float(payload.get("cost", 0.0) or 0.0)
            # M6 成本计量:事件未带费用时按 Agent token 单价(每 1k tokens)折算
            if cost == 0.0 and self.cost_params and (tin or tout):
                in_price = float(self.cost_params.get("input_price_per_1k", 0) or 0)
                out_price = float(self.cost_params.get("output_price_per_1k", 0) or 0)
                cost = round(tin / 1000 * in_price + tout / 1000 * out_price, 6)
                payload = {**payload, "cost": cost}
            self.usage["input_tokens"] += tin
            self.usage["output_tokens"] += tout
            self.usage["cost"] += cost
        data, truncated = _truncate_payload(payload)
        self.db.add(TraceEvent(execution_id=self.execution.id, seq=self.seq,
                               event_type=event_type, payload=data, truncated=truncated))

    def flush(self) -> None:
        self.db.commit()


def _restore_repo(workdir: str, snapshot: dict[str, Any], ctx: _CaseContext) -> None:
    snap_type = snapshot.get("type", "files")
    if snap_type != "files":
        raise CaseFailed(f"repo_snapshot.type={snap_type} 暂未支持(M2 仅支持 files)")
    files: dict[str, str] = snapshot.get("files") or {}
    root = Path(workdir)
    restored = 0
    for relpath, content in files.items():
        p = (root / relpath).resolve()
        if not str(p).startswith(str(root.resolve())):  # 防路径穿越
            raise CaseFailed(f"repo_snapshot 含非法路径: {relpath}")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(str(content), encoding="utf-8")
        restored += 1
    ctx.emit("system", {"kind": "repo_restored", "type": "files", "file_count": restored})


def _normalize_command(item: Any) -> tuple[str, int | None]:
    """auto_commands/install_commands 条目:支持纯字符串或 {"cmd": ..., "expect_exit_code": n}。"""
    if isinstance(item, str):
        return item, 0
    if isinstance(item, dict) and item.get("cmd"):
        expect = item.get("expect_exit_code")
        return str(item["cmd"]), int(expect) if expect is not None else 0
    raise CaseFailed(f"非法命令条目: {item!r}")


def _docker_options(workdir: str, limits: dict[str, Any], testcase: TestCase | None) -> tuple[list[str], str]:
    """构造 docker run 的挂载/资源/网络选项与镜像。镜像只取自用例 env_def.docker_image。"""
    env_def = (testcase.env_def or {}) if testcase is not None else {}
    image = str(env_def.get("docker_image") or "")
    if not image:
        raise CaseFailed("docker 隔离模式下该用例未配置镜像,请在「镜像覆盖」中填写")
    extra_volumes: list[str] = []
    # 用例级依赖缓存挂载(如 maven/gradle/go 的依赖目录),形如 "宿主机路径:容器路径"
    for vol in env_def.get("docker_volumes") or []:
        if isinstance(vol, str) and ":" in vol:
            extra_volumes.append(vol)
    mem_mb = int(limits.get("max_memory_mb") or 0)
    cpu_shares = int(limits.get("max_cpu_shares") or 0)
    network = "none" if str(limits.get("network") or "allow") == "deny" else "bridge"
    args = ["-v", f"{workdir}:/work", "-w", "/work", "--network", network]
    # 用例级依赖缓存挂载(如 maven/gradle/go 的依赖目录),形如 "宿主机路径:容器路径"
    for vol in env_def.get("docker_volumes") or []:
        if isinstance(vol, str) and ":" in vol:
            args += ["-v", vol]
    if mem_mb > 0:
        args += ["--memory", f"{mem_mb}m"]
    if cpu_shares > 0:
        args += ["--cpu-shares", str(cpu_shares)]
    return args, image


def _docker_start_container(workdir: str, limits: dict[str, Any], testcase: TestCase | None) -> str:
    """为单个用例启动常驻容器,让环境准备与验收命令共享同一份运行环境。

    此前每条命令各起一个 --rm 容器,装进系统 site-packages 的依赖(pip 等)随容器退出丢失,
    导致「装完却测不到」。改为一个用例一个容器、docker exec 逐条执行,用例结束统一删除。
    """
    opts, image = _docker_options(workdir, limits, testcase)
    name = f"capeval-case-{uuid.uuid4().hex[:12]}"
    cmd = ["docker", "run", "-d", "--rm", "--name", name, *opts,
           image, "sh", "-c", "while true; do sleep 3600; done"]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False)  # noqa: S603
    if proc.returncode != 0:
        detail = ((proc.stderr or "") + (proc.stdout or "")).strip()[-300:]
        raise CaseFailed(f"Docker 容器启动失败(镜像 {image}): {detail}")
    return proc.stdout.strip()


def _docker_remove_container(container_id: str) -> None:
    """强制删除用例容器;清理失败不影响用例结论。"""
    try:
        subprocess.run(["docker", "rm", "-f", container_id],  # noqa: S603
                       capture_output=True, text=True, timeout=30, check=False)
    except Exception:  # noqa: BLE001
        pass


def _docker_exec_cmd(container_id: str, cmd: str) -> list[str]:
    """在用例常驻容器内执行命令,工作目录与已安装依赖在命令之间保持。"""
    return ["docker", "exec", container_id, "sh", "-c", cmd]


def _run_commands(workdir: str, commands: list[Any], ctx: _CaseContext, *,
                  kind: str, fail_message: str, timeout: int = COMMAND_TIMEOUT_SECONDS,
                  limits: dict[str, Any] | None = None,
                  container: str | None = None, image: str = "") -> None:
    limits = limits or {}
    # 隔离模式只由 Agent 配置决定;用例级仅可覆盖镜像与附加卷
    isolation = str(limits.get("isolation_mode") or "host")
    preexec = make_preexec_limits(limits) if isolation == "host" else None
    for item in commands:
        cmd, expect_rc = _normalize_command(item)
        try:
            if isolation == "docker":
                if not container:
                    raise CaseFailed("docker 隔离模式下缺少用例容器,无法执行命令")
                proc = subprocess.run(  # noqa: S603
                    _docker_exec_cmd(container, cmd), capture_output=True, text=True,
                    timeout=timeout, check=False,
                )
            else:
                proc = subprocess.run(  # noqa: S602
                    cmd, shell=True, cwd=workdir, capture_output=True, text=True,
                    timeout=timeout, check=False, preexec_fn=preexec,
                )
            payload = {
                "kind": kind, "command": cmd, "exit_code": proc.returncode,
                "expect_exit_code": expect_rc, "isolation": isolation,
                "stdout_tail": (proc.stdout or "")[-500:], "stderr_tail": (proc.stderr or "")[-500:],
            }
            if image:
                payload["image"] = image
            ctx.emit("terminal", payload)
            if proc.returncode != expect_rc:
                raise CaseFailed(f"{fail_message}(退出码 {proc.returncode},期望 {expect_rc}): {cmd}")
        except subprocess.TimeoutExpired:
            payload = {"kind": kind, "command": cmd, "exit_code": -1,
                       "expect_exit_code": expect_rc, "isolation": isolation,
                       "stderr_tail": f"命令超时(>{timeout}s)"}
            if image:
                payload["image"] = image
            ctx.emit("terminal", payload)
            raise CaseFailed(f"{fail_message}(超时 > {timeout}s): {cmd}") from None


def _execute_case(db: Session, run: EvaluationRun, case_exec: CaseExecution,
                  agent: Agent, credential: str) -> None:
    testcase = db.get(TestCase, case_exec.testcase_id)
    case_exec.status = "running"
    case_exec.started_at = datetime.now()
    db.commit()

    ctx = _CaseContext(db, case_exec, agent.cost_params)
    workdir = tempfile.mkdtemp(prefix=f"capeval-run{run.id}-case{case_exec.id}-")
    started = time.monotonic()
    adapter: BaseAdapter | None = None
    session: SessionHandle | None = None
    container_id: str | None = None
    limits = agent.resource_limits or {}
    isolation = str(limits.get("isolation_mode") or "host")
    max_turns = int(limits.get("max_turns", 100))
    max_seconds = float(limits.get("max_case_minutes", 30)) * 60
    max_cost = float(limits.get("max_case_cost", 0) or 0)

    try:
        ctx.emit("system", {"kind": "case_started", "run_id": run.id, "testcase_id": testcase.id,
                            "title": testcase.title, "workdir": workdir})
        # docker 模式下镜像只来自用例,缺失时提前判失败;容器按用例启动一次,
        # 环境准备与验收命令共用,保证 pip 等依赖安装不会随容器退出丢失
        if isolation == "docker":
            container_id = _docker_start_container(workdir, limits, testcase)
            ctx.emit("system", {"kind": "container_started", "container_id": container_id,
                                "image": (testcase.env_def or {}).get("docker_image")})
        _restore_repo(workdir, testcase.repo_snapshot or {}, ctx)
        image = str((testcase.env_def or {}).get("docker_image") or "")
        install_cmds = (testcase.env_def or {}).get("install_commands") or []
        if install_cmds:
            _run_commands(workdir, install_cmds, ctx, kind="env_setup",
                          fail_message="环境准备失败", timeout=INSTALL_TIMEOUT_SECONDS,
                          limits=limits, container=container_id, image=image)

        adapter = get_adapter(agent.adapter_key, agent.adapter_config, credential,
                              resource_limits=limits)
        session = adapter.start_session(workdir)
        ctx.emit("system", {"kind": "session_started", "adapter": agent.adapter_key,
                            "session_id": session.session_id})

        script: list[dict[str, Any]] = testcase.task_script or []
        for idx, turn in enumerate(script, start=1):
            if idx > max_turns:
                ctx.emit("system", {"kind": "max_turns_exceeded", "max_turns": max_turns})
                raise CaseFailed(f"超出最大轮次限制({max_turns})")
            if time.monotonic() - started > max_seconds:
                ctx.emit("system", {"kind": "case_timeout", "max_case_minutes": limits.get("max_case_minutes", 30)})
                raise CaseFailed("用例执行超时")
            prompt_text = str(turn.get("message", ""))
            # 记录投递给模型的原始 Prompt(角色 user),与模型回答成对展示
            ctx.emit("message", {"role": "user", "content": prompt_text,
                                 "intent_tag": turn.get("intent_tag", ""), "turn": idx})
            adapter.send_message(session, prompt_text)
            events = adapter.fetch_events(session)
            if not events:
                ctx.emit("system", {"kind": "no_response", "turn": idx})
                ctx.flush()
                raise CaseFailed(f"Agent 在第 {idx} 轮无响应")
            for ev in events:
                ctx.emit(ev.event_type, {**ev.payload, "turn": idx})
            ctx.flush()  # 每轮提交,前端可实时看到轨迹推进
            # M6 费用触限:立即终止该 case,判执行失败并归因「费用超限」(PRD FR-03 异常)
            if max_cost > 0 and ctx.usage["cost"] >= max_cost:
                ctx.emit("system", {"kind": "cost_limit_exceeded",
                                    "cost": round(ctx.usage["cost"], 4), "limit": max_cost})
                ctx.flush()
                raise CaseFailed(f"费用触达资源限制上限({ctx.usage['cost']:.4f} ≥ {max_cost})",
                                 kind="cost_limit")

        ctx.emit("system", {"kind": "completion_declared", "reason": "script_exhausted",
                            "detail": f"任务脚本 {len(script)} 轮已全部投递并获得响应"})

        auto_cmds = (testcase.acceptance or {}).get("auto_commands") or []
        if auto_cmds:
            _run_commands(workdir, auto_cmds, ctx, kind="acceptance",
                          fail_message="验收命令失败", limits=limits, container=container_id, image=image)

        case_exec.status = "succeeded"
        ctx.emit("system", {"kind": "case_succeeded"})
    except AdapterAuthError as exc:
        # FR-01 异常:执行中鉴权失败 → case 判执行失败并归因「接入/凭证问题」,
        # Agent 自动置「不可用」并通知管理员,阻止后续任务误用
        case_exec.status = "failed"
        case_exec.error = f"凭证/鉴权失败: {exc}"
        ctx.emit("system", {"kind": "auth_failed", "error": str(exc)})
        notify.mark_agent_unavailable(db, agent.id, str(exc), run_id=run.id)
    except CaseFailed as exc:
        case_exec.status = "failed"
        case_exec.error = str(exc)
        if exc.kind == "cost_limit":
            notify.notify_admins(db, "agent_alert",
                                 f"Agent「{agent.name}」单 case 费用触限",
                                 f"评测任务 #{run.id} 的用例「{testcase.title}」费用触达上限,该 case 已终止。",
                                 link=f"/runs/{run.id}", dedupe_key=f"cost_limit:{run.id}:{case_exec.id}")
        ctx.emit("system", {"kind": "case_failed", "error": str(exc)})
    except Exception as exc:  # noqa: BLE001
        case_exec.status = "failed"
        case_exec.error = f"平台异常: {type(exc).__name__}: {exc}"
        ctx.emit("system", {"kind": "platform_error", "error": case_exec.error,
                            "trace": traceback.format_exc()[-1500:]})
    finally:
        if container_id:
            _docker_remove_container(container_id)
        if adapter is not None and session is not None:
            try:
                adapter.terminate(session)
            except Exception:  # noqa: BLE001
                pass
        ctx.usage["wall_seconds"] = round(time.monotonic() - started, 2)
        ctx.usage["turns"] = len(testcase.task_script or [])
        case_exec.resource_usage = ctx.usage
        case_exec.finished_at = datetime.now()
        ctx.flush()
        shutil.rmtree(workdir, ignore_errors=True)


def finalize_after_review(db: Session, run: EvaluationRun) -> None:
    """复核后收尾(委托 review 服务,保持向后兼容的入口)。"""
    review.finalize_after_review(db, run, mark="reviewed")


def _finalize_run(db: Session, run: EvaluationRun) -> None:
    db.refresh(run)
    if run.status == "canceled":
        return
    # M3:scoring 阶段真实化——八维评分 + Report 生成
    run.status = "scoring"
    db.commit()
    scoring.score_run(db, run)
    # M4:attributing 阶段真实化——规则归因 + 证据链 + (预留)LLM 归因
    run.status = "attributing"
    db.commit()
    attribution.attribute_run(db, run)
    # M6:集体性失败探测(RC-10 候选)→ 通知用例维护者
    notify.detect_collective_failure(db, run)
    # M5:FR-08 按复核策略(materialize 抽检集合);无待复核项则直接收尾
    outcome = review.apply_review_policy(db, run)
    if run.review_policy != "skip" and outcome["needs_review"]:
        run.status = "reviewing"
        db.commit()
        notify.notify_run_status(db, run)  # M6:待复核 → 创建人
        return
    review.finalize_after_review(db, run, mark="unreviewed")


def execute_run(run_id: int) -> None:
    """同步执行一个 queued Run(由调度器线程或测试直接调用)。"""
    db = SessionLocal()
    try:
        run = db.get(EvaluationRun, run_id)
        if run is None or run.status != "queued":
            return
        run.status = "running"
        db.commit()

        agent = db.get(Agent, run.agent_id)
        credential = decrypt_credential(agent.credential_encrypted) if agent else ""
        pending = (
            db.query(CaseExecution)
            .filter_by(run_id=run.id, status="pending")
            .order_by(CaseExecution.order_no)
            .all()
        )
        for case_exec in pending:
            db.refresh(run)
            if run.status == "canceled":
                break
            _execute_case(db, run, case_exec, agent, credential)
        # 取消时把剩余 pending 一并收尾
        db.refresh(run)
        if run.status == "canceled":
            db.query(CaseExecution).filter_by(run_id=run.id, status="pending") \
                .update({"status": "canceled"}, synchronize_session=False)
            db.commit()
            return
        _finalize_run(db, run)
    except Exception as exc:  # noqa: BLE001
        run = db.get(EvaluationRun, run_id)
        if run is not None and run.status not in ("completed", "failed", "canceled"):
            run.status = "failed"
            run.error = f"平台异常: {type(exc).__name__}: {exc}"
            run.completed_at = datetime.now()
            db.commit()
            notify.notify_run_status(db, run)  # M6:失败 → 创建人
        traceback.print_exc()
    finally:
        db.close()
