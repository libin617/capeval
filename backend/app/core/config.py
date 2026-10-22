from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # ---- 基础 ----
    app_name: str = "CapEval"
    env: str = "dev"

    # MySQL, e.g. mysql+pymysql://root:password@127.0.0.1:3306/capeval?charset=utf8mb4
    database_url: str = "mysql+pymysql://root:password@127.0.0.1:3306/capeval?charset=utf8mb4"

    # 凭证加解密密钥(Fernet),生产环境必须通过环境变量注入
    credential_key: str = ""

    # 冒烟校验超时(秒):Agent 首次连通性探针(如 --version)的最长等待时间
    smoke_timeout_seconds: int = 30

    # 数据保留(PRD 7.4):原始轨迹事件保留天数,到期清理时保留证据链片段;报告永久保留
    trace_retention_days: int = 180

    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # ---- 执行器 / 临时目录 ----
    # 轨迹事件 payload 落库前的截断上限(字符);超出会整体 shrunk 并标记 truncated=True
    trace_payload_max_chars: int = 8000
    # 单条验收命令的最长执行时间(秒),超时直接判该命令失败;host/docker 模式均生效
    command_timeout_seconds: int = 120
    # 环境准备命令(install_commands)的最长执行时间(秒);通常比验收命令长(如 npm install / mvn install)
    install_timeout_seconds: int = 600
    # 临时目录前缀:每条评测用例执行前,平台在系统 tempdir 下创建工作目录时使用的前缀
    temp_dir_prefix: str = "capeval-"

    # ---- Docker 隔离 ----
    # 用例常驻容器名前缀;整条用例生命周期内只启一个容器,环境准备与验收命令共用
    container_name_prefix: str = "capeval-case-"
    # 容器保活命令(传入 image 后常驻执行);容器需持续存活以便 docker exec 逐条执行命令
    container_keepalive_cmd: str = "while true; do sleep 3600; done"
    # docker run 启动容器的超时(秒);超时判定镜像不存在或 pull 太慢,用例直接判失败
    docker_run_timeout_seconds: int = 120
    # docker rm -f 强制清理容器的超时(秒);清理失败不影响用例结论,仅做告警日志
    docker_rm_timeout_seconds: int = 30

    # ---- Agent 资源限制兜底 ----
    # 当 Agent.resource_limits 未配置 max_case_minutes 时,单用例最长执行时间的默认值(分钟)
    default_case_minutes: float = 30.0
    # Agent 资源限制里 max_turns 未配置时,单用例最大交互轮次(turn)的兜底
    default_max_turns: int = 100

    # ---- CLI 型适配器默认参数 ----
    # CLI 型适配器:发送一条 prompt 后,首次等待 Agent 输出的时间(秒);Agent adapter_config 可覆盖
    cli_response_wait_seconds: float = 5.0
    # CLI 型适配器:读到首批输出后,后续持续读取 stdout 的轮询间隔(秒)
    cli_read_interval_seconds: float = 0.3
    # CLI/HTTP 型适配器在 terminate() 后优雅等待进程退出的时间(秒);超时后发 SIGKILL
    terminate_wait_timeout_seconds: int = 5
    # HTTP 型适配器:单条 POST /sessions 或 POST /messages 请求的超时(秒);Agent adapter_config 可覆盖
    http_adapter_timeout_seconds: float = 120
    # 单次工具调用被判定为"无效重试"前的重复出现阈值:同名 + 同 args 出现 ≥N 次即触发扣分信号
    tool_call_repeat_threshold: int = 3

    # ---- 调度器 ----
    # 后台调度器轮询 queued Run 并派发执行的间隔(秒)
    scheduler_poll_seconds: float = 2.0

    # ---- LLM Judge ----
    # 评审模型收到的"执行轨迹摘要"上下文长度上限(字符);超出会整体截断,避免超出模型 token 上限
    judge_context_max_chars: int = 12000

    # ---- 评分(D6 token 效率分档) ----
    # D6 维度:实际 token 消耗 / 用例基线(token_baseline) 的分档边界(倍数)
    # bands = [1.2, 2.0, 3.0, 4.0] 对应 score:
    #   ratio ≤ 1.2 → 5 分, ≤ 2.0 → 4 分, ≤ 3.0 → 3 分, ≤ 4.0 → 2 分, 其余 → 1 分
    d6_ratio_bands: list[float] = [1.2, 2.0, 3.0, 4.0]

    # ---- 复核 ----
    # sample 抽样策略下,低于该置信度的归因结论必须进入人工复核(不随机抽样)
    review_low_confidence: float = 0.7
    # sample 抽样策略下,低置信度 / LLM 结论之外的剩余结论的随机抽样比例
    review_sample_ratio: float = 0.5
    # Run 进入 reviewing 状态后,超过该天数仍未完成复核 → 给创建人 + evaluator 角色发催办通知
    review_overdue_days: int = 3

    # ---- RC-10 集体性失败探测 ----
    # 同一用例在 ≥N 个不同 Agent 的 Run 中均失分,且根因各异,才判定为 RC-10 候选
    rc10_min_agents: int = 3
    # 触发 RC-10 时,历史 + 本次至少需要出现 ≥N 种不同的主根因
    rc10_min_distinct_rootcauses: int = 2
    # D1 代码正确性分数低于该值即视为"该用例在该 Run 中失分"(与执行失败并列判定)
    rc10_d1_fail_score: float = 3.0

    # ---- 报表 ----
    # PDF 渲染优先使用的 Chrome/Chromium 可执行文件路径;为空则 fallback 内置默认路径列表
    chrome_bin: str = ""
    # 无头 Chrome 把 HTML 报告打印为 PDF 的超时(秒);超时判 ExportError 并返回 503
    pdf_render_timeout_seconds: int = 90
    # 报告"失败分析 TOP-N"章节展示的条目上限
    top_failure_count: int = 10

    # ---- 分享 ----
    # 分享链接的默认免登录有效期(天)
    share_default_days: int = 7
    # 分享链接允许设置的最大有效期(天);防止误用超长有效期
    share_max_days: int = 30

    # ---- 通知列表 ----
    # 通知列表查询接口未显式传 limit 时的默认返回条数
    notification_default_limit: int = 50
    # 通知列表查询接口允许的最大 limit,硬上限防止一次拉爆
    notification_max_limit: int = 200

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
