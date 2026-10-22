"""CapEval 全量核心数据模型(对齐 PRD v1.0 第 4.2 节对象模型)。

M1 一次建全,减少后续迁移成本。
"""

from datetime import datetime

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


# ---------- 用户(MVP:管理员 + 评测工程师) ----------
class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="evaluator")  # admin / evaluator / viewer
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# ---------- Agent(FR-01) ----------
class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    version_label: Mapped[str] = mapped_column(String(64), nullable=False, default="")  # 同一 Agent 多版本分别建档
    access_type: Mapped[str] = mapped_column(String(16), nullable=False)  # cli / api,创建后不可改
    adapter_key: Mapped[str] = mapped_column(String(64), nullable=False)  # echo_cli / generic_cli / http_api ...
    adapter_config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)  # 启动命令/endpoint/环境变量等
    credential_masked: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    credential_encrypted: Mapped[str] = mapped_column(Text, nullable=False, default="")
    resource_limits: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)  # 时长/轮次/费用上限
    cost_params: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)  # token 单价(输入/输出)
    # 状态: unverified 未校验 / available 可用 / verify_failed 校验失败 / unavailable 不可用
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="unverified")
    last_verify_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_verify_error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (UniqueConstraint("name", "version_label", name="uq_agent_name_version"),)


# ---------- TestCase 用例(FR-02) ----------
class TestCase(Base):
    __tablename__ = "testcases"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    # 任务脚本:多轮用户消息序列 [{turn, message, intent_tag}],至少 1 轮
    task_script: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    repo_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)  # {type: files/git, ...}
    env_def: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)  # 运行时/依赖安装命令
    # 验收: {auto_commands: [], security_commands: [], artifacts: [], semantic_points: [], process_expectations: []}
    acceptance: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # Gold Patch:标准解答的 unified diff(可选),用于与 Agent 产出对比做正确性参考
    gold_patch: Mapped[str] = mapped_column(Text, nullable=False, default="")
    efficiency_baseline: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)  # token/耗时/工具调用参考值
    difficulty: Mapped[str] = mapped_column(String(16), nullable=False, default="medium")  # easy/medium/hard
    tags: Mapped[list] = mapped_column(JSON, nullable=False, default=list)  # 能力标签,至少 1 个
    est_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")  # active / archived
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


# ---------- CaseSet 用例集(版本化,发布冻结) ----------
class CaseSet(Base):
    __tablename__ = "casesets"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    is_builtin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)  # 官方冒烟用例集
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    versions: Mapped[list["CaseSetVersion"]] = relationship(back_populates="caseset", order_by="CaseSetVersion.version_no")


class CaseSetVersion(Base):
    __tablename__ = "caseset_versions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    caseset_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("casesets.id"), nullable=False, index=True)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")  # draft / published(冻结)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    # 本版本默认使用的 LLM 评审模型(发布时固化口径;任务创建时继承,可覆盖)
    judge_config_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("llm_judge_configs.id"), nullable=True)

    caseset: Mapped[CaseSet] = relationship(back_populates="versions")
    items: Mapped[list["CaseSetItem"]] = relationship(back_populates="version", order_by="CaseSetItem.order_no",
                                                      cascade="all, delete-orphan")
    judge_config = relationship("LlmJudgeConfig", foreign_keys=[judge_config_id])

    @property
    def judge_config_name(self) -> str:
        return self.judge_config.name if self.judge_config else ""

    __table_args__ = (UniqueConstraint("caseset_id", "version_no", name="uq_caseset_version"),)


class CaseSetItem(Base):
    __tablename__ = "caseset_items"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    version_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("caseset_versions.id"), nullable=False, index=True)
    testcase_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("testcases.id"), nullable=False)
    order_no: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    version: Mapped[CaseSetVersion] = relationship(back_populates="items")
    testcase: Mapped[TestCase] = relationship()

    @property
    def title(self) -> str:
        return self.testcase.title if self.testcase else ""

    @property
    def difficulty(self) -> str:
        return self.testcase.difficulty if self.testcase else ""

    @property
    def tags(self) -> list:
        return self.testcase.tags if self.testcase else []


