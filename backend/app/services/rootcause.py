"""根因库在线维护(FR-07):三级条目 CRUD、检测信号绑定、停用、版本化。

约定:
- 编码由系统按层级自动生成(RC-x / RC-x.y / RC-x.y.z),用户不可编辑,杜绝冲突;
- 层级固定三级:level 1 无父级;level 2/3 必须指定各自上一级父节点;
- 检测信号 rules 中的 signal 必须来自评分信号目录(scoring.SIGNAL_CATALOG),保存前校验;
- 任何增改/停用都产生新的根因库版本号;修改检测信号时该条目 introduced_version 提升到
  新版本,确保「仅影响新版本之后的 Run」;
- 已被归因结论引用或有子节点的条目禁止删除(仅可停用),停用后不再参与新归因。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import (
    Attribution, CaseExecution, DimensionScore, RootCause, RootCauseLibraryVersion, TraceEvent,
)
from app.schemas import RootCauseCreate, RootCauseUpdate
from app.services.scoring import SIGNAL_CATALOG


class StateError(Exception):
    """层级/引用约束冲突,API 层转 409。"""


class SignalSyntaxError(Exception):
    """检测信号表达式校验失败,API 层转 422。"""


def latest_version(db: Session) -> RootCauseLibraryVersion | None:
    return db.query(RootCauseLibraryVersion).order_by(RootCauseLibraryVersion.version_no.desc()).first()


def _bump_library(db: Session, note: str) -> int:
    cur = latest_version(db)
    nv = (cur.version_no if cur else 0) + 1
    db.add(RootCauseLibraryVersion(version_no=nv, note=note or f"根因库维护 → v{nv}"))
    return nv


def _normalize_signals(raw: dict[str, Any] | None) -> dict[str, Any]:
    raw = raw or {}
    rules: list[dict[str, Any]] = []
    for item in raw.get("rules") or []:
        if isinstance(item, str):
            rules.append({"signal": item, "primary": False})
        elif isinstance(item, dict) and item.get("signal"):
            rules.append({"signal": str(item["signal"]), "primary": bool(item.get("primary"))})
        else:
            raise SignalSyntaxError(f"非法规则信号条目: {item!r}(需为信号名或 {{signal, primary}})")
    unknown = sorted({r["signal"] for r in rules if r["signal"] not in SIGNAL_CATALOG})
    if unknown:
        raise SignalSyntaxError(f"未知评分信号: {', '.join(unknown)};请从信号目录中选择")
    dims = [str(d) for d in (raw.get("related_dimensions") or [])]
    return {"rules": rules, "llm_prompt": str(raw.get("llm_prompt") or ""),
            "related_dimensions": dims}


def _next_code(db: Session, level: int, parent: RootCause | None) -> str:
    if level == 1:
        top = db.query(RootCause).filter(RootCause.level == 1).all()
        used = {int(rc.code.split("-")[-1]) for rc in top if rc.code.split("-")[-1].isdigit()}
        return f"RC-{max(used, default=0) + 1}"
    siblings = db.query(RootCause).filter(RootCause.level == level,
                                          RootCause.code.like(f"{parent.code}.%")).all()
    used = {int(rc.code[len(parent.code) + 1:]) for rc in siblings
            if rc.code[len(parent.code) + 1:].isdigit()}
    return f"{parent.code}.{max(used, default=0) + 1}"


def _evidence_example(db: Session, execution_id: int) -> str:
    """为「未确定案件转新根因」生成证据示例文本(信号 + 事件数)。"""
    signals: set[str] = set()
    for ds in db.query(DimensionScore).filter_by(execution_id=execution_id).all():
        for sig in (ds.details or {}).get("signals", []):
            if sig.get("verdict") in ("fail", "warn"):
                signals.add(str(sig.get("name")))
    ev_count = db.query(func.count(TraceEvent.id)).filter_by(execution_id=execution_id).scalar() or 0
    ce = db.get(CaseExecution, execution_id)
    title = ce.testcase.title if ce and ce.testcase else ""
    return (f"证据示例(execution #{execution_id} · {title}):命中信号 "
            f"{', '.join(sorted(signals)) or '无'} · 轨迹 {ev_count} 条事件")


def list_with_counts(db: Session) -> list[dict[str, Any]]:
    counts = dict(
        db.query(Attribution.rootcause_id, func.count(Attribution.id))
        .group_by(Attribution.rootcause_id).all()
    )
    rows = db.query(RootCause).order_by(RootCause.code).all()
    out: list[dict[str, Any]] = []
    for rc in rows:
        out.append({
            "id": rc.id, "code": rc.code, "level": rc.level, "parent_id": rc.parent_id,
            "name": rc.name, "description": rc.description,
            "responsibility": rc.responsibility, "detection_signals": rc.detection_signals or {},
            "enabled": rc.enabled, "introduced_version": rc.introduced_version,
            "reference_count": counts.get(rc.id, 0),
        })
    return out


def _validate_parent(db: Session, level: int, parent_id: int | None) -> RootCause | None:
    if level == 1:
        if parent_id is not None:
            raise StateError("一级大类不可指定父节点")
        return None
    if parent_id is None:
        raise StateError(f"{level} 级条目必须指定父节点")
    parent = db.get(RootCause, parent_id)
    if parent is None:
        raise StateError("父节点不存在")
    if parent.level != level - 1:
        raise StateError(f"父节点需为 {level - 1} 级条目(当前 {parent.level} 级)")
    return parent


def create_root_cause(db: Session, payload: RootCauseCreate) -> tuple[RootCause, int]:
    parent = _validate_parent(db, payload.level, payload.parent_id)
    code = _next_code(db, payload.level, parent)
    nv = _bump_library(db, payload.note or f"新增根因 {code}")
    description = payload.description
    if payload.source_execution_id is not None:
        if db.get(CaseExecution, payload.source_execution_id) is None:
            raise StateError("来源 execution 不存在")
        example = _evidence_example(db, payload.source_execution_id)
        description = f"{description}\n{example}".strip()
    rc = RootCause(
        code=code, level=payload.level, parent_id=parent.id if parent else None,
        name=payload.name, description=description, responsibility=payload.responsibility,
        detection_signals=_normalize_signals(payload.detection_signals),
        enabled=True, introduced_version=nv,
    )
    db.add(rc)
    db.commit()
    db.refresh(rc)
    return rc, nv


def update_root_cause(db: Session, rc: RootCause, payload: RootCauseUpdate) -> tuple[RootCause, int, str]:
    changed_signals = payload.detection_signals is not None
    signals_new = _normalize_signals(payload.detection_signals) if changed_signals else None
    nv = _bump_library(db, payload.note or f"修改根因 {rc.code}")
    if payload.name is not None:
        rc.name = payload.name
    if payload.description is not None:
        rc.description = payload.description
    if payload.responsibility is not None:
        rc.responsibility = payload.responsibility
    if payload.enabled is not None:
        rc.enabled = payload.enabled
    warning = ""
    if signals_new is not None:
        before = (rc.detection_signals or {}).get("rules")
        rc.detection_signals = signals_new
        rc.introduced_version = nv  # 仅影响新版本之后的 Run
        warning = "检测信号已修改,该变更仅影响新版本之后的评测任务(历史 Run 口径不受影响)"
        if before == signals_new.get("rules"):
            warning = ""
    db.commit()
    db.refresh(rc)
    return rc, nv, warning


def delete_root_cause(db: Session, rc: RootCause) -> int:
    ref = db.query(func.count(Attribution.id)).filter_by(rootcause_id=rc.id).scalar() or 0
    if ref > 0:
        raise StateError(f"该条目已被 {ref} 条归因结论引用,禁止删除,仅可停用")
    child = db.query(func.count(RootCause.id)).filter_by(parent_id=rc.id).scalar() or 0
    if child > 0:
        raise StateError("该条目存在子节点,禁止删除(请先处理子节点)")
    code = rc.code
    db.delete(rc)
    nv = _bump_library(db, f"删除根因 {code}")
    db.commit()
    return nv
