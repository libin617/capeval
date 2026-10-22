"""评测报告与多维分析(FR-09):结构化报告、HTML/PDF 导出、分享链接、双 Run 对比。

报告章节(PRD FR-09):① 总览 ② 维度详情 ③ 失败分析 TOP-N ④ 归因分布 ⑤ 多维下钻 ⑥ 附录。
设计说明:报告数据在生成时固化(读取已落库的 Report/DimensionScore/Attribution/Report.content),
因此基线 Run 被删除或后续修改都不影响已发布报告的口径。
"""

from __future__ import annotations

import html as html_lib
import shutil
import subprocess
import tempfile
import uuid
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.models import Attribution, CaseExecution, CaseSetVersion, EvaluationRun, Report
from app.services.scoring import DIMENSION_NAMES, SIGNAL_CATALOG

DIM_ORDER = ["D1", "D2", "D3", "D4", "D5", "D6", "D7", "D8"]
RESP_LABEL = {"agent": "Agent", "platform": "平台", "third_party": "第三方", "case_maintainer": "用例维护方"}
CHROME_PATHS = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "google-chrome",
    "chromium",
]


class ExportError(Exception):
    """导出失败(如缺少可用 PDF 渲染器),API 层转 503。"""


def _dimension_rows(dimension_summary: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for d in DIM_ORDER:
        item = dimension_summary.get(d) or {}
        weight = item.get("weight", 0)
        rows.append({
            "dim": d, "name": item.get("name") or DIMENSION_NAMES.get(d, d),
            "weight": weight, "score": item.get("score"),
            "selected": bool(item.get("selected", float(weight or 0) > 0)),
            "scored_cases": item.get("scored_cases", 0),
        })
    return rows


def _attribution_index(db: Session, run_id: int) -> dict[int, list[Attribution]]:
    rows = (
        db.query(Attribution)
        .join(CaseExecution, Attribution.execution_id == CaseExecution.id)
        .filter(CaseExecution.run_id == run_id)
        .order_by(Attribution.is_primary.desc(), Attribution.confidence.desc())
        .all()
    )
    out: dict[int, list[Attribution]] = defaultdict(list)
    for a in rows:
        out[a.execution_id].append(a)
    return out


def _matrix(db: Session, run_id: int) -> dict[str, dict[str, int]]:
    """根因 × 维度交叉矩阵:按证据链信号所属维度累计。"""
    mat: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for a in _attribution_index(db, run_id).values():
        for attr in a:
            dims = {SIGNAL_CATALOG.get(str(s), ("?", ""))[0].split("/")[0]
                    for s in (attr.evidence or {}).get("signals", [])}
            for d in (dims or {"?"}):
                mat[attr.rootcause.code][d] += 1
    return {k: dict(v) for k, v in mat.items()}


def build(db: Session, run: EvaluationRun) -> dict[str, Any]:
    """组装结构化报告(单 Run)。"""
    report = db.query(Report).filter_by(run_id=run.id).one_or_none()
    if report is None:
        raise LookupError("报告尚未生成(任务未到评分阶段)")
    content = report.content or {}
    attribution_stored = content.get("attribution") or {}
    version = db.get(CaseSetVersion, run.caseset_version_id)
    attrs_by_exec = _attribution_index(db, run.id)

    cases = (
        db.query(CaseExecution).filter_by(run_id=run.id)
        .order_by(CaseExecution.order_no).all()
    )
    scored = [c for c in cases if c.status in ("succeeded", "failed")]
    case_map = {c["execution_id"]: c for c in content.get("cases", [])}

    # ③ 失败分析 TOP-N:按扣分排序(质量分最低优先),附主根因 + 证据链
    top: list[dict[str, Any]] = []
    for c in scored:
        cs = case_map.get(c.id, {})
        quality = cs.get("quality_score")
        if quality is None or quality >= 100:
            continue
        attrs = attrs_by_exec.get(c.id, [])
        primary = next((a for a in attrs if a.is_primary), attrs[0] if attrs else None)
        tc = c.testcase
        top.append({
            "execution_id": c.id, "testcase_title": tc.title if tc else "",
            "difficulty": tc.difficulty if tc else "",
            "status": c.status, "quality_score": quality,
            "efficiency_score": cs.get("efficiency_score"),
            "deduction": round(100 - quality, 1),
            "primary": ({"code": primary.rootcause.code, "name": primary.rootcause.name,
                         "level": primary.rootcause.level,
                         "responsibility": primary.rootcause.responsibility}
                        if primary else None),
            "evidence": (primary.evidence or {}) if primary else {},
            "review_status": primary.review_status if primary else "",
        })
    top.sort(key=lambda x: -x["deduction"])

    # ⑤ 多维下钻:按难度/标签切片 + 质量×效率散点
    def _slice(key_fn) -> list[dict[str, Any]]:
        buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for c in scored:
            tc = c.testcase
            for k in (key_fn(tc) if tc else []) or ["未分组"]:
                buckets[k].append(case_map.get(c.id, {}) | {"execution_id": c.id})
        out = []
        for k, items in buckets.items():
            qs = [i["quality_score"] for i in items if i.get("quality_score") is not None]
            es = [i["efficiency_score"] for i in items if i.get("efficiency_score") is not None]
            out.append({"key": k, "cases": len(items),
                        "avg_quality": round(sum(qs) / len(qs), 1) if qs else None,
                        "avg_efficiency": round(sum(es) / len(es), 1) if es else None})
        return sorted(out, key=lambda x: x["key"])

    scatter = [{"execution_id": c["execution_id"], "title": c.get("testcase_title", ""),
                "quality": c.get("quality_score"), "efficiency": c.get("efficiency_score")}
               for c in content.get("cases", [])
               if c.get("quality_score") is not None and c.get("efficiency_score") is not None]

    resp = attribution_stored.get("responsibility_summary", {}) or {}
    agent_n = int(resp.get("agent", 0))
    total_resp = sum(int(v) for v in resp.values()) or 0

    # 基线对比
    baseline = None
    if run.baseline_run_id:
        b = db.query(Report).filter_by(run_id=run.baseline_run_id).one_or_none()
        if b is not None:
            baseline = {
                "run_id": run.baseline_run_id,
                "quality_score": b.quality_score, "efficiency_score": b.efficiency_score,
                "quality_delta": (round((report.quality_score or 0) - (b.quality_score or 0), 1)
                                  if report.quality_score is not None and b.quality_score is not None else None),
                "efficiency_delta": (round((report.efficiency_score or 0) - (b.efficiency_score or 0), 1)
                                     if report.efficiency_score is not None and b.efficiency_score is not None else None),
            }

    selected_dims = {k for k, v in (run.weight_snapshot or {}).items() if float(v or 0) > 0}
    data_integrity = ["D6 效率分依赖用例 token 基线,未配置基线的用例仅记录消耗不评分。",
                      "D4/D7/D8 当前为规则近似评分,接入 LLM Judge 后升级为语义评审。"]
    if "D6" not in selected_dims:
        data_integrity.insert(0, "本次权重模板未选择 D6(成本与效率),不计算效率分。")
    elif any(c.get("efficiency_score") is None for c in content.get("cases", [])):
        data_integrity.append("存在未配置效率基线的用例,效率分为部分样本均值。")

    # M6(FR-04 异常):API 型 Agent 数据缺失降级在报告数据完整性声明中显式标注
    agent = run.agent
    if agent is not None and agent.access_type == "api":
        cfg = agent.adapter_config or {}
        cost = agent.cost_params or {}
        if not bool(cfg.get("expose_tool_calls", False)):
            data_integrity.append("API 型 Agent 未上报工具调用明细,D3(工具调用准确性)按「数据缺失」降级处理。")
        if not bool(cfg.get("expose_tokens", False)):
            data_integrity.append("API 型 Agent 未上报 token 用量,D6(成本与效率)按「数据缺失」降级处理。")
        if not (cost.get("input_price_per_1k") and cost.get("output_price_per_1k")):
            data_integrity.append("API 型 Agent 未配置 token 单价,成本计量参数缺失,费用折算不可用。")

    return {
        "run_id": run.id,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "engine": content.get("engine", ""),
        "overview": {
            "name": run.name, "status": run.status,
            "agent_name": run.agent.name if run.agent else "",
            "agent_version": run.agent.version_label if run.agent else "",
            "caseset_name": version.caseset.name if version else "",
            "caseset_version_no": version.version_no if version else 0,
            "quality_score": report.quality_score, "efficiency_score": report.efficiency_score,
            "review_mark": report.review_mark, "review_policy": run.review_policy,
            "rootcause_version": run.rootcause_version,
            "weight_template_id": run.weight_template_id,
            "weight_snapshot": run.weight_snapshot or {},
            "case_total": len(cases), "case_succeeded": sum(1 for c in cases if c.status == "succeeded"),
            "case_failed": sum(1 for c in cases if c.status == "failed"),
            "completed_at": run.completed_at.isoformat(timespec="seconds") if run.completed_at else None,
            "baseline": baseline,
        },
        "dimensions": _dimension_rows(report.dimension_summary or {}),
        "top_failures": top[:10],
        "attribution": {
            "total_attributions": attribution_stored.get("attributed_cases", 0),
            "distribution": attribution_stored.get("distribution", []),
            "level1_summary": attribution_stored.get("level1_summary", {}),
            "responsibility_summary": resp,
            "agent_responsibility_pct": round(agent_n / total_resp * 100, 1) if total_resp else None,
            "undetermined": attribution_stored.get("undetermined", []),
            "matrix": _matrix(db, run.id),
        },
        "drilldown": {
            "by_difficulty": _slice(lambda tc: [tc.difficulty]),
            "by_tag": _slice(lambda tc: tc.tags or []),
            "scatter": scatter,
        },
        "appendix": {
            "weight_template_id": run.weight_template_id,
            "judge": "NullJudge(未启用 LLM)" if not content.get("judge_enabled") else "LLM Judge",
            "data_integrity": data_integrity,
        },
    }


# ---------- HTML 渲染(自包含,可离线打开) ----------
_CSS = """
body{font-family:-apple-system,'PingFang SC','Microsoft YaHei',sans-serif;color:#1a2332;margin:0;padding:32px;background:#f6f8fb}
.card{background:#fff;border:1px solid #e3e8f0;border-radius:10px;padding:18px 22px;margin:14px 0}
h1{font-size:22px;margin:0 0 4px}h2{font-size:16px;margin:26px 0 10px;border-left:4px solid #2f6bff;padding-left:10px}
table{width:100%;border-collapse:collapse;font-size:13px}th{background:#f0f3f9;text-align:left;padding:7px 10px;border:1px solid #e3e8f0}
td{padding:7px 10px;border:1px solid #e3e8f0;vertical-align:top}
.kpi{display:flex;gap:16px;margin:6px 0}.kpi div{background:#fff;border:1px solid #e3e8f0;border-radius:8px;padding:10px 16px}
.kpi b{font-size:24px}.muted{color:#6b7a93;font-size:12px}
.bar{background:#eef1f6;border-radius:4px;height:10px;position:relative;min-width:80px}.bar>i{display:block;height:10px;border-radius:4px;background:#2f6bff}
.tag{display:inline-block;padding:1px 8px;border-radius:99px;font-size:11px;background:#eaf0ff;color:#2f6bff;margin-right:4px}
.agent{color:#2f6bff}.nonagent{color:#d97706}
footer{margin-top:24px;color:#6b7a93;font-size:12px}
"""


def _esc(v: Any) -> str:
    return html_lib.escape(str(v if v is not None else "-"))


def render_html(payload: dict[str, Any], *, source: str = "CapEval") -> str:
    ov = payload["overview"]
    baseline = ov.get("baseline") or {}
    delta = ""
    if baseline:
        delta = (f"<div class='kpi'><div>vs 基线 #{baseline['run_id']}<br>"
                 f"<b>{baseline.get('quality_delta')}</b> 质量分 · "
                 f"<b>{baseline.get('efficiency_delta')}</b> 效率分</div></div>")

    dim_rows = "".join(
        f"<tr><td>{_esc(r['dim'])}</td><td>{_esc(r['name'])}</td><td>{_esc(r['weight'])}</td>"
        f"<td>{_esc(r['score'])}</td>"
        f"<td><span class='bar'><i style='width:{(r['score'] or 0) / 5 * 100:.0f}%'></i></span></td></tr>"
        for r in payload["dimensions"] if r.get("selected", True))

    top_rows = "".join(
        f"<tr><td>{_esc(t['testcase_title'])}</td><td>{_esc(t['status'])}</td>"
        f"<td>{_esc(t['quality_score'])}</td><td>{_esc(t['deduction'])}</td>"
        f"<td>{_esc((t['primary'] or {}).get('code'))} {_esc((t['primary'] or {}).get('name'))}</td>"
        f"<td>{_esc('、'.join((t['evidence'] or {}).get('signals', [])))}</td></tr>"
        for t in payload["top_failures"])

    dist_rows = "".join(
        f"<tr><td>{_esc(d.get('code'))}</td><td>{_esc(d.get('name'))}</td>"
        f"<td>{_esc(d.get('level'))}</td><td>{_esc(RESP_LABEL.get(d.get('responsibility'), d.get('responsibility')))}</td>"
        f"<td>{_esc(d.get('primary'))}</td><td>{_esc(d.get('secondary'))}</td></tr>"
        for d in payload["attribution"]["distribution"])

    und = payload["attribution"]["undetermined"]
    und_html = ("<p class='muted'>无</p>" if not und else "<ul>" + "".join(
        f"<li>{_esc(u.get('testcase_title'))} · 信号 {_esc('、'.join(u.get('signals', [])))}</li>"
        for u in und) + "</ul>")

    drill_rows = "".join(
        f"<tr><td>{_esc(x['key'])}</td><td>{_esc(x['cases'])}</td>"
        f"<td>{_esc(x['avg_quality'])}</td><td>{_esc(x['avg_efficiency'])}</td></tr>"
        for x in payload["drilldown"]["by_difficulty"])

    appendix = payload["appendix"]
    integrity = "".join(f"<li>{_esc(x)}</li>" for x in appendix["data_integrity"])

    return f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">
<title>评测报告 · {_esc(ov['name'])}</title><style>{_CSS}</style></head><body>
<h1>Code Agent 评测报告</h1>
<div class="muted">报告生成时间 {_esc(payload['generated_at'])} · {_esc(source)} · 引擎 {_esc(payload['engine'])}</div>
<div class="card">
  <div class="muted">Agent</div><div>{_esc(ov['agent_name'])}({_esc(ov['agent_version'])}) · 用例集 {_esc(ov['caseset_name'])} v{_esc(ov['caseset_version_no'])}</div>
  <div class="kpi" style="margin-top:12px">
    <div>质量分(百分制)<br><b>{_esc(ov['quality_score'])}</b></div>
    <div>效率分(百分制)<br><b>{_esc(ov['efficiency_score'])}</b></div>
    <div>复核标记<br><b style="font-size:16px">{_esc(ov['review_mark'])}</b></div>
    <div>用例 成功/失败<br><b>{_esc(ov['case_succeeded'])}/{_esc(ov['case_failed'])}</b></div>
  </div>{delta}
  <div class="muted">根因库版本 v{_esc(ov['rootcause_version'])} · 复核策略 {_esc(ov['review_policy'])} · 权重模板 #{_esc(ov['weight_template_id'])}</div>
</div>
<h2>① 维度详情</h2><div class="card"><table><tr><th>维度</th><th>名称</th><th>权重</th><th>得分(0-5)</th><th>分布</th></tr>{dim_rows}</table></div>
<h2>② 失败分析 TOP-N</h2><div class="card"><table><tr><th>用例</th><th>状态</th><th>质量分</th><th>扣分</th><th>主根因</th><th>证据信号</th></tr>{'<tr><td colspan=6 class=muted>无失分用例</td></tr>' if not top_rows else top_rows}</table></div>
<h2>③ 归因分布</h2><div class="card">
  <div class="muted">Agent 责任占比 {_esc(payload['attribution']['agent_responsibility_pct'])}%</div>
  <table><tr><th>编码</th><th>名称</th><th>层级</th><th>责任归属</th><th>主根因</th><th>次要根因</th></tr>{'<tr><td colspan=6 class=muted>无归因</td></tr>' if not dist_rows else dist_rows}</table>
  <p class="muted" style="margin-top:10px">未确定案件</p>{und_html}
</div>
<h2>④ 多维下钻(按难度)</h2><div class="card"><table><tr><th>难度</th><th>用例数</th><th>平均质量分</th><th>平均效率分</th></tr>{drill_rows}</table></div>
<h2>⑤ 附录</h2><div class="card">
  <div class="muted">评分配置快照</div><div>{''.join(f"<span class='tag'>{_esc(k)} {_esc(v)}</span>" for k, v in (ov.get('weight_snapshot') or {}).items())}</div>
  <div class="muted" style="margin-top:8px">数据完整性声明</div><ul>{integrity}</ul>
</div>
<footer>CapEval · {_esc(payload['generated_at'])} · 数据快照:根因库 v{_esc(ov['rootcause_version'])}</footer>
</body></html>"""


def export_pdf(html_text: str) -> bytes:
    """用无头 Chrome 将自包含 HTML 打印为 PDF。"""
    chrome = next((p for p in CHROME_PATHS if (Path(p).exists() or shutil.which(p))), None)
    if chrome is None:
        raise ExportError("未找到可用的 PDF 渲染器(Chrome/Chromium),请改用 HTML 导出")
    with tempfile.TemporaryDirectory(prefix="capeval-pdf-") as tmp:
        html_path = Path(tmp) / "report.html"
        pdf_path = Path(tmp) / "report.pdf"
        html_path.write_text(html_text, encoding="utf-8")
        try:
            subprocess.run(
                [chrome, "--headless=new", "--disable-gpu", "--no-sandbox",
                 "--no-pdf-header-footer", f"--print-to-pdf={pdf_path}", html_path.as_uri()],
                capture_output=True, timeout=90, check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ExportError("PDF 渲染超时") from exc
        if not pdf_path.exists() or pdf_path.stat().st_size == 0:
            raise ExportError("PDF 渲染失败(Chrome 未产出文件)")
        return pdf_path.read_bytes()


# ---------- 分享链接 ----------
def create_share(db: Session, run: EvaluationRun, *, public: bool, days: int) -> dict[str, Any]:
    report = db.query(Report).filter_by(run_id=run.id).one_or_none()
    if report is None:
        raise LookupError("报告尚未生成,无法分享")
    token = report.share_token or uuid.uuid4().hex
    report.share_token = token
    report.share_expires_at = (datetime.now() + timedelta(days=days)) if public else None
    db.commit()
    return {"token": token, "public": public,
            "expires_at": report.share_expires_at.isoformat(timespec="seconds") if report.share_expires_at else None}


def revoke_share(db: Session, run: EvaluationRun) -> None:
    report = db.query(Report).filter_by(run_id=run.id).one_or_none()
    if report is None:
        raise LookupError("报告尚未生成")
    report.share_token = ""
    report.share_expires_at = None
    db.commit()


def shared_report(db: Session, token: str) -> tuple[EvaluationRun, dict[str, Any]]:
    report = db.query(Report).filter_by(share_token=token).one_or_none()
    if report is None or not token:
        raise LookupError("分享链接无效或已被撤销")
    if report.share_expires_at is not None and report.share_expires_at < datetime.now():
        raise LookupError("分享链接已过期")
    run = db.get(EvaluationRun, report.run_id)
    if run is None:
        raise LookupError("分享链接无效")
    return run, build(db, run)


# ---------- 双 Run 对比 ----------
def compare_runs(db: Session, left: EvaluationRun, right: EvaluationRun) -> dict[str, Any]:
    lr = db.query(Report).filter_by(run_id=left.id).one_or_none()
    rr = db.query(Report).filter_by(run_id=right.id).one_or_none()
    if lr is None or rr is None:
        raise LookupError("对比双方均需为已产出报告的任务")

    def dims(rep: Report) -> dict[str, float | None]:
        return {d: (rep.dimension_summary or {}).get(d, {}).get("score") for d in DIM_ORDER}

    ld, rd = dims(lr), dims(rr)
    dimension_diff = [{"dim": d, "name": DIMENSION_NAMES[d], "left": ld[d], "right": rd[d],
                       "delta": (round((rd[d] or 0) - (ld[d] or 0), 2)
                                 if ld[d] is not None and rd[d] is not None else None)}
                      for d in DIM_ORDER]

    version_matched = (left.caseset_version_id == right.caseset_version_id
                       and left.rootcause_version == right.rootcause_version)

    # 逐 case 分差(仅同用例集版本可比)
    case_waterfall: list[dict[str, Any]] = []
    if left.caseset_version_id == right.caseset_version_id:
        lmap = {c["testcase_id"]: c for c in (lr.content or {}).get("cases", [])}
        rmap = {c["testcase_id"]: c for c in (rr.content or {}).get("cases", [])}
        for tid in sorted(set(lmap) | set(rmap)):
            lq, rq = lmap.get(tid, {}).get("quality_score"), rmap.get(tid, {}).get("quality_score")
            title = (lmap.get(tid) or rmap.get(tid) or {}).get("testcase_title", "")
            case_waterfall.append({
                "testcase_id": tid, "testcase_title": title, "left": lq, "right": rq,
                "delta": round(rq - lq, 1) if lq is not None and rq is not None else None,
            })
    regressions = sorted([c for c in case_waterfall if c["delta"] is not None and c["delta"] <= -1],
                         key=lambda c: c["delta"])

    # 根因分布迁移
    def dist(rep: Report) -> dict[str, int]:
        d: dict[str, int] = defaultdict(int)
        for item in ((rep.content or {}).get("attribution") or {}).get("distribution", []):
            d[item.get("code")] = int(item.get("primary", 0)) + int(item.get("secondary", 0))
        return dict(d)

    ldist, rdist = dist(lr), dist(rr)
    migration = [{"code": code, "left": ldist.get(code, 0), "right": rdist.get(code, 0),
                  "delta": rdist.get(code, 0) - ldist.get(code, 0)}
                 for code in sorted(set(ldist) | set(rdist))]

    def eff(rep: Report) -> float | None:
        return rep.efficiency_score

    return {
        "left": {"run_id": left.id, "name": left.name,
                 "agent": f"{left.agent.name}({left.agent.version_label})" if left.agent else "",
                 "quality_score": lr.quality_score, "efficiency_score": eff(lr)},
        "right": {"run_id": right.id, "name": right.name,
                  "agent": f"{right.agent.name}({right.agent.version_label})" if right.agent else "",
                  "quality_score": rr.quality_score, "efficiency_score": eff(rr)},
        "delta": {"quality": (round((rr.quality_score or 0) - (lr.quality_score or 0), 1)
                              if lr.quality_score is not None and rr.quality_score is not None else None),
                  "efficiency": (round((rr.efficiency_score or 0) - (lr.efficiency_score or 0), 1)
                                 if lr.efficiency_score is not None and rr.efficiency_score is not None else None)},
        "version_matched": version_matched,
        "version_note": "" if version_matched else "两侧用例集版本或根因库版本不一致,仅允许总分/维度分对比",
        "dimension_diff": dimension_diff,
        "case_waterfall": case_waterfall if version_matched else [],
        "regressions": regressions if version_matched else [],
        "rootcause_migration": migration if version_matched else [],
    }
