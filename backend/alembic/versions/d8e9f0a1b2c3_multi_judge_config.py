"""LLM 评审模型支持多套配置,并绑定到评测集版本与评测任务

Revision ID: d8e9f0a1b2c3
Revises: c7d8e9f0a1b2
Create Date: 2026-10-21

变更:
1. llm_judge_configs 增加 name / is_default / rubrics / created_at,支持多行;
2. caseset_versions 增加 judge_config_id(评测集版本默认评审模型);
3. evaluation_runs 增加 judge_config_id(任务创建时继承,可覆盖)。
"""

import sqlalchemy as sa
from alembic import op

revision = "d8e9f0a1b2c3"
down_revision = "c7d8e9f0a1b2"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    insp = sa.inspect(op.get_bind())
    return {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    judge_cols = _columns("llm_judge_configs")
    if "name" not in judge_cols:
        op.add_column("llm_judge_configs",
                      sa.Column("name", sa.String(64), nullable=False, server_default=""))
        op.create_unique_constraint("uq_llm_judge_config_name", "llm_judge_configs", ["name"])
    if "is_default" not in judge_cols:
        op.add_column("llm_judge_configs",
                      sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.text("0")))
    if "rubrics" not in judge_cols:
        op.add_column("llm_judge_configs", sa.Column("rubrics", sa.JSON(), nullable=True))
    if "created_at" not in judge_cols:
        op.add_column("llm_judge_configs",
                      sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()))

    # 旧单行配置回填名称,并标记为默认评审模型
    op.execute("UPDATE llm_judge_configs SET name = '默认评审模型' WHERE name = '' OR name IS NULL")
    op.execute("UPDATE llm_judge_configs SET is_default = 1 WHERE id = 1")

    version_cols = _columns("caseset_versions")
    if "judge_config_id" not in version_cols:
        op.add_column("caseset_versions",
                      sa.Column("judge_config_id", sa.BigInteger(), nullable=True))
        op.create_foreign_key("fk_caseset_version_judge_config", "caseset_versions",
                              "llm_judge_configs", ["judge_config_id"], ["id"])

    run_cols = _columns("evaluation_runs")
    if "judge_config_id" not in run_cols:
        op.add_column("evaluation_runs",
                      sa.Column("judge_config_id", sa.BigInteger(), nullable=True))
        op.create_foreign_key("fk_evaluation_run_judge_config", "evaluation_runs",
                              "llm_judge_configs", ["judge_config_id"], ["id"])


def downgrade() -> None:
    run_cols = _columns("evaluation_runs")
    if "judge_config_id" in run_cols:
        op.drop_constraint("fk_evaluation_run_judge_config", "evaluation_runs", type_="foreignkey")
        op.drop_column("evaluation_runs", "judge_config_id")

    version_cols = _columns("caseset_versions")
    if "judge_config_id" in version_cols:
        op.drop_constraint("fk_caseset_version_judge_config", "caseset_versions", type_="foreignkey")
        op.drop_column("caseset_versions", "judge_config_id")

    judge_cols = _columns("llm_judge_configs")
    if "created_at" in judge_cols:
        op.drop_column("llm_judge_configs", "created_at")
    if "rubrics" in judge_cols:
        op.drop_column("llm_judge_configs", "rubrics")
    if "is_default" in judge_cols:
        op.drop_column("llm_judge_configs", "is_default")
    if "name" in judge_cols:
        op.drop_constraint("uq_llm_judge_config_name", "llm_judge_configs", type_="unique")
        op.drop_column("llm_judge_configs", "name")
