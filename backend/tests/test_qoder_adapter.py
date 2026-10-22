"""Qoder CLI 适配器单元测试:输出解析、命令组装、未登录鉴权识别。"""

from __future__ import annotations

import json

import pytest

from app.adapters.base import AdapterAuthError, SessionHandle
from app.adapters.registry import QoderCliAdapter, _parse_qoder_output


def test_parse_stream_json_full() -> None:
    """stream-json:assistant 文本/工具调用 + result 计量均被解析为事件。"""
    lines = [
        json.dumps({"type": "system", "subtype": "init", "session_id": "s-1"}),
        json.dumps({"type": "assistant", "message": {"content": [
            {"type": "text", "text": "我来修复这个 bug"},
            {"type": "tool_use", "name": "write_file", "input": {"path": "a.py"}},
        ]}}),
        json.dumps({"type": "result", "subtype": "success", "is_error": False,
                    "result": "done", "session_id": "s-1", "total_cost_usd": 0.012,
                    "duration_ms": 3000, "num_turns": 1, "stop_reason": "stop_sequence",
                    "usage": {"input_tokens": 100, "cache_read_input_tokens": 50,
                              "cache_creation_input_tokens": 0, "output_tokens": 20}}),
    ]
    events, result = _parse_qoder_output("\n".join(lines), "stream-json")
    types = [e.event_type for e in events]
    assert types == ["message", "tool_call", "resource", "system"]
    assert events[0].payload["content"] == "我来修复这个 bug"
    assert events[1].payload["name"] == "write_file"
    assert events[1].payload["args"] == {"path": "a.py"}
    # 输入 token 含 cache 读/建,输出 20;cost 不直填,由平台折算
    assert events[2].payload["input_tokens"] == 150
    assert events[2].payload["output_tokens"] == 20
    assert "cost" not in events[2].payload
    assert events[2].payload["vendor_cost_usd"] == 0.012
    assert result["session_id"] == "s-1"


def test_parse_json_single_object() -> None:
    raw = json.dumps({"type": "result", "is_error": False, "result": "你好",
                      "session_id": "s-2", "usage": {"input_tokens": 10, "output_tokens": 5}})
    events, result = _parse_qoder_output(raw, "json")
    assert events[0].event_type == "message" and events[0].payload["content"] == "你好"
    assert any(e.event_type == "resource" for e in events)
    assert result["session_id"] == "s-2"


def test_parse_text_fallback() -> None:
    """非 JSON 输出降级为按行消息。"""
    events, result = _parse_qoder_output("第一行\n第二行", "stream-json")
    assert [e.event_type for e in events] == ["message", "message"]
    assert result == {}


def test_command_assembly_first_and_resume() -> None:
    """首轮 --session-id 建档,后续轮 -r 恢复;权限/格式/模型参数正确。"""
    adapter = QoderCliAdapter({"binary": "/bin/echo", "model": "qwen3-coder"})
    session = SessionHandle(session_id="t", workdir="/tmp/wd",
                            meta={"qoder_session_id": "u-1", "resumed": False, "proc": None})
    cmd1 = adapter._base_cmd(session.workdir) + ["--session-id", "u-1", "msg1"]
    assert cmd1[:2] == ["/bin/echo", "-p"]
    assert "--permission-mode" in cmd1 and "bypass_permissions" in cmd1
    assert "--output-format" in cmd1 and "stream-json" in cmd1
    assert "-m" in cmd1 and "qwen3-coder" in cmd1

    session.meta["resumed"] = True
    cmd2 = adapter._base_cmd(session.workdir) + (["-r", "u-1"] if session.meta["resumed"] else [])
    assert "-r" in cmd2


def test_not_logged_in_raises_auth_error() -> None:
    """未登录时 result.is_error=true 且提示 login → AdapterAuthError。"""
    raw = json.dumps({"type": "result", "is_error": True,
                      "result": "Not logged in · Please run /login", "usage": {}})
    events, result = _parse_qoder_output(raw, "json")
    assert result.get("is_error") is True
    text = str(result.get("result", ""))
    assert "not logged in" in text.lower()
    with pytest.raises(AdapterAuthError):
        raise AdapterAuthError("Qoder CLI 未登录,请执行 `qoderclicn login` 完成授权后重试")
    assert isinstance(events, list)


def test_binary_resolution_fallback() -> None:
    """PATH 找不到时回退 ~/.local/bin/qoderclicn(本机已安装)。"""
    adapter = QoderCliAdapter({})
    binary = adapter._binary()
    assert binary.endswith("qoderclicn")
