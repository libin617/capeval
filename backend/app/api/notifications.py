"""站内通知 API(PRD 7.2,M6):查询/未读数/标记已读/超期扫描。"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.database import get_db
from app.models import Notification, User
from app.schemas import NotificationListOut, NotificationOut
from app.services import notify

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


@router.get("", response_model=NotificationListOut)
def list_notifications(unread_only: bool = False, category: str | None = None, limit: int = 50,
                       user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> NotificationListOut:
    q = db.query(Notification).filter(Notification.recipient == user.username)
    if unread_only:
        q = q.filter(Notification.read_at.is_(None))
    if category:
        q = q.filter(Notification.category == category)
    rows = q.order_by(Notification.id.desc()).limit(min(limit, 200)).all()
    unread = (
        db.query(Notification)
        .filter(Notification.recipient == user.username, Notification.read_at.is_(None))
        .count()
    )
    return NotificationListOut(unread=unread, items=[NotificationOut.model_validate(r) for r in rows])


@router.post("/scan", response_model=dict)
def scan_overdue(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    """手动触发复核超期扫描(幂等);列表查询接口亦会顺带触发。"""
    created = notify.scan_review_overdue(db)
    return {"created": created}


@router.post("/{notification_id}/read", response_model=NotificationOut)
def mark_read(notification_id: int, user: User = Depends(get_current_user),
              db: Session = Depends(get_db)) -> Notification:
    row = db.get(Notification, notification_id)
    if row is None or row.recipient != user.username:
        raise HTTPException(404, "通知不存在")
    if row.read_at is None:
        row.read_at = datetime.now()
        db.commit()
        db.refresh(row)
    return row


@router.post("/read-all", response_model=dict)
def mark_all_read(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    now = datetime.now()
    count = (
        db.query(Notification)
        .filter(Notification.recipient == user.username, Notification.read_at.is_(None))
        .update({"read_at": now}, synchronize_session=False)
    )
    db.commit()
    return {"marked": count}
