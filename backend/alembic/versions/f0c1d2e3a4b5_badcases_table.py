"""BadCase 回流表(FR-08 BadCase 管理)

Revision ID: f0c1d2e3a4b5
Revises: e9f0a1b2c3d4
Create Date: 2026-10-22

新增 badcases 表,承载从复核工作台/自动规则生成的 BadCase,
用于回流到 Agent 改进清单 / 用例维护清单 / 根因库增补 / 评分规则修正。
"""

import sqlalchemy as sa
from alembic import op

revision = "f0c1d2e3a4b5"
down_revision = "e9f0a1b2c3d4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    existing = insp.get_table_names()
    if "badcases" in existing:
        return  # 已存在,幂等

    op.execute("""
    CREATE TABLE badcases (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        run_id BIGINT NOT NULL,
        testcase_id BIGINT NOT NULL,
        case_execution_id BIGINT NOT NULL,
        attribution_ids JSON NULL,
        reason VARCHAR(32) NOT NULL COMMENT 'agent_bug/case_bug/rootcause_gap/scoring_bug/false_positive/other',
        reason_detail TEXT NOT NULL COMMENT '问题详细描述(自然语言)',
        severity VARCHAR(16) NOT NULL DEFAULT 'medium' COMMENT 'critical/high/medium/low',
        ownership VARCHAR(32) NOT NULL DEFAULT 'platform' COMMENT 'agent_provider/case_maintainer/platform',
        status VARCHAR(16) NOT NULL DEFAULT 'open' COMMENT 'open/triaged/fixed/wontfix/resolved',
        owner VARCHAR(64) NULL,
        solution TEXT NULL,
        solution_type VARCHAR(32) NULL COMMENT 'disable_agent/archive_testcase/add_rootcause/fix_scoring/manual',
        fixed_agent_version VARCHAR(64) NULL,
        fixed_testcase_version INT NULL,
        fixed_rootcause_id BIGINT NULL,
        verified_run_id BIGINT NULL,
        created_by VARCHAR(64) NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        resolved_at DATETIME NULL,
        INDEX idx_badcases_run (run_id),
        INDEX idx_badcases_testcase (testcase_id),
        INDEX idx_badcases_execution (case_execution_id),
        INDEX idx_badcases_reason (reason),
        INDEX idx_badcases_severity (severity),
        INDEX idx_badcases_status (status),
        INDEX idx_badcases_ownership (ownership),
        CONSTRAINT fk_badcases_run
            FOREIGN KEY (run_id) REFERENCES evaluation_runs(id) ON DELETE CASCADE,
        CONSTRAINT fk_badcases_testcase
            FOREIGN KEY (testcase_id) REFERENCES testcases(id) ON DELETE CASCADE,
        CONSTRAINT fk_badcases_execution
            FOREIGN KEY (case_execution_id) REFERENCES case_executions(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS badcases")
