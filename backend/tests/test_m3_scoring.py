"""M3 八维评分引擎(FR-05):规则评分落库、质量/效率分聚合、Report upsert、评分 API。"""

import uuid

from fastapi.testclient import TestClient

from app.main import app
from app.services.executor import execute_run

client = TestClient(app)


def _suffix() -> str:
    return uuid.uuid4().hex[:8]


def _mk_agent() -> dict:
    r = client.post("/api/agents", json={
        "name": f"pytest-m3-agent-{_suffix()}", "version_label": "v1", "access_type": "cli",
        "adapter_key": "echo_cli", "adapter_config": {},
    })
    assert r.status_code == 201, r.text
    agent = r.json()
    client.post(f"/api/agents/{agent['id']}/verify")
    return agent


def _mk_case(title: str, auto_commands: list, message: str = "ping",
             efficiency_baseline: dict | None = None) -> dict:
    r = client.post("/api/testcases", json={
        "title": f"{title}-{_suffix()}",
        "task_script": [{"turn": 1, "message": message, "intent_tag": ""}],
        "repo_snapshot": {"type": "files", "files": {"README.md": "# demo\n"}},
        "env_def": {"runtime": "none", "install_commands": [], "target_isolation": "any", "docker_image": "python:3.12-slim"},
        "acceptance": {"auto_commands": auto_commands},
        "efficiency_baseline": efficiency_baseline or {},
        "tags": ["m3"], "est_minutes": 1,
    })
    assert r.status_code == 201, r.text
    return r.json()


def _mk_published_version(name: str, case_ids: list[int]) -> int:
    r = client.post("/api/casesets", json={
        "name": f"{name}-{_suffix()}", "description": "m3", "testcase_ids": case_ids,
    })
    assert r.status_code == 201, r.text
    cs = r.json()
    vid = cs["versions"][0]["id"]
    r = client.post(f"/api/casesets/{cs['id']}/versions/{vid}/publish")
    assert r.status_code == 200
    return vid


def _default_weight_id() -> int:
    r = client.get("/api/weight-templates")
    return [w for w in r.json() if w["is_default"]][0]["id"]


def _run_to_terminal(agent_id: int, version_id: int, fail_threshold: float = 0.5) -> dict:
    r = client.post("/api/runs", json={
        "agent_id": agent_id, "caseset_version_id": version_id,
        "weight_template_id": _default_weight_id(), "fail_threshold": fail_threshold,
    })
    assert r.status_code == 201, r.text
    run = r.json()
    client.post(f"/api/runs/{run['id']}/submit")
    execute_run(run["id"])
    return client.get(f"/api/runs/{run['id']}").json()


def _exec_scores(execution_id: int) -> dict[str, dict]:
    r = client.get(f"/api/executions/{execution_id}/scores")
    assert r.status_code == 200, r.text
    return {s["dimension"]: s for s in r.json()}


def test_m3_scoring_happy_path() -> None:
    """echo 成功 case:D1=5、八维落库(除无基线 D6)、Report 生成、效率分为 None。"""
    agent = _mk_agent()
    case = _mk_case("m3-ok", ["test -f echo_output.txt"])
    vid = _mk_published_version("m3-ok-set", [case["id"]])
    run = _run_to_terminal(agent["id"], vid)
    assert run["status"] == "completed", run["error"]

    exec_id = run["cases"][0]["id"]
    scores = _exec_scores(exec_id)
    assert set(scores) == {"D1", "D2", "D3", "D4", "D5", "D7", "D8"}  # D6 无基线不评分
    assert scores["D1"]["score"] == 5.0
    assert scores["D1"]["details"]["stats"]["pass_rate"] == 1.0
    assert scores["D5"]["score"] == 5.0
    assert scores["D4"]["details"]["method"] == "rule_approx"  # LLM Judge 未启用
    assert "LLM Judge" in scores["D4"]["details"]["note"]

    # Run 级评分 API
    r = client.get(f"/api/runs/{run['id']}/scores")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["quality_score"] is not None and body["quality_score"] > 90
    assert body["efficiency_score"] is None  # 无效率基线
    summary = body["dimension_summary"]
    assert summary["D1"]["score"] == 5.0 and summary["D1"]["weight"] == 0.30
    assert summary["D6"]["score"] is None and summary["D6"]["scored_cases"] == 0
    assert body["cases"][0]["execution_id"] == exec_id
    assert body["cases"][0]["dimensions"]["D6"] is None


