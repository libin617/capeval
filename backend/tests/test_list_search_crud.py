"""列表模糊查询(q 参数)与编辑/删除端点测试(Runs 草稿删除 / CaseSet 信息编辑)。"""

import uuid

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _suffix() -> str:
    return uuid.uuid4().hex[:8]


def _mk_agent(name: str) -> dict:
    r = client.post("/api/agents", json={
        "name": name, "version_label": "v1", "access_type": "cli",
        "adapter_key": "echo_cli", "adapter_config": {},
    })
    assert r.status_code == 201, r.text
    agent = r.json()
    r = client.post(f"/api/agents/{agent['id']}/verify")
    assert r.json()["passed"] is True
    return agent


def _mk_case(title: str) -> dict:
    r = client.post("/api/testcases", json={
        "title": title,
        "task_script": [{"turn": 1, "message": "ping", "intent_tag": ""}],
        "repo_snapshot": {"type": "files", "files": {"README.md": "# demo\n"}},
        "env_def": {"runtime": "none", "install_commands": [], "target_isolation": "any", "docker_image": "python:3.12-slim"},
        "acceptance": {"auto_commands": [{"cmd": "true", "expect_exit_code": 0}],
                       "semantic_points": ["Agent 给出响应"]},
        "tags": ["search"], "est_minutes": 1,
    })
    assert r.status_code == 201, r.text
    return r.json()


def _mk_caseset(name: str, case_ids: list[int], publish: bool = True) -> dict:
    r = client.post("/api/casesets", json={
        "name": name, "description": "search-test", "testcase_ids": case_ids,
    })
    assert r.status_code == 201, r.text
    cs = r.json()
    vid = cs["versions"][0]["id"]
    if publish:
        r = client.post(f"/api/casesets/{cs['id']}/versions/{vid}/publish")
        assert r.status_code == 200
    return cs


def _default_weight_id() -> int:
    r = client.get("/api/weight-templates")
    return [w for w in r.json() if w["is_default"]][0]["id"]


def test_agents_fuzzy_search() -> None:
    token = f"uniqagent{_suffix()}"
    agent = _mk_agent(f"{token}-bot")
    r = client.get("/api/agents", params={"q": token})
    assert r.status_code == 200
    ids = [a["id"] for a in r.json()]
    assert agent["id"] in ids
    # 不存在的关键词 → 不命中
    r = client.get("/api/agents", params={"q": f"nope-{_suffix()}"})
    assert agent["id"] not in [a["id"] for a in r.json()]


def test_testcases_fuzzy_search() -> None:
    token = f"uniqcase{_suffix()}"
    case = _mk_case(f"{token} 修复除零")
    r = client.get("/api/testcases", params={"q": token})
    assert r.status_code == 200
    assert case["id"] in [c["id"] for c in r.json()]
    # q + 难度组合过滤:该用例未指定难度默认 medium
    r = client.get("/api/testcases", params={"q": token, "difficulty": "hard"})
    assert case["id"] not in [c["id"] for c in r.json()]


def test_caseset_update_and_search() -> None:
    token = _suffix()
    case = _mk_case(f"cs-case-{token}")
    cs_a = _mk_caseset(f"uniqsetA-{token}", [case["id"]])
    cs_b = _mk_caseset(f"uniqsetB-{token}", [case["id"]])

    # PATCH 修改名称/描述
    r = client.patch(f"/api/casesets/{cs_b['id']}", json={
        "name": f"uniqsetB-renamed-{token}", "description": "已更新描述",
    })
    assert r.status_code == 200, r.text
    assert r.json()["description"] == "已更新描述"

    # 名称唯一冲突 → 409
    r = client.patch(f"/api/casesets/{cs_b['id']}", json={"name": cs_a["name"]})
    assert r.status_code == 409

    # q 模糊搜索:按新名称命中
    r = client.get("/api/casesets", params={"q": f"renamed-{token}"})
    assert cs_b["id"] in [s["id"] for s in r.json()]
    assert cs_a["id"] not in [s["id"] for s in r.json()]


