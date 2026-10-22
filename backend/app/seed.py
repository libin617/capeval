"""种子数据(幂等):
1. 默认八维权重模板(PRD FR-03);
2. 初始三级根因库(PRD FR-06 表,库版本 v1);
3. 官方冒烟用例 + 官方冒烟用例集(已发布 v1);
4. 内置回声 Agent(已通过校验,便于演示)。

用法: python -m app.seed
"""

from app.core.database import SessionLocal
from app.models import (
    Agent, CaseSet, CaseSetItem, CaseSetVersion, RootCause, RootCauseLibraryVersion,
    TestCase, User, WeightTemplate,
)

DEFAULT_WEIGHTS = {"D1": 0.30, "D2": 0.12, "D3": 0.12, "D4": 0.12, "D5": 0.12,
                   "D6": 0.08, "D7": 0.08, "D8": 0.06}

# (code, level, parent_code, name, description, responsibility, related_dimensions, rules)
# rules: 规则信号绑定列表 [{"signal": <评分信号名>, "primary": <是否主根因提示>}],
#        由归因引擎(FR-06)按评分明细 signals 命中;为空表示仅可被人工复核选用。
ROOT_CAUSES: list[tuple[str, int, str | None, str, str, str, list[str], list[dict]]] = [
    ("RC-1", 1, None, "需求理解", "对任务目标与约束的认知偏差", "agent", ["D1"], []),
    ("RC-1.1", 2, "RC-1", "需求要点遗漏", "验收点未被覆盖且轨迹中无对应探索", "agent", ["D1"], []),
    ("RC-1.1.1", 3, "RC-1.1", "显性验收点遗漏", "需求明文列出的条目未实现", "agent", ["D1"],
     [{"signal": "acceptance_failed", "primary": True}]),
    ("RC-1.1.2", 3, "RC-1.1", "隐含约束遗漏", "性能/兼容性/风格等未言明但行业默认的约束被忽视", "agent", ["D1", "D7"],
     [{"signal": "trailing_whitespace", "primary": True}, {"signal": "mixed_indent", "primary": False},
      {"signal": "hardcoded_endpoint", "primary": False}]),
    ("RC-1.2", 2, "RC-1", "需求曲解", "实现方向与需求描述相悖", "agent", ["D1"], []),
    ("RC-1.2.1", 3, "RC-1.2", "功能方向性误解", "理解反了需求语义", "agent", ["D1"], []),
    ("RC-1.2.2", 3, "RC-1.2", "过度实现(镀金)", "实现超出需求范围并引入风险/成本", "agent", ["D1", "D6"],
     [{"signal": "excessive_file_changes", "primary": True}]),
    ("RC-2", 1, None, "上下文管理", "会话与文件上下文的保持失败", "agent", ["D4"], []),
    ("RC-2.1", 2, "RC-2", "已确认事实遗忘", "违背前轮用户已确认的决策", "agent", ["D4"], []),
    ("RC-2.1.1", 3, "RC-2.1", "用户决策遗忘", "如用户说“用 PostgreSQL”,后续却用了 SQLite", "agent", ["D4"], []),
    ("RC-2.1.2", 3, "RC-2.1", "已读内容遗忘", "刚读过的文件内容在后续修改中被违背", "agent", ["D4", "D1"], []),
    ("RC-2.2", 2, "RC-2", "上下文截断丢失", "长会话/大文件导致信息丢失", "agent", ["D4"], []),
    ("RC-2.2.1", 3, "RC-2.2", "长会话信息丢失", "轮次增加后重复询问已知信息/重复劳动", "agent", ["D4", "D6"],
     [{"signal": "partial_response", "primary": True}]),
    ("RC-2.2.2", 3, "RC-2.2", "大文件截断误用", "基于截断内容对大文件做出错误修改", "agent", ["D4", "D1"], []),
    ("RC-3", 1, None, "规划与推理", "行动前的计划与方案缺陷", "agent", ["D2"], []),
    ("RC-3.1", 2, "RC-3", "计划缺失", "无计划直接编码,导致返工", "agent", ["D2"], []),
    ("RC-3.2", 2, "RC-3", "方案选型失误", "技术路线判断错误", "agent", ["D1", "D2"], []),
    ("RC-3.2.1", 3, "RC-3.2", "选型与约束冲突", "选用的库/方案与项目约束不兼容", "agent", ["D1"], []),
    ("RC-3.2.2", 3, "RC-3.2", "修改范围误判", "漏改关联文件/改错位置,引发连锁错误", "agent", ["D1", "D2"], []),
    ("RC-4", 1, None, "工具使用", "工具选择、参数与时序问题", "agent", ["D3"], []),
    ("RC-4.1", 2, "RC-4", "工具误选", "意图与所选工具不匹配", "agent", ["D3"], []),
    ("RC-4.2", 2, "RC-4", "参数错误", "调用参数不合法或不恰当", "agent", ["D3"],
     [{"signal": "tool_call_failed", "primary": False}]),
    ("RC-4.2.1", 3, "RC-4.2", "路径/标识误用", "相对绝对路径混淆、文件标识错误", "agent", ["D3"], []),
    ("RC-4.2.2", 3, "RC-4.2", "schema 违规", "参数类型/结构不满足工具 schema", "agent", ["D3"], []),
    ("RC-4.3", 2, "RC-4", "时序错误", "调用顺序反模式", "agent", ["D3", "D2"], []),
    ("RC-4.3.1", 3, "RC-4.3", "写前未读", "未读取即覆盖修改文件", "agent", ["D3", "D2"], []),
    ("RC-4.3.2", 3, "RC-4.3", "盲目重试", "相同参数失败 ≥3 次无策略调整", "agent", ["D3", "D6"],
     [{"signal": "repeated_tool_call", "primary": True}]),
    ("RC-4.4", 2, "RC-4", "失败未恢复", "工具报错被无视,继续推进", "agent", ["D3", "D1"],
     [{"signal": "tool_call_failed", "primary": True}]),
    ("RC-5", 1, None, "代码生成能力", "产物本身的缺陷", "agent", ["D1"], []),
    ("RC-5.1", 2, "RC-5", "语法/编译错误", "编译或解释失败", "agent", ["D1"], []),
    ("RC-5.2", 2, "RC-5", "逻辑错误", "功能逻辑不正确", "agent", ["D1"],
     [{"signal": "acceptance_failed", "primary": False}, {"signal": "execution_failed", "primary": True}]),
    ("RC-5.2.1", 3, "RC-5.2", "边界条件缺失", "空值/溢出/并发边界未处理", "agent", ["D1"], []),
    ("RC-5.2.2", 3, "RC-5.2", "业务逻辑错误", "核心路径逻辑与需求不符", "agent", ["D1"], []),
    ("RC-5.3", 2, "RC-5", "回归引入", "破坏既有功能", "agent", ["D1"], []),
    ("RC-6", 1, None, "验证缺失", "交付前后的自检问题", "agent", ["D2"], []),
    ("RC-6.1", 2, "RC-6", "未执行验证", "轨迹中无任何测试/构建事件即声明完成", "agent", ["D2"],
     [{"signal": "no_tool_calls", "primary": True}]),
    ("RC-6.2", 2, "RC-6", "验证失败未处理", "测试失败后直接声明完成或绕过验证", "agent", ["D2", "D1"], []),
    ("RC-6.2.1", 3, "RC-6.2", "篡改验证以通过", "删除/弱化断言、注释失败测试(严重)", "agent", ["D2", "D5"], []),
    ("RC-7", 1, None, "安全与合规", "安全维度的根因(对应 D5)", "agent", ["D5"], []),
    ("RC-7.1", 2, "RC-7", "漏洞引入", "注入/反序列化/路径穿越等扫描命中", "agent", ["D5"], []),
    ("RC-7.1.1", 3, "RC-7.1", "注入类漏洞", "SQL/命令/模板注入", "agent", ["D5"],
     [{"signal": "dangerous_command", "primary": True}]),
    ("RC-7.1.2", 3, "RC-7.1", "不安全 API 使用", "已知危险的函数/配置误用", "agent", ["D5"], []),
    ("RC-7.2", 2, "RC-7", "敏感信息泄露", "硬编码密钥/日志泄露/越权访问尝试", "agent", ["D5"],
     [{"signal": "sensitive_info", "primary": True}]),
    ("RC-8", 1, None, "效率失控", "资源消耗维度的根因(对应 D6)", "agent", ["D6"], []),
    ("RC-8.1", 2, "RC-8", "循环空转", "同状态重复调用,无信息增量", "agent", ["D6"],
     [{"signal": "max_turns_exceeded", "primary": True}, {"signal": "case_timeout", "primary": False}]),
    ("RC-8.2", 2, "RC-8", "冗余探索", "大范围无目的读取/搜索,远超任务所需", "agent", ["D6"],
     [{"signal": "token_efficiency", "primary": True}, {"signal": "excessive_file_changes", "primary": False}]),
    ("RC-9", 1, None, "环境与接入", "沙箱故障/依赖安装失败/凭证失效/Agent 无响应/费用超限", "platform", [],
     [{"signal": "env_setup_failed", "primary": True}, {"signal": "platform_error", "primary": True},
      {"signal": "no_response", "primary": True}, {"signal": "case_timeout", "primary": False},
      {"signal": "auth_failed", "primary": True}, {"signal": "cost_limit_exceeded", "primary": True}]),
    ("RC-10", 1, None, "用例缺陷", "验收条件错误/环境定义错误/需求描述歧义", "case_maintainer", [], []),
]

