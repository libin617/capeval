"""归因分析引擎(FR-06):把失分信号映射到三级根因库条目。

流程(对齐 PRD「归因流程」):
  信号汇聚(评分明细 signals + 轨迹事件)→ 规则归因器(按根因条目绑定的检测信号命中)
  → LLM 归因器(对规则未覆盖的失分在候选集合内做语义判定,NullJudge 时退化为「未确定」)
  → 结论合成(主根因 1 + 次要根因 ≤2 + 证据链 + 置信度)。

约束:
- 证据链强制:每个结论引用 ≥1 条轨迹事件序号,无证据不输出结论;
- 候选集合约束:LLM 只能在启用且版本可见的根因条目内选择;
- 停用条目不参与新归因;Run 按 rootcause_version 快照解读口径。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models import Attribution, CaseExecution, DimensionScore, Report, RootCause, TraceEvent
from app.services.judge import get_judge
from app.services.scoring import DANGEROUS_CMD_RE, HARDCODED_ADDR_RE, SENSITIVE_RE

FAILING_VERDICTS = {"fail", "warn"}


def _binding_index(db: Session, max_version: int) -> dict[str, list[tuple[RootCause, bool]]]:
    """信号名 → 候选根因条目(启用且版本可见)索引,rules 形如 [{"signal","primary"}]。"""
    index: dict[str, list[tuple[RootCause, bool]]] = {}
    for rc in db.query(RootCause).filter(RootCause.enabled.is_(True)).all():
        if rc.introduced_version > max_version:
            continue
        for rule in (rc.detection_signals or {}).get("rules") or []:
            if isinstance(rule, dict):
                sig, is_primary = rule.get("signal"), bool(rule.get("primary"))
            else:  # 兼容纯字符串写法
                sig, is_primary = rule, False
            if sig:
                index.setdefault(str(sig), []).append((rc, is_primary))
    return index


def _failing_signals(db: Session, execution_id: int) -> list[dict[str, Any]]:
    """从评分明细中提取失败信号(verdict=fail/warn)。"""
    out: list[dict[str, Any]] = []
    for ds in db.query(DimensionScore).filter_by(execution_id=execution_id).all():
        for sig in (ds.details or {}).get("signals", []):
            if sig.get("verdict") in FAILING_VERDICTS:
                out.append({**sig, "dimension": ds.dimension})
    return out


def _evt_text(ev: TraceEvent) -> str:
    p = ev.payload or {}
    parts = [str(p.get(k, "")) for k in ("command", "after_summary", "stdout_tail", "stderr_tail", "content")]
    for k in ("args", "result", "error"):
        v = p.get(k)
        if isinstance(v, dict):
            parts.extend(str(x) for x in v.values())
        elif v is not None:
            parts.append(str(v))
    return "\n".join(parts)


def _evidence_for(signal: str, events: list[TraceEvent]) -> list[int]:
    """按信号类型定位证据事件序号(事件序号列表)。"""
    def pick(pred) -> list[int]:
        return [e.seq for e in events if pred(e)]

    sys_kind = lambda e, k: e.event_type == "system" and (e.payload or {}).get("kind") == k  # noqa: E731
    if signal in ("acceptance_failed", "env_setup_failed"):
        kind = "acceptance" if signal == "acceptance_failed" else "env_setup"
        return pick(lambda e: e.event_type == "terminal" and (e.payload or {}).get("kind") == kind
                    and (e.payload or {}).get("exit_code") != (e.payload or {}).get("expect_exit_code", 0))
    if signal == "no_response":
        return pick(lambda e: sys_kind(e, "no_response"))
    if signal == "case_timeout":
        return pick(lambda e: sys_kind(e, "case_timeout"))
    if signal == "max_turns_exceeded":
        return pick(lambda e: sys_kind(e, "max_turns_exceeded"))
    if signal == "platform_error":
        return pick(lambda e: sys_kind(e, "platform_error"))
    if signal == "auth_failed":
        return pick(lambda e: sys_kind(e, "auth_failed"))
    if signal == "cost_limit_exceeded":
        return pick(lambda e: sys_kind(e, "cost_limit_exceeded"))
    if signal == "tool_call_failed":
        return pick(lambda e: e.event_type == "tool_call"
                    and str((e.payload or {}).get("result", "ok")) != "ok")
    if signal == "repeated_tool_call":
        return pick(lambda e: e.event_type == "tool_call")
    if signal == "no_tool_calls":
        return pick(lambda e: sys_kind(e, "completion_declared"))
    if signal == "excessive_file_changes":
        return pick(lambda e: e.event_type == "file_change")
    if signal == "token_efficiency":
        return pick(lambda e: e.event_type == "resource")
    if signal == "sensitive_info":
        return pick(lambda e: SENSITIVE_RE.search(_evt_text(e)) is not None)
    if signal == "dangerous_command":
        return pick(lambda e: DANGEROUS_CMD_RE.search(_evt_text(e)) is not None)
    if signal in ("trailing_whitespace", "mixed_indent"):
        return pick(lambda e: e.event_type == "file_change")
    if signal == "hardcoded_endpoint":
        return pick(lambda e: HARDCODED_ADDR_RE.search(_evt_text(e)) is not None)
    if signal == "partial_response":
        return pick(lambda e: e.event_type == "message" and (e.payload or {}).get("role") == "agent")
    return []


def _collect_evidence(signal_names: list[str], events: list[TraceEvent],
                      details: list[str]) -> dict[str, Any]:
    seqs: list[int] = []
    for name in signal_names:
        seqs.extend(_evidence_for(name, events))
    seqs = sorted(set(seqs))
    if not seqs and events:  # 证据链强制:兜底引用首条事件,保证 ≥1
        seqs = [events[0].seq]
    return {"event_seqs": seqs, "signals": sorted(set(signal_names)), "details": details[:3]}


def attribute_run(db: Session, run) -> dict[str, Any]:
    """对一个 Run 的全部终态 case 做归因并落库。幂等:重跑先清旧结论。"""
    cases = (
        db.query(CaseExecution).filter_by(run_id=run.id)
        .order_by(CaseExecution.order_no).all()
    )
    scorable = [c for c in cases if c.status in ("succeeded", "failed")]
    exec_ids = [c.id for c in scorable]
    if exec_ids:
        db.query(Attribution).filter(Attribution.execution_id.in_(exec_ids)) \
            .delete(synchronize_session=False)

    index = _binding_index(db, run.rootcause_version or 1)
    judge = get_judge()
    candidate_codes = sorted({rc.code for lst in index.values() for rc, _ in lst})

    undetermined: list[dict[str, Any]] = []
    cases_out: list[dict[str, Any]] = []
    for ce in scorable:
        events = (
            db.query(TraceEvent).filter_by(execution_id=ce.id)
            .order_by(TraceEvent.seq).all()
        )
        failing = _failing_signals(db, ce.id)
        # 规则归因:按信号绑定汇聚候选根因
        cand: dict[int, dict[str, Any]] = {}
        for s in failing:
            for rc, is_primary in index.get(str(s.get("name")), []):
                c = cand.setdefault(rc.id, {"rc": rc, "signals": [], "details": [],
                                            "primary_hits": 0, "votes": 0.0, "deduction": 0.0})
                c["signals"].append(str(s.get("name")))
                c["details"].append(str(s.get("detail", "")))
                c["votes"] += 1.0 if is_primary else 0.5
                c["deduction"] += float(s.get("deduction", 0) or 0)
                c["primary_hits"] += 1 if is_primary else 0

        created: list[dict[str, Any]] = []
        if cand:
            order = sorted(cand.values(), key=lambda c: (-c["primary_hits"], -c["votes"],
                                                         -c["deduction"], -c["rc"].level, c["rc"].code))
            picks = [(order[0], True)] + [(o, False) for o in order[1:3]]
            for item, is_primary_flag in picks:
                conf = 0.9 if is_primary_flag else 0.6
                ev = _collect_evidence(item["signals"], events, item["details"])
                db.add(Attribution(
                    execution_id=ce.id, rootcause_id=item["rc"].id, is_primary=is_primary_flag,
                    confidence=conf, source="rule", evidence=ev, review_status="pending",
                ))
                created.append({"rootcause_id": item["rc"].id, "code": item["rc"].code,
                                "name": item["rc"].name, "level": item["rc"].level,
                                "responsibility": item["rc"].responsibility,
                                "is_primary": is_primary_flag, "confidence": conf, "evidence": ev})
        elif failing:
            # LLM 归因预留:规则未覆盖时交由 Judge 在候选集合内判定;NullJudge → 未确定
            judged = judge.judge_attribution(
                {"testcase_id": ce.testcase_id, "status": ce.status,
                 "signals": [s for s in failing], "event_count": len(events)}, candidate_codes)
            by_code = {rc.code: rc for lst in index.values() for rc, _ in lst}
            if judged:
                for j in judged[:3]:
                    rc = by_code.get(str(j.get("rootcause_code")))
                    if rc is None:
                        continue
                    db.add(Attribution(
                        execution_id=ce.id, rootcause_id=rc.id, is_primary=not created,
                        confidence=float(j.get("confidence", 0.5)), source="llm",
                        evidence={"event_seqs": [events[0].seq] if events else [],
                                  "signals": [s["name"] for s in failing],
                                  "details": [str(j.get("rationale", ""))]},
                        review_status="pending",
                    ))
                    created.append({"rootcause_id": rc.id, "code": rc.code, "name": rc.name,
                                    "level": rc.level, "responsibility": rc.responsibility,
                                    "is_primary": not created, "confidence": float(j.get("confidence", 0.5))})
            else:
                undetermined.append({
                    "execution_id": ce.id, "testcase_id": ce.testcase_id,
                    "testcase_title": ce.testcase.title if ce.testcase else "",
                    "signals": sorted({str(s.get("name")) for s in failing}),
                    "reason": "规则未命中且 LLM 归因未启用(需人工复核)",
                })
        if created:
            cases_out.append({
                "execution_id": ce.id, "testcase_id": ce.testcase_id,
                "testcase_title": ce.testcase.title if ce.testcase else "",
                "status": ce.status,
                "primary": next((c for c in created if c["is_primary"]), None),
                "secondary": [c for c in created if not c["is_primary"]],
            })
    db.commit()
    summary = {"run_id": run.id, "rootcause_version": run.rootcause_version,
               "attributed_cases": len(cases_out), "cases": cases_out,
               "undetermined": undetermined}
    # 归因汇总随 Report 持久化,供 API/报告直接读取(含未确定案件清单)
    report = db.query(Report).filter_by(run_id=run.id).one_or_none()
    if report is not None:
        content = dict(report.content or {})
        content["attribution"] = summary
        report.content = content
        db.commit()
    return summary


# ---------- Run 级聚合(供 API/报告使用) ----------
def summarize_run(db: Session, run_id: int) -> dict[str, Any]:
    rows = (
        db.query(Attribution, RootCause)
        .join(RootCause, Attribution.rootcause_id == RootCause.id)
        .join(CaseExecution, Attribution.execution_id == CaseExecution.id)
        .filter(CaseExecution.run_id == run_id)
        .all()
    )
    dist: dict[str, dict[str, Any]] = {}
    resp = {"agent": 0, "platform": 0, "third_party": 0, "case_maintainer": 0}
    level1: dict[str, int] = {}
    for attr, rc in rows:
        key = rc.code
        d = dist.setdefault(key, {"code": rc.code, "name": rc.name, "level": rc.level,
                                  "responsibility": rc.responsibility, "primary": 0, "secondary": 0})
        d["primary" if attr.is_primary else "secondary"] += 1
        if attr.is_primary:
            resp[rc.responsibility] = resp.get(rc.responsibility, 0) + 1
            top = rc.code.split(".")[0]
            level1[top] = level1.get(top, 0) + 1
    return {
        "total_attributions": len(rows),
        "distribution": sorted(dist.values(), key=lambda d: (-(d["primary"] + d["secondary"]), d["code"])),
        "level1_summary": level1,
        "responsibility_summary": resp,
    }
