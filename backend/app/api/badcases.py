"""BadCase 回流管理(FR-08):从复核工作台/自动规则生成,回流到 Agent/用例/根因库。"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.auth import get_current_user, require_writer
from app.core.database import get_db
from app.models import (
    Agent, BadCase, CaseExecution, EvaluationRun, RootCause, TestCase, User,
)
from app.schemas import (
    BadCaseBatchFromReviewIn, BadCaseCreateIn, BadCaseListOut, BadCaseOut, BadCaseUpdateIn,
)

router = APIRouter(prefix="/api/badcases", tags=["badcases"])


def _to_out(db: Session, bc: BadCase) -> BadCaseOut:
    run = db.get(EvaluationRun, bc.run_id)
    tc = db.get(TestCase, bc.testcase_id)
    ce = db.get(CaseExecution, bc.case_execution_id)
    return BadCaseOut(
        id=bc.id,
        run_id=bc.run_id,
        run_name=run.name if run else "",
        testcase_id=bc.testcase_id,
        testcase_title=tc.title if tc else "",
        case_execution_id=bc.case_execution_id,
        case_status=ce.status if ce else "",
        attribution_ids=bc.attribution_ids,
        reason=bc.reason,
        reason_detail=bc.reason_detail,
        severity=bc.severity,
        ownership=bc.ownership,
        status=bc.status,
        owner=bc.owner,
        solution=bc.solution,
        solution_type=bc.solution_type,
        fixed_agent_version=bc.fixed_agent_version,
        fixed_testcase_version=bc.fixed_testcase_version,
        fixed_rootcause_id=bc.fixed_rootcause_id,
        verified_run_id=bc.verified_run_id,
        created_by=bc.created_by,
        created_at=bc.created_at,
        updated_at=bc.updated_at,
        resolved_at=bc.resolved_at,
    )


@router.get("", response_model=BadCaseListOut)
def list_badcases(
    status: str | None = None,
    reason: str | None = None,
    severity: str | None = None,
    ownership: str | None = None,
    run_id: int | None = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> BadCaseListOut:
    q = db.query(BadCase)
    if status:
        q = q.filter(BadCase.status == status)
    if reason:
        q = q.filter(BadCase.reason == reason)
    if severity:
        q = q.filter(BadCase.severity == severity)
    if ownership:
        q = q.filter(BadCase.ownership == ownership)
    if run_id:
        q = q.filter(BadCase.run_id == run_id)

    total = q.count()
    rows = q.order_by(BadCase.id.desc()).offset(offset).limit(max(1, min(limit, 200))).all()

    status_dist = {row[0]: row[1] for row in
                   db.query(BadCase.status, func.count()).group_by(BadCase.status).all()}
    reason_dist = {row[0]: row[1] for row in
                   db.query(BadCase.reason, func.count()).group_by(BadCase.reason).all()}
    severity_dist = {row[0]: row[1] for row in
                     db.query(BadCase.severity, func.count()).group_by(BadCase.severity).all()}

    return BadCaseListOut(
        items=[_to_out(db, r) for r in rows],
        total=total,
        status_dist=status_dist,
        reason_dist=reason_dist,
        severity_dist=severity_dist,
    )


@router.get("/{bc_id}", response_model=BadCaseOut)
def get_badcase(bc_id: int, db: Session = Depends(get_db),
                _: User = Depends(get_current_user)) -> BadCaseOut:
    bc = db.get(BadCase, bc_id)
    if bc is None:
        raise HTTPException(404, "BadCase 不存在")
    return _to_out(db, bc)


@router.post("", response_model=BadCaseOut, status_code=201)
def create_badcase(payload: BadCaseCreateIn, db: Session = Depends(get_db),
                   user: User = Depends(require_writer)) -> BadCaseOut:
    # 校验关联资源存在
    if db.get(EvaluationRun, payload.run_id) is None:
        raise HTTPException(404, "评测任务不存在")
    if db.get(TestCase, payload.testcase_id) is None:
        raise HTTPException(404, "评测用例不存在")
    if db.get(CaseExecution, payload.case_execution_id) is None:
        raise HTTPException(404, "执行记录不存在")
    bc = BadCase(**payload.model_dump(exclude_none=True),
                 created_by=user.display_name or user.username)
    db.add(bc)
    db.commit()
    db.refresh(bc)
    return _to_out(db, bc)


@router.post("/batch-from-review", response_model=list[BadCaseOut], status_code=201)
def batch_from_review(payload: BadCaseBatchFromReviewIn,
                      db: Session = Depends(get_db),
                      user: User = Depends(require_writer)) -> list[BadCaseOut]:
    """从复核工作台批量提 BadCase:每组 case_execution_id 各生成一条。"""
    run = db.get(EvaluationRun, payload.run_id)
    if run is None:
        raise HTTPException(404, "评测任务不存在")
    created: list[BadCase] = []
    for exec_id in payload.case_execution_ids:
        ce = db.get(CaseExecution, exec_id)
        if ce is None or ce.run_id != payload.run_id:
            continue  # 跳过无效的
        # 幂等:同一 run + 同一 execution + reason 的 BadCase 只保留一条
        existing = db.query(BadCase).filter_by(
            run_id=payload.run_id, case_execution_id=exec_id, reason=payload.reason,
        ).one_or_none()
        if existing:
            continue
        bc = BadCase(
            run_id=payload.run_id,
            testcase_id=ce.testcase_id,
            case_execution_id=exec_id,
            reason=payload.reason,
            reason_detail=payload.reason_detail,
            severity=payload.severity,
            ownership=payload.ownership,
            created_by=user.display_name or user.username,
        )
        db.add(bc)
        created.append(bc)
    db.commit()
    for bc in created:
        db.refresh(bc)
    return [_to_out(db, bc) for bc in created]


@router.patch("/{bc_id}", response_model=BadCaseOut)
def update_badcase(bc_id: int, payload: BadCaseUpdateIn,
                   db: Session = Depends(get_db),
                   user: User = Depends(require_writer)) -> BadCaseOut:
    bc = db.get(BadCase, bc_id)
    if bc is None:
        raise HTTPException(404, "BadCase 不存在")
    data = payload.model_dump(exclude_none=True)

    # 状态流转简单校验
    if "status" in data:
        new_status = data["status"]
        valid_trans = {
            "open": {"triaged", "fixed", "wontfix", "resolved"},
            "triaged": {"fixed", "wontfix", "resolved", "open"},
            "fixed": {"resolved"},
            "wontfix": {"resolved", "open"},
            "resolved": {"open"},
        }
        allowed = valid_trans.get(bc.status, set())
        if new_status not in allowed and new_status != bc.status:
            raise HTTPException(
                409, f"非法状态流转:{bc.status} → {new_status};允许 → {allowed}")

        # 标记 resolved 时写 resolved_at
        if new_status == "resolved" and bc.status != "resolved":
            bc.resolved_at = datetime.utcnow()

    # 关联修复凭证校验
    if "fixed_rootcause_id" in data and data["fixed_rootcause_id"] is not None:
        if db.get(RootCause, data["fixed_rootcause_id"]) is None:
            raise HTTPException(404, "修复用根因条目不存在")
    if "verified_run_id" in data and data["verified_run_id"] is not None:
        if db.get(EvaluationRun, data["verified_run_id"]) is None:
            raise HTTPException(404, "验证 Run 不存在")

    for k, v in data.items():
        setattr(bc, k, v)
    db.commit()
    db.refresh(bc)
    return _to_out(db, bc)


@router.delete("/{bc_id}", status_code=204)
def delete_badcase(bc_id: int, db: Session = Depends(get_db),
                   user: User = Depends(require_writer)) -> None:
    bc = db.get(BadCase, bc_id)
    if bc is None:
        raise HTTPException(404, "BadCase 不存在")
    db.delete(bc)
    db.commit()


@router.post("/{bc_id}/auto-fix", response_model=BadCaseOut)
def auto_fix_badcase(bc_id: int,
                     db: Session = Depends(get_db),
                     user: User = Depends(require_writer)) -> BadCaseOut:
    """自动修复:对 critical 的 agent_bug 禁用 Agent,对 case_bug 归档用例。"""
    bc = db.get(BadCase, bc_id)
    if bc is None:
        raise HTTPException(404, "BadCase 不存在")
    if bc.status not in ("open", "triaged"):
        raise HTTPException(409, "BadCase 已处理,不可再自动修复")

    action_taken: list[str] = []

    # agent_bug:禁用该 run 关联的 Agent
    if bc.reason == "agent_bug":
        run = db.get(EvaluationRun, bc.run_id)
        if run and run.agent_id:
            agent = db.get(Agent, run.agent_id)
            if agent and agent.enabled:
                agent.enabled = False
                action_taken.append(f"禁用 Agent {agent.name}({agent.id})")
                bc.solution_type = "disable_agent"
                bc.fixed_agent_version = agent.version_label
            else:
                action_taken.append("Agent 已禁用")

    # case_bug:标记用例 disabled(归档)
    if bc.reason == "case_bug":
        tc = db.get(TestCase, bc.testcase_id)
        if tc and tc.enabled:
            tc.enabled = False
            action_taken.append(f"归档用例 {tc.title}({tc.id})")
            bc.solution_type = "archive_testcase"

    # 严重度不是 critical 时,标记为 triaged 而非 fixed,留人工确认
    if bc.severity == "critical" and action_taken:
        bc.status = "fixed"
        bc.resolved_at = datetime.utcnow()
    else:
        bc.status = "triaged"

    bc.solution = "; ".join(action_taken) if action_taken else bc.solution or "无自动动作可用"
    bc.owner = user.display_name or user.username

    db.commit()
    db.refresh(bc)
    return _to_out(db, bc)
