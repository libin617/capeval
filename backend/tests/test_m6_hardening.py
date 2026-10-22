"""M6 加固验证:API 型适配器全链路 + 成本计量、鉴权失败处理、三角色权限、
站内通知、数据保留策略、P1(趋势/误报率)与 RC-10 集体失败探测。"""

import json
import threading
import uuid
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.main import app
from app.models import Attribution, CaseExecution, EvaluationRun, Notification, TraceEvent
from app.services.executor import execute_run
from app.services.notify import detect_collective_failure

client = TestClient(app)


def _suffix() -> str:
    return uuid.uuid4().hex[:8]


def _weight_id() -> int:
    r = client.get("/api/weight-templates")
    return [w for w in r.json() if w["is_default"]][0]["id"]


def _mk_case(title: str, auto_commands: list) -> dict:
    r = client.post("/api/testcases", json={
        "title": f"{title}-{_suffix()}",
        "task_script": [{"turn": 1, "message": "ping", "intent_tag": ""}],
        "repo_snapshot": {"type": "files", "files": {"README.md": "# demo\n"}},
        "env_def": {"runtime": "none", "install_commands": [], "target_isolation": "any", "docker_image": "python:3.12-slim"},
        "acceptance": {"auto_commands": auto_commands, "semantic_points": ["Agent 给出响应"]},
        "tags": ["m6"], "est_minutes": 1,
    })
    assert r.status_code == 201, r.text
    return r.json()


def _mk_caseset(case_ids: list[int]) -> dict:
    r = client.post("/api/casesets", json={
        "name": f"m6-set-{_suffix()}", "description": "m6", "testcase_ids": case_ids,
    })
    assert r.status_code == 201, r.text
    cs = r.json()
    vid = cs["versions"][0]["id"]
    r = client.post(f"/api/casesets/{cs['id']}/versions/{vid}/publish")
    assert r.status_code == 200
    return {"caseset": cs, "version_id": vid}


def _mk_run(agent_id: int, version_id: int, created_by: str = "admin") -> dict:
    r = client.post("/api/runs", json={
        "agent_id": agent_id, "caseset_version_id": version_id,
        "weight_template_id": _weight_id(), "created_by": created_by,
    })
    assert r.status_code == 201, r.text
    return r.json()


def _run_and_wait(run_id: int) -> None:
    assert client.post(f"/api/runs/{run_id}/submit").status_code == 200
    execute_run(run_id)


