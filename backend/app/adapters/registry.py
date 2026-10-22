"""内置适配器:echo_cli(演示/测试,完整多轮)、generic_cli(行协议 CLI)、qoder_cli(Qoder print 模式)、http_api(HTTP 契约执行闭环)。"""

from __future__ import annotations

import itertools
import json
import os
import select
import shutil
import subprocess
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from app.adapters.base import AdapterAuthError, AdapterEvent, BaseAdapter, SessionHandle, make_preexec_limits

_session_seq = itertools.count(1)

ECHO_OUTPUT_FILE = "echo_output.txt"


class EchoCliAdapter(BaseAdapter):
    """内置回声适配器:不启动真实进程,用于演示、联调与官方冒烟。

    每消费一条用户消息,产出一组模拟事件(message/tool_call/file_change/resource),
    并真实把回复追加写入工作目录 echo_output.txt,使 `test -f` 类验收命令可真实通过。
    """

    key = "echo_cli"
    name = "内置回声 CLI 适配器"
    access_type = "cli"
    description = "无需真实 Agent,回显消息并产出样例事件(会真实写入 echo_output.txt),用于校验平台链路。"
    required_config: list[str] = []
    supports_execution = True

    def start_session(self, workdir: str) -> SessionHandle:
        return SessionHandle(session_id=f"echo-{next(_session_seq)}", workdir=workdir, meta={"inbox": []})

    def send_message(self, session: SessionHandle, message: str) -> None:
        session.meta.setdefault("inbox", []).append(message)

    def fetch_events(self, session: SessionHandle) -> list[AdapterEvent]:
        inbox: list[str] = session.meta.setdefault("inbox", [])
        if not inbox:
            return []
        msg = inbox.pop(0)
        reply = f"echo: {msg}"
        # 真实落盘,供验收命令与 file_change 事件使用
        content_after = ""
        if session.workdir:
            path = Path(session.workdir) / ECHO_OUTPUT_FILE
            try:
                with path.open("a", encoding="utf-8") as f:
                    f.write(reply + "\n")
                content_after = path.read_text(encoding="utf-8")[:200]
            except OSError:
                pass
        return [
            AdapterEvent("message", {"role": "agent", "content": reply}),
            AdapterEvent("tool_call", {
                "name": "write_file",
                "args": {"path": ECHO_OUTPUT_FILE, "content": reply},
                "result": "ok",
            }),
            AdapterEvent("file_change", {
                "path": ECHO_OUTPUT_FILE, "change": "append", "after_summary": content_after,
            }),
            AdapterEvent("resource", {
                "input_tokens": max(1, len(msg) // 4), "output_tokens": max(1, len(reply) // 4), "cost": 0.0,
            }),
        ]

    def terminate(self, session: SessionHandle) -> None:
        session.meta.clear()


class GenericCliAdapter(BaseAdapter):
    """通用 CLI 适配器:按配置模板启动进程,通过 stdin/stdout 行协议交互。

    冒烟执行 probe_command(默认 `<cmd> --version`);执行期 start_command 以 Popen 拉起,
    send_message 写入一行,fetch_events 非阻塞读取输出行作为 agent 消息事件。
    """

    key = "generic_cli"
    name = "通用 CLI 适配器"
    access_type = "cli"
    description = "通过命令行模板驱动 Agent 进程(stdin/stdout 行协议);冒烟执行 probe_command(默认 `--version`)。"
    required_config = ["start_command"]
    supports_execution = True

    def _probe(self, workdir: str) -> str:
        cmd: list[str] = self.config.get("start_command") or []
        if not cmd:
            raise ValueError("adapter_config.start_command 未配置")
        probe: list[str] = self.config.get("probe_command") or cmd[:1] + ["--version"]
        proc = subprocess.run(
            probe, cwd=workdir or None, capture_output=True, text=True, timeout=30, check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"探针命令退出码 {proc.returncode}: {(proc.stderr or proc.stdout)[:300]}")
        return (proc.stdout or "")[:500]

    def start_session(self, workdir: str) -> SessionHandle:
        probe_output = self._probe(workdir)
        cmd: list[str] = self.config["start_command"]
        try:
            proc = subprocess.Popen(  # noqa: S603
                cmd, cwd=workdir or None, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, bufsize=1,
                preexec_fn=make_preexec_limits(self.resource_limits),
            )
        except OSError as exc:
            raise RuntimeError(f"进程启动失败: {exc}") from exc
        os.set_blocking(proc.stdout.fileno(), False)  # type: ignore[arg-type]
        return SessionHandle(
            session_id=f"cli-{next(_session_seq)}", workdir=workdir,
            meta={"proc": proc, "probe_output": probe_output},
        )

    def send_message(self, session: SessionHandle, message: str) -> None:
        proc: subprocess.Popen = session.meta["proc"]
        if proc.poll() is not None:
            raise RuntimeError(f"Agent 进程已退出(退出码 {proc.returncode})")
        assert proc.stdin is not None
        proc.stdin.write(message + "\n")
        proc.stdin.flush()

    def fetch_events(self, session: SessionHandle) -> list[AdapterEvent]:
        proc: subprocess.Popen = session.meta["proc"]
        wait_seconds = float(self.config.get("response_wait_seconds", 5))
        fd = proc.stdout.fileno()  # type: ignore[union-attr]
        lines: list[str] = []
        # 先等待首行数据,之后连续读到无数据为止
        ready = select.select([fd], [], [], wait_seconds)[0]
        while ready:
            chunk = os.read(fd, 65536).decode("utf-8", errors="replace")
            if not chunk:
                break
            lines.extend(chunk.splitlines())
            ready = select.select([fd], [], [], 0.3)[0]
        events: list[AdapterEvent] = [AdapterEvent("message", {"role": "agent", "content": line})
                                      for line in lines if line.strip()]
        if proc.poll() is not None:
            events.append(AdapterEvent("system", {"kind": "process_exited", "exit_code": proc.returncode}))
        return events

    def terminate(self, session: SessionHandle) -> None:
        proc: subprocess.Popen | None = session.meta.get("proc")
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        session.meta.clear()


class HttpApiAdapter(BaseAdapter):
    """通用 HTTP API 适配器(FR-01 API 型,M6 完整执行闭环)。

    约定的最小 HTTP 契约(远端 Agent 服务需实现):
      POST {endpoint}/sessions                     -> {"session_id": "..."}
      POST {endpoint}/messages {"session_id","message"}
                                                   -> {"events": [{"event_type","payload"}, ...]}
      DELETE {endpoint}/sessions/{session_id}      -> 2xx
    事件类型与 CLI 适配器一致(message/tool_call/file_change/terminal/resource/system)。

    配置:
      endpoint           必填,远端服务根地址
      auth_type          none / bearer / api_key(默认 bearer;凭据取 Agent 凭证字段)
      auth_header        自定义鉴权头名(默认 Authorization / X-API-Key)
      expose_tool_calls  远端是否上报工具调用明细(默认 False)
      expose_tokens      远端是否上报 token 用量(默认 False)
      request_timeout    单请求超时秒数(默认 120)

    鉴权失败(HTTP 401/403)抛 AdapterAuthError:执行中判 case 失败、Agent 置不可用并通知管理员。
    未暴露 tool_call/token 用量时,对应维度评分降级并在报告中标注「数据缺失」。
    """

    key = "http_api"
    name = "通用 HTTP API 适配器"
    access_type = "api"
    description = ("通过 HTTP 契约驱动远端 Agent 服务:"
                   "POST /sessions、POST /messages、DELETE /sessions/{id};"
                   "支持 bearer/api_key 鉴权与成本计量参数。")
    required_config = ["endpoint"]
    supports_execution = True

    @property
    def expose_tool_calls(self) -> bool:
        return bool(self.config.get("expose_tool_calls", False))

    @property
    def expose_tokens(self) -> bool:
        return bool(self.config.get("expose_tokens", False))

    def _auth_headers(self) -> dict[str, str]:
        if not self.credential:
            return {}
        auth_type = str(self.config.get("auth_type") or ("bearer" if self.credential else "none"))
        if auth_type == "bearer":
            return {"Authorization": f"Bearer {self.credential}"}
        if auth_type == "api_key":
            return {str(self.config.get("auth_header") or "X-API-Key"): self.credential}
        return {}

    def _request(self, method: str, url: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        for k, v in self._auth_headers().items():
            req.add_header(k, v)
        timeout = float(self.config.get("request_timeout", 120))
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
                raw = resp.read().decode("utf-8", errors="replace")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise AdapterAuthError(f"鉴权失败: HTTP {exc.code}({url[:120]})") from exc
            raise RuntimeError(f"endpoint 返回 HTTP {exc.code}: {url[:120]}") from exc
        except AdapterAuthError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"endpoint 请求失败: {type(exc).__name__}: {exc}") from exc

    def start_session(self, workdir: str) -> SessionHandle:
        endpoint: str = (self.config.get("endpoint") or "").rstrip("/")
        if not endpoint:
            raise ValueError("adapter_config.endpoint 未配置")
        data = self._request("POST", f"{endpoint}/sessions", body={"workdir": workdir})
        session_id = str(data.get("session_id") or "")
        if not session_id:
            raise RuntimeError("远端未返回 session_id")
        return SessionHandle(session_id=f"api-{next(_session_seq)}",
                             workdir=workdir, meta={"remote_session_id": session_id, "endpoint": endpoint})

    def send_message(self, session: SessionHandle, message: str) -> None:
        endpoint = session.meta["endpoint"]
        remote_id = session.meta["remote_session_id"]
        data = self._request("POST", f"{endpoint}/messages",
                             body={"session_id": remote_id, "message": message})
        events: list[AdapterEvent] = []
        for item in data.get("events") or []:
            if isinstance(item, dict) and item.get("event_type"):
                events.append(AdapterEvent(str(item["event_type"]), dict(item.get("payload") or {})))
        session.meta["pending_events"] = events

    def fetch_events(self, session: SessionHandle) -> list[AdapterEvent]:
        events: list[AdapterEvent] = session.meta.pop("pending_events", [])
        if not events:
            events = [AdapterEvent("message", {"role": "agent", "content": "(远端未返回事件)"})]
        return events

    def terminate(self, session: SessionHandle) -> None:
        endpoint = session.meta.get("endpoint")
        remote_id = session.meta.get("remote_session_id")
        if endpoint and remote_id:
            try:
                self._request("DELETE", f"{endpoint}/sessions/{remote_id}")
            except Exception:  # noqa: BLE001,S110
                pass
        session.meta.clear()


def _parse_qoder_output(raw: str, output_format: str) -> tuple[list[AdapterEvent], dict[str, Any]]:
    """解析 qoderclicn -p 的输出,返回 (事件列表, result 摘要)。

    stream-json: 每行一个 JSON 对象(assistant/user/result);
    json: 单个 JSON 对象;text: 纯文本按行作为消息。
    result 摘要含 session_id/usage/cost/is_error,供适配器做鉴权识别与计量。
    """
    events: list[AdapterEvent] = []
    result_info: dict[str, Any] = {}
    lines = [ln for ln in raw.splitlines() if ln.strip()]

    def handle_result(obj: dict[str, Any]) -> None:
        result_info.update(obj)
        usage = obj.get("usage") or {}
        tin = int(usage.get("input_tokens", 0) or 0) \
            + int(usage.get("cache_creation_input_tokens", 0) or 0) \
            + int(usage.get("cache_read_input_tokens", 0) or 0)
        tout = int(usage.get("output_tokens", 0) or 0)
        # cost 字段不直填:由平台按 Agent cost_params 单价折算(口径与 API 型一致,单位元);
        # 厂商原值仅记录展示
        events.append(AdapterEvent("resource", {
            "input_tokens": tin, "output_tokens": tout,
            "vendor_cost_usd": obj.get("total_cost_usd", 0),
            "duration_ms": obj.get("duration_ms", 0),
            "num_turns": obj.get("num_turns", 1),
            "session_id": obj.get("session_id", ""),
        }))
        events.append(AdapterEvent("system", {
            "kind": "agent_turn_completed",
            "is_error": bool(obj.get("is_error")),
            "stop_reason": obj.get("stop_reason", ""),
            # 失败时保留 CLI 返回的原因,便于定位(如会话恢复失败/参数非法)
            "error": str(obj.get("result", ""))[:500] if obj.get("is_error") else "",
            "subtype": obj.get("subtype", ""),
        }))

    def handle_assistant(obj: dict[str, Any]) -> None:
        for block in (obj.get("message") or {}).get("content") or []:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text" and str(block.get("text", "")).strip():
                events.append(AdapterEvent("message", {"role": "agent", "content": block["text"]}))
            elif btype == "tool_use":
                # 统一六类轨迹契约:与 echo_cli 一致使用 name/args/result 字段
                events.append(AdapterEvent("tool_call", {
                    "name": block.get("name", ""), "args": block.get("input") or {},
                }))

    if output_format == "json":
        try:
            obj = json.loads(raw.strip())
        except json.JSONDecodeError:
            output_format = "text"  # 降级按纯文本
        else:
            if obj.get("type") == "result":
                handle_result(obj)
                text = str(obj.get("result", "")).strip()
                if text and not obj.get("is_error"):
                    events.insert(0, AdapterEvent("message", {"role": "agent", "content": text}))
            return events, result_info

    if output_format == "stream-json":
        parsed_any = False
        for ln in lines:
            try:
                obj = json.loads(ln)
            except json.JSONDecodeError:
                continue
            parsed_any = True
            otype = obj.get("type")
            if otype == "assistant":
                handle_assistant(obj)
            elif otype == "result":
                handle_result(obj)
        if parsed_any:
            return events, result_info
        # 一行都解析不出来 → 降级纯文本

    for ln in lines:
        events.append(AdapterEvent("message", {"role": "agent", "content": ln}))
    return events, result_info


def _qoder_failure_detail(raw: str, result: dict[str, Any], returncode: int | None) -> str:
    """拼装 Qoder CLI 失败详情:退出码 + result 原因 + 原始输出尾部。

    CLI 在 is_error 且 result 为空时不会给出任何可读原因,此前仅报「未知原因」导致无法定位;
    这里保留退出码、subtype/stop_reason 与压平后的原始输出尾部,供轨迹与错误字段追溯。
    """
    reason = str(result.get("result", "") or "").strip()
    subtype = str(result.get("subtype", "") or "")
    stop_reason = str(result.get("stop_reason", "") or "")
    tail = " ".join(raw.split())[-1200:]
    parts = [f"退出码={returncode}"]
    if reason:
        parts.append(f"reason={reason[:500]}")
    if subtype:
        parts.append(f"subtype={subtype}")
    if stop_reason:
        parts.append(f"stop_reason={stop_reason}")
    if tail:
        parts.append(f"raw_tail={tail}")
    return "Qoder CLI 本轮失败: " + "; ".join(parts)


class QoderCliAdapter(BaseAdapter):
    """Qoder CLI 适配器(qoderclicn):print 一次性命令 + 会话恢复实现多轮评测。

    交互模型:每轮消息启动一次 `qoderclicn -p` 进程并等待输出;首轮以
    `--session-id <uuid>` 建档,后续轮 `-r <uuid>` 恢复,保证多轮上下文一致。

    配置:
      binary            可执行文件(默认 "qoderclicn",PATH 查找;可配绝对路径)
      model             模型名(可选,对应 -m)
      permission_mode   权限模式(默认 bypass_permissions,自动化评测需跳过交互确认)
      output_format     stream-json(默认,逐事件含工具调用明细) / json / text
      extra_args        追加参数列表(可选)

    冒烟执行 `<binary> --version`;执行期未登录(result.is_error 且提示 login)
    抛 AdapterAuthError,走 Agent 置不可用 + 通知管理员流程。
    """

    key = "qoder_cli"
    name = "Qoder CLI 适配器"
    access_type = "cli"
    description = ("驱动 qoderclicn print 模式执行评测:首轮 --session-id 建档、后续轮 -r 恢复会话;"
                   "stream-json 输出解析工具调用与 token 用量。")
    required_config = []
    supports_execution = True

    def _binary(self) -> str:
        binary = str(self.config.get("binary") or "qoderclicn")
        if os.sep not in binary:  # PATH 查找,找不到时回退常见安装位置
            found = shutil.which(binary)
            if not found:
                fallback = Path.home() / ".local" / "bin" / binary
                if fallback.exists():
                    found = str(fallback)
            binary = found or binary
        return binary

    def _base_cmd(self, workdir: str) -> list[str]:
        fmt = str(self.config.get("output_format") or "stream-json")
        cmd = [self._binary(), "-p", "-w", workdir or ".",
               "--permission-mode", str(self.config.get("permission_mode") or "bypass_permissions"),
               "--output-format", fmt]
        if self.config.get("model"):
            cmd += ["-m", str(self.config["model"])]
        cmd += [str(a) for a in (self.config.get("extra_args") or [])]
        return cmd

    def _probe(self, workdir: str) -> str:
        binary = self._binary()
        try:
            proc = subprocess.run(  # noqa: S603
                [binary, "--version"], cwd=workdir or None,
                capture_output=True, text=True, timeout=30, check=False,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                f"Qoder CLI 未安装或不在 PATH: {binary}(安装: curl -fsSL https://qoder.com.cn/install | bash)"
            ) from exc
        if proc.returncode != 0:
            raise RuntimeError(f"探针命令退出码 {proc.returncode}: {(proc.stderr or proc.stdout)[:300]}")
        return (proc.stdout or "").strip()[:200]

    def start_session(self, workdir: str) -> SessionHandle:
        probe_output = self._probe(workdir)
        return SessionHandle(
            session_id=f"qoder-{next(_session_seq)}", workdir=workdir,
            meta={"qoder_session_id": str(uuid.uuid4()), "resumed": False,
                  "probe_output": probe_output, "proc": None},
        )

    def send_message(self, session: SessionHandle, message: str) -> None:
        cmd = self._base_cmd(session.workdir)
        qid = session.meta["qoder_session_id"]
        # 首轮指定会话 ID 建档;后续轮恢复同一会话,保持多轮上下文
        cmd += (["-r", qid] if session.meta.get("resumed") else ["--session-id", qid])
        cmd.append(message)
        try:
            proc = subprocess.Popen(  # noqa: S603
                cmd, cwd=session.workdir or None,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                preexec_fn=make_preexec_limits(self.resource_limits),
            )
        except OSError as exc:
            raise RuntimeError(f"Qoder CLI 进程启动失败: {exc}") from exc
        session.meta["proc"] = proc

    def fetch_events(self, session: SessionHandle) -> list[AdapterEvent]:
        proc: subprocess.Popen | None = session.meta.get("proc")
        if proc is None:
            return []
        timeout = float(self.config.get("turn_timeout_seconds", 600))
        try:
            raw, _ = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            raise RuntimeError(f"Qoder CLI 本轮执行超时({timeout:.0f}s)") from None
        session.meta["proc"] = None
        session.meta["resumed"] = True  # 无论成败,会话 ID 已建档,后续走 -r 恢复

        fmt = str(self.config.get("output_format") or "stream-json")
        events, result = _parse_qoder_output(raw or "", fmt)

        # CLI 报错但 result 为空时(如额度耗尽、会话异常),优先取最后一条可读消息,
        # 否则回落到原始输出尾部,避免只留下一个空错误字段导致失败原因不可追溯
        if result.get("is_error"):
            reason = str(result.get("result", "") or "").strip()
            if not reason:
                texts = [str(ev.payload.get("content", "")).strip() for ev in events
                         if ev.event_type == "message"]
                tail = " ".join((raw or "").split())[-1200:]
                reason = texts[-1] if texts and texts[-1] else (tail or "CLI 未输出任何内容")
            for ev in events:
                if ev.event_type == "system" and ev.payload.get("kind") == "agent_turn_completed" \
                        and not ev.payload.get("error"):
                    ev.payload["error"] = reason[:1500]

        # 未登录/凭证失效:CLI 以 is_error=true + 提示登录返回 → 鉴权异常闭环
        result_text = str(result.get("result", ""))
        if result.get("is_error") and ("not logged in" in result_text.lower()
                                       or "login" in result_text.lower() and not events):
            raise AdapterAuthError("Qoder CLI 未登录,请执行 `qoderclicn login` 完成授权后重试")
        productive = [e for e in events if e.event_type in ("message", "tool_call")]
        if result.get("is_error") and not productive:
            raise RuntimeError(_qoder_failure_detail(raw or "", result, proc.returncode))
        if proc.returncode not in (0, None) and not events:
            raise RuntimeError(_qoder_failure_detail(raw or "", result, proc.returncode))
        return events

    def terminate(self, session: SessionHandle) -> None:
        proc: subprocess.Popen | None = session.meta.get("proc")
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        session.meta.clear()


ADAPTERS: dict[str, type[BaseAdapter]] = {
    a.key: a for a in (EchoCliAdapter, GenericCliAdapter, HttpApiAdapter, QoderCliAdapter)
}


def get_adapter(adapter_key: str, config: dict[str, Any], credential: str = "",
                resource_limits: dict[str, Any] | None = None) -> BaseAdapter:
    cls = ADAPTERS.get(adapter_key)
    if cls is None:
        raise KeyError(f"未知适配器: {adapter_key}")
    return cls(config, credential, resource_limits)
