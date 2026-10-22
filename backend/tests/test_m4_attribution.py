"""M4 归因分析引擎(FR-06):规则信号→三级根因库、证据链、归因 API、人工复核流程。"""

import uuid

from fastapi.testclient import TestClient

from app.main import app
from app.services.executor import execute_run

client = TestClient(app)


def _suffix() -> str:
    return uuid.uuid4().hex[:8]


def _mk_agent() -> dict:
    r = client.post("/api/agents", json={
        "name": f"pytest-m4-agent-{_suffix()}", "version_label": "v1", "access_type": "cli",
        "adapter_key": "echo_cli", "adapter_config": {},
    })
    assert r.status_code == 201, r.text
    agent = r.json()
    client.post(f"/api/agents/{agent['id']}/verify")
    return agent


def _mk_case(title: str, auto_commands: list, message: str = "ping") -> dict:
    r = client.post("/api/testcases", json={
        "title": f"{title}-{_suffix()}",
        "task_script": [{"turn": 1, "message": message, "intent_tag": ""}],
        "repo_snapshot": {"type": "files", "files": {"README.md": "# demo\n"}},
        "env_def": {"runtime": "none", "install_commands": [], "target_isolation": "any", "docker_image": "python:3.12-slim"},
        "acceptance": {"auto_commands": auto_commands, "semantic_points": ["Agent 给出响应"]},
        "efficiency_baseline": {},
        "tags": ["m4"], "est_minutes": 1,
    })
    assert r.status_code == 201, r.text
    return r.json()