# ---------- 模拟远端 Agent HTTP 服务 ----------
class _MockAgentHandler(BaseHTTPRequestHandler):
    """实现 M6 HTTP 契约的最小远端 Agent:
    POST /sessions、POST /messages(返回 message/tool_call/resource 事件)、DELETE /sessions/{id}。
    类属性控制行为:require_token(期望的 Bearer token,空=不校验)、messages_fail_auth(消息阶段恒 401)。"""

    require_token = ""
    messages_fail_auth = False
    sessions: dict[str, list] = {}

    def log_message(self, *args) -> None:  # 静默访问日志
        pass

    def _check_auth(self) -> bool:
        token = type(self).require_token
        if not token:
            return True
        return self.headers.get("Authorization") == f"Bearer {token}"

    def _json(self, code: int, body: dict) -> None:
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self) -> None:  # noqa: N802
        if not self._check_auth():
            self._json(401, {"detail": "invalid token"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        if self.path == "/sessions":
            sid = f"mock-{uuid.uuid4().hex[:10]}"
            type(self).sessions[sid] = []
            self._json(200, {"session_id": sid})
            return
        if self.path == "/messages":
            if type(self).messages_fail_auth:
                self._json(401, {"detail": "token rotated"})
                return
            events = [
                {"event_type": "message", "payload": {"role": "agent", "content": f"ack: {body.get('message', '')}"}},
                {"event_type": "tool_call",
                 "payload": {"name": "write_file", "args": {"path": "out.txt"}, "result": "ok"}},
                {"event_type": "resource", "payload": {"input_tokens": 1000, "output_tokens": 500}},
            ]
            self._json(200, {"events": events})
            return
        self._json(404, {"detail": "not found"})

    def do_DELETE(self) -> None:  # noqa: N802
        if not self._check_auth():
            self._json(401, {"detail": "invalid token"})
            return
        self._json(200, {"ok": True})


def _start_mock(**behaviors) -> tuple[ThreadingHTTPServer, str]:
    # 每次显式重置全部行为开关,避免类属性跨测试泄漏
    behaviors.setdefault("require_token", "")
    behaviors.setdefault("messages_fail_auth", False)
    for k, v in behaviors.items():
        setattr(_MockAgentHandler, k, v)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _MockAgentHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}"


def _mk_api_agent(endpoint: str, credential: str = "", config_extra: dict | None = None) -> dict:
    cfg = {"endpoint": endpoint, "auth_type": "bearer" if credential else "none",
           "expose_tool_calls": True, "expose_tokens": True}
    cfg.update(config_extra or {})
    r = client.post("/api/agents", json={
        "name": f"pytest-m6-api-{_suffix()}", "version_label": "v1", "access_type": "api",
        "adapter_key": "http_api", "adapter_config": cfg, "credential": credential,
        "resource_limits": {"max_case_minutes": 5, "max_turns": 10},
        "cost_params": {"input_price_per_1k": 0.01, "output_price_per_1k": 0.03},
    })
    assert r.status_code == 201, r.text
    return r.json()


def test_m6_http_api_full_run_and_cost_metering() -> None:
    """API 型适配器全链路:HTTP 契约执行 + 成本计量(事件无 cost 时按单价折算)。"""
    server, endpoint = _start_mock(require_token="secret-token")
    try:
        agent = _mk_api_agent(endpoint, credential="secret-token")
        assert client.post(f"/api/agents/{agent['id']}/verify").json()["passed"] is True

        case = _mk_case("m6-api-ok", ["true"])
        cs = _mk_caseset([case["id"]])
        run = _mk_run(agent["id"], cs["version_id"])
        _run_and_wait(run["id"])

        body = client.get(f"/api/runs/{run['id']}").json()
        assert body["status"] == "completed", body["error"]
        ce = body["cases"][0]
        assert ce["status"] == "succeeded"
        # 成本计量:1000 in / 500 out → 1000/1000*0.01 + 500/1000*0.03 = 0.025
        assert abs(ce["resource_usage"]["cost"] - 0.025) < 1e-6
        assert ce["resource_usage"]["input_tokens"] == 1000

        events = client.get(f"/api/executions/{ce['id']}/events").json()
        assert any(e["event_type"] == "tool_call" for e in events)
        res = [e for e in events if e["event_type"] == "resource"]
        assert res and res[0]["payload"].get("cost")  # 折算费用回填进事件

        # 已暴露工具调用/token:创建 Run 无降级预警
        assert body.get("warning", "") == ""
    finally:
        server.shutdown()


def test_m6_auth_failure_marks_agent_unavailable() -> None:
    """执行中鉴权失败(消息阶段 401):case 失败 + auth_failed 事件 + Agent 置不可用 + 管理员通知。
    校验阶段先正常通过,再翻转 mock 行为模拟 token 运行中失效。"""
    server, endpoint = _start_mock(require_token="secret-token")
    try:
        agent = _mk_api_agent(endpoint, credential="secret-token")
        # 校验阶段消息可达,冒烟通过
        assert client.post(f"/api/agents/{agent['id']}/verify").json()["passed"] is True
        # 模拟 token 运行中失效:消息阶段恒 401
        _MockAgentHandler.messages_fail_auth = True

        case = _mk_case("m6-auth-fail", ["true"])
        cs = _mk_caseset([case["id"]])
        run = _mk_run(agent["id"], cs["version_id"])
        _run_and_wait(run["id"])

        body = client.get(f"/api/runs/{run['id']}").json()
        assert body["status"] == "failed"
        ce = body["cases"][0]
        assert ce["status"] == "failed"
        events = client.get(f"/api/executions/{ce['id']}/events").json()
        auth_evts = [e for e in events if e["event_type"] == "system"
                     and e["payload"].get("kind") == "auth_failed"]
        assert auth_evts, "缺少 auth_failed 系统事件"

        # Agent 置不可用 + 管理员收到 agent_alert
        agent_row = client.get(f"/api/agents/{agent['id']}").json()
        assert agent_row["status"] == "unavailable"
        notes = client.get("/api/notifications", params={"category": "agent_alert"}).json()
        assert any("不可用" in n["title"] for n in notes["items"])
    finally:
        server.shutdown()


def _valid_case_payload(title: str) -> dict:
    return {
        "title": title,
        "task_script": [{"turn": 1, "message": "ping", "intent_tag": ""}],
        "repo_snapshot": {"type": "files", "files": {"README.md": "# demo\n"}},
        "env_def": {"runtime": "none", "install_commands": [], "target_isolation": "any", "docker_image": "python:3.12-slim"},
        "acceptance": {"auto_commands": [], "semantic_points": ["Agent 给出响应"]},
        "tags": ["m6"], "est_minutes": 1,
    }


def test_m6_permission_matrix() -> None:
    """三角色权限:viewer 写 403/评论 201;evaluator 凭证 403;未知身份 401;无头默认 admin。"""
    # viewer 不可创建用例(合法请求体,仅权限拦截)
    r = client.post("/api/testcases", json=_valid_case_payload(f"m6-viewer-forbidden-{_suffix()}"),
                    headers={"X-Acting-User": "viewer"})
    assert r.status_code == 403
    # viewer 不可创建根因条目
    r = client.post("/api/root-causes", json={"level": 1, "name": f"m6-viewer-rc-{_suffix()}",
                                              "responsibility": "agent"},
                    headers={"X-Acting-User": "viewer"})
    assert r.status_code == 403
    # evaluator 不可管理凭证(创建 Agent 带凭证)
    r = client.post("/api/agents", json={
        "name": f"pytest-m6-evaluator-{_suffix()}", "version_label": "v1", "access_type": "cli",
        "adapter_key": "echo_cli", "adapter_config": {}, "credential": "should-forbid",
    }, headers={"X-Acting-User": "evaluator"})
    assert r.status_code == 403
    # evaluator 可正常写(不带凭证)
    r = client.post("/api/testcases", json=_valid_case_payload(f"m6-evaluator-ok-{_suffix()}"),
                    headers={"X-Acting-User": "evaluator"})
    assert r.status_code == 201
    # 未知身份 401
    r = client.get("/api/notifications", headers={"X-Acting-User": "ghost"})
    assert r.status_code == 401
    # 未带头默认 admin(向后兼容):可写
    r = client.post("/api/testcases", json=_valid_case_payload(f"m6-default-admin-{_suffix()}"))
    assert r.status_code == 201

    # viewer 可对归因结论提交异议评论(只读成员唯一可写操作)
    db = SessionLocal()
    try:
        attr = db.query(Attribution).first()
        assert attr is not None, "前置数据缺失:库中无归因结论"
        attr_id = attr.id
    finally:
        db.close()
    r = client.post(f"/api/attributions/{attr_id}/comments",
                    json={"content": "M6 测试:对该归因有异议"},
                    headers={"X-Acting-User": "viewer"})
    assert r.status_code == 201, r.text
    comments = client.get(f"/api/attributions/{attr_id}/comments").json()
    assert any(c["author"] == "只读成员" and c["role"] == "viewer" for c in comments)


def test_m6_run_status_notification_and_mark_read() -> None:
    """Run 完成通知创建人 + 通知查询/已读/全部已读。"""
    r = client.post("/api/agents", json={
        "name": f"pytest-m6-echo-{_suffix()}", "version_label": "v1", "access_type": "cli",
        "adapter_key": "echo_cli", "adapter_config": {},
    })
    agent = r.json()
    assert client.post(f"/api/agents/{agent['id']}/verify").json()["passed"] is True
    case = _mk_case("m6-notify", ["test -f echo_output.txt"])
    cs = _mk_caseset([case["id"]])
    run = _mk_run(agent["id"], cs["version_id"], created_by="admin")
    _run_and_wait(run["id"])
    assert client.get(f"/api/runs/{run['id']}").json()["status"] == "completed"

    notes = client.get("/api/notifications").json()
    hit = next((n for n in notes["items"] if n["category"] == "run_status"
                and f"#{run['id']} " in n["title"]), None)
    assert hit is not None, "创建人未收到任务完成通知"
    assert hit["link"] == f"/runs/{run['id']}"

    # 标记已读 → 未读数下降;read-all 清零
    r = client.post(f"/api/notifications/{hit['id']}/read")
    assert r.status_code == 200
    after = client.get("/api/notifications").json()
    assert all(n["id"] != hit["id"] or n["read_at"] for n in after["items"])
    r = client.post("/api/notifications/read-all")
    assert r.status_code == 200
    assert client.get("/api/notifications").json()["unread"] == 0
    # 幂等扫描不报错
    assert "created" in client.post("/api/notifications/scan").json()


def test_m6_retention_purge_keeps_evidence() -> None:
    """数据保留:过期轨迹被物理清理,但被归因证据链引用的片段豁免;接口仅 admin 可清理。"""
    agent_row = client.get("/api/agents").json()
    echo_agent = next(a for a in agent_row if a["adapter_key"] == "echo_cli" and a["status"] == "available")
    case = _mk_case("m6-retain", ["exit 1"])  # 恒失败 → 产生归因
    cs = _mk_caseset([case["id"]])
    run = _mk_run(echo_agent["id"], cs["version_id"])
    _run_and_wait(run["id"])
    body = client.get(f"/api/runs/{run['id']}").json()
    ce = body["cases"][0]

    db = SessionLocal()
    try:
        exec_id, attr = ce["id"], db.query(Attribution).filter_by(execution_id=ce["id"]).first()
        assert attr is not None
        # 造 3 条"过期"事件:seq 1 被 证据链引用(豁免),seq 2/3 无引用(应被清理)
        old = datetime.now() - timedelta(days=400)
        for seq in (1, 2, 3):
            db.add(TraceEvent(execution_id=exec_id, seq=1000 + seq, event_type="message",
                              payload={"role": "agent", "content": f"old-{seq}"},
                              created_at=old))
        db.commit()
        attr.evidence = {"event_seqs": [1001], "signals": ["acceptance_failed"], "details": []}
        db.commit()

        # viewer/admin 权限:viewer 不可清理
        r = client.post("/api/admin/retention/purge", headers={"X-Acting-User": "viewer"})
        assert r.status_code == 403
        stats = client.get("/api/admin/retention").json()
        assert stats["retention_days"] == 180 and stats["trace_events_expired"] >= 3
        r = client.post("/api/admin/retention/purge")
        assert r.status_code == 200 and r.json()["removed"] >= 2

        left = {e.seq for e in db.query(TraceEvent).filter_by(execution_id=exec_id).all()}
        assert 1001 in left, "证据链引用的事件片段被误删"
        assert 1002 not in left and 1003 not in left
        # 成员列表可用
        users = client.get("/api/admin/users").json()
        assert {u["username"] for u in users} >= {"admin", "evaluator", "viewer"}
    finally:
        db.close()


def test_m6_trend_and_false_positive_stats() -> None:
    """P1:趋势看板点列 + 归因误报率统计(≥30% 生成调整建议)。"""
    agent_row = client.get("/api/agents").json()
    echo_agent = next(a for a in agent_row if a["adapter_key"] == "echo_cli" and a["status"] == "available")
    ok_case = _mk_case("m6-trend-ok", ["test -f echo_output.txt"])
    cs = _mk_caseset([ok_case["id"]])
    run = _mk_run(echo_agent["id"], cs["version_id"])
    _run_and_wait(run["id"])

    trend = client.get("/api/stats/trend", params={"agent_id": echo_agent["id"]}).json()
    assert trend["agent_name"]
    assert any(p["run_id"] == run["id"] and p["quality_score"] is not None for p in trend["points"])

    # 造一个专属根因 + 三条已复核归因(2 错 1 对 → 误报率 2/3 ≥ 30% → 建议非空)
    level2 = next(rc for rc in client.get("/api/root-causes").json() if rc["level"] == 2)
    r = client.post("/api/root-causes", json={
        "level": 3, "parent_id": level2["id"], "name": f"m6-fp-{_suffix()}",
        "responsibility": "agent", "detection_signals": {"rules": [{"signal": "acceptance_failed"}]},
    })
    assert r.status_code == 201, r.text
    rc_id = r.json()["rootcause"]["id"]

    body = client.get(f"/api/runs/{run['id']}").json()
    exec_id = body["cases"][0]["id"]
    db = SessionLocal()
    try:
        for i, status in enumerate(("rejected", "corrected", "confirmed")):
            db.add(Attribution(execution_id=exec_id, rootcause_id=rc_id, is_primary=(i == 0),
                               confidence=0.9, source="rule", evidence={}, review_status=status))
        db.commit()
    finally:
        db.close()

    fp = client.get("/api/stats/false-positive-stats").json()
    item = next(x for x in fp["items"] if x["rootcause_id"] == rc_id)
    assert item["reviewed"] == 3 and item["confirmed"] == 1
    assert abs(item["error_rate"] - 2 / 3) < 1e-3
    assert item["suggestion"], "误报率 ≥30% 应生成信号调整建议"


def _rc_by_code(code: str) -> dict:
    for rc in client.get("/api/root-causes").json():
        if rc["code"] == code:
            return rc
    raise AssertionError(f"根因 {code} 不存在")


def test_m6_rc10_collective_failure_notification() -> None:
    """RC-10 集体失败探测:同一用例 ≥3 个不同 Agent 失分且根因各异 → 通知 evaluator(幂等)。"""
    agent_row = client.get("/api/agents").json()
    echo_agent = next(a for a in agent_row if a["adapter_key"] == "echo_cli" and a["status"] == "available")
    all_agents = [a for a in agent_row if a["status"] == "available" and a["access_type"] == "cli"]
    other_agent_ids = [a["id"] for a in all_agents if a["id"] != echo_agent["id"]][:2]
    if len(other_agent_ids) < 2:
        # 不足则补建两个 echo Agent
        for _ in range(2 - len(other_agent_ids)):
            r = client.post("/api/agents", json={
                "name": f"pytest-m6-rc10-{_suffix()}", "version_label": "v1",
                "access_type": "cli", "adapter_key": "echo_cli", "adapter_config": {},
            })
            other_agent_ids.append(r.json()["id"])

    case = _mk_case("m6-rc10", ["exit 1"])
    cs = _mk_caseset([case["id"]])
    run = _mk_run(echo_agent["id"], cs["version_id"])
    _run_and_wait(run["id"])  # 恒失败 → 主根因归因(acceptance_failed → RC-1 系)

    rc_codes = ["RC-1", "RC-2", "RC-3"]
    db = SessionLocal()
    try:
        rc_ids = [_rc_by_code(c)["id"] for c in rc_codes]
        my_ce = db.query(CaseExecution).filter_by(run_id=run["id"]).first()
        my_attr = db.query(Attribution).filter_by(execution_id=my_ce.id, is_primary=True).first()
        my_attr.rootcause_id = rc_ids[0]  # 固化本次主根因为 RC-1
        db.commit()
        # 两个"历史"Run(不同 Agent,不同主根因)直接落库
        for i, agent_id in enumerate(other_agent_ids):
            hist_run = EvaluationRun(
                name=f"m6-rc10-hist-{i}-{_suffix()}", agent_id=agent_id,
                caseset_version_id=cs["version_id"], weight_template_id=_weight_id(),
                weight_snapshot={}, rootcause_version=1, review_policy="skip",
                fail_threshold=0.5, status="completed",
            )
            db.add(hist_run)
            db.flush()
            hist_ce = CaseExecution(run_id=hist_run.id, testcase_id=case["id"],
                                    order_no=1, status="failed")
            db.add(hist_ce)
            db.flush()
            db.add(Attribution(execution_id=hist_ce.id, rootcause_id=rc_ids[i + 1],
                               is_primary=True, confidence=0.8, source="rule",
                               evidence={}, review_status="confirmed"))
        db.commit()

        run_model = db.get(EvaluationRun, run["id"])  # 服务层需要 ORM 对象而非 API dict
        detect_collective_failure(db, run_model)
        db.commit()
        notes = (
            db.query(Notification)
            .filter_by(category="rc10_candidate", dedupe_key=f"rc10:{case['id']}")
            .all()
        )
        assert notes, "集体失败候选未通知 evaluator"
        assert all(n.recipient == "evaluator" for n in notes)
        count_before = len(notes)

        detect_collective_failure(db, run_model)  # 幂等:重复探测不重复通知
        db.commit()
        count_after = (
            db.query(Notification).filter_by(category="rc10_candidate",
                                             dedupe_key=f"rc10:{case['id']}").count()
        )
        assert count_after == count_before
    finally:
        db.close()


def test_m6_data_missing_warning_and_report_integrity() -> None:
    """API 型未暴露工具/token:Run 创建预警 + 报告数据完整性声明标注「数据缺失」。"""
    server, endpoint = _start_mock()
    try:
        agent = _mk_api_agent(endpoint, config_extra={"expose_tool_calls": False, "expose_tokens": False})
        assert client.post(f"/api/agents/{agent['id']}/verify").json()["passed"] is True
        case = _mk_case("m6-missing", ["true"])
        cs = _mk_caseset([case["id"]])
        run = _mk_run(agent["id"], cs["version_id"])
        assert "D3" in run["warning"] and "D6" in run["warning"], run["warning"]
        _run_and_wait(run["id"])

        report = client.get(f"/api/runs/{run['id']}/report").json()
        integrity = " ".join(report["appendix"]["data_integrity"])
        assert "D3" in integrity and "数据缺失" in integrity
        assert "D6" in integrity
    finally:
        server.shutdown()
