"""统计分析 API(M6,P1 项挑选):趋势看板(跨时间能力曲线)、归因误报率统计。"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.database import get_db
from app.models import Agent, Attribution, CaseSetVersion, EvaluationRun, Report, RootCause, User

router = APIRouter(prefix="/api/stats", tags=["stats"])


@router.get("/trend")
def trend(agent_id: int | None = None, user: User = Depends(get_current_user),
          db: Session = Depends(get_db)) -> dict:
    """趋势看板(P1):同一 Agent 跨时间的能力曲线(质量分/效率分),按完成时间升序。"""
    q = (
        db.query(EvaluationRun, Report, CaseSetVersion)
        .join(Report, Report.run_id == EvaluationRun.id)
        .join(CaseSetVersion, CaseSetVersion.id == EvaluationRun.caseset_version_id)
        .filter(EvaluationRun.status == "completed")
        .order_by(EvaluationRun.completed_at.asc(), EvaluationRun.id.asc())
    )
    agent_name = ""
    if agent_id is not None:
        agent = db.get(Agent, agent_id)
        if agent is None:
            raise HTTPException(404, "Agent 不存在")
        agent_name = f"{agent.name}({agent.version_label})"
        q = q.filter(EvaluationRun.agent_id == agent_id)
    points = [
        {
            "run_id": run.id, "name": run.name,
            "agent_name": (f"{run.agent.name}({run.agent.version_label})"
                           if run.agent else ""),
            "caseset_name": f"{version.caseset.name} v{version.version_no}" if version else "",
            "completed_at": run.completed_at.isoformat(timespec="seconds")
            if run.completed_at else "",
            "quality_score": report.quality_score, "efficiency_score": report.efficiency_score,
        }
        for run, report, version in q.all()
    ]
    return {"agent_id": agent_id, "agent_name": agent_name, "points": points}


@router.get("/false-positive-stats")
def false_positive_stats(user: User = Depends(get_current_user),
                         db: Session = Depends(get_db)) -> dict:
    """归因误报率统计(P1,FR-08 修正数据沉淀):按根因条目统计人工复核中被
    修正/判为误评的比例;误差率 ≥30% 时生成检测信号调整建议。"""
    rows = (
        db.query(Attribution.rootcause_id, Attribution.review_status, func.count())
        .filter(Attribution.review_status.in_(("confirmed", "corrected", "rejected")))
        .group_by(Attribution.rootcause_id, Attribution.review_status)
        .all()
    )
    agg: dict[int, dict[str, int]] = {}
    for rc_id, status, cnt in rows:
        bucket = agg.setdefault(rc_id, {"confirmed": 0, "corrected": 0, "rejected": 0})
        bucket[status] = cnt
    items: list[dict] = []
    for rc_id, bucket in agg.items():
        rc = db.get(RootCause, rc_id)
        if rc is None:
            continue
        reviewed = bucket["confirmed"] + bucket["corrected"] + bucket["rejected"]
        errors = bucket["corrected"] + bucket["rejected"]
        rate = round(errors / reviewed, 4) if reviewed else None
        suggestion = ""
        if rate is not None and rate >= 0.3:
            suggestion = (f"误报率 {rate:.0%} 偏高,建议复核该条目绑定的检测信号"
                          f"({len((rc.detection_signals or {}).get('rules') or [])} 条规则)或收窄触发条件")
        items.append({
            "rootcause_id": rc.id, "code": rc.code, "name": rc.name, "level": rc.level,
            "enabled": rc.enabled,
            "confirmed": bucket["confirmed"], "corrected": bucket["corrected"],
            "rejected": bucket["rejected"], "reviewed": reviewed,
            "error_rate": rate, "suggestion": suggestion,
        })
    items.sort(key=lambda x: (x["error_rate"] is None, -(x["error_rate"] or 0), x["code"]))
    total_reviewed = sum(i["reviewed"] for i in items)
    total_errors = sum(i["corrected"] + i["rejected"] for i in items)
    return {
        "overall": {"reviewed": total_reviewed,
                    "error_rate": round(total_errors / total_reviewed, 4) if total_reviewed else None},
        "items": items,
    }
