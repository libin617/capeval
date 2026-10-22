"""m5 review history + share expiry

Revision ID: a1b2c3d4e5f6
Revises: cc2fd69eb78e
Create Date: 2026-10-20 21:10:00.000000

FR-08 复核留痕(review_history)、FR-09 分享链接有效期(share_expires_at)。
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = 'a1b2c3d4e5f6'
down_revision: str | None = 'cc2fd69eb78e'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    attr_cols = {c["name"] for c in insp.get_columns("attributions")}
    if "review_history" not in attr_cols:
        op.add_column("attributions", sa.Column("review_history", sa.JSON(), nullable=True))
        op.execute("UPDATE attributions SET review_history = JSON_ARRAY() WHERE review_history IS NULL")
        op.alter_column("attributions", "review_history", existing_type=sa.JSON(), nullable=False)
    report_cols = {c["name"] for c in insp.get_columns("reports")}
    if "share_expires_at" not in report_cols:
        op.add_column("reports", sa.Column("share_expires_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column('reports', 'share_expires_at')
    op.drop_column('attributions', 'review_history')