def test_m3_acceptance_failure_caps_d1() -> None:
    """验收命令失败:D1≤1 且 Run 级维度分拉低;流程类信号有记录。"""
    agent = _mk_agent()
    ok_case = _mk_case("m3-ok2", ["test -f echo_output.txt"])
    bad_case = _mk_case("m3-bad", ["exit 1"])
    vid = _mk_published_version("m3-fail-set", [ok_case["id"], bad_case["id"]])
    run = _run_to_terminal(agent["id"], vid, fail_threshold=0.6)  # 1/2 失败 < 0.6 → completed
    assert run["status"] == "completed"

    cases = {c["testcase_id"]: c for c in run["cases"]}
    bad_scores = _exec_scores(cases[bad_case["id"]]["id"])
    assert bad_scores["D1"]["score"] <= 1.0
    signal_names = [s["name"] for s in bad_scores["D1"]["details"]["signals"]]
    assert "acceptance_failed" in signal_names

    r = client.get(f"/api/runs/{run['id']}/scores")
    summary = r.json()["dimension_summary"]
    assert summary["D1"]["scored_cases"] == 2
    assert summary["D1"]["score"] < 5.0  # 被失败 case 拉低


def test_m3_efficiency_baseline_scored() -> None:
    """配置 token_baseline:D6 已评,效率分折算为百分制。"""
    agent = _mk_agent()
    # echo "ping":input=1,output=2,共 3 tokens,远低于基线 100 → D6=5,效率分=100
    case = _mk_case("m3-eff", ["test -f echo_output.txt"],
                    efficiency_baseline={"token_baseline": 100})
    vid = _mk_published_version("m3-eff-set", [case["id"]])
    run = _run_to_terminal(agent["id"], vid)
    assert run["status"] == "completed"

    exec_id = run["cases"][0]["id"]
    scores = _exec_scores(exec_id)
    assert "D6" in scores
    assert scores["D6"]["score"] == 5.0
    assert scores["D6"]["details"]["stats"]["ratio"] <= 1.2

    r = client.get(f"/api/runs/{run['id']}/scores")
    body = r.json()
    assert body["efficiency_score"] == 100.0
    assert body["cases"][0]["efficiency_score"] == 100.0


def test_m3_sensitive_info_deduction() -> None:
    """产出内容命中敏感信息模式:D5 扣分;信号含 sensitive_info。"""
    agent = _mk_agent()
    case = _mk_case("m3-secret", ["test -f echo_output.txt"], message="api_key = sk-12345")
    vid = _mk_published_version("m3-secret-set", [case["id"]])
    run = _run_to_terminal(agent["id"], vid)
    assert run["status"] == "completed"

    exec_id = run["cases"][0]["id"]
    scores = _exec_scores(exec_id)
    assert scores["D5"]["score"] < 5.0
    signal_names = [s["name"] for s in scores["D5"]["details"]["signals"]]
    assert "sensitive_info" in signal_names
    assert scores["D5"]["details"]["stats"]["sensitive_hits"] >= 1


def test_m3_scores_api_guards() -> None:
    """API 边界:草稿 Run 无 Report 返回 404;不存在的资源 404。"""
    agent = _mk_agent()
    case = _mk_case("m3-guard", ["true"])
    vid = _mk_published_version("m3-guard-set", [case["id"]])
    r = client.post("/api/runs", json={
        "agent_id": agent["id"], "caseset_version_id": vid,
        "weight_template_id": _default_weight_id(),
    })
    draft = r.json()
    r = client.get(f"/api/runs/{draft['id']}/scores")
    assert r.status_code == 404

    assert client.get("/api/runs/999999/scores").status_code == 404
    assert client.get("/api/executions/999999/scores").status_code == 404
