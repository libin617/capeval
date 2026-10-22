"""数据保留策略(PRD 7.4,M6):
- 报告与固化快照永久保留(不做任何清理);
- 原始轨迹事件保留 trace_retention_days(默认 180)天,到期物理清理,但保留被
  归因证据链引用的事件片段(证据链片段随聚合结果长期可查);
- 物理清理仅由管理员触发(接口层已限制 admin 角色)。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import Attribution, Report, TraceEvent


def retention_days() -> int:
    return int(get_settings().trace_retention_days)


def stats(db: Session) -> dict:
    """保留策略概览:总量、过期量、报告数(永久保留)。"""
    days = retention_days()
    threshold = datetime.now() - timedelta(days=days)
    total = db.query(TraceEvent).count()
    expired = db.query(TraceEvent).filter(TraceEvent.created_at < threshold).count()
    return {
        "retention_days": days,
        "trace_events_total": total,
        "trace_events_expired": expired,
        "reports_total": db.query(Report).count(),
        "note": "报告与固化快照永久保留;过期轨迹事件清理时保留被证据链引用的片段",
    }


def _evidence_seqs(db: Session) -> set[tuple[int, int]]:
    """被任意归因结论证据链引用的 (execution_id, seq) 片段,清理时豁免。"""
    kept: set[tuple[int, int]] = set()
    for attr in db.query(Attribution).all():
        exec_id = attr.execution_id
        for seq in (attr.evidence or {}).get("event_seqs") or []:
            try:
                kept.add((exec_id, int(seq)))
            except (TypeError, ValueError):
                continue
    return kept


def purge_expired(db: Session) -> dict:
    """物理清理保留期外的轨迹事件(保留证据链片段)。返回删除数量。"""
    days = retention_days()
    threshold = datetime.now() - timedelta(days=days)
    kept = _evidence_seqs(db)
    removed = 0
    # 分批按执行维度处理,避免一次性载入全表
    exec_ids = [row[0] for row in db.query(TraceEvent.execution_id).distinct().all()]
    for exec_id in exec_ids:
        rows = (
            db.query(TraceEvent)
            .filter(TraceEvent.execution_id == exec_id, TraceEvent.created_at < threshold)
            .all()
        )
        for ev in rows:
            if (ev.execution_id, ev.seq) in kept:
                continue
            db.delete(ev)
            removed += 1
        if removed and removed % 500 < len(rows):
            db.commit()
    db.commit()
    return {"removed": removed, "retention_days": days}
