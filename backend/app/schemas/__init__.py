"""Pydantic 请求/响应模型。"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

DIMENSIONS = ["D1", "D2", "D3", "D4", "D5", "D6", "D7", "D8"]


# ---------- Agent ----------
class AgentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    version_label: str = Field(default="", max_length=64)
    access_type: Literal["cli", "api"]
    adapter_key: str = Field(min_length=1, max_length=64)
    adapter_config: dict[str, Any] = Field(default_factory=dict)
    credential: str = ""  # 明文只在创建/更新时传入,永不回显
    resource_limits: dict[str, Any] = Field(default_factory=dict)
    cost_params: dict[str, Any] = Field(default_factory=dict)


class AgentUpdate(BaseModel):
    name: str | None = None
    version_label: str | None = None
    adapter_key: str | None = None
    adapter_config: dict[str, Any] | None = None
    credential: str | None = None
    resource_limits: dict[str, Any] | None = None
    cost_params: dict[str, Any] | None = None


class AgentOut(BaseModel):
    id: int
    name: str
    version_label: str
    access_type: str
    adapter_key: str
    adapter_config: dict[str, Any]
    credential_masked: str
    resource_limits: dict[str, Any]
    cost_params: dict[str, Any]
    status: str
    last_verify_at: datetime | None
    last_verify_error: str
    created_at: datetime

    model_config = {"from_attributes": True}


class AdapterInfo(BaseModel):
    key: str
    name: str
    access_type: str
    description: str
    required_config: list[str]


class VerifyResult(BaseModel):
    agent_id: int
    status: str
    passed: bool
    message: str


# ---------- TestCase ----------
class ScriptTurn(BaseModel):
    turn: int = Field(ge=1)
    message: str = Field(min_length=1)
    intent_tag: str = ""  # 多轮时每轮的考察意图标签


class TestCaseCreate(BaseModel):
    title: str = Field(min_length=1, max_length=256)
    task_script: list[ScriptTurn] = Field(min_length=1)
    repo_snapshot: dict[str, Any] = Field(default_factory=dict)
    env_def: dict[str, Any] = Field(default_factory=dict)
    acceptance: dict[str, Any] = Field(default_factory=dict)
    gold_patch: str | None = ""  # 标准解答 unified diff(可选),用于与 Agent 产出对比
    efficiency_baseline: dict[str, Any] = Field(default_factory=dict)
    difficulty: Literal["easy", "medium", "hard"] = "medium"
    tags: list[str] = Field(min_length=1)
    est_minutes: int = Field(default=30, ge=1)

    @field_validator("acceptance")
    @classmethod
    def acceptance_not_empty(cls, v: dict[str, Any]) -> dict[str, Any]:
        has_auto = bool(v.get("auto_commands"))
        has_semantic = bool(v.get("semantic_points"))
        if not (has_auto or has_semantic):
            raise ValueError("验收至少包含一条自动化验收(auto_commands)或语义验收(semantic_points)")
        return v

    @field_validator("task_script")
    @classmethod
    def turns_sequential(cls, v: list[ScriptTurn]) -> list[ScriptTurn]:
        turns = [t.turn for t in v]
        if turns != list(range(1, len(v) + 1)):
            raise ValueError("task_script 轮次必须从 1 开始连续编号")
        if len(v) > 1 and any(not t.intent_tag for t in v):
            raise ValueError("多轮脚本需为每轮标注考察意图标签 intent_tag")
        return v


class TestCaseUpdate(BaseModel):
    title: str | None = None
    task_script: list[ScriptTurn] | None = None
    repo_snapshot: dict[str, Any] | None = None
    env_def: dict[str, Any] | None = None
    acceptance: dict[str, Any] | None = None
    gold_patch: str | None = None
    efficiency_baseline: dict[str, Any] | None = None
    difficulty: Literal["easy", "medium", "hard"] | None = None
    tags: list[str] | None = None
    est_minutes: int | None = None


class TestCaseOut(BaseModel):
    id: int
    title: str
    task_script: list[dict[str, Any]]
    repo_snapshot: dict[str, Any]
    env_def: dict[str, Any]
    acceptance: dict[str, Any]
    gold_patch: str | None = ""
    efficiency_baseline: dict[str, Any]
    difficulty: str
    tags: list[str]
    est_minutes: int
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


# ---------- CaseSet ----------
class CaseSetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str = ""
    testcase_ids: list[int] = Field(min_length=1)  # 首个草稿版本的用例(有序)


class CaseSetVersionCreate(BaseModel):
    testcase_ids: list[int] = Field(min_length=1)  # 基于发布版内容修改后的新草稿


class CaseSetVersionJudge(BaseModel):
    """评测集版本默认评审模型(为空表示不绑定,使用平台默认配置)。"""
    judge_config_id: int | None = None


class CaseSetUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = None


class CaseSetItemOut(BaseModel):
    testcase_id: int
    order_no: int
    title: str
    difficulty: str
    tags: list[str]

    model_config = {"from_attributes": True}


class CaseSetVersionOut(BaseModel):
    id: int
    version_no: int
    status: str
    published_at: datetime | None
    judge_config_id: int | None = None
    judge_config_name: str = ""
    items: list[CaseSetItemOut]

    model_config = {"from_attributes": True}


class CaseSetOut(BaseModel):
    id: int
    name: str
    description: str
    is_builtin: bool
    created_at: datetime
    versions: list[CaseSetVersionOut]

    model_config = {"from_attributes": True}


# ---------- WeightTemplate(FR-05 权重配置,可在线维护) ----------
class WeightTemplateOut(BaseModel):
    id: int
    name: str
    is_default: bool
    weights: dict[str, float]

    model_config = {"from_attributes": True}


class WeightTemplateCreate(BaseModel):
    name: str
    weights: dict[str, float]
    is_default: bool = False


class WeightTemplateUpdate(BaseModel):
    name: str | None = None
    weights: dict[str, float] | None = None
    is_default: bool | None = None


# ---------- RootCause 维护(FR-07,M5) ----------
class RootCauseOut(BaseModel):
    id: int
    code: str
    level: int
    parent_id: int | None
    name: str
    description: str
    responsibility: str
    detection_signals: dict[str, Any]
    enabled: bool
    introduced_version: int
    reference_count: int = 0  # 被归因结论引用的次数(>0 时禁止删除,仅可停用)

    model_config = {"from_attributes": True}


class RootCauseCreate(BaseModel):
    level: Literal[1, 2, 3]
    parent_id: int | None = None
    name: str = Field(min_length=1, max_length=128)
    description: str = ""
    responsibility: Literal["agent", "platform", "third_party", "case_maintainer"] = "agent"
    detection_signals: dict[str, Any] = Field(default_factory=dict)
    source_execution_id: int | None = None  # 「未确定」案件一键转新根因时带入证据示例
    note: str = ""  # 本次版本说明


class RootCauseUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=128)
    description: str | None = None
    responsibility: Literal["agent", "platform", "third_party", "case_maintainer"] | None = None
    detection_signals: dict[str, Any] | None = None
    enabled: bool | None = None
    note: str = ""


class RootCauseWriteOut(BaseModel):
    rootcause: RootCauseOut
    library_version: int
    warning: str = ""


class RootCauseLibraryVersionOut(BaseModel):
    version_no: int
    note: str


class SignalCatalogOut(BaseModel):
    signals: list[str]


# ---------- Evaluation Run(FR-03,状态机见 PRD 4.3) ----------
class RunCreate(BaseModel):
    name: str = Field(default="", max_length=256)
    agent_id: int
    caseset_version_id: int
    weight_template_id: int
    review_policy: Literal["skip", "sample", "full"] = "skip"
    fail_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    baseline_run_id: int | None = None
    judge_config_id: int | None = None  # 为空则继承评测集版本的评审模型
    pass_k: int = Field(default=1, ge=1, le=20)  # Pass@k 重复执行次数


class RunUpdate(BaseModel):
    """草稿任务编辑:字段均为可选,仅更新显式传入的字段。"""
    name: str | None = Field(default=None, max_length=256)
    agent_id: int | None = None
    caseset_version_id: int | None = None
    weight_template_id: int | None = None
    review_policy: Literal["skip", "sample", "full"] | None = None
    fail_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    judge_config_id: int | None = None
    pass_k: int | None = Field(default=None, ge=1, le=20)


class CaseExecutionOut(BaseModel):
    id: int
    run_id: int
    testcase_id: int
    order_no: int
    status: str  # pending/running/succeeded/failed/canceled
    rerun_count: int
    repetition_no: int = 0  # Pass@k 重复序号
    resource_usage: dict[str, Any]
    error: str
    started_at: datetime | None
    finished_at: datetime | None
    testcase_title: str = ""
    difficulty: str = ""

    model_config = {"from_attributes": True}


class RunProgress(BaseModel):
    total: int = 0
    pending: int = 0
    running: int = 0
    succeeded: int = 0
    failed: int = 0
    canceled: int = 0


class RunOut(BaseModel):
    id: int
    name: str
    agent_id: int
    agent_name: str = ""
    agent_version_label: str = ""
    caseset_version_id: int
    caseset_name: str = ""
    caseset_version_no: int = 0
    weight_template_id: int
    weight_snapshot: dict[str, Any]
    rootcause_version: int
    review_policy: str
    fail_threshold: float
    baseline_run_id: int | None
    pass_k: int = 1
    judge_config_id: int | None = None
    judge_config_name: str = ""
    status: str
    error: str
    created_by: str
    created_at: datetime
    submitted_at: datetime | None
    completed_at: datetime | None
    progress: RunProgress = RunProgress()
    # M6:API 型 Agent 数据缺失/成本计量缺失预警(创建时即提示)
    warning: str = ""

    model_config = {"from_attributes": True}


class RunDetailOut(RunOut):
    cases: list[CaseExecutionOut] = []


class TraceEventOut(BaseModel):
    id: int
    seq: int
    event_type: str
    turn: int = 0  # 轮次:表无独立列,序列化时从 payload.turn 提取(系统事件无轮次为 0)
    payload: dict[str, Any]
    truncated: bool
    created_at: datetime

    model_config = {"from_attributes": True}


# ---------- LLM-as-Judge 评审模型配置(FR-05,M7,支持多套) ----------
JUDGE_DIMENSIONS = Literal["D4", "D7", "D8"]


class JudgeConfigOut(BaseModel):
    id: int
    name: str
    enabled: bool
    is_default: bool
    provider: str
    base_url: str
    model: str
    api_key_masked: str
    has_api_key: bool
    temperature: float
    max_tokens: int
    timeout_seconds: int
    dimensions: list[str]
    rubrics: dict[str, str]
    updated_by: str
    created_at: datetime | None = None
    updated_at: datetime | None = None


class JudgeConfigCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    enabled: bool = False
    is_default: bool = False
    provider: Literal["openai_compatible"] = "openai_compatible"
    base_url: str = Field(default="", max_length=256)
    model: str = Field(default="", max_length=128)
    api_key: str = Field(default="", max_length=512)
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    max_tokens: int = Field(default=800, ge=64, le=8192)
    timeout_seconds: int = Field(default=60, ge=5, le=600)
    dimensions: list[JUDGE_DIMENSIONS] = ["D4", "D7", "D8"]
    rubrics: dict[str, str] = Field(default_factory=dict)


class JudgeConfigUpdate(BaseModel):
    """编辑配置:api_key 留空表示保持原密钥不变。"""
    name: str | None = Field(default=None, min_length=1, max_length=64)
    enabled: bool | None = None
    is_default: bool | None = None
    provider: Literal["openai_compatible"] | None = None
    base_url: str | None = Field(default=None, max_length=256)
    model: str | None = Field(default=None, max_length=128)
    api_key: str | None = Field(default=None, max_length=512)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=64, le=8192)
    timeout_seconds: int | None = Field(default=None, ge=5, le=600)
    dimensions: list[JUDGE_DIMENSIONS] | None = None
    rubrics: dict[str, str] | None = None


class JudgeTestIn(BaseModel):
    """连通性测试:可传未保存的表单参数;api_key 留空则用已保存密钥。"""
    id: int | None = None
    base_url: str = ""
    model: str = ""
    api_key: str = ""
    timeout_seconds: int = Field(default=30, ge=5, le=600)


class JudgeTestOut(BaseModel):
    passed: bool
    message: str
    latency_ms: int = 0



class ExecutionDetailOut(CaseExecutionOut):
    tags: list[str] = []
    est_minutes: int = 0
    # 投递给模型的任务脚本(每轮 prompt)与期望输出(验收命令/语义验收点/过程期望)
    task_script: list[dict[str, Any]] = []
    acceptance: dict[str, Any] = {}


# ---------- 维度评分(FR-05,M3) ----------
class DimensionScoreOut(BaseModel):
    dimension: str  # D1..D8
    score: float  # 0-5
    details: dict[str, Any] = {}

    model_config = {"from_attributes": True}


class CaseScoresOut(BaseModel):
    execution_id: int
    testcase_id: int
    testcase_title: str = ""
    status: str
    quality_score: float | None = None  # 百分制
    efficiency_score: float | None = None  # 百分制(D6 折算)
    dimensions: dict[str, float | None] = {}


class RunScoresOut(BaseModel):
    run_id: int
    quality_score: float | None = None
    efficiency_score: float | None = None
    dimension_summary: dict[str, Any] = {}
    cases: list[CaseScoresOut] = []


# ---------- 归因结论(FR-06,M4) ----------
class RootCauseRefOut(BaseModel):
    id: int
    code: str
    name: str
    level: int
    responsibility: str


class AttributionOut(BaseModel):
    id: int
    execution_id: int
    rootcause: RootCauseRefOut
    is_primary: bool
    confidence: float
    source: str  # rule / llm / human
    evidence: dict[str, Any] = {}
    review_status: str  # pending / confirmed / corrected / rejected / not_sampled
    reviewed_by: str = ""
    review_note: str = ""
    review_history: list[dict[str, Any]] = []


class CaseAttributionOut(BaseModel):
    execution_id: int
    testcase_id: int
    testcase_title: str = ""
    status: str
    primary: dict[str, Any] | None = None
    secondary: list[dict[str, Any]] = []


class UndeterminedCaseOut(BaseModel):
    execution_id: int
    testcase_id: int
    testcase_title: str = ""
    signals: list[str] = []
    reason: str = ""


class RunAttributionOut(BaseModel):
    run_id: int
    rootcause_version: int
    review_policy: str = "skip"
    status: str = ""
    total_attributions: int = 0
    attributed_cases: int = 0
    pending_review: int = 0
    not_sampled: int = 0
    review_mark: str = "unreviewed"
    distribution: list[dict[str, Any]] = []
    level1_summary: dict[str, int] = {}
    responsibility_summary: dict[str, int] = {}
    undetermined: list[UndeterminedCaseOut] = []
    cases: list[CaseAttributionOut] = []


class AttributionReviewIn(BaseModel):
    action: Literal["confirm", "correct", "reject"]
    rootcause_id: int | None = None  # correct 时必填:改指向的根因条目
    note: str = ""  # 修正/误评时建议填写理由
    reviewed_by: str = "evaluator"


# ---------- M6:通知/权限/评论/保留策略/统计 ----------
class NotificationOut(BaseModel):
    id: int
    category: str  # run_status / agent_alert / rc10_candidate / review_overdue
    title: str
    content: str
    link: str
    read_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class NotificationListOut(BaseModel):
    unread: int = 0
    items: list[NotificationOut] = []


class AttributionCommentOut(BaseModel):
    id: int
    attribution_id: int
    author: str
    role: str
    content: str
    created_at: datetime

    model_config = {"from_attributes": True}


class AttributionCommentIn(BaseModel):
    content: str = Field(min_length=1, max_length=2000)


class UserOut(BaseModel):
    id: int
    username: str
    display_name: str
    role: str  # admin / evaluator / viewer

    model_config = {"from_attributes": True}


class RetentionOut(BaseModel):
    retention_days: int
    trace_events_total: int
    trace_events_expired: int
    reports_total: int
    note: str


class RetentionPurgeOut(BaseModel):
    removed: int
    retention_days: int


class TrendPointOut(BaseModel):
    run_id: int
    name: str
    agent_name: str
    caseset_name: str
    completed_at: str
    quality_score: float | None
    efficiency_score: float | None


class TrendOut(BaseModel):
    agent_id: int | None
    agent_name: str
    points: list[TrendPointOut]


class FalsePositiveItemOut(BaseModel):
    rootcause_id: int
    code: str
    name: str
    level: int
    enabled: bool
    confirmed: int
    corrected: int
    rejected: int
    reviewed: int
    error_rate: float | None
    suggestion: str


class FalsePositiveStatsOut(BaseModel):
    overall: dict[str, Any]
    items: list[FalsePositiveItemOut]