# ---------- 维度权重模板(FR-03,八维) ----------
class WeightTemplate(Base):
    __tablename__ = "weight_templates"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # {"D1": 0.30, "D2": 0.12, ..., "D8": 0.06}
    weights: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="published")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# ---------- 根因库(FR-06/FR-07,三级 + 版本化) ----------
class RootCauseLibraryVersion(Base):
    __tablename__ = "rootcause_library_versions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    version_no: Mapped[int] = mapped_column(Integer, unique=True, nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class RootCause(Base):
    __tablename__ = "root_causes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)  # RC-1 / RC-1.1 / RC-1.1.1
    level: Mapped[int] = mapped_column(Integer, nullable=False)  # 1/2/3
    parent_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("root_causes.id"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # 责任归属: agent / platform / third_party / case_maintainer
    responsibility: Mapped[str] = mapped_column(String(32), nullable=False, default="agent")
    # 检测信号: {"rules": [...], "llm_prompt": "...", "related_dimensions": ["D1", ...]}
    detection_signals: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)  # 停用不删除
    introduced_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)  # 引入时的根因库版本
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    parent: Mapped["RootCause | None"] = relationship(remote_side="RootCause.id")


# ---------- Evaluation Run(中心对象,状态机见 PRD 4.3) ----------
class EvaluationRun(Base):
    __tablename__ = "evaluation_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    agent_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("agents.id"), nullable=False, index=True)
    caseset_version_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("caseset_versions.id"), nullable=False)
    weight_template_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("weight_templates.id"), nullable=False)
    weight_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)  # 提交时快照
    rootcause_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)  # 提交时快照
    review_policy: Mapped[str] = mapped_column(String(32), nullable=False, default="skip")  # skip/sample/full
    fail_threshold: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    baseline_run_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Pass@k:每个用例重复执行次数(默认 1),用于统计非确定性 Agent 的通过率
    pass_k: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # 本任务使用的评审模型快照(创建时默认继承评测集版本,可覆盖)
    judge_config_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("llm_judge_configs.id"), nullable=True)
    # draft/queued/running/scoring/attributing/reviewing/completed/failed/canceled
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="draft", index=True)
    error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_by: Mapped[str] = mapped_column(String(64), nullable=False, default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    agent: Mapped[Agent] = relationship()
    judge_config = relationship("LlmJudgeConfig", foreign_keys=[judge_config_id])


class CaseExecution(Base):
    __tablename__ = "case_executions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("evaluation_runs.id"), nullable=False, index=True)
    testcase_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("testcases.id"), nullable=False)
    order_no: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # pending/running/succeeded/failed
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    rerun_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Pass@k 重复序号:同一 testcase 的第几次执行(0 表示首次/仅一次)
    repetition_no: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    resource_usage: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)  # token/耗时/费用
    error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    testcase: Mapped[TestCase] = relationship()


class TraceEvent(Base):
    __tablename__ = "trace_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    execution_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("case_executions.id"), nullable=False, index=True)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    # message / tool_call / file_change / terminal / resource / system
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    truncated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class DimensionScore(Base):
    __tablename__ = "dimension_scores"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    execution_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("case_executions.id"), nullable=False, index=True)
    dimension: Mapped[str] = mapped_column(String(8), nullable=False)  # D1..D8
    score: Mapped[float] = mapped_column(Float, nullable=False)  # 0-5
    details: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)  # 判定明细与扣分理由
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Attribution(Base):
    __tablename__ = "attributions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    execution_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("case_executions.id"), nullable=False, index=True)
    rootcause_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("root_causes.id"), nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)  # 主 1 + 次 ≤2
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="rule")  # rule/llm/human
    evidence: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)  # 证据链(事件序号引用)
    # pending/confirmed/corrected/rejected/not_sampled(抽样未命中,自动通过)
    review_status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    reviewed_by: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    review_note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # 复核留痕:操作人/时间/前后结论 [{action, by, at, from_rootcause_id, to_rootcause_id, note}]
    review_history: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    rootcause: Mapped[RootCause] = relationship()


