"""Agent 适配器契约(FR-01):对上层暴露统一能力。

start_session / send_message / fetch_events / terminate。
M1 只需支撑连通性校验,M2 才完整用于执行闭环。
"""

from __future__ import annotations

import os
import resource
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable


def make_preexec_limits(limits: dict[str, Any]) -> Callable[[], None] | None:
    """根据 resource_limits 构建子进程 preexec_fn(setrlimit 内存/CPU)。

    max_memory_mb → RLIMIT_AS(虚拟内存上限,MB)
    max_cpu_seconds → RLIMIT_CPU(CPU 时间上限,秒)
    返回 None 表示无限制。
    注意:部分平台(如 macOS)对 RLIMIT_AS 支持有限,setrlimit 失败时降级为不限制,
    不阻断子进程启动;Docker 隔离模式下内存/CPU 由 cgroup 强制,不依赖 setrlimit。
    """
    mem_mb = int(limits.get("max_memory_mb") or 0)
    cpu_sec = int(limits.get("max_cpu_seconds") or 0)
    if not mem_mb and not cpu_sec:
        return None

    def _apply() -> None:
        if mem_mb > 0:
            try:
                resource.setrlimit(resource.RLIMIT_AS,
                                   (mem_mb * 1024 * 1024, mem_mb * 1024 * 1024))
            except (ValueError, OSError):
                pass  # 平台不支持时降级
        if cpu_sec > 0:
            try:
                resource.setrlimit(resource.RLIMIT_CPU, (cpu_sec, cpu_sec))
            except (ValueError, OSError):
                pass

    return _apply


@dataclass
class SessionHandle:
    session_id: str
    workdir: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class AdapterEvent:
    event_type: str  # message/tool_call/file_change/terminal/resource/system
    payload: dict[str, Any]


class AdapterAuthError(Exception):
    """适配器鉴权/凭证失败(HTTP 401/403):执行中触发时 case 判失败、Agent 置不可用并通知管理员。"""


class BaseAdapter(ABC):
    key: str = "base"
    name: str = "基础适配器"
    access_type: str = "cli"
    description: str = ""
    required_config: list[str] = []
    supports_execution: bool = False  # M2:是否支持完整任务执行(非仅冒烟)

    def __init__(self, config: dict[str, Any], credential: str = "",
                 resource_limits: dict[str, Any] | None = None) -> None:
        self.config = config
        self.credential = credential
        self.resource_limits = resource_limits or {}

    @abstractmethod
    def start_session(self, workdir: str) -> SessionHandle: ...

    @abstractmethod
    def send_message(self, session: SessionHandle, message: str) -> None: ...

    @abstractmethod
    def fetch_events(self, session: SessionHandle) -> list[AdapterEvent]: ...

    @abstractmethod
    def terminate(self, session: SessionHandle) -> None: ...

    def smoke_check(self, timeout_seconds: int = 30) -> tuple[bool, str]:
        """最小交互冒烟:启动会话 → 投递一条消息 → 拿到事件 → 终止。"""
        session: SessionHandle | None = None
        try:
            session = self.start_session(workdir=".")
            self.send_message(session, "ping: please reply 'pong'")
            events = self.fetch_events(session)
            if not events:
                return False, "冒烟失败: 未收到任何事件"
            return True, f"冒烟通过: 收到 {len(events)} 条事件"
        except Exception as exc:  # noqa: BLE001
            return False, f"冒烟失败: {type(exc).__name__}: {exc}"
        finally:
            if session is not None:
                try:
                    self.terminate(session)
                except Exception:  # noqa: BLE001,S110
                    pass
