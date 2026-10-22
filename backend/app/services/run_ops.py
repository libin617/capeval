"""评测任务状态机服务(PRD 4.3):create/submit/cancel/retry 的集中校验。

允许流转:draft→queued;queued/running/draft→canceled;failed→queued(仅重跑失败 case);
执行期流转(queued→running→…→completed/failed)由 executor 负责。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.adapters.registry import ADAPTERS
from app.models import (
    Agent, Attribution, CaseExecution, CaseSetVersion, DimensionScore, EvaluationRun,
    LlmJudgeConfig, RootCauseLibraryVersion, TraceEvent, WeightTemplate,
)
from app.schemas import RunCreate, RunUpdate

TERMINAL_STATUS = {"completed", "failed", "canceled"}


class StateError(Exception):
    """非法状态流转,API 层转为 409。"""


def _check_agent(db: Session, agent_id: int) -> Agent:
    agent = db.get(Agent, agent_id)
    if agent is None:
        raise LookupError("Agent 不存在")
    if agent.status != "available":
        raise StateError(f"Agent 当前状态为 {agent.status},仅「可用」状态可用于评测任务")
    adapter_cls = ADAPTERS.get(agent.adapter_key)
    if adapter_cls is None or not adapter_cls.supports_execution:
        raise StateError(f"适配器 {agent.adapter_key} 暂不支持任务执行(M2 仅支持 cli 型)")
    return agent


def _check_version(db: Session, version_id: int) -> CaseSetVersion:
    version = db.get(CaseSetVersion, version_id)
    if version is None:
        raise LookupError("用例集版本不存在")
    if version.status != "published":
        raise StateError("仅「已发布」的用例集版本可用于评测(草稿版本需先发布)")
    if not version.items:
        raise StateError("用例集版本为空,不可评测")
    return version


def _check_judge_config(db: Session, config_id: int) -> LlmJudgeConfig:
    cfg = db.get(LlmJudgeConfig, config_id)
    if cfg is None:
        raise LookupError("评审模型配置不存在")
    if not cfg.enabled:
        raise StateError("所选评审模型未启用,请先在「评审模型」页签启用")
    return cfg


def update_run(db: Session, run: EvaluationRun, payload: RunUpdate) -> EvaluationRun:
    """编辑草稿任务:变更 Agent/用例集版本/权重模板时重新校验并刷新快照。"""
    if run.status != "draft":
        raise StateError("仅草稿状态的任务可编辑;已提交任务的配置快照已固化")
    data = payload.model_dump(exclude_unset=True)

    agent = _check_agent(db, data["agent_id"]) if "agent_id" in data else run.agent
    if "caseset_version_id" in data:
        version = _check_version(db, data["caseset_version_id"])
    else:
        version = db.get(CaseSetVersion, run.caseset_version_id)
    _check_case_execution(agent, version)
    if "weight_template_id" in data:
        tpl = db.get(WeightTemplate, data["weight_template_id"])
        if tpl is None:
            raise LookupError("权重模板不存在")
        run.weight_template_id = tpl.id
        run.weight_snapshot = dict(tpl.weights)

    if "agent_id" in data:
        run.agent_id = agent.id
    if "caseset_version_id" in data:
        run.caseset_version_id = version.id
    if "name" in data:
        # 显式传空串时按新组合重新生成默认名
        run.name = data["name"] or f"{agent.name}({agent.version_label}) × {version.caseset.name} v{version.version_no}"
    if "review_policy" in data:
        run.review_policy = data["review_policy"]
    if "fail_threshold" in data:
        run.fail_threshold = data["fail_threshold"]
    if "judge_config_id" in data:
        # 显式传 null 表示清除覆盖,回退到评测集版本绑定的评审模型
        if data["judge_config_id"] is None:
            run.judge_config_id = None
        else:
            run.judge_config_id = _check_judge_config(db, data["judge_config_id"]).id
    if "pass_k" in data:
        run.pass_k = data["pass_k"]
    db.commit()
    db.refresh(run)
    return run


def _check_case_execution(agent: Agent, version: CaseSetVersion) -> None:
    """校验用例声明的执行方式与 Agent 隔离模式兼容,并兜底检查 docker 下的镜像。"""
    mode = str((agent.resource_limits or {}).get("isolation_mode") or "host")
    incompatible: list[str] = []
    missing_image: list[str] = []
    for item in version.items:
        case = item.testcase
        env_def = case.env_def or {}
        target = str(env_def.get("target_isolation") or "any")
        if target != "any" and target != mode:
            incompatible.append(f"{case.title}(声明 {target})")
        if mode == "docker" and target != "host" and not str(env_def.get("docker_image") or "").strip():
            missing_image.append(case.title)
    if incompatible:
        raise StateError(f"以下评测用例的执行方式与所选 Agent({mode}) 不兼容:" + "、".join(incompatible))
    if missing_image:
        raise StateError("以下评测用例未配置镜像,无法在 docker 隔离的 Agent 下执行:" + "、".join(missing_image))


def create_run(db: Session, payload: RunCreate) -> EvaluationRun:
    agent = db.get(Agent, payload.agent_id)
    if agent is None:
        raise LookupError("Agent 不存在")
    if agent.status != "available":
        raise StateError(f"Agent 当前状态为 {agent.status},仅「可用」状态可创建评测任务")
    adapter_cls = ADAPTERS.get(agent.adapter_key)
    if adapter_cls is None or not adapter_cls.supports_execution:
        raise StateError(f"适配器 {agent.adapter_key} 暂不支持任务执行(M2 仅支持 cli 型)")

    version = db.get(CaseSetVersion, payload.caseset_version_id)
    if version is None:
        raise LookupError("用例集版本不存在")
    if version.status != "published":
        raise StateError("仅「已发布」的用例集版本可用于评测(草稿版本需先发布)")
    if not version.items:
        raise StateError("用例集版本为空,不可评测")
    _check_case_execution(agent, version)

    tpl = db.get(WeightTemplate, payload.weight_template_id)
    if tpl is None:
        raise LookupError("权重模板不存在")

    if payload.baseline_run_id is not None and db.get(EvaluationRun, payload.baseline_run_id) is None:
        raise LookupError("基线 Run 不存在")

    # 评审模型:显式指定则校验启用状态,否则继承评测集版本绑定
    judge_config_id = payload.judge_config_id
    if judge_config_id is not None:
        judge_config_id = _check_judge_config(db, judge_config_id).id
    else:
        judge_config_id = version.judge_config_id

    rc_version = max((v.version_no for v in db.query(RootCauseLibraryVersion).all()), default=1)
    name = payload.name or f"{agent.name}({agent.version_label}) × {version.caseset.name} v{version.version_no}"
    run = EvaluationRun(
        name=name, agent_id=agent.id, caseset_version_id=version.id,
        weight_template_id=tpl.id, weight_snapshot=dict(tpl.weights),
        rootcause_version=rc_version, review_policy=payload.review_policy,
        fail_threshold=payload.fail_threshold, baseline_run_id=payload.baseline_run_id,
        judge_config_id=judge_config_id, pass_k=payload.pass_k,
        status="draft",
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def submit_run(db: Session, run: EvaluationRun) -> None:
    if run.status != "draft":
        raise StateError(f"当前状态 {run.status} 不可提交(仅草稿可提交)")
    version = db.get(CaseSetVersion, run.caseset_version_id)
    pass_k = max(1, int(run.pass_k or 1))
    for item in version.items:
        # Pass@k:每个用例创建 pass_k 条 case_execution,repetition_no 0..pass_k-1
        for rep in range(pass_k):
            db.add(CaseExecution(run_id=run.id, testcase_id=item.testcase_id,
                                 order_no=item.order_no, repetition_no=rep,
                                 status="pending"))
    run.status = "queued"
    run.submitted_at = datetime.now()
    db.commit()


def cancel_run(db: Session, run: EvaluationRun) -> None:
    if run.status in TERMINAL_STATUS:
        raise StateError(f"任务已终态({run.status}),不可取消")
    run.status = "canceled"
    run.completed_at = datetime.now()
    # queued 时调度器不会再领取,直接收尾;running 时执行器在 case 边界协作式停止
    db.query(CaseExecution).filter_by(run_id=run.id, status="pending") \
        .update({"status": "canceled"}, synchronize_session=False)
    db.commit()


def retry_run(db: Session, run: EvaluationRun) -> None:
    if run.status != "failed":
        raise StateError(f"当前状态 {run.status} 不可重试(仅失败任务可重试)")
    failed_cases = (
        db.query(CaseExecution).filter_by(run_id=run.id, status="failed")
        .order_by(CaseExecution.order_no).all()
    )
    if not failed_cases:
        raise StateError("无失败用例可重跑")
    for ce in failed_cases:
        db.query(TraceEvent).filter_by(execution_id=ce.id).delete(synchronize_session=False)
        # M3/M4:清除旧维度评分与归因结论,重跑后由评分/归因引擎重算
        db.query(DimensionScore).filter_by(execution_id=ce.id).delete(synchronize_session=False)
        db.query(Attribution).filter_by(execution_id=ce.id).delete(synchronize_session=False)
        ce.status = "pending"
        ce.error = ""
        ce.rerun_count += 1
        ce.started_at = None
        ce.finished_at = None
        ce.resource_usage = {}
    run.status = "queued"
    run.error = ""
    run.completed_at = None
    db.commit()