def _mk_published_version(name: str, case_ids: list[int]) -> int:
    r = client.post("/api/casesets", json={
        "name": f"{name}-{_suffix()}", "description": "m4", "testcase_ids": case_ids,
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


def _create_run(agent_id: int, version_id: int, *, review_policy: str = "skip",
                fail_threshold: float = 0.5) -> dict:
    r = client.post("/api/runs", json={
        "agent_id": agent_id, "caseset_version_id": version_id,
        "weight_template_id": _default_weight_id(),
        "review_policy": review_policy, "fail_threshold": fail_threshold,
    })
    assert r.status_code == 201, r.text
    return r.json()


def _run_to_terminal(agent_id: int, version_id: int, *, review_policy: str = "skip",
                     fail_threshold: float = 0.5) -> dict:
    run = _create_run(agent_id, version_id, review_policy=review_policy,
                      fail_threshold=fail_threshold)
    client.post(f"/api/runs/{run['id']}/submit")
    execute_run(run["id"])
    return client.get(f"/api/runs/{run['id']}").json()


def _exec_attrs(execution_id: int) -> list[dict]:
    r = client.get(f"/api/executions/{execution_id}/attributions")
    assert r.status_code == 200, r.text
    return r.json()


def _rc_id_by_code(code: str) -> int:
    r = client.get("/api/root-causes")
    return next(rc for rc in r.json() if rc["code"] == code)["id"]


def test_m4_rule_attribution_and_evidence() -> None:
    """验收失败 case → RC-1.1.1 主根因 + 证据链引用轨迹事件序号。"""
    agent = _mk_agent()
    ok_case = _mk_case("m4-ok", ["test -f echo_output.txt"])
    bad_case = _mk_case("m4-bad", ["exit 1"])
    vid = _mk_published_version("m4-rule-set", [ok_case["id"], bad_case["id"]])
    run = _run_to_terminal(agent["id"], vid, fail_threshold=0.6)  # 1/2 失败 < 0.6 → completed
    assert run["status"] == "completed", run["error"]

    r = client.get(f"/api/runs/{run['id']}/attributions")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "completed"
    assert body["rootcause_version"] >= 1
    assert body["attributed_cases"] == 1
    dist = {d["code"]: d for d in body["distribution"]}
    assert "RC-1.1.1" in dist and dist["RC-1.1.1"]["primary"] >= 1
    assert body["responsibility_summary"]["agent"] >= 1

    bad_exec = next(c["id"] for c in run["cases"] if c["testcase_id"] == bad_case["id"])
    attrs = _exec_attrs(bad_exec)
    assert attrs, "应产出归因结论"
    primary = next(a for a in attrs if a["is_primary"])
    assert primary["rootcause"]["code"] == "RC-1.1.1"
    assert primary["source"] == "rule" and primary["review_status"] == "pending"
    assert primary["evidence"]["event_seqs"], "证据链必须引用 ≥1 条轨迹事件"


def test_m4_sensitive_info_maps_rc_7_2() -> None:
    """产出硬编码密钥 → sensitive_info 信号命中 RC-7.2(敏感信息泄露)。"""
    agent = _mk_agent()
    case = _mk_case("m4-secret", ["test -f echo_output.txt"], message="api_key = sk-12345")
    vid = _mk_published_version("m4-secret-set", [case["id"]])
    run = _run_to_terminal(agent["id"], vid)
    assert run["status"] == "completed", run["error"]

    r = client.get(f"/api/runs/{run['id']}/attributions")
    body = r.json()
    dist = {d["code"]: d for d in body["distribution"]}
    assert "RC-7.2" in dist and dist["RC-7.2"]["primary"] >= 1


def test_m4_review_flow() -> None:
    """review_policy=full:进 reviewing → 复核(confirm/correct)→ 收尾。"""
    agent = _mk_agent()
    ok_case = _mk_case("m4-rok", ["test -f echo_output.txt"])
    bad_case = _mk_case("m4-rbad", ["exit 1"])
    vid = _mk_published_version("m4-review-set", [ok_case["id"], bad_case["id"]])
    run = _run_to_terminal(agent["id"], vid, review_policy="full", fail_threshold=0.6)
    assert run["status"] == "reviewing", run["status"]

    r = client.get(f"/api/runs/{run['id']}/attributions")
    body = r.json()
    assert body["status"] == "reviewing" and body["review_policy"] == "full"

    bad_exec = next(c["id"] for c in run["cases"] if c["testcase_id"] == bad_case["id"])
    attrs = _exec_attrs(bad_exec)
    assert all(a["review_status"] == "pending" for a in attrs)
    target = attrs[0]

    # confirm:认可原判定
    r = client.patch(f"/api/attributions/{target['id']}",
                     json={"action": "confirm", "note": "归因合理", "reviewed_by": "qa"})
    assert r.status_code == 200, r.text
    assert r.json()["review_status"] == "confirmed"

    # correct:改判到另一个根因条目
    rc9 = _rc_id_by_code("RC-9")
    r = client.patch(f"/api/attributions/{target['id']}",
                     json={"action": "correct", "rootcause_id": rc9, "note": "实为环境问题"})
    assert r.status_code == 200, r.text
    corrected = r.json()
    assert corrected["review_status"] == "corrected"
    assert corrected["source"] == "human" and corrected["confidence"] == 1.0
    assert corrected["rootcause"]["code"] == "RC-9"

    # 复核完成 → 收尾为终态
    r = client.post(f"/api/runs/{run['id']}/review/complete")
    assert r.status_code == 200, r.text
    assert r.json()["status"] in ("completed", "failed")

    # 未到复核态的任务不可重复收尾
    assert client.post(f"/api/runs/{run['id']}/review/complete").status_code == 409


def test_m4_attribution_guards() -> None:
    """API 边界:草稿 Run 无归因 404;资源不存在 404;correct 缺 rootcause_id 422。"""
    agent = _mk_agent()
    case = _mk_case("m4-guard", ["exit 1"])
    vid = _mk_published_version("m4-guard-set", [case["id"]])
    draft = _create_run(agent["id"], vid)
    assert client.get(f"/api/runs/{draft['id']}/attributions").status_code == 404

    assert client.get("/api/runs/999999/attributions").status_code == 404
    assert client.get("/api/executions/999999/attributions").status_code == 404
    assert client.patch("/api/attributions/999999", json={"action": "confirm"}).status_code == 404

    run = _run_to_terminal(agent["id"], vid)
    exec_id = run["cases"][0]["id"]
    attrs = _exec_attrs(exec_id)
    assert attrs, "失败 case 应产出归因"
    r = client.patch(f"/api/attributions/{attrs[0]['id']}", json={"action": "correct"})
    assert r.status_code == 422
