"""人工抽检与修正(FR-08):抽样策略、复核留痕、报告复核标记、放弃复核。

抽检集合(PRD):
- full:全部归因结论进入复核;
- sample:全部低置信度结论(<0.7)+ 全部 LLM 结论 + 按比例随机抽样,其余标记 not_sampled;
- skip:不复核,直接按失败阈值收尾,报告标记「未经人工复核」。
全部「未确定」兜底案件无论策略均强制进入复核。
"""

from __future__ import annotations

import random
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models import Attribution, CaseExecution, EvaluationRun, Report
from app.services import notify
from app.core.config import get_settings

_settings = get_settings()

# 低置信度阈值:归因 confidence 低于该值即进入人工复核(不抽样);可全局调整,也考虑后续扩展为 Run 级覆盖
LOW_CONFIDENCE = _settings.review_low_confidence
# sample 抽样策略下,低置信度 / LLM 结论之外的剩余随机抽样比例
SAMPLE_RATIO = _settings.review_sample_ratio


def _attributions(db: Session, run_id: int) -> list[Attribution]:
    return (
        db.query(Attribution)
        .join(CaseExecution, Attribution.execution_id == CaseExecution.id)
        .filter(CaseExecution.run_id == run_id)
        .order_by(Attribution.id)
        .all()
    )


def apply_review_policy(db: Session, run: EvaluationRun) -> dict[str, Any]:
    """归因完成后按复核策略筛选待复核集合,返回复核工作台摘要。"""
    attrs = _attributions(db, run.id)
    report = db.query(Report).filter_by(run_id=run.id).one_or_none()
    undetermined = ((report.content or {}).get("attribution") or {}).get("undetermined", []) if report else []

    if run.review_policy == "full":
        for a in attrs:
            a.review_status = "pending"
    elif run.review_policy == "sample":
        must = [a for a in attrs if a.confidence < LOW_CONFIDENCE or a.source == "llm"]
        rest = [a for a in attrs if a not in must]
        rng = random.Random(run.id)  # 同 Run 抽样稳定可复现
        k = max(1, round(len(rest) * SAMPLE_RATIO)) if rest else 0
        sampled = set(id(a) for a in rng.sample(rest, k=k))
        for a in attrs:
            if a in must or id(a) in sampled:
                a.review_status = "pending"
            else:
                a.review_status = "not_sampled"
    db.commit()
    pending = sum(1 for a in attrs if a.review_status == "pending")
    return {
        "policy": run.review_policy,
        "pending": pending,
        "not_sampled": sum(1 for a in attrs if a.review_status == "not_sampled"),
        "undetermined": len(undetermined),
        "needs_review": pending > 0 or len(undetermined) > 0,
    }


def append_history(attr: Attribution, action: str, *, reviewed_by: str,
                   from_rootcause_id: int | None, to_rootcause_id: int | None,
                   note: str, from_status: str) -> None:
    """复核动作留痕:操作人、时间、前后结论(PRD FR-08)。"""
    history = list(attr.review_history or [])
    history.append({
        "action": action, "by": reviewed_by,
        "at": datetime.now().isoformat(timespec="seconds"),
        "from_status": from_status, "to_status": attr.review_status,
        "from_rootcause_id": from_rootcause_id, "to_rootcause_id": to_rootcause_id,
        "note": note,
    })
    attr.review_history = history


def _set_mark(db: Session, run: EvaluationRun, mark: str, **extra: Any) -> None:
    report = db.query(Report).filter_by(run_id=run.id).one_or_none()
    if report is None:
        return
    # 报告复核标记只有两态:reviewed / unreviewed(未复核的细分原因记入 content.review)
    report.review_mark = "reviewed" if mark == "reviewed" else "unreviewed"
    content = dict(report.content or {})
    review = dict(content.get("review") or {})
    review.update({"mark": mark, "policy": run.review_policy, **extra})
    content["review"] = review
    report.content = content
    db.commit()


def finalize_after_review(db: Session, run: EvaluationRun, *, mark: str = "reviewed") -> None:
    """复核(或放弃复核)后收尾:按失败阈值判定 failed/completed,并写报告复核标记。"""
    cases = db.query(CaseExecution).filter_by(run_id=run.id).all()
    total = len(cases)
    failed = sum(1 for c in cases if c.status == "failed")
    if failed > 0 and total > 0 and failed / total >= run.fail_threshold:
        run.status = "failed"
        run.error = f"失败用例占比 {failed}/{total} 达到阈值 {run.fail_threshold:.0%}"
    else:
        run.status = "completed"
        run.error = ""
    run.completed_at = datetime.now()
    db.commit()
    _set_mark(db, run, mark, completed_at=run.completed_at.isoformat(timespec="seconds"))
    notify.notify_run_status(db, run)  # M6:完成/失败 → 创建人
