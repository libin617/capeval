"""LLM-as-Judge 评审模型配置测试:多套配置 CRUD、脱敏、密钥保留、维度门控与连通性。"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.core.database import SessionLocal
from app.main import app
from app.models import LlmJudgeConfig
from app.services.judge import HttpJudge, _extract_json, _normalize_judgement

client = TestClient(app)
HEADERS = {"X-Acting-User": "admin"}


@pytest.fixture()
def created_ids() -> list[int]:
    ids: list[int] = []
    yield ids
    db = SessionLocal()
    try:
        if ids:
            db.execute(delete(LlmJudgeConfig).where(LlmJudgeConfig.id.in_(ids)))
            db.commit()
    finally:
        db.close()


def _create(payload: dict) -> dict:
    r = client.post("/api/judge-configs", json=payload, headers=HEADERS)
    assert r.status_code == 201, r.text
    return r.json()


def _payload(name: str, **overrides) -> dict:
    base = {
        "name": name, "enabled": True, "base_url": "https://api.example.com/v1",
        "model": "judge-model", "api_key": "sk-test-1234567890",
        "temperature": 0.1, "max_tokens": 600, "timeout_seconds": 30,
        "dimensions": ["D4", "D7"], "rubrics": {"D4": "自定义多轮一致性标准"},
    }
    base.update(overrides)
    return base


def test_create_multiple_and_mask(created_ids: list[int]) -> None:
    suffix = uuid.uuid4().hex[:6]
    first = _create(_payload(f"评审A-{suffix}"))
    second = _create(_payload(f"评审B-{suffix}", dimensions=["D8"], rubrics={"D8": "自定义工程化标准"}))
    created_ids.extend([first["id"], second["id"]])

    assert first["has_api_key"] is True
    assert first["api_key_masked"] == "****7890"
    assert "sk-test-1234567890" not in str(first)
    # 未自定义的维度返回内置默认 rubric,便于前端直接编辑
    assert first["rubrics"]["D4"] == "自定义多轮一致性标准"
    assert "代码风格" in first["rubrics"]["D7"]

    listed = client.get("/api/judge-configs").json()
    names = {c["name"] for c in listed}
    assert f"评审A-{suffix}" in names and f"评审B-{suffix}" in names

    # 名称唯一
    dup = client.post("/api/judge-configs", json=_payload(f"评审A-{suffix}"), headers=HEADERS)
    assert dup.status_code == 409


def test_update_keeps_key_and_sets_single_default(created_ids: list[int]) -> None:
    suffix = uuid.uuid4().hex[:6]
    first = _create(_payload(f"默认A-{suffix}", is_default=True))
    second = _create(_payload(f"默认B-{suffix}", api_key="sk-second-000011112222"))
    created_ids.extend([first["id"], second["id"]])

    r = client.put(f"/api/judge-configs/{first['id']}", json={
        "name": f"默认A改-{suffix}", "api_key": "", "dimensions": ["D4", "D7", "D8"],
    }, headers=HEADERS)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["name"] == f"默认A改-{suffix}"
    assert data["has_api_key"] is True and data["api_key_masked"] == "****7890"
    assert data["dimensions"] == ["D4", "D7", "D8"]

    # 设为默认后,其余配置自动取消默认
    r = client.put(f"/api/judge-configs/{second['id']}", json={"is_default": True}, headers=HEADERS)
    assert r.status_code == 200
    listed = {c["id"]: c for c in client.get("/api/judge-configs").json()}
    assert listed[second["id"]]["is_default"] is True
    assert listed[first["id"]]["is_default"] is False


def test_enabled_requires_complete_config() -> None:
    suffix = uuid.uuid4().hex[:4]
    r = client.post("/api/judge-configs", json={
        "name": f"缺配置-{suffix}", "enabled": True, "base_url": "", "model": "", "api_key": "",
    }, headers=HEADERS)
    assert r.status_code == 400
    assert "需填写" in r.json()["detail"]


def test_delete_config(created_ids: list[int]) -> None:
    suffix = uuid.uuid4().hex[:6]
    cfg = _create(_payload(f"待删-{suffix}"))
    created_ids.append(cfg["id"])
    r = client.delete(f"/api/judge-configs/{cfg['id']}", headers=HEADERS)
    assert r.status_code == 204
    stored = SessionLocal().get(LlmJudgeConfig, cfg["id"])
    assert stored is None


def test_test_endpoint_reports_failure_readably() -> None:
    """不可达地址应返回 passed=false 且带可读原因,不能 500。"""
    r = client.post("/api/judge-configs/test", json={
        "base_url": "http://127.0.0.1:9", "model": "m", "api_key": "k",
        "timeout_seconds": 5,
    }, headers=HEADERS)
    assert r.status_code == 200
    data = r.json()
    assert data["passed"] is False
    assert data["message"].startswith("连接失败")


def test_test_endpoint_requires_complete_config() -> None:
    r = client.post("/api/judge-configs/test",
                    json={"id": None, "base_url": "", "model": ""}, headers=HEADERS)
    assert r.status_code == 200
    data = r.json()
    # 无已保存密钥可回退时必须提示配置不完整(若库中已有配置,则可能尝试连接,但不允许 500)
    assert data["passed"] is False or "连接" in data["message"]


def test_extract_and_normalize_judgement() -> None:
    raw = '```json\n{"score": 3.5, "rationale": "命名一般", "signals": [' \
          '{"signal": "naming", "verdict": "warn", "detail": "变量名过短", "deduction": 1.5}]}\n```'
    obj = _extract_json(raw)
    assert obj is not None and obj["score"] == 3.5
    norm = _normalize_judgement(obj)
    assert norm is not None
    assert norm["score"] == 3.5
    assert norm["signals"][0]["verdict"] == "warn"

    assert _normalize_judgement({"score": 9})["score"] == 5.0
    assert _normalize_judgement({"score": "abc"}) is None


def test_dimension_gating_and_custom_rubric() -> None:
    """未启用的维度不触发模型调用;自定义 rubric 优先生效。"""
    judge = HttpJudge({"base_url": "https://x", "model": "m", "api_key": "k",
                       "dimensions": ["D4"], "rubrics": {"D4": "自定义标准"}})
    assert judge.ready is True
    assert judge.rubrics["D4"] == "自定义标准"
    assert judge.judge_dimension("D7", {}) is None
    assert judge.judge_dimension("D9", {}) is None
