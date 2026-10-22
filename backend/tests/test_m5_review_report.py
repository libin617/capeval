"""M5 功能验收:FR-07 根因库在线维护、FR-08 人工抽检与修正、FR-09 报告与对比。"""

import uuid

from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.main import app
from app.models import Attribution, RootCause
from app.services.executor import execute_run

client = TestClient(app)


def _suffix() -> str:
    return uuid.uuid4().hex[:8]


def _purge_root_causes(prefixes: tuple[str, ...]) -> None:
    """清理历史遗留的测试根因条目(仅测试环境使用),保证用例可重复执行。"""
    db = SessionLocal()
    try:
        rows = db.query(RootCause).all()
        ids = [rc.id for rc in rows if rc.name.startswith(prefixes)]
        if ids:
            db.query(Attribution).filter(Attribution.rootcause_id.in_(ids)) \
                .delete(synchronize_session=False)
            db.query(RootCause).filter(RootCause.id.in_(ids)).delete(synchronize_session=False)
            db.commit()
    finally:
        db.close()


def _mk_agent() -> dict:
    r = client.post("/api/agents", json={
        "name": f"pytest-m5-agent-{_suffix()}", "version_label": "v1", "access_type": "cli",
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
        "efficiency_baseline": {"token_baseline": 100},
        "tags": ["m5"], "est_minutes": 1,
    })
    assert r.status_code == 201, r.text
    return r.json()


def _mk_published_version(name: str, case_ids: list[int]) -> int:
    r = client.post("/api/casesets", json={
        "name": f"{name}-{_suffix()}", "description": "m5", "testcase_ids": case_ids,
    })
    assert r.status_code == 201, r.text
    cs = r.json()
    vid = cs["versions"][0]["id"]
    assert client.post(f"/api/casesets/{cs['id']}/versions/{vid}/publish").status_code == 200
    return vid


def _default_weight_id() -> int:
    r = client.get("/api/weight-templates")
    return [w for w in r.json() if w["is_default"]][0]["id"]


def _run_to_terminal(agent_id: int, version_id: int, *, review_policy: str = "skip",
                     fail_threshold: float = 0.6) -> dict:
    r = client.post("/api/runs", json={
        "agent_id": agent_id, "caseset_version_id": version_id,
        "weight_template_id": _default_weight_id(),
        "review_policy": review_policy, "fail_threshold": fail_threshold,
    })
    assert r.status_code == 201, r.text
    run = r.json()
    client.post(f"/api/runs/{run['id']}/submit")
    execute_run(run["id"])
    return client.get(f"/api/runs/{run['id']}").json()


def _rc_by_code(code: str) -> dict:
    return next(rc for rc in client.get("/api/root-causes").json() if rc["code"] == code)


