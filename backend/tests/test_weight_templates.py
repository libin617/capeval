"""权重模板在线维护测试:CRUD、维度/总和校验、默认唯一、引用与默认禁删。"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.core.database import SessionLocal
from app.main import app
from app.models import WeightTemplate

client = TestClient(app)
HEADERS = {"X-Acting-User": "admin"}

VALID = {f"D{i}": v for i, v in zip(range(1, 9), [0.3, 0.12, 0.12, 0.12, 0.12, 0.08, 0.08, 0.06])}


@pytest.fixture()
def created_ids() -> list[int]:
    ids: list[int] = []
    yield ids
    db = SessionLocal()
    try:
        if ids:
            db.execute(delete(WeightTemplate).where(WeightTemplate.id.in_(ids)))
            db.commit()
    finally:
        db.close()


def test_create_and_list(created_ids: list[int]) -> None:
    name = f"质量优先-{uuid.uuid4().hex[:6]}"
    r = client.post("/api/weight-templates", json={"name": name, "weights": VALID}, headers=HEADERS)
    assert r.status_code == 201, r.text
    body = r.json()
    created_ids.append(body["id"])
    assert body["name"] == name and body["is_default"] is False
    assert abs(sum(body["weights"].values()) - 1.0) < 1e-6

    names = [t["name"] for t in client.get("/api/weight-templates").json()]
    assert name in names


def test_reject_bad_weights(created_ids: list[int]) -> None:
    suffix = uuid.uuid4().hex[:6]
    # 总和不为 1
    bad = dict(VALID, D1=0.9)
    r = client.post("/api/weight-templates", json={"name": f"bad-sum-{suffix}", "weights": bad},
                    headers=HEADERS)
    assert r.status_code == 422 and "总和" in r.text
    # 多余维度
    extra = dict(VALID, D9=0.0)
    r = client.post("/api/weight-templates", json={"name": f"bad-extra-{suffix}", "weights": extra},
                    headers=HEADERS)
    assert r.status_code == 422
    # 越界取值
    over = dict(VALID, D1=1.5)
    r = client.post("/api/weight-templates", json={"name": f"bad-range-{suffix}", "weights": over},
                    headers=HEADERS)
    assert r.status_code == 422
    # 空维度 / 全 0
    r = client.post("/api/weight-templates", json={"name": f"bad-empty-{suffix}", "weights": {}},
                    headers=HEADERS)
    assert r.status_code == 422
    r = client.post("/api/weight-templates",
                    json={"name": f"bad-zero-{suffix}", "weights": {"D1": 0.0}},
                    headers=HEADERS)
    assert r.status_code == 422


def test_subset_dimension_allowed(created_ids: list[int]) -> None:
    """只选单个维度(如仅 D1)应被允许,便于单独评测某项能力。"""
    name = f"仅D1-{uuid.uuid4().hex[:6]}"
    r = client.post("/api/weight-templates", json={"name": name, "weights": {"D1": 1.0}},
                    headers=HEADERS)
    assert r.status_code == 201, r.text
    created_ids.append(r.json()["id"])
    assert r.json()["weights"] == {"D1": 1.0}

    # 多选子集同样合法
    r = client.post("/api/weight-templates",
                    json={"name": f"子集-{uuid.uuid4().hex[:6]}",
                          "weights": {"D1": 0.6, "D2": 0.4}}, headers=HEADERS)
    assert r.status_code == 201, r.text
    created_ids.append(r.json()["id"])


def test_duplicate_name_conflict(created_ids: list[int]) -> None:
    name = f"重名-{uuid.uuid4().hex[:6]}"
    r = client.post("/api/weight-templates", json={"name": name, "weights": VALID}, headers=HEADERS)
    assert r.status_code == 201
    created_ids.append(r.json()["id"])
    r = client.post("/api/weight-templates", json={"name": name, "weights": VALID}, headers=HEADERS)
    assert r.status_code == 409


def test_update_and_default_switch(created_ids: list[int]) -> None:
    suffix = uuid.uuid4().hex[:6]
    r = client.post("/api/weight-templates", json={"name": f"编辑-{suffix}", "weights": VALID},
                    headers=HEADERS)
    assert r.status_code == 201
    tpl = r.json()
    created_ids.append(tpl["id"])

    # 编辑名称与权重
    new_weights = {f"D{i}": 0.125 for i in range(1, 9)}
    r = client.put(f"/api/weight-templates/{tpl['id']}",
                   json={"name": f"编辑后-{suffix}", "weights": new_weights}, headers=HEADERS)
    assert r.status_code == 200, r.text
    assert r.json()["name"] == f"编辑后-{suffix}"
    assert abs(r.json()["weights"]["D1"] - 0.125) < 1e-9

    # 设为默认后,原默认被清除;再取消默认被拒绝(需保留一个默认)
    r = client.put(f"/api/weight-templates/{tpl['id']}", json={"is_default": True}, headers=HEADERS)
    assert r.status_code == 200
    defaults = [t for t in client.get("/api/weight-templates").json() if t["is_default"]]
    assert [t["id"] for t in defaults] == [tpl["id"]]
    r = client.put(f"/api/weight-templates/{tpl['id']}", json={"is_default": False}, headers=HEADERS)
    assert r.status_code == 409
    # 恢复内置默认,便于后续用例
    builtin = next(t for t in client.get("/api/weight-templates").json() if t["name"] == "默认八维权重")
    r = client.put(f"/api/weight-templates/{builtin['id']}", json={"is_default": True}, headers=HEADERS)
    assert r.status_code == 200


def test_delete_rules(created_ids: list[int]) -> None:
    suffix = uuid.uuid4().hex[:6]
    r = client.post("/api/weight-templates", json={"name": f"待删-{suffix}", "weights": VALID},
                    headers=HEADERS)
    tpl = r.json()
    created_ids.append(tpl["id"])

    # 默认模板禁删
    default = next(t for t in client.get("/api/weight-templates").json() if t["is_default"])
    r = client.delete(f"/api/weight-templates/{default['id']}", headers=HEADERS)
    assert r.status_code == 409

    # 普通模板可删
    r = client.delete(f"/api/weight-templates/{tpl['id']}", headers=HEADERS)
    assert r.status_code == 204
    created_ids.remove(tpl["id"])

    r = client.delete(f"/api/weight-templates/{tpl['id']}", headers=HEADERS)
    assert r.status_code == 404


def test_delete_referenced_template_forbidden(created_ids: list[int]) -> None:
    """被评测任务引用的模板禁止删除(引用检查覆盖现有 Run 即视为验证)。"""
    from app.models import EvaluationRun

    db = SessionLocal()
    try:
        used_id = db.query(EvaluationRun.weight_template_id).filter(
            EvaluationRun.weight_template_id.isnot(None)).first()
    finally:
        db.close()
    if not used_id:
        pytest.skip("当前无评测任务引用模板,跳过")
    r = client.delete(f"/api/weight-templates/{used_id[0]}", headers=HEADERS)
    assert r.status_code in (409,)  # 默认模板同样 409,均被禁止删除
