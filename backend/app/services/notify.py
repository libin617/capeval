"""站内通知(PRD 7.2,M6):
① Run 完成/失败/待复核 → 创建人(即时);② Agent 凭证失效/校验失败/费用触限 → 管理员(即时);
③ 集体性失败 RC-10 候选(同一用例 ≥3 个不同 Agent 的 Run 均失分且根因各异) → 用例维护者;
④ 复核待办超 3 个工作日 → 复核人 + 创建人。
MVP 角色映射:管理员=admin 角色,用例维护者/复核人=evaluator 角色。dedupe_key 保证同一事件只提醒一次。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models import (
    Agent, Attribution, CaseExecution, DimensionScore, EvaluationRun, Notification, RootCause, User,
)
from app.core.config import get_settings

_settings = get_settings()

# 复核待办超期天数:Run 提交 reviewing 后超过该天数仍未完成复核 → 通知创建人 + evaluator
REVIEW_OVERDUE_DAYS = _settings.review_overdue_days
REVIEW_OVERDUE_DEDUPE = "review_overdue:{run_id}"

# RC-10 集体性失败探测阈值:
# 同一用例在 ≥RC10_MIN_AGENTS 个不同 Agent 的 Run 中失分(D1<RC10_D1_FAIL_SCORE 或执行失败),
# 且根因各异(主根因 ≥RC10_MIN_DISTINCT_ROOTCAUSES 种) → 建议用例维护者核查
RC10_MIN_AGENTS = _settings.rc10_min_agents
RC10_MIN_DISTINCT_ROOTCAUSES = _settings.rc10_min_distinct_rootcauses
RC10_D1_FAIL_SCORE = _settings.rc10_d1_fail_score


def _recipients_by_role(db: Session, role: str) -> list[str]:
    return [u.username for u in db.query(User).filter(User.role == role).all()]


def notify(db: Session, recipient: str, category: str, title: str, content: str = "",
           link: str = "", dedupe_key: str = "") -> Notification | None:
    if not recipient:
        return None
    if dedupe_key:
        exists = db.query(Notification).filter_by(recipient=recipient, dedupe_key=dedupe_key).first()
        if exists:
            return None
    row = Notification(recipient=recipient, category=category, title=title, content=content,
                       link=link, dedupe_key=dedupe_key)
    db.add(row)
    db.commit()
    return row


def notify_admins(db: Session, category: str, title: str, content: str = "",
                  link: str = "", dedupe_key: str = "") -> None:
    for username in _recipients_by_role(db, "admin"):
        notify(db, username, category, title, content, link, f"{dedupe_key}:{username}" if dedupe_key else "")


def notify_run_status(db: Session, run: EvaluationRun) -> None:
    """Run 完成/失败/待复核 → 创建人(PRD 7.2①)。"""
    if run.status not in ("completed", "failed", "reviewing"):
        return
    label = {"completed": "已完成", "failed": "已失败", "reviewing": "进入待复核"}[run.status]
    detail = f"错误摘要:{run.error[:200]}" if run.status == "failed" and run.error else ""
    notify(db, run.created_by, "run_status", f"评测任务 #{run.id} {label}",
           f"「{run.name}」状态变更为 {label}。{detail}", link=f"/runs/{run.id}",
           dedupe_key=f"run_status:{run.id}:{run.status}")


def mark_agent_unavailable(db: Session, agent_id: int, reason: str, run_id: int | None = None) -> None:
    """执行中鉴权失败/凭证失效:Agent 置「不可用」并通知管理员(PRD FR-01 异常)。"""
    admins = _recipients_by_role(db, "admin")
    agent = db.get(Agent, agent_id)
    if agent is not None:
        agent.status = "unavailable"
        agent.last_verify_error = reason
        db.commit()
    agent_name = agent.name if agent else str(agent_id)
    for username in admins:
        notify(db, username, "agent_alert", f"Agent「{agent_name}」已置为不可用",
               f"原因:{reason}" + (f"(发生于评测任务 #{run_id})" if run_id else ""),
               link="/agents", dedupe_key=f"agent_unavailable:{agent_id}:{reason[:40]}")


def notify_verify_failed(db: Session, agent) -> None:
    """连通性校验失败 → 管理员(PRD 7.2②)。"""
    notify_admins(db, "agent_alert",
                  f"Agent「{agent.name}({agent.version_label})」连通性校验失败",
                  f"错误摘要:{agent.last_verify_error[:200]}。修复后可重新校验。",
                  link="/agents", dedupe_key=f"agent_verify_failed:{agent.id}")


def detect_collective_failure(db: Session, run: EvaluationRun) -> None:
    """集体性失败探测(PRD FR-06):同一用例在 ≥N 个不同 Agent 的 Run 中均失分且根因各异
    → 建议 RC-10 候选并通知用例维护者(evaluator)。幂等:dedupe_key=rc10:{testcase_id}。"""
    cases = db.query(CaseExecution).filter_by(run_id=run.id).all()
    if not cases:
        return
    for ce in cases:
        # 失分定义:执行失败,或 D1 代码正确性低于阈值(默认 3.0)
        failing = ce.status == "failed"
        if not failing:
            d1 = db.query(DimensionScore).filter_by(execution_id=ce.id, dimension="D1").first()
            failing = d1 is not None and d1.score < RC10_D1_FAIL_SCORE
        if not failing:
            continue
        # 历史上该用例在其它 Run(不同 Agent)中的失分情况
        others = (
            db.query(CaseExecution, EvaluationRun, Attribution, RootCause)
            .join(EvaluationRun, CaseExecution.run_id == EvaluationRun.id)
            .join(Attribution, (Attribution.execution_id == CaseExecution.id)
                  & Attribution.is_primary.is_(True))
            .join(RootCause, Attribution.rootcause_id == RootCause.id)
            .filter(CaseExecution.testcase_id == ce.testcase_id,
                    CaseExecution.status.in_(("succeeded", "failed")),
                    EvaluationRun.id != run.id,
                    EvaluationRun.status.in_(("completed", "failed", "reviewing")))
            .all()
        )
        agent_ids = {r.agent_id for _, r, _, _ in others}
        codes = {rc.code for _, _, _, rc in others}
        distinct_agents = len(agent_ids | {run.agent_id})
        # 根因各异:含本次至少出现 ≥N 种不同主根因
        my_primary = (
            db.query(RootCause).join(Attribution, Attribution.rootcause_id == RootCause.id)
            .filter(Attribution.execution_id == ce.id, Attribution.is_primary.is_(True)).first()
        )
        all_codes = set(codes) | ({my_primary.code} if my_primary else set())
        if distinct_agents < RC10_MIN_AGENTS or len(all_codes) < RC10_MIN_DISTINCT_ROOTCAUSES:
            continue
        for username in _recipients_by_role(db, "evaluator"):
            notify(db, username, "rc10_candidate",
                   f"用例 #{ce.testcase_id} 疑似集体性失败(RC-10 候选)",
                   f"该用例已在 {distinct_agents} 个不同 Agent 的评测中失分,且根因各异"
                   f"({', '.join(sorted(all_codes))}),建议核查用例本身是否存在缺陷。",
                   link=f"/runs/{run.id}", dedupe_key=f"rc10:{ce.testcase_id}")


def scan_review_overdue(db: Session) -> int:
    """复核待办超期扫描(幂等,可由查询接口顺带触发或手动触发):超 3 天仍 reviewing 的 Run
    → 通知创建人 + evaluator(复核人)。返回本次新增通知数。"""
    threshold = datetime.now() - timedelta(days=REVIEW_OVERDUE_DAYS)
    runs = (
        db.query(EvaluationRun)
        .filter(EvaluationRun.status == "reviewing", EvaluationRun.submitted_at.isnot(None),
                EvaluationRun.submitted_at < threshold)
        .all()
    )
    created = 0
    for run in runs:
        key = REVIEW_OVERDUE_DEDUPE.format(run_id=run.id)
        msg_title = f"评测任务 #{run.id} 复核待办已超期"
        msg_content = f"「{run.name}」自提交起已超过 {REVIEW_OVERDUE_DAYS} 天未完成复核,请及时处理或放弃复核直接发布。"
        for recipient in {run.created_by, *_recipients_by_role(db, "evaluator")}:
            row = notify(db, recipient, "review_overdue", msg_title, msg_content,
                         link=f"/runs/{run.id}", dedupe_key=f"{key}:{recipient}")
            created += 1 if row else 0
    return created
