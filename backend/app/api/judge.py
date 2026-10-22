"""LLM-as-Judge 评审模型配置(FR-05,M7):多套配置的增删改查与连通性测试。

密钥只以脱敏形式出参;编辑时 api_key 留空表示保持原值不变。
评测集版本 / 评测任务通过 judge_config_id 引用具体配置。
"""

import time

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.auth import require_writer
from app.core.database import get_db
from app.models import CaseSetVersion, EvaluationRun, LlmJudgeConfig, User
from app.schemas import (
    JudgeConfigCreate, JudgeConfigOut, JudgeConfigUpdate, JudgeTestIn, JudgeTestOut,
)
from app.services.judge import DIMENSION_RUBRIC, HttpJudge

router = APIRouter(prefix="/api", tags=["judge"])

DEFAULT_DIMENSIONS = ["D4", "D7", "D8"]


def _mask(key: str) -> str:
    if not key:
        return ""
    return f"****{key[-4:]}" if len(key) > 8 else "****"


def _rubrics(row: LlmJudgeConfig) -> dict[str, str]:
    """返回完整三维 rubric:配置缺失的维度用内置默认标准补齐,便于前端直接编辑。"""
    stored = row.rubrics or {}
    return {d: str(stored.get(d) or DIMENSION_RUBRIC.get(d) or "") for d in DEFAULT_DIMENSIONS}


def _to_out(row: LlmJudgeConfig) -> JudgeConfigOut:
    return JudgeConfigOut(
        id=row.id, name=row.name, enabled=row.enabled, is_default=row.is_default,
        provider=row.provider, base_url=row.base_url, model=row.model,
        api_key_masked=_mask(row.api_key), has_api_key=bool(row.api_key),
        temperature=row.temperature, max_tokens=row.max_tokens,
        timeout_seconds=row.timeout_seconds, dimensions=list(row.dimensions or []),
        rubrics=_rubrics(row), updated_by=row.updated_by,
        created_at=row.created_at, updated_at=row.updated_at,
    )


def _ensure_name_free(db: Session, name: str, exclude_id: int | None = None) -> None:
    q = db.query(LlmJudgeConfig).filter(LlmJudgeConfig.name == name)
    if exclude_id is not None:
        q = q.filter(LlmJudgeConfig.id != exclude_id)
    if q.first() is not None:
        raise HTTPException(409, f"评审模型名称「{name}」已存在")


def _ensure_complete(row: LlmJudgeConfig) -> None:
    if row.enabled and not (row.base_url and row.model and row.api_key):
        raise HTTPException(400, "启用评审模型前需填写 base_url、模型名与 API Key")


def _clear_other_defaults(db: Session, keep_id: int) -> None:
    db.query(LlmJudgeConfig).filter(
        LlmJudgeConfig.id != keep_id, LlmJudgeConfig.is_default.is_(True)
    ).update({"is_default": False}, synchronize_session=False)


@router.get("/judge-configs", response_model=list[JudgeConfigOut])
def list_judge_configs(db: Session = Depends(get_db)) -> list[JudgeConfigOut]:
    rows = (
        db.query(LlmJudgeConfig)
        .order_by(LlmJudgeConfig.is_default.desc(), LlmJudgeConfig.id.asc())
        .all()
    )
    return [_to_out(r) for r in rows]


@router.post("/judge-configs", response_model=JudgeConfigOut, status_code=201)
def create_judge_config(payload: JudgeConfigCreate, db: Session = Depends(get_db),
                        user: User = Depends(require_writer)) -> JudgeConfigOut:
    _ensure_name_free(db, payload.name)
    row = LlmJudgeConfig(
        name=payload.name, enabled=payload.enabled, is_default=payload.is_default,
        provider=payload.provider, base_url=payload.base_url.rstrip("/"),
        model=payload.model, api_key=payload.api_key,
        temperature=payload.temperature, max_tokens=payload.max_tokens,
        timeout_seconds=payload.timeout_seconds,
        dimensions=list(payload.dimensions), rubrics=dict(payload.rubrics),
        updated_by=user.username,
    )
    _ensure_complete(row)
    db.add(row)
    db.flush()
    if row.is_default:
        _clear_other_defaults(db, row.id)
    db.commit()
    db.refresh(row)
    return _to_out(row)


@router.put("/judge-configs/{config_id}", response_model=JudgeConfigOut)
def update_judge_config(config_id: int, payload: JudgeConfigUpdate, db: Session = Depends(get_db),
                        user: User = Depends(require_writer)) -> JudgeConfigOut:
    row = db.get(LlmJudgeConfig, config_id)
    if row is None:
        raise HTTPException(404, "评审模型配置不存在")
    data = payload.model_dump(exclude_unset=True)
    if data.get("name"):
        _ensure_name_free(db, data["name"], exclude_id=config_id)
    for key, value in data.items():
        if key == "api_key":
            if value:
                row.api_key = value
            continue
        if key == "rubrics":
            row.rubrics = dict(value or {})
        elif value is not None:
            setattr(row, key, value)
    if row.base_url:
        row.base_url = row.base_url.rstrip("/")
    _ensure_complete(row)
    if row.is_default:
        _clear_other_defaults(db, row.id)
    row.updated_by = user.username
    db.commit()
    db.refresh(row)
    return _to_out(row)


@router.delete("/judge-configs/{config_id}", status_code=204)
def delete_judge_config(config_id: int, db: Session = Depends(get_db),
                        user: User = Depends(require_writer)) -> None:
    row = db.get(LlmJudgeConfig, config_id)
    if row is None:
        raise HTTPException(404, "评审模型配置不存在")
    used_by_version = db.query(CaseSetVersion).filter_by(judge_config_id=config_id).first()
    used_by_run = db.query(EvaluationRun).filter_by(judge_config_id=config_id).first()
    if used_by_version or used_by_run:
        raise HTTPException(409, "该评审模型已被评测集版本或评测任务引用,禁止删除;可先解除绑定或停用")
    db.delete(row)
    db.commit()


@router.post("/judge-configs/test", response_model=JudgeTestOut)
def test_judge_config(payload: JudgeTestIn, db: Session = Depends(get_db),
                      user: User = Depends(require_writer)) -> JudgeTestOut:
    """连通性测试:表单参数优先;密钥留空时回退指定/默认配置已保存的密钥。"""
    saved = db.get(LlmJudgeConfig, payload.id) if payload.id else None
    if saved is None and not payload.id:
        saved = (
            db.query(LlmJudgeConfig)
            .order_by(LlmJudgeConfig.is_default.desc(), LlmJudgeConfig.id.asc())
            .first()
        )
    judge = HttpJudge({
        "base_url": payload.base_url or (saved.base_url if saved else ""),
        "model": payload.model or (saved.model if saved else ""),
        "api_key": payload.api_key or (saved.api_key if saved else ""),
        "timeout_seconds": payload.timeout_seconds,
        "temperature": 0.0, "max_tokens": 16,
    })
    if not judge.ready:
        return JudgeTestOut(passed=False, message="配置不完整:需填写 base_url、模型名与 API Key")
    started = time.monotonic()
    try:
        reply = judge.ping()
    except Exception as exc:  # noqa: BLE001 —— 测试端点需回传可读错误而非 500
        return JudgeTestOut(passed=False, latency_ms=int((time.monotonic() - started) * 1000),
                            message=f"连接失败: {type(exc).__name__}: {str(exc)[:300]}")
    return JudgeTestOut(passed=True, latency_ms=int((time.monotonic() - started) * 1000),
                        message=f"连接成功,模型响应: {reply[:120]}")
