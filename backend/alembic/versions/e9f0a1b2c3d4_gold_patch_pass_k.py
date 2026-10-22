"""Gold Patch / Pass@k / 用例自检相关字段

Revision ID: e9f0a1b2c3d4
Revises: d8e9f0a1b2c3
Create Date: 2026-10-21

变更:
1. testcases 增加 gold_patch(Text):标准解答 unified diff,用于与 Agent 产出对比;
2. evaluation_runs 增加 pass_k(Integer):每个用例重复执行次数(默认 1);
3. case_executions 增加 repetition_no(Integer):Pass@k 重复序号(0 表示首次)。
"""

import sqlalchemy as sa
from alembic import op

revision = "e9f0a1b2c3d4"
down_revision = "d8e9f0a1b2c3"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    insp = sa.inspect(op.get_bind())
    return {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    tc_cols = _columns("testcases")
    if "gold_patch" not in tc_cols:
        # MySQL 不允许 TEXT 列有 DEFAULT,置 nullable=True,应用层始终写入空串
        op.add_column("testcases",
                      sa.Column("gold_patch", sa.Text(), nullable=True))

    run_cols = _columns("evaluation_runs")
    if "pass_k" not in run_cols:
        op.add_column("evaluation_runs",
                      sa.Column("pass_k", sa.Integer(), nullable=False, server_default=sa.text("1")))

    ce_cols = _columns("case_executions")
    if "repetition_no" not in ce_cols:
        op.add_column("case_executions",
                      sa.Column("repetition_no", sa.Integer(), nullable=False, server_default=sa.text("0")))


def downgrade() -> None:
    ce_cols = _columns("case_executions")
    if "repetition_no" in ce_cols:
        op.drop_column("case_executions", "repetition_no")

    run_cols = _columns("evaluation_runs")
    if "pass_k" in run_cols:
        op.drop_column("evaluation_runs", "pass_k")

    tc_cols = _columns("testcases")
    if "gold_patch" in tc_cols:
        op.drop_column("testcases", "gold_patch")