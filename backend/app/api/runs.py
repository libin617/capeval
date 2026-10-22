"""评测任务 API(FR-03/FR-04/FR-06/FR-08):Run 全流程与轨迹回放数据、归因与人工复核。"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.auth import get_current_user, require_writer
from app.core.database import get_db
from app.models import (
    Agent, Attribution, AttributionComment, CaseExecution, CaseSet, CaseSetVersion,
    DimensionScore, EvaluationRun, Report, RootCause, TestCase, TraceEvent, User,
)
from app.schemas import (
    AttributionCommentIn, AttributionCommentOut, AttributionOut, AttributionReviewIn,
    CaseAttributionOut, CaseExecutionOut, CaseScoresOut, DimensionScoreOut, ExecutionDetailOut,
    RootCauseRefOut, RunAttributionOut, RunCreate, RunDetailOut, RunOut, RunProgress, RunScoresOut,
    RunUpdate, TraceEventOut, UndeterminedCaseOut,
)
from app.services import attribution, notify, review, run_ops


def _api_agent_warning(agent: Agent | None) -> str:
    """FR-01/FR-04 异常预警:API 型 Agent 未暴露工具调用/token 用量、或未配置成本计量参数时,
    创建 Run 即提示对应维度将按「数据缺失」降级。"""
    if agent is None or agent.access_type != "api":
        return ""
    cfg = agent.adapter_config or {}
    missing: list[str] = []
    if not bool(cfg.get("expose_tool_calls", False)):
        missing.append("工具调用明细(D3)")
    if not bool(cfg.get("expose_tokens", False)):
        missing.append("token 用量(D6)")
    cost = agent.cost_params or {}
    if not (cost.get("input_price_per_1k") and cost.get("output_price_per_1k")):
        missing.append("成本计量参数(token 单价,API 型必填)")
    if not missing:
        return ""
    return ("API 型 Agent 未暴露/未配置:" + "、".join(missing)
            + ";相关维度评分将降级,并在报告「数据完整性声明」中标注「数据缺失」")


router = APIRouter(prefix="/api/runs", tags=["runs"])
exec_router = APIRouter(prefix="/api/executions", tags=["executions"])
attr_router = APIRouter(prefix="/api/attributions", tags=["attributions"])


def _progress_map(db: Session, run_ids: list[int]) -> dict[int, RunProgress]:
    if not run_ids:
        return {}
    rows = (
        db.query(CaseExecution.run_id, CaseExecution.status, func.count())
        .filter(CaseExecution.run_id.in_(run_ids))
        .group_by(CaseExecution.run_id, CaseExecution.status)
        .all()
    )
    result: dict[int, RunProgress] = {}
    for run_id, status, cnt in rows:
        p = result.setdefault(run_id, RunProgress())
        p.total += cnt
        if status in ("pending", "running", "succeeded", "failed", "canceled"):
            setattr(p, status, getattr(p, status) + cnt)
    return result


def _to_out(db: Session, run: EvaluationRun, progress: RunProgress | None = None) -> RunOut:
    version = db.get(CaseSetVersion, run.caseset_version_id)
    return RunOut(
        id=run.id, name=run.name, agent_id=run.agent_id,
        agent_name=run.agent.name if run.agent else "",
        agent_version_label=run.agent.version_label if run.agent else "",
        caseset_version_id=run.caseset_version_id,
        caseset_name=version.caseset.name if version else "",
        caseset_version_no=version.version_no if version else 0,
        weight_template_id=run.weight_template_id, weight_snapshot=run.weight_snapshot,
        rootcause_version=run.rootcause_version, review_policy=run.review_policy,
        fail_threshold=run.fail_threshold, baseline_run_id=run.baseline_run_id,
        pass_k=run.pass_k,
        judge_config_id=run.judge_config_id,
        judge_config_name=(run.judge_config.name if run.judge_config else
                           (version.judge_config.name if version and version.judge_config else "")),
        status=run.status, error=run.error, created_by=run.created_by,
        created_at=run.created_at, submitted_at=run.submitted_at, completed_at=run.completed_at,
        progress=progress or RunProgress(),
        warning=_api_agent_warning(run.agent),
    )


def _case_out(ce: CaseExecution) -> CaseExecutionOut:
    return CaseExecutionOut(
        id=ce.id, run_id=ce.run_id, testcase_id=ce.testcase_id, order_no=ce.order_no,
        status=ce.status, rerun_count=ce.rerun_count, repetition_no=ce.repetition_no,
        resource_usage=ce.resource_usage,
        error=ce.error, started_at=ce.started_at, finished_at=ce.finished_at,
        testcase_title=ce.testcase.title if ce.testcase else "",
        difficulty=ce.testcase.difficulty if ce.testcase else "",
    )


@router.get("", response_model=list[RunOut])
def list_runs(status: str | None = None, q: str | None = None, db: Session = Depends(get_db),
              user: User = Depends(get_current_user)) -> list[RunOut]:
    # M6:顺带触发复核超期扫描(幂等,失败不影响主流程)
    try:
        notify.scan_review_overdue(db)
    except Exception:  # noqa: BLE001,S110
        pass
    query = db.query(EvaluationRun)
    if status:
        query = query.filter(EvaluationRun.status == status)
    if q:
        # 模糊匹配:任务名称 / Agent 名称 / 用例集名称
        like = f"%{q.strip()}%"
        matching_versions = (
            db.query(CaseSetVersion.id)
            .join(CaseSet, CaseSetVersion.caseset_id == CaseSet.id)
            .filter(CaseSet.name.like(like))
        )
        query = (
            query.outerjoin(Agent, EvaluationRun.agent_id == Agent.id)
            .filter(
                (EvaluationRun.name.like(like))
                | (Agent.name.like(like))
                | (EvaluationRun.caseset_version_id.in_(matching_versions))
            )
        )
    runs = query.order_by(EvaluationRun.id.desc()).all()
    progress = _progress_map(db, [r.id for r in runs])
    return [_to_out(db, r, progress.get(r.id)) for r in runs]


@router.delete("/{run_id}", status_code=204)
def delete_run(run_id: int, db: Session = Depends(get_db),
               user: User = Depends(require_writer)) -> None:
    """删除评测任务:草稿与终态(已完成/失败/已取消)可删,关联执行/评分/归因/报告级联删除;
    进行中(排队/执行/归因/复核)的任务请先取消。"""
    run = db.get(EvaluationRun, run_id)
    if run is None:
        raise HTTPException(404, "评测任务不存在")
    deletable = {"draft", "completed", "failed", "canceled"}
    if run.status not in deletable:
        raise HTTPException(409, "任务正在进行中,请先取消后再删除")
    # 级联清理(自子向父)
    exec_ids = [e.id for e in db.query(CaseExecution.id).filter(CaseExecution.run_id == run_id)]
    if exec_ids:
        attr_ids = [a.id for a in db.query(Attribution.id).filter(Attribution.execution_id.in_(exec_ids))]
        if attr_ids:
            db.query(AttributionComment).filter(AttributionComment.attribution_id.in_(attr_ids)).delete(synchronize_session=False)
        db.query(Attribution).filter(Attribution.execution_id.in_(exec_ids)).delete(synchronize_session=False)
        db.query(DimensionScore).filter(DimensionScore.execution_id.in_(exec_ids)).delete(synchronize_session=False)
        db.query(TraceEvent).filter(TraceEvent.execution_id.in_(exec_ids)).delete(synchronize_session=False)
    db.query(Report).filter(Report.run_id == run_id).delete(synchronize_session=False)
    db.query(CaseExecution).filter(CaseExecution.run_id == run_id).delete(synchronize_session=False)
    db.delete(run)
    db.commit()


@router.post("", response_model=RunOut, status_code=201)
def create_run(payload: RunCreate, db: Session = Depends(get_db),
               user: User = Depends(require_writer)) -> RunOut:
    try:
        run = run_ops.create_run(db, payload)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except run_ops.StateError as exc:
        raise HTTPException(409, str(exc)) from exc
    # M6:创建人取当前身份(通知/留痕按创建人投递)
    run.created_by = user.username
    db.commit()
    db.refresh(run)
    return _to_out(db, run)


@router.patch("/{run_id}", response_model=RunOut)
def update_run(run_id: int, payload: RunUpdate, db: Session = Depends(get_db),
               user: User = Depends(require_writer)) -> RunOut:
    """编辑草稿任务:名称/Agent/用例集版本/权重模板/失败阈值;提交后配置快照固化不可改。"""
    run = db.get(EvaluationRun, run_id)
    if run is None:
        raise HTTPException(404, "评测任务不存在")
    try:
        run = run_ops.update_run(db, run, payload)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except run_ops.StateError as exc:
        raise HTTPException(409, str(exc)) from exc
    return _to_out(db, run)


@router.get("/{run_id}", response_model=RunDetailOut)
def get_run(run_id: int, db: Session = Depends(get_db)) -> RunDetailOut:
    run = db.get(EvaluationRun, run_id)
    if run is None:
        raise HTTPException(404, "评测任务不存在")
    progress = _progress_map(db, [run.id]).get(run.id)
    base = _to_out(db, run, progress).model_dump()
    cases = (
        db.query(CaseExecution).filter_by(run_id=run.id)
        .order_by(CaseExecution.order_no).all()
    )
    return RunDetailOut(**base, cases=[_case_out(c) for c in cases])


@router.post("/{run_id}/submit", response_model=RunOut)
def submit_run(run_id: int, db: Session = Depends(get_db),
               user: User = Depends(require_writer)) -> RunOut:
    run = db.get(EvaluationRun, run_id)
    if run is None:
        raise HTTPException(404, "评测任务不存在")
    try:
        run_ops.submit_run(db, run)
    except run_ops.StateError as exc:
        raise HTTPException(409, str(exc)) from exc
    db.refresh(run)
    return _to_out(db, run, _progress_map(db, [run.id]).get(run.id))


@router.post("/{run_id}/cancel", response_model=RunOut)
def cancel_run(run_id: int, db: Session = Depends(get_db),
               user: User = Depends(require_writer)) -> RunOut:
    run = db.get(EvaluationRun, run_id)
    if run is None:
        raise HTTPException(404, "评测任务不存在")
    try:
        run_ops.cancel_run(db, run)
    except run_ops.StateError as exc:
        raise HTTPException(409, str(exc)) from exc
    db.refresh(run)
    return _to_out(db, run, _progress_map(db, [run.id]).get(run.id))


@router.post("/{run_id}/retry", response_model=RunOut)
def retry_run(run_id: int, db: Session = Depends(get_db),
              user: User = Depends(require_writer)) -> RunOut:
    run = db.get(EvaluationRun, run_id)
    if run is None:
        raise HTTPException(404, "评测任务不存在")
    try:
        run_ops.retry_run(db, run)
    except run_ops.StateError as exc:
        raise HTTPException(409, str(exc)) from exc
    db.refresh(run)
    return _to_out(db, run, _progress_map(db, [run.id]).get(run.id))


@exec_router.get("/{execution_id}", response_model=ExecutionDetailOut)
def get_execution(execution_id: int, db: Session = Depends(get_db)) -> ExecutionDetailOut:
    ce = db.get(CaseExecution, execution_id)
    if ce is None:
        raise HTTPException(404, "执行记录不存在")
    tc: TestCase | None = ce.testcase
    base = _case_out(ce).model_dump()
    return ExecutionDetailOut(**base, tags=tc.tags if tc else [],
                              est_minutes=tc.est_minutes if tc else 0,
                              task_script=tc.task_script if tc else [],
                              acceptance=tc.acceptance if tc else {})


@exec_router.get("/{execution_id}/events", response_model=list[TraceEventOut])
def list_execution_events(execution_id: int, db: Session = Depends(get_db)) -> list[TraceEventOut]:
    if db.get(CaseExecution, execution_id) is None:
        raise HTTPException(404, "执行记录不存在")
    rows = (
        db.query(TraceEvent).filter_by(execution_id=execution_id)
        .order_by(TraceEvent.seq).all()
    )
    # 表无 turn 列:从 payload.turn 提取填充出参,供前端按轮次展示
    return [
        TraceEventOut(id=r.id, seq=r.seq, event_type=r.event_type,
                      turn=int((r.payload or {}).get("turn", 0) or 0),
                      payload=r.payload or {}, truncated=r.truncated, created_at=r.created_at)
        for r in rows
    ]


# ---------- 评分结果(FR-05,M3) ----------
@router.get("/{run_id}/scores", response_model=RunScoresOut)
def get_run_scores(run_id: int, db: Session = Depends(get_db)) -> RunScoresOut:
    """Run 级评分:质量分/效率分/维度汇总 + 各 case 维度分(数据来自 Report)。"""
    run = db.get(EvaluationRun, run_id)
    if run is None:
        raise HTTPException(404, "评测任务不存在")
    report = db.query(Report).filter_by(run_id=run_id).one_or_none()
    if report is None:
        raise HTTPException(404, "评分尚未生成(任务未到评分阶段或无可评分用例)")
    cases = [CaseScoresOut(**c) for c in (report.content or {}).get("cases", [])]
    return RunScoresOut(
        run_id=run_id, quality_score=report.quality_score,
        efficiency_score=report.efficiency_score,
        dimension_summary=report.dimension_summary or {}, cases=cases,
    )


@exec_router.get("/{execution_id}/scores", response_model=list[DimensionScoreOut])
def get_execution_scores(execution_id: int, db: Session = Depends(get_db)) -> list[DimensionScore]:
    """单 case 八维评分明细(含 signals 判定理由)。"""
    if db.get(CaseExecution, execution_id) is None:
        raise HTTPException(404, "执行记录不存在")
    return (
        db.query(DimensionScore).filter_by(execution_id=execution_id)
        .order_by(DimensionScore.dimension).all()
    )


# ---------- 归因结论(FR-06,M4) ----------
def _rc_ref(rc: RootCause) -> RootCauseRefOut:
    return RootCauseRefOut(id=rc.id, code=rc.code, name=rc.name, level=rc.level,
                           responsibility=rc.responsibility)


def _attr_out(attr: Attribution, rc: RootCause) -> AttributionOut:
    return AttributionOut(
        id=attr.id, execution_id=attr.execution_id, rootcause=_rc_ref(rc),
        is_primary=attr.is_primary, confidence=attr.confidence, source=attr.source,
        evidence=attr.evidence or {}, review_status=attr.review_status,
        reviewed_by=attr.reviewed_by or "", review_note=attr.review_note or "",
        review_history=attr.review_history or [],
    )


@router.get("/{run_id}/attributions", response_model=RunAttributionOut)
def get_run_attributions(run_id: int, db: Session = Depends(get_db)) -> RunAttributionOut:
    """Run 级归因:分布/责任归属/未确定清单 + 逐 case 主次根因。"""
    run = db.get(EvaluationRun, run_id)
    if run is None:
        raise HTTPException(404, "评测任务不存在")
    report = db.query(Report).filter_by(run_id=run_id).one_or_none()
    stored = (report.content or {}).get("attribution") if report else None
    if not stored:
        raise HTTPException(404, "归因尚未生成(任务未到归因阶段)")
    agg = attribution.summarize_run(db, run_id)
    attrs = (
        db.query(Attribution)
        .join(CaseExecution, Attribution.execution_id == CaseExecution.id)
        .filter(CaseExecution.run_id == run_id).all()
    )
    return RunAttributionOut(
        run_id=run_id, rootcause_version=run.rootcause_version,
        review_policy=run.review_policy, status=run.status,
        total_attributions=agg["total_attributions"],
        attributed_cases=stored.get("attributed_cases", 0),
        pending_review=sum(1 for a in attrs if a.review_status == "pending"),
        not_sampled=sum(1 for a in attrs if a.review_status == "not_sampled"),
        review_mark=report.review_mark if report else "unreviewed",
        distribution=agg["distribution"], level1_summary=agg["level1_summary"],
        responsibility_summary=agg["responsibility_summary"],
        undetermined=[UndeterminedCaseOut(**u) for u in stored.get("undetermined", [])],
        cases=[CaseAttributionOut(**c) for c in stored.get("cases", [])],
    )


@exec_router.get("/{execution_id}/attributions", response_model=list[AttributionOut])
def get_execution_attributions(execution_id: int, db: Session = Depends(get_db)) -> list[AttributionOut]:
    """单 case 归因结论(主/次根因 + 证据链 + 复核状态)。"""
    if db.get(CaseExecution, execution_id) is None:
        raise HTTPException(404, "执行记录不存在")
    rows = (
        db.query(Attribution, RootCause)
        .join(RootCause, Attribution.rootcause_id == RootCause.id)
        .filter(Attribution.execution_id == execution_id)
        .order_by(Attribution.is_primary.desc(), Attribution.id)
        .all()
    )
    return [_attr_out(a, rc) for a, rc in rows]


@attr_router.patch("/{attribution_id}", response_model=AttributionOut)
def review_attribution(attribution_id: int, payload: AttributionReviewIn,
                       db: Session = Depends(get_db),
                       user: User = Depends(require_writer)) -> AttributionOut:
    """人工复核:confirm 认可 / correct 改判 / reject 判为误评,全部动作留痕(FR-08)。
    复核人取当前登录身份(仅管理员/评测工程师可复核)。"""
    attr = db.get(Attribution, attribution_id)
    if attr is None:
        raise HTTPException(404, "归因结论不存在")
    reviewed_by = user.display_name or user.username
    before_status = attr.review_status
    before_rc = attr.rootcause_id
    if payload.action == "confirm":
        attr.review_status = "confirmed"
    elif payload.action == "reject":
        attr.review_status = "rejected"
    else:  # correct:改指根因条目
        if payload.rootcause_id is None:
            raise HTTPException(422, "correct 操作需提供 rootcause_id")
        rc = db.get(RootCause, payload.rootcause_id)
        if rc is None:
            raise HTTPException(404, "目标根因条目不存在")
        if not rc.enabled:
            raise HTTPException(409, "目标根因条目已停用,不可作为改判目标")
        attr.rootcause_id = rc.id
        attr.source = "human"
        attr.confidence = 1.0
        attr.review_status = "corrected"
    attr.reviewed_by = reviewed_by
    attr.review_note = payload.note
    review.append_history(attr, payload.action, reviewed_by=reviewed_by,
                          from_rootcause_id=before_rc, to_rootcause_id=attr.rootcause_id,
                          note=payload.note, from_status=before_status)
    db.commit()
    db.refresh(attr)
    return _attr_out(attr, attr.rootcause)


# ---------- 归因异议评论(FR-08 异常,M6):任意角色(含只读成员)可提交 ----------
@attr_router.get("/{attribution_id}/comments", response_model=list[AttributionCommentOut])
def list_comments(attribution_id: int, db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)) -> list[AttributionComment]:
    if db.get(Attribution, attribution_id) is None:
        raise HTTPException(404, "归因结论不存在")
    return (
        db.query(AttributionComment).filter_by(attribution_id=attribution_id)
        .order_by(AttributionComment.id).all()
    )


@attr_router.post("/{attribution_id}/comments", response_model=AttributionCommentOut, status_code=201)
def add_comment(attribution_id: int, payload: AttributionCommentIn, db: Session = Depends(get_db),
                user: User = Depends(get_current_user)) -> AttributionComment:
    """对归因结论提交异议评论;viewer 亦可(只读成员唯一可写操作)。"""
    if db.get(Attribution, attribution_id) is None:
        raise HTTPException(404, "归因结论不存在")
    comment = AttributionComment(
        attribution_id=attribution_id, author=user.display_name or user.username,
        role=user.role, content=payload.content,
    )
    db.add(comment)
    db.commit()
    db.refresh(comment)
    return comment


@router.post("/{run_id}/review/complete", response_model=RunOut)
def complete_review(run_id: int, db: Session = Depends(get_db),
                    user: User = Depends(require_writer)) -> RunOut:
    """复核完成:按 fail_threshold 收尾到 completed/failed,报告标记「已复核」。"""
    run = db.get(EvaluationRun, run_id)
    if run is None:
        raise HTTPException(404, "评测任务不存在")
    if run.status != "reviewing":
        raise HTTPException(409, "任务不处于复核阶段")
    review.finalize_after_review(db, run, mark="reviewed")
    db.refresh(run)
    return _to_out(db, run, _progress_map(db, [run.id]).get(run.id))


@router.post("/{run_id}/review/waive", response_model=RunOut)
def waive_review(run_id: int, db: Session = Depends(get_db),
                 user: User = Depends(require_writer)) -> RunOut:
    """放弃复核直接发布(FR-08 异常):报告标注「未经人工复核」。"""
    run = db.get(EvaluationRun, run_id)
    if run is None:
        raise HTTPException(404, "评测任务不存在")
    if run.status != "reviewing":
        raise HTTPException(409, "任务不处于复核阶段")
    review.finalize_after_review(db, run, mark="waived")
    db.refresh(run)
    return _to_out(db, run, _progress_map(db, [run.id]).get(run.id))