def test_runs_search_and_draft_delete() -> None:
    token = _suffix()
    agent = _mk_agent(f"runagent-{token}")
    case = _mk_case(f"run-case-{token}")
    cs = _mk_caseset(f"run-set-{token}", [case["id"]])
    version_id = cs["versions"][0]["id"]

    r = client.post("/api/runs", json={
        "agent_id": agent["id"], "caseset_version_id": version_id,
        "weight_template_id": _default_weight_id(), "fail_threshold": 0.5,
    })
    assert r.status_code == 201, r.text
    run = r.json()
    assert run["status"] == "draft"

    # q 按 Agent 名模糊命中
    r = client.get("/api/runs", params={"q": f"runagent-{token}"})
    assert run["id"] in [x["id"] for x in r.json()]
    # q 按用例集名模糊命中
    r = client.get("/api/runs", params={"q": f"run-set-{token}"})
    assert run["id"] in [x["id"] for x in r.json()]
    # 不命中关键词
    r = client.get("/api/runs", params={"q": f"nope-{_suffix()}"})
    assert run["id"] not in [x["id"] for x in r.json()]

    # 进行中(已提交排队)不可删除
    r = client.post(f"/api/runs/{run['id']}/submit")
    assert r.status_code == 200
    r = client.delete(f"/api/runs/{run['id']}")
    assert r.status_code == 409
    # 取消后(终态)可删除,且关联数据级联清理
    r = client.post(f"/api/runs/{run['id']}/cancel")
    assert r.status_code == 200
    r = client.delete(f"/api/runs/{run['id']}")
    assert r.status_code == 204
    r = client.get(f"/api/runs/{run['id']}")
    assert r.status_code == 404

    # 新草稿可删除
    r = client.post("/api/runs", json={
        "agent_id": agent["id"], "caseset_version_id": version_id,
        "weight_template_id": _default_weight_id(), "fail_threshold": 0.5,
    })
    run2 = r.json()
    r = client.delete(f"/api/runs/{run2['id']}")
    assert r.status_code == 204
    r = client.get(f"/api/runs/{run2['id']}")
    assert r.status_code == 404


def test_run_draft_edit() -> None:
    """草稿任务可编辑(名称/Agent/用例集版本/权重/阈值),提交后禁止编辑。"""
    token = _suffix()
    agent = _mk_agent(f"edit-agent-{token}")
    case = _mk_case(f"edit-case-{token}")
    cs = _mk_caseset(f"edit-set-{token}", [case["id"]])
    version_id = cs["versions"][0]["id"]

    r = client.post("/api/runs", json={
        "agent_id": agent["id"], "caseset_version_id": version_id,
        "weight_template_id": _default_weight_id(), "fail_threshold": 0.5,
    })
    assert r.status_code == 201, r.text
    run = r.json()

    # 编辑名称与阈值
    r = client.patch(f"/api/runs/{run['id']}", json={"name": f"改名-{token}", "fail_threshold": 0.3})
    assert r.status_code == 200, r.text
    assert r.json()["name"] == f"改名-{token}"
    assert r.json()["fail_threshold"] == 0.3

    # 名称置空 → 按组合自动生成
    r = client.patch(f"/api/runs/{run['id']}", json={"name": ""})
    assert r.status_code == 200
    assert r.json()["name"]

    # 校验失败:不存在的用例集版本 404
    r = client.patch(f"/api/runs/{run['id']}", json={"caseset_version_id": 99999999})
    assert r.status_code == 404

    # 提交后禁止编辑(快照固化)
    r = client.post(f"/api/runs/{run['id']}/submit")
    assert r.status_code == 200
    r = client.patch(f"/api/runs/{run['id']}", json={"name": "x"})
    assert r.status_code == 409
    r = client.post(f"/api/runs/{run['id']}/cancel")
    assert r.status_code == 200


def test_caseset_delete_cascade() -> None:
    """未被引用的用例集删除时级联清理版本与条目(回归:曾因此 500)。"""
    token = _suffix()
    case = _mk_case(f"del-case-{token}")
    cs = _mk_caseset(f"del-set-{token}", [case["id"]], publish=False)
    vid = cs["versions"][0]["id"]

    r = client.delete(f"/api/casesets/{cs['id']}")
    assert r.status_code == 204, r.text
    r = client.get(f"/api/casesets/{cs['id']}")
    assert r.status_code == 404
    # 版本级联删除
    r = client.get(f"/api/casesets/{cs['id']}/versions/{vid}")
    assert r.status_code == 404

    # 被评测任务引用的用例集 → 409
    agent = _mk_agent(f"del-agent-{token}")
    cs2 = _mk_caseset(f"del-set2-{token}", [case["id"]])
    r = client.post("/api/runs", json={
        "agent_id": agent["id"], "caseset_version_id": cs2["versions"][0]["id"],
        "weight_template_id": _default_weight_id(), "fail_threshold": 0.5,
    })
    assert r.status_code == 201
    r = client.delete(f"/api/casesets/{cs2['id']}")
    assert r.status_code == 409
