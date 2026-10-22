"""用例集 API(FR-02):版本化,发布后冻结,修改生成新版本。"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session



from app.core.auth import require_writer
from app.core.database import get_db
from app.models import (
    CaseSet, CaseSetItem, CaseSetVersion, EvaluationRun, LlmJudgeConfig, TestCase, User,
)
from app.schemas import (
    CaseSetCreate, CaseSetOut, CaseSetUpdate, CaseSetVersionCreate, CaseSetVersionJudge,
    CaseSetVersionOut,
)

router = APIRouter(prefix="/api/casesets", tags=["casesets"])


def _validate_case_ids(db: Session, ids: list[int]) -> None:
    if len(ids) != len(set(ids)):
        raise HTTPException(400, "用例清单存在重复")
    cases = db.query(TestCase).filter(TestCase.id.in_(ids)).all()
    found = {c.id: c for c in cases}
    missing = [i for i in ids if i not in found]
    if missing:
        raise HTTPException(400, f"用例不存在: {missing}")
    archived = [i for i in ids if found[i].status != "active"]
    if archived:
        raise HTTPException(400, f"用例已归档,不可加入用例集: {archived}")


def _replace_items(db: Session, version: CaseSetVersion, ids: list[int]) -> None:
    version.items.clear()
    db.flush()
    for idx, cid in enumerate(ids, start=1):
        version.items.append(CaseSetItem(testcase_id=cid, order_no=idx))
    db.flush()


@router.get("", response_model=list[CaseSetOut])
def list_casesets(q: str | None = None, db: Session = Depends(get_db)) -> list[CaseSet]:
    query = db.query(CaseSet)
    if q:
        like = f"%{q.strip()}%"
        query = query.filter((CaseSet.name.like(like)) | (CaseSet.description.like(like)))
    return query.order_by(CaseSet.id.desc()).all()


@router.patch("/{set_id}", response_model=CaseSetOut)
def update_caseset(set_id: int, payload: CaseSetUpdate, db: Session = Depends(get_db),
                   user: User = Depends(require_writer)) -> CaseSet:
    """更新用例集名称/描述(不影响已冻结的版本内容)。"""
    cs = db.get(CaseSet, set_id)
    if cs is None:
        raise HTTPException(404, "用例集不存在")
    data = payload.model_dump(exclude_unset=True)
    if "name" in data:
        dup = db.query(CaseSet).filter(CaseSet.name == data["name"], CaseSet.id != set_id).first()
        if dup:
            raise HTTPException(409, "用例集名称已存在")
    for k, v in data.items():
        setattr(cs, k, v)
    db.commit()
    db.refresh(cs)
    return cs


@router.post("", response_model=CaseSetOut, status_code=201)
def create_caseset(payload: CaseSetCreate, db: Session = Depends(get_db),
                   user: User = Depends(require_writer)) -> CaseSet:
    if db.query(CaseSet).filter_by(name=payload.name).first():
        raise HTTPException(409, "用例集名称已存在")
    _validate_case_ids(db, payload.testcase_ids)
    cs = CaseSet(name=payload.name, description=payload.description)
    db.add(cs)
    db.flush()
    v1 = CaseSetVersion(caseset_id=cs.id, version_no=1, status="draft")
    db.add(v1)
    db.flush()
    _replace_items(db, v1, payload.testcase_ids)
    db.commit()
    db.refresh(cs)
    return cs


@router.get("/{set_id}", response_model=CaseSetOut)
def get_caseset(set_id: int, db: Session = Depends(get_db)) -> CaseSet:
    cs = db.get(CaseSet, set_id)
    if cs is None:
        raise HTTPException(404, "用例集不存在")
    return cs


@router.post("/{set_id}/versions", response_model=CaseSetVersionOut, status_code=201)
def create_draft_version(set_id: int, payload: CaseSetVersionCreate, db: Session = Depends(get_db),
                         user: User = Depends(require_writer)) -> CaseSetVersion:
    """基于当前内容创建新草稿版本(同集同时只允许一个草稿)。"""
    cs = db.get(CaseSet, set_id)
    if cs is None:
        raise HTTPException(404, "用例集不存在")
    if db.query(CaseSetVersion).filter_by(caseset_id=set_id, status="draft").first():
        raise HTTPException(409, "已存在未发布的草稿版本,请先发布或删除")
    _validate_case_ids(db, payload.testcase_ids)
    next_no = (max((v.version_no for v in cs.versions), default=0)) + 1
    latest = max(cs.versions, key=lambda v: v.version_no, default=None)
    v = CaseSetVersion(caseset_id=set_id, version_no=next_no, status="draft",
                       judge_config_id=latest.judge_config_id if latest else None)
    db.add(v)
    db.flush()
    _replace_items(db, v, payload.testcase_ids)
    db.commit()
    db.refresh(v)
    return v


@router.put("/{set_id}/versions/{version_id}/judge-config", response_model=CaseSetVersionOut)
def set_version_judge_config(set_id: int, version_id: int, payload: CaseSetVersionJudge,
                             db: Session = Depends(get_db),
                             user: User = Depends(require_writer)) -> CaseSetVersion:
    """设置评测集版本的默认评审模型(仅草稿可改,发布后随版本冻结)。"""
    v = db.get(CaseSetVersion, version_id)
    if v is None or v.caseset_id != set_id:
        raise HTTPException(404, "版本不存在")
    if v.status != "draft":
        raise HTTPException(409, "已发布版本冻结,评审模型不可修改;请创建新版本")
    if payload.judge_config_id is not None:
        cfg = db.get(LlmJudgeConfig, payload.judge_config_id)
        if cfg is None:
            raise HTTPException(404, "评审模型配置不存在")
        if not cfg.enabled:
            raise HTTPException(400, "该评审模型未启用,不可绑定到评测集版本")
    v.judge_config_id = payload.judge_config_id
    db.commit()
    db.refresh(v)
    return v


@router.put("/{set_id}/versions/{version_id}/items", response_model=CaseSetVersionOut)
def replace_draft_items(set_id: int, version_id: int, payload: CaseSetVersionCreate,
                        db: Session = Depends(get_db),
                        user: User = Depends(require_writer)) -> CaseSetVersion:
    v = db.get(CaseSetVersion, version_id)
    if v is None or v.caseset_id != set_id:
        raise HTTPException(404, "版本不存在")
    if v.status != "draft":
        raise HTTPException(409, "已发布版本冻结,不可修改;请创建新版本")
    _validate_case_ids(db, payload.testcase_ids)
    _replace_items(db, v, payload.testcase_ids)
    db.commit()
    db.refresh(v)
    return v


@router.post("/{set_id}/versions/{version_id}/publish", response_model=CaseSetVersionOut)
def publish_version(set_id: int, version_id: int, db: Session = Depends(get_db),
                    user: User = Depends(require_writer)) -> CaseSetVersion:
    v = db.get(CaseSetVersion, version_id)
    if v is None or v.caseset_id != set_id:
        raise HTTPException(404, "版本不存在")
    if v.status == "published":
        raise HTTPException(409, "该版本已发布")
    if not v.items:
        raise HTTPException(400, "空用例集不可发布")
    v.status = "published"
    v.published_at = datetime.now()
    db.commit()
    db.refresh(v)
    return v


@router.delete("/{set_id}", status_code=204)
def delete_caseset(set_id: int, db: Session = Depends(get_db),
                   user: User = Depends(require_writer)) -> None:
    cs = db.get(CaseSet, set_id)
    if cs is None:
        raise HTTPException(404, "用例集不存在")
    referenced = (
        db.query(EvaluationRun).join(CaseSetVersion, EvaluationRun.caseset_version_id == CaseSetVersion.id)
        .filter(CaseSetVersion.caseset_id == set_id).first()
    )
    if referenced:
        raise HTTPException(409, "该用例集已被评测任务引用,禁止删除")
    # versions 关系未配置级联,需先显式删除(items 随 version 级联删除),否则外键约束报 500
    for version in cs.versions:
        db.delete(version)
    db.delete(cs)
    db.commit()