class Report(Base):
    __tablename__ = "reports"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("evaluation_runs.id"), nullable=False, unique=True)
    quality_score: Mapped[float | None] = mapped_column(Float, nullable=True)  # 百分制
    efficiency_score: Mapped[float | None] = mapped_column(Float, nullable=True)  # 百分制(D6 折算)
    dimension_summary: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    content: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)  # 报告全文结构
    export_path: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    share_token: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    # 分享链接免登录有效期,为空表示仅登录可见(撤销时清空 share_token)
    share_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    review_mark: Mapped[str] = mapped_column(String(24), nullable=False, default="unreviewed")  # unreviewed/reviewed
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# ---------- 站内通知(PRD 7.2,M6) ----------
class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    recipient: Mapped[str] = mapped_column(String(64), nullable=False, index=True)  # 用户名
    # run_status(Run 完成/失败/待复核) / agent_alert(凭证失效/校验失败/费用触限)
    # rc10_candidate(集体性失败) / review_overdue(复核超期)
    category: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    link: Mapped[str] = mapped_column(String(256), nullable=False, default="")  # 前端跳转路由
    # 幂等去重键:同一事件(如同一 Run 的复核超期)只提醒一次
    dedupe_key: Mapped[str] = mapped_column(String(191), nullable=False, default="", index=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# ---------- 归因异议评论(PRD FR-08 异常,只读成员可提交,M6) ----------
class AttributionComment(Base):
    __tablename__ = "attribution_comments"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    attribution_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("attributions.id"), nullable=False, index=True)
    author: Mapped[str] = mapped_column(String(64), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="viewer")
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# ---------- BadCase 回流(FR-08) ----------
class BadCase(Base):
    __tablename__ = "badcases"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # 关联:来自哪次 run / 哪个用例 / 哪次执行 / 哪些 attribution
    run_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("evaluation_runs.id", ondelete="CASCADE"),
                                        nullable=False, index=True)
    testcase_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("testcases.id", ondelete="CASCADE"),
                                              nullable=False, index=True)
    case_execution_id: Mapped[int] = mapped_column(BigInteger,
                                                    ForeignKey("case_executions.id", ondelete="CASCADE"),
                                                    nullable=False, index=True)
    attribution_ids: Mapped[list[int] | None] = mapped_column(JSON, nullable=True)

    # 问题分类(决定回流方向)
    # agent_bug / case_bug / rootcause_gap / scoring_bug / false_positive / other
    reason: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    reason_detail: Mapped[str] = mapped_column(Text, nullable=False)

    # 严重度与归属
    # critical / high / medium / low
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="medium", index=True)
    # agent_provider / case_maintainer / platform
    ownership: Mapped[str] = mapped_column(String(32), nullable=False, default="platform", index=True)

    # 处理状态:open → triaged → fixed / wontfix → resolved
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open", index=True)
    owner: Mapped[str | None] = mapped_column(String(64), nullable=True)
    solution: Mapped[str | None] = mapped_column(Text, nullable=True)
    # disable_agent / archive_testcase / add_rootcause / fix_scoring / manual
    solution_type: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # 回流验证凭证
    fixed_agent_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fixed_testcase_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fixed_rootcause_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    verified_run_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # 审计
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(),
                                                  onupdate=func.now(), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # ---- 关系(可选,方便 API 组装) ----
    run: Mapped["EvaluationRun | None"] = relationship("EvaluationRun")
    testcase: Mapped["TestCase | None"] = relationship("TestCase")
    case_execution: Mapped["CaseExecution | None"] = relationship("CaseExecution")


# ---------- LLM-as-Judge 评审模型配置(FR-05,支持多套配置) ----------
class LlmJudgeConfig(Base):
    __tablename__ = "llm_judge_configs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="openai_compatible")
    base_url: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    model: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    api_key: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    temperature: Mapped[float] = mapped_column(Float, nullable=False, default=0.2)
    max_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=800)
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    # 启用语义评审的维度(D4 多轮一致性 / D7 代码风格 / D8 工程化)
    dimensions: Mapped[list] = mapped_column(JSON, nullable=False, default=lambda: ["D4", "D7", "D8"])
    # 各维度评分标准 {"D4": "...", "D7": "...", "D8": "..."},模型据此打分
    rubrics: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    updated_by: Mapped[str] = mapped_column(String(64), nullable=False, default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now())

