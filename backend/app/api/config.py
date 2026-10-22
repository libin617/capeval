"""权重模板(FR-05:在线维护 CRUD)与根因库(FR-07 在线维护:CRUD / 信号绑定 / 停用 / 版本化)。"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.auth import require_writer
from app.core.database import get_db
from app.models import EvaluationRun, RootCause, RootCauseLibraryVersion, User, WeightTemplate
from app.schemas import (
    RootCauseCreate, RootCauseLibraryVersionOut, RootCauseOut, RootCauseUpdate,
    RootCauseWriteOut, SignalCatalogOut, WeightTemplateCreate, WeightTemplateOut,
    WeightTemplateUpdate,
)
from app.services import rootcause
from app.services.scoring import DIMENSIONS, SIGNAL_CATALOG

router = APIRouter(prefix="/api", tags=["config"])


@router.get("/weight-templates", response_model=list[WeightTemplateOut])
def list_weight_templates(db: Session = Depends(get_db)) -> list[WeightTemplate]:
    return db.query(WeightTemplate).order_by(WeightTemplate.is_default.desc(), WeightTemplate.id).all()


def _validate_weights(weights: dict[str, float]) -> None:
    """权重支持按维度子集配置:至少选择 1 项、取值 [0,1]、所选维度总和为 1(容差 0.001)。

    仅选部分维度(如只选 D1)时,评分只使用所选维度,便于单独评测某项能力。
    """
    if not weights:
        raise HTTPException(422, "请至少选择一个评测维度")
    extra = [k for k in weights if k not in DIMENSIONS]
    if extra:
        raise HTTPException(422, f"权重维度必须为 D1~D8,非法维度:{extra}")
    for k, v in weights.items():
        if not isinstance(v, (int, float)) or not 0 <= float(v) <= 1:
            raise HTTPException(422, f"维度 {k} 的权重必须为 0~1 的数字")
    selected = {k: float(v) for k, v in weights.items() if float(v) > 0}
    if not selected:
        raise HTTPException(422, "至少一个维度的权重需大于 0")
    total = sum(selected.values())
    if abs(total - 1.0) > 0.001:
        raise HTTPException(422, f"所选维度权重总和必须为 1.0,当前为 {total:.4f}")


def _apply_default(db: Session, keep: WeightTemplate) -> None:
    """设置默认模板时清除其他模板的默认标记。"""
    db.query(WeightTemplate).filter(
        WeightTemplate.is_default.is_(True), WeightTemplate.id != keep.id,
    ).update({"is_default": False}, synchronize_session=False)


@router.post("/weight-templates", response_model=WeightTemplateOut, status_code=201)
def create_weight_template(payload: WeightTemplateCreate, db: Session = Depends(get_db),
                           user: User = Depends(require_writer)) -> WeightTemplate:
    name = payload.name.strip()
    if not name:
        raise HTTPException(422, "模板名称不能为空")
    if db.query(WeightTemplate).filter(WeightTemplate.name == name).first():
        raise HTTPException(409, "模板名称已存在")
    _validate_weights(payload.weights)
    tpl = WeightTemplate(name=name, weights={k: float(v) for k, v in payload.weights.items()},
                         is_default=payload.is_default)
    db.add(tpl)
    db.flush()
    if tpl.is_default:
        _apply_default(db, tpl)
    db.commit()
    db.refresh(tpl)
    return tpl


@router.put("/weight-templates/{tpl_id}", response_model=WeightTemplateOut)
def update_weight_template(tpl_id: int, payload: WeightTemplateUpdate,
                           db: Session = Depends(get_db),
                           user: User = Depends(require_writer)) -> WeightTemplate:
    tpl = db.get(WeightTemplate, tpl_id)
    if tpl is None:
        raise HTTPException(404, "权重模板不存在")
    if payload.name is not None:
        name = payload.name.strip()
        if not name:
            raise HTTPException(422, "模板名称不能为空")
        dup = db.query(WeightTemplate).filter(
            WeightTemplate.name == name, WeightTemplate.id != tpl_id).first()
        if dup:
            raise HTTPException(409, "模板名称已存在")
        tpl.name = name
    if payload.weights is not None:
        _validate_weights(payload.weights)
        tpl.weights = {k: float(v) for k, v in payload.weights.items()}
    if payload.is_default is not None:
        if not payload.is_default and tpl.is_default:
            raise HTTPException(409, "需保留一个默认模板,请先设置其他模板为默认")
        tpl.is_default = payload.is_default
        if tpl.is_default:
            _apply_default(db, tpl)
    db.commit()
    db.refresh(tpl)
    return tpl


@router.delete("/weight-templates/{tpl_id}", status_code=204)
def delete_weight_template(tpl_id: int, db: Session = Depends(get_db),
                           user: User = Depends(require_writer)) -> None:
    """删除权重模板:默认模板与被评测任务引用的模板禁止删除。"""
    tpl = db.get(WeightTemplate, tpl_id)
    if tpl is None:
        raise HTTPException(404, "权重模板不存在")
    if tpl.is_default:
        raise HTTPException(409, "默认模板不可删除,请先将其他模板设为默认")
    used = db.query(EvaluationRun.id).filter(EvaluationRun.weight_template_id == tpl_id).first()
    if used:
        raise HTTPException(409, "该模板已被评测任务引用,禁止删除")
    db.delete(tpl)
    db.commit()


@router.get("/root-causes", response_model=list[RootCauseOut])
def list_root_causes(db: Session = Depends(get_db)) -> list[RootCauseOut]:
    return [RootCauseOut(**row) for row in rootcause.list_with_counts(db)]


@router.get("/root-causes/signal-catalog", response_model=SignalCatalogOut)
def get_signal_catalog() -> SignalCatalogOut:
    """可绑定的规则信号目录(FR-07 检测信号绑定下拉)。"""
    return SignalCatalogOut(signals=sorted(SIGNAL_CATALOG))


@router.get("/root-causes/library-version", response_model=RootCauseLibraryVersionOut)
def get_library_version(db: Session = Depends(get_db)) -> RootCauseLibraryVersionOut:
    latest = rootcause.latest_version(db)
    return RootCauseLibraryVersionOut(
        version_no=latest.version_no if latest else 0,
        note=latest.note if latest else "",
    )


@router.get("/root-causes/library-versions", response_model=list[RootCauseLibraryVersionOut])
def list_library_versions(db: Session = Depends(get_db)) -> list[RootCauseLibraryVersion]:
    return db.query(RootCauseLibraryVersion).order_by(RootCauseLibraryVersion.version_no.desc()).all()


def _write_out(db: Session, rc: RootCause, nv: int, warning: str = "") -> RootCauseWriteOut:
    row = next(r for r in rootcause.list_with_counts(db) if r["id"] == rc.id)
    return RootCauseWriteOut(rootcause=RootCauseOut(**row), library_version=nv, warning=warning)


@router.post("/root-causes", response_model=RootCauseWriteOut, status_code=201)
def create_root_cause(payload: RootCauseCreate, db: Session = Depends(get_db),
                      user: User = Depends(require_writer)) -> RootCauseWriteOut:
    """新增根因条目:编码按层级自动生成,立即产生新根因库版本。"""
    try:
        rc, nv = rootcause.create_root_cause(db, payload)
    except rootcause.StateError as exc:
        raise HTTPException(409, str(exc)) from exc
    except rootcause.SignalSyntaxError as exc:
        raise HTTPException(422, str(exc)) from exc
    return _write_out(db, rc, nv)


@router.patch("/root-causes/{rc_id}", response_model=RootCauseWriteOut)
def update_root_cause(rc_id: int, payload: RootCauseUpdate, db: Session = Depends(get_db),
                      user: User = Depends(require_writer)) -> RootCauseWriteOut:
    """修改条目/启用停用/检测信号绑定;修改信号仅影响新版本之后的 Run。"""
    rc = db.get(RootCause, rc_id)
    if rc is None:
        raise HTTPException(404, "根因条目不存在")
    try:
        rc, nv, warning = rootcause.update_root_cause(db, rc, payload)
    except rootcause.SignalSyntaxError as exc:
        raise HTTPException(422, str(exc)) from exc
    return _write_out(db, rc, nv, warning)


@router.delete("/root-causes/{rc_id}", response_model=RootCauseWriteOut)
def delete_root_cause(rc_id: int, db: Session = Depends(get_db),
                      user: User = Depends(require_writer)) -> RootCauseWriteOut:
    """删除条目:已被归因引用或有子节点时禁止,只能停用(PRD FR-07)。"""
    rc = db.get(RootCause, rc_id)
    if rc is None:
        raise HTTPException(404, "根因条目不存在")
    try:
        nv = rootcause.delete_root_cause(db, rc)
    except rootcause.StateError as exc:
        raise HTTPException(409, str(exc)) from exc
    return RootCauseWriteOut(
        rootcause=RootCauseOut(id=rc_id, code=rc.code, level=rc.level, parent_id=rc.parent_id,
                               name=rc.name, description=rc.description,
                               responsibility=rc.responsibility, detection_signals={},
                               enabled=False, introduced_version=rc.introduced_version),
        library_version=nv, warning="条目已删除",
    )
