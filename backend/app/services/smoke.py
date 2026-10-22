"""冒烟校验服务(FR-01):创建后必须校验通过,Agent 才为"可用"。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.adapters.registry import get_adapter
from app.core.config import get_settings
from app.core.security import decrypt_credential
from app.models import Agent
from app.services import notify


def run_smoke_check(db: Session, agent: Agent) -> tuple[bool, str]:
    settings = get_settings()
    credential = decrypt_credential(agent.credential_encrypted)
    adapter = get_adapter(agent.adapter_key, agent.adapter_config, credential)
    passed, message = adapter.smoke_check(timeout_seconds=settings.smoke_timeout_seconds)
    agent.status = "available" if passed else "verify_failed"
    agent.last_verify_at = datetime.now()
    agent.last_verify_error = "" if passed else message
    db.commit()
    db.refresh(agent)
    if not passed:
        notify.notify_verify_failed(db, agent)  # M6:校验失败 → 管理员
    return passed, message
