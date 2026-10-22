"""m6 notifications + attribution comments

Revision ID: b6c7d8e9f0a1
Revises: a1b2c3d4e5f6
Create Date: 2026-10-20 22:00:00.000000

M6 加固:站内通知(PRD 7.2)与归因异议评论(PRD FR-08 异常)。
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = 'b6c7d8e9f0a1'
down_revision: str | None = 'a1b2c3d4e5f6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "notifications" not in insp.get_table_names():
        op.create_table(
            "notifications",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("recipient", sa.String(64), nullable=False, index=True),
            sa.Column("category", sa.String(32), nullable=False, index=True),
            sa.Column("title", sa.String(256), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("link", sa.String(256), nullable=False),
            sa.Column("dedupe_key", sa.String(191), nullable=False, server_default="", index=True),
            sa.Column("read_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        )
    if "attribution_comments" not in insp.get_table_names():
        op.create_table(
            "attribution_comments",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("attribution_id", sa.BigInteger(),
                      sa.ForeignKey("attributions.id"), nullable=False, index=True),
            sa.Column("author", sa.String(64), nullable=False),
            sa.Column("role", sa.String(32), nullable=False, server_default="viewer"),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        )


def downgrade() -> None:
    op.drop_table('attribution_comments')
    op.drop_table('notifications')