SMOKE_CASE = {
    "title": "官方冒烟用例:最小交互",
    "task_script": [{"turn": 1, "message": "ping: please reply 'pong'", "intent_tag": ""}],
    "repo_snapshot": {"type": "files", "files": {}},
    "env_def": {"runtime": "none", "install_commands": []},
    "acceptance": {"auto_commands": [], "semantic_points": ["Agent 对 ping 消息给出响应"]},
    "efficiency_baseline": {"input_tokens": 50, "output_tokens": 50, "tool_calls": 0, "wall_seconds": 30},
    "difficulty": "easy",
    "tags": ["冒烟"],
    "est_minutes": 1,
}


def seed() -> None:
    db = SessionLocal()
    try:
        # 1. 默认权重模板
        if not db.query(WeightTemplate).filter_by(name="默认八维权重").first():
            db.add(WeightTemplate(name="默认八维权重", is_default=True, weights=DEFAULT_WEIGHTS))

        # 2. 初始三级根因库(库版本 v1)
        if not db.query(RootCauseLibraryVersion).filter_by(version_no=1).first():
            db.add(RootCauseLibraryVersion(version_no=1, note="初始内置根因库(PRD FR-06)"))
        existing = {rc.code: rc for rc in db.query(RootCause).all()}
        # M6 信号扩充:对历史库中 RC-9 增量回填新增信号(鉴权失败/费用超限),不覆盖在线维护结果
        rc9 = existing.get("RC-9")
        if rc9 is not None:
            signals = dict(rc9.detection_signals or {})
            rules = [r for r in (signals.get("rules") or [])]
            bound = {r.get("signal") if isinstance(r, dict) else r for r in rules}
            new_rules = [{"signal": s, "primary": True}
                         for s in ("auth_failed", "cost_limit_exceeded") if s not in bound]
            if new_rules:
                signals["rules"] = rules + new_rules
                rc9.detection_signals = signals
        code_to_id: dict[str, int] = {}
        for code, level, parent_code, name, desc, resp, dims, rules in ROOT_CAUSES:
            if code in existing:
                rc = existing[code]
                code_to_id[code] = rc.id
                # 回填:仅当历史数据尚未绑定信号时补写规则,避免覆盖在线维护(FR-07)的结果
                signals = dict(rc.detection_signals or {})
                if not signals.get("rules") and rules:
                    signals["rules"] = list(rules)
                    signals.setdefault("llm_prompt", "")
                    signals.setdefault("related_dimensions", dims)
                    rc.detection_signals = signals
                continue
            rc = RootCause(
                code=code, level=level, parent_id=code_to_id.get(parent_code) if parent_code else None,
                name=name, description=desc, responsibility=resp,
                detection_signals={"rules": list(rules), "llm_prompt": "", "related_dimensions": dims},
                enabled=True, introduced_version=1,
            )
            db.add(rc)
            db.flush()
            code_to_id[code] = rc.id

        # 3. 官方冒烟用例 + 用例集(发布态)
        case = db.query(TestCase).filter_by(title=SMOKE_CASE["title"]).first()
        if case is None:
            case = TestCase(**SMOKE_CASE)
            db.add(case)
            db.flush()
        cs = db.query(CaseSet).filter_by(name="官方冒烟用例集").first()
        if cs is None:
            cs = CaseSet(name="官方冒烟用例集", description="Agent 接入校验与新用户演示(M1 内置 1 例,后续扩充 ≥5)", is_builtin=True)
            db.add(cs)
            db.flush()
            v1 = CaseSetVersion(caseset_id=cs.id, version_no=1, status="published")
            db.add(v1)
            db.flush()
            v1.items.append(CaseSetItem(testcase_id=case.id, order_no=1))

        # 4. 内置回声 Agent(已校验可用)
        if not db.query(Agent).filter_by(name="内置回声Agent", version_label="v1").first():
            db.add(Agent(name="内置回声Agent", version_label="v1", access_type="cli", adapter_key="echo_cli",
                         adapter_config={}, resource_limits={"max_case_minutes": 30, "max_turns": 100},
                         status="available"))

        # 5. 内置用户(M6 权限:管理员/评测工程师/只读成员,MVP 简化身份切换)
        for username, display, role in (
            ("admin", "管理员", "admin"),
            ("evaluator", "评测工程师", "evaluator"),
            ("viewer", "只读成员", "viewer"),
        ):
            if not db.query(User).filter_by(username=username).first():
                db.add(User(username=username, display_name=display, role=role))

        db.commit()
        print("seed ok:",
              f"weight_templates={db.query(WeightTemplate).count()}",
              f"root_causes={db.query(RootCause).count()}",
              f"testcases={db.query(TestCase).count()}",
              f"casesets={db.query(CaseSet).count()}",
              f"agents={db.query(Agent).count()}",
              f"users={db.query(User).count()}")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
