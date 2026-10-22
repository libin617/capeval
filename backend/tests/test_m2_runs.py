"""M2 全链路冒烟:Run 创建/提交/执行/轨迹/失败阈值/重试/取消 + 状态机约束。"""

import uuid

from fastapi.testclient import TestClient

from app.main import app
from app.services.executor import execute_run

client = TestClient(app)


def _suffix() -> str:
    return uuid.uuid4().hex[:8]


def _mk_agent() -> dict:
    r = client.post("/api/agents", json={
        "name": f"pytest-m2-agent-{_suffix()}", "version_label": "v1", "access_type": "cli",
        "adapter_key": "echo_cli", "adapter_config": {},
    })
    assert r.status_code == 201, r.text
    agent = r.json()
    r = client.post(f"/api/agents/{agent['id']}/verify")
    assert r.json()["passed"] is True
    return agent


def _mk_case(title: str, auto_commands: list) -> dict:
    r = client.post("/api/testcases", json={
        "title": f"{title}-{_suffix()}",
        "task_script": [{"turn": 1, "message": "ping", "intent_tag": ""}],
        "repo_snapshot": {"type": "files", "files": {"README.md": "# demo\n"}},
        "env_def": {"runtime": "none", "install_commands": [], "target_isolation": "any", "docker_image": "python:3.12-slim"},
        "acceptance": {"auto_commands": auto_commands,
                       "semantic_points": ["Agent 给出响应"]},
        "tags": ["m2"], "est_minutes": 1,
    })
    assert r.status_code == 201, r.text
    return r.json()


def _mk_caseset(name: str, case_ids: list[int]) -> dict:
    r = client.post("/api/casesets", json={
        "name": f"{name}-{_suffix()}", "description": "m2", "testcase_ids": case_ids,
    })
    assert r.status_code == 201, r.text
    cs = r.json()
    vid = cs["versions"][0]["id"]
    r = client.post(f"/api/casesets/{cs['id']}/versions/{vid}/publish")
    assert r.status_code == 200
    return {"caseset": cs, "version_id": vid}


def _mk_run(agent_id: int, version_id: int, weight_id: int, fail_threshold: float = 0.5) -> dict:
    r = client.post("/api/runs", json={
        "agent_id": agent_id, "caseset_version_id": version_id,
        "weight_template_id": weight_id, "fail_threshold": fail_threshold,
    })
    assert r.status_code == 201, r.text
    return r.json()


def _default_weight_id() -> int:
    r = client.get("/api/weight-templates")
    return [w for w in r.json() if w["is_default"]][0]["id"]


def test_m2_happy_path_and_trace() -> None:
    """全成功路径:创建→提交→同步执行→completed;轨迹含五类事件;资源有累计。"""
    agent = _mk_agent()
    case = _mk_case("m2-ok", ["test -f echo_output.txt"])
    cs = _mk_caseset("m2-ok-set", [case["id"]])
    run = _mk_run(agent["id"], cs["version_id"], _default_weight_id())

    # 草稿态:不可重试
    r = client.post(f"/api/runs/{run['id']}/retry")
    assert r.status_code == 409

    r = client.post(f"/api/runs/{run['id']}/submit")
    assert r.status_code == 200
    assert r.json()["status"] == "queued"
    assert r.json()["progress"]["total"] == 1 and r.json()["progress"]["pending"] == 1

    # 快照:权重与根因库版本
    assert run["weight_snapshot"]["D1"] == 0.30
    assert run["rootcause_version"] >= 1

    execute_run(run["id"])

    r = client.get(f"/api/runs/{run['id']}")
    body = r.json()
    assert body["status"] == "completed", body["error"]
    assert body["progress"]["succeeded"] == 1
    case_exec = body["cases"][0]
    assert case_exec["status"] == "succeeded"
    assert case_exec["resource_usage"]["input_tokens"] > 0
    assert case_exec["resource_usage"]["wall_seconds"] >= 0

    # 轨迹:五类事件齐全 + 完成声明 + 验收命令
    r = client.get(f"/api/executions/{case_exec['id']}/events")
    events = r.json()
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
    types = {e["event_type"] for e in events}
    assert {"message", "tool_call", "file_change", "resource", "system"} <= types
    kinds = [e["payload"].get("kind") for e in events if e["event_type"] == "system"]
    assert "repo_restored" in kinds and "completion_declared" in kinds and "case_succeeded" in kinds
    terminal = [e for e in events if e["event_type"] == "terminal"]
    assert terminal and terminal[0]["payload"]["exit_code"] == 0

    # 终态不可取消
    r = client.post(f"/api/runs/{run['id']}/cancel")
    assert r.status_code == 409


