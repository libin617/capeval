"""llm judge config

Revision ID: c7d8e9f0a1b2
Revises: b6c7d8e9f0a1
Create Date: 2026-10-21 10:00:00.000000

LLM-as-Judge 评审模型配置(单行配置表):启用开关、OpenAI 兼容接口参数、
启用语义评审的维度。
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = 'c7d8e9f0a1b2'
down_revision: str | None = 'b6c7d8e9f0a1'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "llm_judge_configs" not in insp.get_table_names():
        op.create_table(
            "llm_judge_configs",
            sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("provider", sa.String(32), nullable=False, server_default="openai_compatible"),
            sa.Column("base_url", sa.String(256), nullable=False, server_default=""),
            sa.Column("model", sa.String(128), nullable=False, server_default=""),
            sa.Column("api_key", sa.String(512), nullable=False, server_default=""),
            sa.Column("temperature", sa.Float(), nullable=False, server_default="0.2"),
            sa.Column("max_tokens", sa.Integer(), nullable=False, server_default="800"),
            sa.Column("timeout_seconds", sa.Integer(), nullable=False, server_default="60"),
            sa.Column("dimensions", sa.JSON(), nullable=False),
            sa.Column("updated_by", sa.String(64), nullable=False, server_default="system"),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )


def downgrade() -> None:
    op.drop_table("llm_judge_configs")
