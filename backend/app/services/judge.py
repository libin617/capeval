"""LLM-as-Judge(FR-05):对难以规则化的维度(D4/D7/D8)做语义评审。

配置来源:llm_judge_configs 表(前端「评审模型」页签维护,支持多套配置)。
评测集版本可绑定默认评审模型,评测任务创建时继承并可覆盖;评分前按
任务→评测集版本→默认配置的优先级装配。未启用或调用失败时一律返回 None,
引擎自动回退规则近似评分,并在 details.note 标注「LLM Judge 未启用」。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Protocol, runtime_checkable

from sqlalchemy.orm import Session

# Judge 评审上下文:轨迹事件摘要 + 用例信息,由评分引擎组装
JudgeContext = dict[str, Any]

# 各维度的默认评审标准(配置未自定义时使用,拼入 system prompt)
DIMENSION_RUBRIC: dict[str, str] = {
    "D4": ("多轮会话一致性:考察 Agent 在跨轮对话中是否保持上下文、是否正确承接上一轮的"
           "文件改动/约束,是否出现前后矛盾、遗忘既定要求、重复询问已确认信息。"),
    "D7": ("代码风格与可维护性:命名是否清晰一致、结构是否合理、有没有明显冗余代码、"
           "是否遵循项目既有风格、注释是否恰当(不过度、不缺失)。"),
    "D8": ("工程化程度:是否考虑错误处理与边界条件、是否做了必要验证(运行/测试)、"
           "改动范围是否克制(不无关重构)、是否引入硬编码或临时调试代码。"),
}

OUTPUT_SCHEMA = """严格返回 JSON,不要任何额外文本:
{"score": <0-5 的数字,可含一位小数>, "rationale": "<50 字以内的评审结论>",
 "signals": [{"signal": "<短英文标识>", "verdict": "pass|warn|fail|info",
              "detail": "<中文说明>", "deduction": <扣分数,0-3>}]}"""


@runtime_checkable
class BaseJudge(Protocol):
    """LLM 评审协议:对指定维度给出 0-5 分,返回 None 表示无法评审(回退规则)。"""

    def judge_dimension(self, dimension: str, context: JudgeContext) -> dict[str, Any] | None:
        """返回 {"score": 0-5, "rationale": "...", "signals": [...]} 或 None。"""
        ...

    def judge_attribution(self, context: JudgeContext,
                          candidate_codes: list[str]) -> list[dict[str, Any]] | None:
        """对规则未覆盖的失分做语义归因,只能在 candidate_codes 内选择。"""
        ...


class NullJudge:
    """默认 Judge:不启用 LLM,一律返回 None,由引擎回退规则近似/规则归因。"""

    def judge_dimension(self, dimension: str, context: JudgeContext) -> dict[str, Any] | None:
        return None

    def judge_attribution(self, context: JudgeContext,
                          candidate_codes: list[str]) -> list[dict[str, Any]] | None:
        return None


def _extract_json(text: str) -> dict[str, Any] | None:
    """从模型输出中提取首个 JSON 对象(容忍 ```json 围栏与前后噪声)。"""
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1] if raw.count("```") >= 2 else raw.strip("`")
        if raw.lstrip().startswith("json"):
            raw = raw.lstrip()[4:]
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        obj = json.loads(raw[start:end + 1])
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def _normalize_judgement(obj: dict[str, Any]) -> dict[str, Any] | None:
    """校验并规整评分结果;score 非法视为评审失败(回退规则)。"""
    try:
        score = float(obj.get("score"))
    except (TypeError, ValueError):
        return None
    score = max(0.0, min(5.0, score))
    signals: list[dict[str, Any]] = []
    for s in obj.get("signals") or []:
        if not isinstance(s, dict) or not s.get("signal"):
            continue
        verdict = str(s.get("verdict") or "info")
        if verdict not in ("pass", "warn", "fail", "info"):
            verdict = "info"
        try:
            deduction = float(s.get("deduction") or 0)
        except (TypeError, ValueError):
            deduction = 0.0
        signals.append({"signal": str(s["signal"]), "verdict": verdict,
                        "detail": str(s.get("detail") or ""), "deduction": deduction})
    return {"score": score, "rationale": str(obj.get("rationale") or ""), "signals": signals}


class HttpJudge:
    """OpenAI 兼容 Chat Completions 实现的 LLM Judge。

    适用于 OpenAI / DeepSeek / Moonshot / 通义 / Qwen 等兼容
    `POST {base_url}/chat/completions` 的服务;鉴权走 Bearer。
    """

    def __init__(self, config: dict[str, Any]) -> None:
        self.base_url = str(config.get("base_url") or "").rstrip("/")
        self.model = str(config.get("model") or "")
        self.api_key = str(config.get("api_key") or "")
        self.temperature = float(config.get("temperature") or 0.2)
        self.max_tokens = int(config.get("max_tokens") or 800)
        self.timeout = int(config.get("timeout_seconds") or 60)
        self.dimensions = {str(d) for d in (config.get("dimensions") or [])} or set(DIMENSION_RUBRIC)
        # 维度级评分标准(可配置);缺失时回退内置默认 rubric
        self.rubrics = {str(k): str(v) for k, v in (config.get("rubrics") or {}).items() if v}

    @property
    def ready(self) -> bool:
        return bool(self.base_url and self.model and self.api_key)

    def _chat(self, system_prompt: str, user_prompt: str) -> str:
        """调用 chat/completions,返回首个 choice 的文本内容。"""
        body = {
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(body).encode("utf-8"), method="POST",
        )
        req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", f"Bearer {self.api_key}")
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError("评审模型未返回 choices")
        return str((choices[0].get("message") or {}).get("content") or "")

    def ping(self) -> str:
        """连通性测试:最小请求验证 base_url/模型/密钥可用。"""
        text = self._chat("你是连通性测试助手。", "只回复 OK")
        return text.strip()[:200]

    def judge_dimension(self, dimension: str, context: JudgeContext) -> dict[str, Any] | None:
        rubric = self.rubrics.get(dimension) or DIMENSION_RUBRIC.get(dimension)
        if not rubric or not self.ready or dimension not in self.dimensions:
            return None
        system_prompt = (
            "你是 Code Agent 评测平台的资深评审专家,负责对单条评测用例的执行轨迹做语义评分。\n"
            f"评测维度 {dimension} 的评分标准(rubric):{rubric}\n"
            "评分区间 0-5,5 表示完全符合,3 表示基本符合但存在明显瑕疵,0 表示严重不达标。"
            "只依据给定轨迹证据与用例语义验收点判断,证据不足时应给中性分并在 rationale 中说明。"
            "若 context 中包含 semantic_points(本用例的语义验收点),必须逐条核对是否满足,"
            "并作为评分的重要依据。\n"
            + OUTPUT_SCHEMA
        )
        user_prompt = "执行轨迹摘要如下(JSON):\n" + json.dumps(context, ensure_ascii=False, default=str)[:12000]
        try:
            raw = self._chat(system_prompt, user_prompt)
        except (urllib.error.URLError, TimeoutError, RuntimeError, ValueError, json.JSONDecodeError):
            return None  # 评审失败回退规则,不阻断评分流程(含非法地址 ValueError)
        obj = _extract_json(raw)
        return _normalize_judgement(obj) if obj else None

    def judge_attribution(self, context: JudgeContext,
                          candidate_codes: list[str]) -> list[dict[str, Any]] | None:
        """LLM 归因当前未启用:返回 None 由规则归因兜底(保持既有归因口径)。"""
        return None


_judge: BaseJudge | None = None


def get_judge() -> BaseJudge:
    """返回当前装配的 Judge;未配置时为 NullJudge(规则近似)。"""
    return _judge if _judge is not None else NullJudge()


def set_judge(judge: BaseJudge | None) -> None:
    """注入自定义 Judge(测试或配置装配)。"""
    global _judge
    _judge = judge


def _config_dict(row: Any) -> dict[str, Any]:
    return {
        "base_url": row.base_url, "model": row.model, "api_key": row.api_key,
        "temperature": row.temperature, "max_tokens": row.max_tokens,
        "timeout_seconds": row.timeout_seconds, "dimensions": row.dimensions,
        "rubrics": row.rubrics or {},
    }


def resolve_for_run(db: Session, run: Any) -> BaseJudge:
    """按任务→评测集版本→默认配置的优先级装配评审器。"""
    from app.models import CaseSetVersion, LlmJudgeConfig

    config_id = getattr(run, "judge_config_id", None)
    if not config_id:
        version = db.get(CaseSetVersion, getattr(run, "caseset_version_id", 0))
        config_id = version.judge_config_id if version else None
    row = db.get(LlmJudgeConfig, config_id) if config_id else _default_config(db)
    if row is None or not row.enabled:
        judge: BaseJudge = NullJudge()
    else:
        candidate = HttpJudge(_config_dict(row))
        judge = candidate if candidate.ready else NullJudge()
    set_judge(judge)
    return judge


def _default_config(db: Session) -> Any:
    from app.models import LlmJudgeConfig

    return (
        db.query(LlmJudgeConfig)
        .filter(LlmJudgeConfig.enabled.is_(True))
        .order_by(LlmJudgeConfig.is_default.desc(), LlmJudgeConfig.id.asc())
        .first()
    )


def configure_from_db(db: Session, config_id: int | None = None) -> BaseJudge:
    """按指定配置或默认配置装配 Judge;配置无效时回退 NullJudge。"""
    from app.models import LlmJudgeConfig

    row = db.get(LlmJudgeConfig, config_id) if config_id else _default_config(db)
    if row is None or not row.enabled:
        judge: BaseJudge = NullJudge()
    else:
        candidate = HttpJudge(_config_dict(row))
        judge = candidate if candidate.ready else NullJudge()
    set_judge(judge)
    return judge