def test_m2_fail_threshold_and_retry() -> None:
    """失败占比≥阈值 → Run failed;重试仅重跑失败 case。"""
    agent = _mk_agent()
    ok_case = _mk_case("m2-ok2", ["test -f echo_output.txt"])
    bad_case = _mk_case("m2-bad", ["exit 1"])
    cs = _mk_caseset("m2-fail-set", [ok_case["id"], bad_case["id"]])
    run = _mk_run(agent["id"], cs["version_id"], _default_weight_id(), fail_threshold=0.5)
    client.post(f"/api/runs/{run['id']}/submit")

    execute_run(run["id"])
    r = client.get(f"/api/runs/{run['id']}")
    body = r.json()
    assert body["status"] == "failed"
    assert "1/2" in body["error"]
    cases = {c["testcase_id"]: c for c in body["cases"]}
    assert cases[ok_case["id"]]["status"] == "succeeded"
    assert cases[bad_case["id"]]["status"] == "failed"
    assert "验收命令失败" in cases[bad_case["id"]]["error"]

    # 重试:仅失败 case 重跑
    r = client.post(f"/api/runs/{run['id']}/retry")
    assert r.status_code == 200 and r.json()["status"] == "queued"
    execute_run(run["id"])

    r = client.get(f"/api/runs/{run['id']}")
    body = r.json()
    assert body["status"] == "failed"  # exit 1 恒失败,仍超阈值
    cases = {c["testcase_id"]: c for c in body["cases"]}
    assert cases[ok_case["id"]]["rerun_count"] == 0
    assert cases[ok_case["id"]]["status"] == "succeeded"
    assert cases[bad_case["id"]]["rerun_count"] == 1
    assert cases[bad_case["id"]]["status"] == "failed"


def test_m2_cancel_queued_run() -> None:
    """queued 取消:case 一并取消,执行器不再消费。"""
    agent = _mk_agent()
    case = _mk_case("m2-cancel", [])
    cs = _mk_caseset("m2-cancel-set", [case["id"]])
    run = _mk_run(agent["id"], cs["version_id"], _default_weight_id())
    client.post(f"/api/runs/{run['id']}/submit")

    r = client.post(f"/api/runs/{run['id']}/cancel")
    assert r.status_code == 200 and r.json()["status"] == "canceled"

    execute_run(run["id"])  # 已取消,不应执行
    r = client.get(f"/api/runs/{run['id']}")
    body = r.json()
    assert body["status"] == "canceled"
    assert body["cases"][0]["status"] == "canceled"
    r = client.get(f"/api/executions/{body['cases'][0]['id']}/events")
    assert r.json() == []


def test_m2_create_guards() -> None:
    """创建校验:Agent 不可用/草稿用例集/不存在的外键。"""
    agent = _mk_agent()
    case = _mk_case("m2-guard", [])
    # 草稿(未发布)用例集
    r = client.post("/api/casesets", json={
        "name": f"m2-draft-set-{_suffix()}", "description": "", "testcase_ids": [case["id"]],
    })
    draft_vid = r.json()["versions"][0]["id"]
    r = client.post("/api/runs", json={
        "agent_id": agent["id"], "caseset_version_id": draft_vid,
        "weight_template_id": _default_weight_id(),
    })
    assert r.status_code == 409

    r = client.post("/api/runs", json={
        "agent_id": 999999, "caseset_version_id": draft_vid,
        "weight_template_id": _default_weight_id(),
    })
    assert r.status_code == 404

    # 未校验 Agent 不可创建
    r = client.post("/api/agents", json={
        "name": f"pytest-m2-unverified-{_suffix()}", "version_label": "v1", "access_type": "cli",
        "adapter_key": "echo_cli", "adapter_config": {},
    })
    unverified = r.json()
    cs = _mk_caseset("m2-guard-set", [case["id"]])
    r = client.post("/api/runs", json={
        "agent_id": unverified["id"], "caseset_version_id": cs["version_id"],
        "weight_template_id": _default_weight_id(),
    })
    assert r.status_code == 409
    client.delete(f"/api/agents/{unverified['id']}")
