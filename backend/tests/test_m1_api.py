"""M1 API 冒烟:注册 Agent → 校验 → 建用例 → 建用例集 → 发布 → 冻结校验。"""

import uuid

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

SUFFIX = uuid.uuid4().hex[:8]


def test_health() -> None:
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_m1_journey() -> None:
    # 1. 适配器列表
    r = client.get("/api/agents/adapters")
    assert r.status_code == 200
    keys = [a["key"] for a in r.json()]
    assert "echo_cli" in keys and "generic_cli" in keys and "http_api" in keys

    # 2. 注册 Agent(echo_cli,凭证应掩码不回显)
    r = client.post("/api/agents", json={
        "name": f"pytest-agent-{SUFFIX}", "version_label": "v0.1", "access_type": "cli",
        "adapter_key": "echo_cli", "adapter_config": {}, "credential": "sk-test-123456",
    })
    assert r.status_code == 201, r.text
    agent = r.json()
    assert agent["status"] == "unverified"
    assert "sk-test-123456" not in str(agent)
    assert agent["credential_masked"].startswith("sk")

    # 3. 冒烟校验 → 可用
    r = client.post(f"/api/agents/{agent['id']}/verify")
    assert r.status_code == 200
    body = r.json()
    assert body["passed"] is True and body["status"] == "available"

    # 4. 创建多轮用例(含验收校验)
    r = client.post("/api/testcases", json={
        "title": "pytest-用例:修复除零错误",
        "task_script": [
            {"turn": 1, "message": "修复 calc.py 的除零错误", "intent_tag": "初始需求"},
            {"turn": 2, "message": "改为抛 ValueError 而不是返回 None", "intent_tag": "需求变更"},
        ],
        "repo_snapshot": {"type": "files", "files": {"calc.py": "def div(a,b):\n    return a/b\n"}},
        "env_def": {"runtime": "python3.12", "install_commands": [], "target_isolation": "any", "docker_image": "python:3.12-slim"},
        "acceptance": {
            "auto_commands": [{"cmd": "python -m pytest tests/", "expect_exit_code": 0}],
            "security_commands": [{"cmd": "bandit -r .", "expect_exit_code": 0}],
            "semantic_points": ["除零时抛出 ValueError", "正常除法结果不变"],
            "process_expectations": ["先运行现有测试再修改"],
        },
        "efficiency_baseline": {"input_tokens": 2000, "output_tokens": 800, "tool_calls": 8, "wall_seconds": 300},
        "difficulty": "easy", "tags": ["修 bug"], "est_minutes": 15,
    })
    assert r.status_code == 201, r.text
    case = r.json()

    # 4b. 校验:空验收被拒
    r = client.post("/api/testcases", json={
        "title": "bad", "task_script": [{"turn": 1, "message": "x", "intent_tag": ""}],
        "acceptance": {}, "tags": ["t"],
    })
    assert r.status_code == 422

    # 5. 创建用例集(草稿)并发布
    r = client.post("/api/casesets", json={
        "name": f"pytest-用例集-{SUFFIX}", "description": "M1 冒烟", "testcase_ids": [case["id"]],
    })
    assert r.status_code == 201, r.text
    cs = r.json()
    assert cs["versions"][0]["status"] == "draft"
    vid = cs["versions"][0]["id"]

    r = client.post(f"/api/casesets/{cs['id']}/versions/{vid}/publish")
    assert r.status_code == 200
    assert r.json()["status"] == "published"
    assert r.json()["items"][0]["title"] == "pytest-用例:修复除零错误"

    # 6. 发布版冻结:修改 items 应被拒
    r = client.put(f"/api/casesets/{cs['id']}/versions/{vid}/items",
                   json={"testcase_ids": [case["id"], case["id"]]})
    assert r.status_code == 409

    # 7. 新草稿版本 → 发布 v2
    r = client.post(f"/api/casesets/{cs['id']}/versions", json={"testcase_ids": [case["id"]]})
    assert r.status_code == 201
    v2 = r.json()
    assert v2["version_no"] == 2 and v2["status"] == "draft"
    r = client.post(f"/api/casesets/{cs['id']}/versions/{v2['id']}/publish")
    assert r.status_code == 200

    # 8. 权重模板与根因库
    r = client.get("/api/weight-templates")
    assert r.status_code == 200
    default = [w for w in r.json() if w["is_default"]][0]
    assert abs(sum(default["weights"].values()) - 1.0) < 1e-6
    assert set(default["weights"]) == {f"D{i}" for i in range(1, 9)}

    r = client.get("/api/root-causes")
    assert r.status_code == 200
    codes = [rc["code"] for rc in r.json()]
    for expect in ["RC-1", "RC-1.1", "RC-1.1.1", "RC-9", "RC-10"]:
        assert expect in codes
    levels = {rc["code"]: rc["level"] for rc in r.json()}
    assert levels["RC-1"] == 1 and levels["RC-1.1"] == 2 and levels["RC-1.1.1"] == 3

    # 9. 被已发布用例集引用的用例:删除 → 归档而非物理删除
    r = client.delete(f"/api/testcases/{case['id']}")
    assert r.status_code == 204
    r = client.get(f"/api/testcases/{case['id']}")
    assert r.json()["status"] == "archived"

    # 10. 清理:删除 Agent(未被 Run 引用)
    r = client.delete(f"/api/agents/{agent['id']}")
    assert r.status_code == 204