# ---------- FR-07 根因库在线维护 ----------
def test_m5_rootcause_crud_and_versioning() -> None:
    """三级层级校验 + 编码自动生成 + 每次增改产生新版本。"""
    _purge_root_causes(("测试二级-", "测试三级"))
    before = client.get("/api/root-causes/library-version").json()["version_no"]
    top = _rc_by_code("RC-1")

    r = client.post("/api/root-causes", json={
        "level": 2, "parent_id": top["id"], "name": f"测试二级-{_suffix()}",
        "description": "M5 在线新增", "responsibility": "agent",
        "detection_signals": {"rules": [{"signal": "tool_call_failed", "primary": False}],
                              "related_dimensions": ["D3"]},
    })
    assert r.status_code == 201, r.text
    created = r.json()
    code = created["rootcause"]["code"]
    assert code.startswith("RC-1.") and created["rootcause"]["enabled"] is True
    assert created["library_version"] == before + 1
    assert created["rootcause"]["introduced_version"] == before + 1

    # 三级挂在新增二级下 → 编码 RC-1.x.1
    r = client.post("/api/root-causes", json={
        "level": 3, "parent_id": created["rootcause"]["id"], "name": "测试三级",
        "detection_signals": {"rules": ["acceptance_failed"]},
    })
    assert r.status_code == 201, r.text
    assert r.json()["rootcause"]["code"] == f"{code}.1"

    # 层级不匹配 → 409;未知信号 → 422;一级带父级 → 409
    assert client.post("/api/root-causes", json={
        "level": 3, "parent_id": top["id"], "name": "错误层级"}).status_code == 409
    assert client.post("/api/root-causes", json={
        "level": 3, "parent_id": created["rootcause"]["id"], "name": "坏信号",
        "detection_signals": {"rules": [{"signal": "not_a_real_signal"}]}}).status_code == 422
    assert client.post("/api/root-causes", json={
        "level": 1, "parent_id": top["id"], "name": "一级带父"}).status_code == 409

    # 修改信号:版本再 +1,introduced_version 提升(仅影响新版本之后的 Run)
    rc_id = created["rootcause"]["id"]
    r = client.patch(f"/api/root-causes/{rc_id}", json={
        "detection_signals": {"rules": [{"signal": "tool_call_failed", "primary": True}],
                              "llm_prompt": "判断是否工具参数错误"},
        "note": "调整信号绑定"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["library_version"] == before + 3
    assert body["rootcause"]["introduced_version"] == before + 3
    assert body["warning"]

    # 停用后可再启用
    r = client.patch(f"/api/root-causes/{rc_id}", json={"enabled": False})
    assert r.json()["rootcause"]["enabled"] is False
    assert client.patch(f"/api/root-causes/{rc_id}", json={"enabled": True}).json()["rootcause"]["enabled"]

    # 有子节点 → 禁止删除
    assert client.delete(f"/api/root-causes/{rc_id}").status_code == 409

    # 叶子(三级)可删除
    leaf = next(rc for rc in client.get("/api/root-causes").json() if rc["code"] == f"{code}.1")
    assert client.delete(f"/api/root-causes/{leaf['id']}").status_code == 200
    # 再次删除 → 404
    assert client.delete(f"/api/root-causes/{leaf['id']}").status_code == 404


def test_m5_rootcause_library_takes_effect_in_attribution() -> None:
    """新增根因绑定的信号立即参与后续归因,无需改代码。"""
    # 清理历史遗留同名根因,避免旧条目抢占主根因导致断言不稳定
    _purge_root_causes(("新增-验收失败-",))
    rc = client.post("/api/root-causes", json={
        "level": 3, "parent_id": _rc_by_code("RC-6.2")["id"],
        "name": f"新增-验收失败-{_suffix()}", "description": "新沉淀的失败模式",
        "responsibility": "agent",
        "detection_signals": {"rules": [{"signal": "acceptance_failed", "primary": True},
                                        {"signal": "execution_failed", "primary": True}]},
    }).json()["rootcause"]

    agent = _mk_agent()
    bad = _mk_case("m5-rc-bad", ["exit 1"])
    vid = _mk_published_version("m5-rc-set", [bad["id"]])
    run = _run_to_terminal(agent["id"], vid)
    body = client.get(f"/api/runs/{run['id']}/attributions").json()
    codes = {d["code"] for d in body["distribution"]}
    assert rc["code"] in codes, f"新根因应参与归因,实际 {codes}"

    # 停用后不参与新归因
    assert client.patch(f"/api/root-causes/{rc['id']}", json={"enabled": False}).status_code == 200
    run2 = _run_to_terminal(agent["id"], vid)
    body2 = client.get(f"/api/runs/{run2['id']}/attributions").json()
    assert rc["code"] not in {d["code"] for d in body2["distribution"]}
    client.patch(f"/api/root-causes/{rc['id']}", json={"enabled": True})


def test_m5_rootcause_from_undetermined_case() -> None:
    """复核时对「未确定」案件一键转新根因:自动带入证据示例。"""
    _purge_root_causes(("未确定转化-",))
    agent = _mk_agent()
    case = _mk_case("m5-und", [])
    vid = _mk_published_version("m5-und-set", [case["id"]])
    run = _run_to_terminal(agent["id"], vid)
    exec_id = run["cases"][0]["id"]

    r = client.post("/api/root-causes", json={
        "level": 3, "parent_id": _rc_by_code("RC-5.2")["id"],
        "name": f"未确定转化-{_suffix()}", "source_execution_id": exec_id,
        "detection_signals": {"rules": []},
    })
    assert r.status_code == 201, r.text
    assert "证据示例" in r.json()["rootcause"]["description"]
    assert str(exec_id) in r.json()["rootcause"]["description"]


# ---------- FR-08 人工抽检与修正 ----------
def test_m5_sample_policy_and_review_trace() -> None:
    """sample 策略仅抽样部分结论(其余 not_sampled),复核留痕写入 review_history。"""
    agent = _mk_agent()
    ok = _mk_case("m5-s-ok", ["test -f echo_output.txt"])
    bad1 = _mk_case("m5-s-bad1", ["exit 1"])
    bad2 = _mk_case("m5-s-bad2", ["exit 2"])
    bad3 = _mk_case("m5-s-bad3", ["exit 3"])
    vid = _mk_published_version("m5-sample-set", [ok["id"], bad1["id"], bad2["id"], bad3["id"]])
    run = _run_to_terminal(agent["id"], vid, review_policy="sample", fail_threshold=0.9)
    assert run["status"] == "reviewing", run

    body = client.get(f"/api/runs/{run['id']}/attributions").json()
    assert body["review_policy"] == "sample"
    assert body["pending_review"] >= 1
    assert body["pending_review"] + body["not_sampled"] == body["total_attributions"]

    exec_id = next(c["id"] for c in run["cases"] if c["testcase_id"] == bad1["id"])
    attr = client.get(f"/api/executions/{exec_id}/attributions").json()[0]
    # M6:复核人取 X-Acting-User 身份(evalator 角色),不再使用自由文本 reviewed_by
    r = client.patch(f"/api/attributions/{attr['id']}", json={
        "action": "correct", "rootcause_id": _rc_by_code("RC-9")["id"],
        "note": "确认是环境准备问题"}, headers={"X-Acting-User": "evaluator"})
    assert r.status_code == 200, r.text
    updated = r.json()
    assert updated["review_status"] == "corrected"
    assert len(updated["review_history"]) == 1
    hist = updated["review_history"][0]
    assert hist["action"] == "correct" and hist["by"] == "评测工程师"
    assert hist["from_rootcause_id"] == attr["rootcause"]["id"]
    assert hist["to_rootcause_id"] == updated["rootcause"]["id"]

    # 未到复核态的 Run 不可重复收尾/放弃
    r = client.post(f"/api/runs/{run['id']}/review/complete")
    assert r.status_code == 200, r.text
    assert r.json()["status"] in ("completed", "failed")
    assert client.post(f"/api/runs/{run['id']}/review/waive").status_code == 409

    report = client.get(f"/api/runs/{run['id']}/report").json()
    assert report["overview"]["review_mark"] == "reviewed"


def test_m5_waive_review_marks_unreviewed() -> None:
    """放弃复核直接发布 → 报告标记 unreviewed,并在 content.review 留存 waived 原因。"""
    agent = _mk_agent()
    bad = _mk_case("m5-waive-bad", ["exit 1"])
    vid = _mk_published_version("m5-waive-set", [bad["id"]])
    run = _run_to_terminal(agent["id"], vid, review_policy="full", fail_threshold=0.9)
    assert run["status"] == "reviewing"

    r = client.post(f"/api/runs/{run['id']}/review/waive")
    assert r.status_code == 200, r.text
    assert r.json()["status"] in ("completed", "failed")

    report = client.get(f"/api/runs/{run['id']}/report").json()
    assert report["overview"]["review_mark"] == "unreviewed"


def test_m5_skip_policy_never_enters_reviewing() -> None:
    """skip 策略:不进入 reviewing,报告标记 unreviewed。"""
    agent = _mk_agent()
    bad = _mk_case("m5-skip-bad", ["exit 1"])
    vid = _mk_published_version("m5-skip-set", [bad["id"]])
    run = _run_to_terminal(agent["id"], vid, review_policy="skip")
    assert run["status"] in ("completed", "failed")
    report = client.get(f"/api/runs/{run['id']}/report").json()
    assert report["overview"]["review_mark"] == "unreviewed"


# ---------- FR-09 报告 / 导出 / 分享 / 对比 ----------
def test_m5_report_structure_and_export() -> None:
    agent = _mk_agent()
    ok = _mk_case("m5-rep-ok", ["test -f echo_output.txt"])
    bad = _mk_case("m5-rep-bad", ["exit 1"])
    vid = _mk_published_version("m5-report-set", [ok["id"], bad["id"]])
    run = _run_to_terminal(agent["id"], vid)

    r = client.get(f"/api/runs/{run['id']}/report")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["overview"]["quality_score"] is not None
    assert len(body["dimensions"]) == 8
    assert body["top_failures"] and body["top_failures"][0]["primary"]["code"]
    assert "matrix" in body["attribution"]
    assert body["drilldown"]["by_difficulty"]
    assert body["overview"]["weight_snapshot"]

    r = client.get(f"/api/runs/{run['id']}/report/export?format=html")
    assert r.status_code == 200
    assert "attachment" in r.headers["content-disposition"]
    assert "Code Agent 评测报告" in r.text
    assert "<svg" not in r.text  # 自包含 HTML 不依赖外部资源
    assert "http://" not in r.text.split("<body")[1][:200]

    # 未评分(草稿)Run → 404
    draft = client.post("/api/runs", json={
        "agent_id": agent["id"], "caseset_version_id": vid,
        "weight_template_id": _default_weight_id()}).json()
    assert client.get(f"/api/runs/{draft['id']}/report").status_code == 404


def test_m5_share_link_lifecycle() -> None:
    agent = _mk_agent()
    bad = _mk_case("m5-share-bad", ["exit 1"])
    vid = _mk_published_version("m5-share-set", [bad["id"]])
    run = _run_to_terminal(agent["id"], vid)

    r = client.post(f"/api/runs/{run['id']}/report/share", json={"public": True, "days": 7})
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    assert r.json()["expires_at"]

    r = client.get(f"/api/reports/shared/{token}")
    assert r.status_code == 200, r.text
    assert r.json()["run_id"] == run["id"]

    assert client.get("/api/reports/shared/not-a-token").status_code == 404

    assert client.delete(f"/api/runs/{run['id']}/report/share").status_code == 200
    assert client.get(f"/api/reports/shared/{token}").status_code == 404


def test_m5_compare_two_runs() -> None:
    agent = _mk_agent()
    ok = _mk_case("m5-cmp-ok", ["test -f echo_output.txt"])
    bad = _mk_case("m5-cmp-bad", ["exit 1"])
    vid = _mk_published_version("m5-cmp-set", [ok["id"], bad["id"]])
    left = _run_to_terminal(agent["id"], vid)
    right = _run_to_terminal(agent["id"], vid)

    r = client.get(f"/api/report/compare?left={left['id']}&right={right['id']}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["version_matched"] is True
    assert len(body["dimension_diff"]) == 8
    assert body["case_waterfall"]
    assert body["delta"]["quality"] == 0.0  # 同一 Agent 同一用例集,两次结果一致

    assert client.get(f"/api/report/compare?left={left['id']}&right={left['id']}").status_code == 422

    # 版本不一致:仅允许总分/维度对比
    other_case = _mk_case("m5-cmp-other", [])
    vid2 = _mk_published_version("m5-cmp-set2", [other_case["id"]])
    third = _run_to_terminal(agent["id"], vid2)
    body = client.get(f"/api/report/compare?left={left['id']}&right={third['id']}").json()
    assert body["version_matched"] is False
    assert body["case_waterfall"] == [] and body["version_note"]
