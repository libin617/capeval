"""系统管理 API(M6):数据保留策略(仅管理员)、成员列表。"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.auth import get_current_user, require_admin
from app.core.database import get_db
from app.models import User
from app.schemas import RetentionOut, RetentionPurgeOut, UserOut
from app.services import retention

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/retention", response_model=RetentionOut)
def get_retention(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    return retention.stats(db)


@router.post("/retention/purge", response_model=RetentionPurgeOut)
def purge_expired_traces(user: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    """物理清理保留期外的轨迹事件(保留证据链片段)。仅管理员可执行。"""
    return retention.purge_expired(db)


@router.get("/users", response_model=list[UserOut])
def list_users(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[User]:
    """成员列表(供前端身份切换)。"""
    return db.query(User).order_by(User.id).all()
