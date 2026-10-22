"""MVP 鉴权(PRD 7.3 权限矩阵):前端通过 X-Acting-User 头声明当前身份,
后端按 users 表解析角色并强制写权限。未携带身份头时默认 admin(向后兼容脚本与测试)。
三角色:admin(管理员)/ evaluator(评测工程师)/ viewer(只读成员)。
"""

from __future__ import annotations

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models import User

WRITE_ROLES = ("admin", "evaluator")  # 一般写操作:管理员 + 评测工程师
CREDENTIAL_ROLES = ("admin",)         # 凭证管理:仅管理员


def get_current_user(db: Session = Depends(get_db),
                     x_acting_user: str | None = Header(default=None)) -> User:
    username = (x_acting_user or "admin").strip()
    user = db.query(User).filter(User.username == username).first()
    if user is None:
        raise HTTPException(401, f"未知身份: {username}(请在右上角切换有效身份)")
    return user


def require_roles(*roles: str):
    """写权限依赖工厂:当前用户角色不在允许列表时返回 403。"""
    allowed = set(roles)

    def _dep(user: User = Depends(get_current_user)) -> User:
        if user.role not in allowed:
            raise HTTPException(403, f"当前身份「{user.display_name or user.username}」({user.role})无此操作权限")
        return user

    return _dep


# 模块级实例化:端点直接 Depends(require_writer) / Depends(require_admin) 使用
require_writer = require_roles(*WRITE_ROLES)
require_admin = require_roles(*CREDENTIAL_ROLES)
