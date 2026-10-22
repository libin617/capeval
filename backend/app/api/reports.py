"""评测报告与多维对比 API(FR-09):结构化报告、HTML/PDF 导出、分享链接、双 Run 对比。"""

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models import EvaluationRun
from app.services import report as report_svc

router = APIRouter(prefix="/api", tags=["reports"])


class ShareIn(BaseModel):
    public: bool = False  # true 时开启免登录访问
    days: int = Field(default=7, ge=1, le=30)  # 免登录有效期(天)


def _get_run(db: Session, run_id: int) -> EvaluationRun:
    run = db.get(EvaluationRun, run_id)
    if run is None:
        raise HTTPException(404, "评测任务不存在")
    return run


@router.get("/runs/{run_id}/report")
def get_report(run_id: int, db: Session = Depends(get_db)) -> dict:
    """结构化评测报告(总览/维度/失败 TOP-N/归因分布/多维下钻/附录)。"""
    run = _get_run(db, run_id)
    try:
        return report_svc.build(db, run)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/runs/{run_id}/report/export")
def export_report(run_id: int, format: str = Query(default="html", pattern="^(html|pdf)$"),
                  db: Session = Depends(get_db)) -> Response:
    """导出报告:html(自包含,可离线打开)/ pdf(无头 Chrome 渲染)。"""
    run = _get_run(db, run_id)
    try:
        payload = report_svc.build(db, run)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    html_text = report_svc.render_html(payload)
    stem = f"capeval-report-run{run_id}"
    if format == "html":
        return HTMLResponse(content=html_text, headers={
            "Content-Disposition": f'attachment; filename="{stem}.html"'})
    try:
        pdf = report_svc.export_pdf(html_text)
    except report_svc.ExportError as exc:
        raise HTTPException(503, str(exc)) from exc
    return Response(content=pdf, media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="{stem}.pdf"'})


@router.post("/runs/{run_id}/report/share")
def create_share(run_id: int, payload: ShareIn, db: Session = Depends(get_db)) -> dict:
    """生成只读分享链接;默认需登录,可显式开启免登录 7 天有效。"""
    run = _get_run(db, run_id)
    try:
        return report_svc.create_share(db, run, public=payload.public, days=payload.days)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.delete("/runs/{run_id}/report/share")
def revoke_share(run_id: int, db: Session = Depends(get_db)) -> dict:
    """撤销分享链接(撤销后立即失效)。"""
    run = _get_run(db, run_id)
    try:
        report_svc.revoke_share(db, run)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"revoked": True}


@router.get("/reports/shared/{token}")
def get_shared_report(token: str, db: Session = Depends(get_db)) -> dict:
    """免登录查看分享报告(受有效期约束)。"""
    try:
        _, payload = report_svc.shared_report(db, token)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    return payload


@router.get("/report/compare")
def compare(left: int, right: int, db: Session = Depends(get_db)) -> dict:
    """双 Run 对比:双总分/八维分差、逐 case 瀑布、根因迁移、退化清单、效率变化。"""
    if left == right:
        raise HTTPException(422, "请选择两个不同的任务进行对比")
    lrun, rrun = _get_run(db, left), _get_run(db, right)
    try:
        return report_svc.compare_runs(db, lrun, rrun)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
