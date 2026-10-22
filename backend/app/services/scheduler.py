"""任务调度器(FR-03):后台线程轮询 queued Run 并派发执行。

约束:同一 Agent 同时只执行一个 Run(资源互斥);不同 Agent 可并行。
派发后由 execute_run 在独立线程中同步执行;取消通过 DB 状态传递(协作式)。
"""

from __future__ import annotations

import threading
import traceback

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models import EvaluationRun
from app.services.executor import execute_run

_settings = get_settings()

# 后台调度器轮询间隔(秒),默认 2 秒;单测可直接覆盖模块级变量 POLL_INTERVAL_SECONDS
POLL_INTERVAL_SECONDS = _settings.scheduler_poll_seconds


class Dispatcher:
    def __init__(self, poll_interval: float = POLL_INTERVAL_SECONDS) -> None:
        self.poll_interval = poll_interval
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._agent_busy: set[int] = set()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="capeval-dispatcher", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception:  # noqa: BLE001
                traceback.print_exc()
            self._stop.wait(self.poll_interval)

    def _tick(self) -> None:
        db = SessionLocal()
        try:
            candidates = [
                (r.id, r.agent_id)
                for r in db.query(EvaluationRun)
                .filter_by(status="queued")
                .order_by(EvaluationRun.submitted_at)
                .all()
            ]
        finally:
            db.close()
        for run_id, agent_id in candidates:
            with self._lock:
                if agent_id in self._agent_busy:
                    continue
                self._agent_busy.add(agent_id)
            threading.Thread(target=self._run_one, args=(run_id, agent_id),
                             name=f"capeval-run-{run_id}", daemon=True).start()

    def _run_one(self, run_id: int, agent_id: int) -> None:
        try:
            execute_run(run_id)
        except Exception:  # noqa: BLE001
            traceback.print_exc()
        finally:
            with self._lock:
                self._agent_busy.discard(agent_id)


dispatcher = Dispatcher()
