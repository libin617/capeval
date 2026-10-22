# CapEval 使用手册

> 面向 Code Agent 的工程化能力评测平台 · 从接入到出报告的完整操作指南
>
> 适用版本：当前 `main` 分支 · 文档更新：2026-10

---

## 目录

1. [这份文档怎么用](#1-这份文档怎么用)
2. [10 分钟跑通全链路](#2-10-分钟跑通全链路)
3. [部署与初始化](#3-部署与初始化)
4. [核心概念](#4-核心概念)
5. [角色与身份切换](#5-角色与身份切换)
6. [接入 Agent](#6-接入-agent)
7. [编写评测用例](#7-编写评测用例)
8. [组装评测集并发布版本](#8-组装评测集并发布版本)
9. [配置评审模型(可选)](#9-配置评审模型可选)
10. [创建与执行评测任务](#10-创建与执行评测任务)
11. [看报告、轨迹与对比](#11-看报告轨迹与对比)
12. [人工复核与归因](#12-人工复核与归因)
13. [运维与数据维护](#13-运维与数据维护)
14. [常见问题排查](#14-常见问题排查)
15. [最佳实践](#15-最佳实践)
16. [API 速查](#16-api-速查)

---

## 1. 这份文档怎么用

- **第一次使用**：按第 2 → 3 → 6 → 7 → 8 → 10 节顺序操作，即可完成第一轮真实评测。
- **只想快速验证部署**：直接看第 2 节的最小验证清单。
- **遇到报错**：先查第 14 节，大部分问题能直接定位。
- **想接入自己的 Agent**：重点看第 6 节的适配器选择与 HTTP 契约。

平台的核心工作流：

```
接入 Agent → 编写评测用例 → 组装评测集(发布版本) → 新建评测任务 → 执行 → 评分 → 归因 → 复核 → 报告/对比
```

前四步是一次性资产准备；之后每次评测只需「选 Agent + 选评测集版本 + 提交」。

## 2. 10 分钟跑通全链路

最小验证清单（不依赖真实 Agent，用 `echo_cli` 回显器验证平台链路）：

1. 启动前后端，打开 `http://127.0.0.1:5173`。
2. 「Agent 管理」→ 新建 Agent：
   - 接入类型 `cli`，适配器 `echo_cli`，版本标识随意填；
   - 保存后点「校验」，状态变为 `available`。
3. 「评测用例」→ 新建：
   - 任务脚本：`输出 hello`；
   - 自动化验收命令：`python3 hello.py | grep hello`；
   - 执行方式选「仅 Host」（echo 不产生文件，docker 无意义）；
   - 能力标签至少填一个。
4. 「评测集」→ 新建，选中该用例 → 版本管理 → 发布 `v1`。
5. 「评测任务」→ 新建，选该 Agent 与评测集 `v1`，用默认权重模板，点「创建并提交」。
6. 任务完成后打开「报告」与「轨迹回放」，确认状态机走完、事件时间线正常。

> `echo_cli` 只回显、不改文件，D1 不通过属于预期。验证真实能力请接入 Qoder CLI 或 HTTP API Agent。

## 3. 部署与初始化

### 3.1 环境要求

| 组件 | 版本 | 说明 |
| --- | --- | --- |
| Python | 3.12 | 后端 |
| Node.js | ≥ 20(建议 22 LTS) | 前端 |
| MySQL | 8.0 | 业务库，`utf8mb4` |
| Docker | ≥ 20(可选) | 用例选择 Docker 执行时必需 |
| Chrome/Chromium | 可选 | 报告导出 PDF |

### 3.2 初始化数据库

```sql
CREATE DATABASE capeval DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
```

### 3.3 后端

```bash
cd backend
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

新建 `backend/.env`：

```dotenv
DATABASE_URL=mysql+pymysql://<user>:<password>@127.0.0.1:3306/capeval?charset=utf8mb4
CREDENTIAL_KEY=<Fernet 密钥，生产必填>
SMOKE_TIMEOUT_SECONDS=30
TRACE_RETENTION_DAYS=180
CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
ENV=dev
```

```bash
alembic upgrade head     # 建表
python -m app.seed       # 幂等种子数据：根因库/权重模板/默认用户
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

验证：

```bash
curl http://127.0.0.1:8000/api/health
```

API 文档：`http://127.0.0.1:8000/api/docs`。

### 3.4 前端

```bash
cd frontend
npm install
npm run dev      # http://127.0.0.1:5173
```

生产构建：

```bash
npm run build    # 产物在 dist/
```

Nginx 托管 `dist/`，并把 `/api` 反向代理到后端 `:8000`。

## 4. 核心概念

| 概念 | 说明 |
| --- | --- |
| 评测用例(TestCase) | 最小执行单元：一份仓库快照 + 多轮任务脚本 + 验收标准 + 执行环境 |
| 评测集(CaseSet) | 用例的集合；**版本化**，草稿可编辑、发布后冻结 |
| 评测集版本 | 任务实际绑定的对象；引用顺序即执行顺序 |
| 评测任务(Run) | 一次评测；提交时固化权重模板、根因库版本与用例集版本 |
| 执行记录(Execution) | 一个用例在一次任务中的一次执行(Pass@k > 1 时会有多条) |
| 轨迹事件(TraceEvent) | `message / tool_call / file_change / terminal / resource / system` 六类 |
| 八维评分 | D1 正确性、D2 流程、D3 工具、D4 多轮、D5 安全、D6 效率、D7 风格、D8 工程化 |
| 归因(Attribution) | 用评分信号匹配根因库，给出主/次根因、置信度、责任方与证据事件序号 |
| 权重模板 | 八维权重，支持任意维度子集；选中维度权重和须为 1.0 |
| 根因库 | 三级自引用结构，内置条目可在线维护，版本会随任务固化 |

**两条隔离原则**：仓库快照是只读真相源，每次执行还原到独立临时目录；评测结束后临时目录整体删除，Agent 改动不会回流。

## 5. 角色与身份切换

| 角色 | 权限 |
| --- | --- |
| `admin` | 全部操作，含用户管理、数据清理 |
| `evaluator` | 创建/编辑/提交评测任务与资产，复核归因 |
| `viewer` | 只读 |

接口通过 `X-Acting-User` 头识别当前身份，前端右上角可切换。所有写操作都有角色守卫，`viewer` 调用写接口会返回 403。

## 6. 接入 Agent

### 6.1 操作步骤

1. 「Agent 管理」→ 新建，填写配置。
2. 保存后点「校验」做连通性冒烟检查（超时由 `SMOKE_TIMEOUT_SECONDS` 控制）。
3. 校验通过状态变为 `available`，才能被任务选用。
4. 修改配置或凭证后需重新校验。

### 6.2 字段说明

| 字段 | 说明 |
| --- | --- |
| 名称 / 版本标识 | 展示名与版本，报告会标注版本 |
| 接入类型 | `cli` 本地进程 / `api` 远程 HTTP |
| 适配器 | `echo_cli` / `generic_cli` / `qoder_cli` / `http_api` |
| 启动命令、工作目录 | CLI 类型必填 |
| API Endpoint | API 类型必填，如 `http://host:8080` |
| 鉴权方式 + 凭证 | `bearer` / `api_key`，凭证加密落库、页面掩码 |
| 数据暴露 | Agent 是否上报工具调用、token 用量 |
| token 单价 | API 类型用于成本回填 |
| 执行隔离 | `host` 或 `docker`；macOS 建议 docker |
| 资源限制 | 内存、CPU、最大轮次、单用例时长、费用上限 |

> Agent **不配置镜像**。Docker 模式下的镜像来自每条评测用例的「镜像」字段，便于同一 Agent 评测多语言项目。

### 6.3 适配器选择

| 适配器 | 适用场景 | 关键配置 |
| --- | --- | --- |
| `echo_cli` | 平台自测、链路联调 | 无需额外配置 |
| `generic_cli` | 任意本地 CLI | `start_command`(数组)、`probe_command`(可选) |
| `qoder_cli` | Qoder CLI | 默认 `qoderclicn`、`bypass_permissions`、`stream-json`；首轮 `--session-id`，后续 `-r` 续接 |
| `http_api` | 远程 Agent 服务 | `endpoint`、鉴权方式、`request_timeout` |

**Qoder CLI 必须非沙箱运行**：它需要读取 `~/.qoder-cn` 与 Keychain 凭证，沙箱会拦截导致会话立即失败。

### 6.4 HTTP API 契约

远程 Agent 需实现三个接口：

```http
POST   {endpoint}/sessions             # 创建会话，返回 session_id
POST   {endpoint}/messages             # 发送一轮消息，返回回复与事件
DELETE {endpoint}/sessions/{id}        # 结束会话
```

鉴权头与页面配置一致。响应建议携带 `tool_call / file_change / terminal / resource` 事件；缺失时 D3 降级中性分、D6 不评分，报告会在 `data_integrity` 中声明。

### 6.5 校验失败常见原因

| 现象 | 原因 |
| --- | --- |
| 校验超时 | 启动命令错误、进程未就绪、超时太短 |
| Qoder 会话秒失败 | 沙箱限制、凭证目录不可读、额度耗尽 |
| API 401/403 | 鉴权方式或凭证与 Agent 端不一致 |
| 状态一直是 `unavailable` | 校验未通过，或保存后未重新校验 |

## 7. 编写评测用例

### 7.1 一条好用例的标准

1. **目标单一**：只考察一个明确能力，失败原因可分离。
2. **可判定**：自动化验收或语义验收点至少一项。
3. **可复现**：仓库快照包含运行所需全部文件，不依赖内网资源。
4. **预埋 bug 有区分度**：初始状态应失败，正确修复后通过（可用自检验证）。

### 7.2 字段说明

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| 标题 | 是 | 用例名称 |
| 任务脚本 | 是 | 多轮 Prompt；多轮时每轮需 `intent_tag`，轮次从 1 连续编号 |
| 仓库快照 | 是 | 「相对路径 → 文件内容」，可由目录/ZIP 导入 |
| 执行方式 | 是 | 仅 Docker / 仅 Host / 任意 |
| 镜像 | 条件必填 | Docker 或任意模式必填，如 `python:3.12-slim`、`node:20-slim`、`maven:3.9-eclipse-temurin-17` |
| 附加卷 | 否 | 每行 `宿主机路径:容器路径`，用于依赖缓存 |
| 环境准备命令 | 否 | 安装依赖；每行一条，支持 `{"cmd":"...","expect_exit_code":0}` |
| 自动化验收命令 | 否* | 退出码等于期望值即通过（默认 0） |
| 语义验收点 | 否* | 自然语言验收要求，供 LLM 评审参考 |
| 流程规范期望 | 否 | 对研发流程的要求，用于 D2 |
| 标准答案(Gold Patch) | 否 | 参考 unified diff，用于与 Agent 产出对照 |
| 难度 / 标签 | 是 | `easy/medium/hard`，标签用于检索与统计 |
| 预估时长 | 否 | 用于超时与效率观测 |
| 效率基线 | 否 | `{"token_baseline": N}`，D6 依据；不填则 D6 仅记录消耗、不评分 |

\* 自动化验收与语义验收至少配置一项。

### 7.3 仓库快照

- **编写**：逐行添加文件路径与内容。路径必须非空、非绝对路径、不含 `..`、不可重复。
- **导入**：支持本地目录递归导入、ZIP 解压导入；自动过滤 `.git`、`node_modules`、缓存与二进制文件，路径自动相对化。
- **还原**：执行时写入独立临时目录；用例结束删除，原始数据零改动。

一个最小 Python 快照示例：

```json
{
  "type": "files",
  "files": {
    "app/__init__.py": "",
    "app/pricing.py": "def total(items):\n    return sum(i['price'] for i in items)\n",
    "tests/test_pricing.py": "from app.pricing import total\n\ndef test_total():\n    assert total([{'price': 1.5}, {'price': 2.5}]) == 4.0\n",
    "requirements.txt": ""
  }
}
```

### 7.4 执行方式与镜像

| 执行方式 | 含义 | 镜像 |
| --- | --- | --- |
| 仅 Docker | 只能由 docker Agent 执行 | 必填 |
| 仅 Host | 只能由 host Agent 执行 | 忽略 |
| 任意 | 两种模式均可 | 必填 |

创建任务时会校验「Agent 隔离模式 × 用例执行方式」的兼容性，不兼容或 Docker 缺镜像会直接拒绝创建并列出用例名。

`env_def` 完整示例：

```json
{
  "target_isolation": "docker",
  "docker_image": "python:3.12-slim",
  "docker_volumes": ["/Users/me/.capeval-cache/pip:/root/.cache/pip"],
  "install_commands": ["pip install -q -r requirements.txt"]
}
```

> 依赖缓存卷可显著缩短 Maven/npm 的安装时间，建议 CI 环境也做持久化。

### 7.5 不同技术栈示例

**Python**

```json
{
  "env_def": {
    "target_isolation": "docker",
    "docker_image": "python:3.12-slim",
    "install_commands": ["pip install -q -r requirements.txt"]
  },
  "acceptance": {
    "auto_commands": [{"cmd": "python -m pytest -q", "expect_exit_code": 0}],
    "semantic_points": ["金额计算必须使用 Decimal，不得出现浮点误差"]
  }
}
```

**Node.js**

```json
{
  "env_def": {
    "target_isolation": "docker",
    "docker_image": "node:20-slim",
    "install_commands": ["npm install --no-audit --no-fund"]
  },
  "acceptance": {
    "auto_commands": ["node --test test/*.test.js"]
  }
}
```

> 不要写成 `node --test test`：Node 23 会把目录当作模块路径而报错。

**Java / Maven**

```json
{
  "env_def": {
    "target_isolation": "docker",
    "docker_image": "maven:3.9-eclipse-temurin-17",
    "docker_volumes": ["/Users/me/.capeval-cache/m2:/root/.m2"],
    "install_commands": ["mvn -q -DskipTests compile"]
  },
  "acceptance": {
    "auto_commands": ["mvn -q test"]
  }
}
```

### 7.6 用例自检

保存后在用例行点「自检」，平台会依次执行：

```
还原快照 → 环境准备命令 → 自动化验收命令
```

逐阶段展示命令、退出码与输出：

- **通过**：说明用例环境与验收命令有效。
- **失败**：预埋 bug 用例的预期行为；若环境准备就失败，说明镜像/依赖/网络配置有问题。

建议每条用例发布前都自检一次，避免把「题目写错」误判为「Agent 不行」。

### 7.7 构建链自动检测

新建用例时可点「检测构建链」，平台根据快照文件识别 Maven / Gradle / npm / pnpm / Go / Python，并给出推荐的安装与验收命令，可直接采用后微调。

## 8. 组装评测集并发布版本

1. 「评测集」→ 新建，填写名称、描述，多选用例。**选择顺序即执行顺序**。
2. 创建后自动生成草稿 `v1`。
3. 打开「版本管理」：
   - 草稿：可编辑用例清单、可绑定评审模型；
   - 点「发布」后版本冻结，用例与评审模型绑定均不可修改。
4. 修改已发布内容：基于最新发布版创建新草稿（`v2`），编辑后再发布。
5. 名称/描述可随时编辑；删除会级联删除版本与条目，被任务引用时禁止删除。

> 历史任务永远绑定当时的版本，新版本不会影响旧 Run 的可比性。

## 9. 配置评审模型(可选)

用于 D4 / D7 / D8 的语义评分，接口需兼容 OpenAI Chat Completions。

1. 「评审模型」→ 新建，填写 base_url、模型名、API Key。
2. 勾选评审维度，并为**每个**维度填写 rubrics。
3. 点「测试连接」验证；保存后可在列表复测。
4. 需要全局兜底时勾选「设为默认」，并打开「启用语义评审」。
5. 在评测集草稿版本绑定，或创建任务时做任务级覆盖。

**rubric 示例(D8 工程化)**

```
5 分：有完整错误处理与边界校验，提供可运行的验证手段，改动范围克制
4 分：主流程与多数边界已处理，验证方式基本充分
3 分：功能可用但缺少关键边界处理或验证
1-2 分：无错误处理、硬编码明显、改动范围失控
```

**绑定优先级**

```
任务级覆盖 > 评测集版本绑定 > 全局默认配置 > 回退规则评分
```

未启用、超时或解析失败会自动回退规则近似，流程不中断，报告标注 `rule_approx`。

## 10. 创建与执行评测任务

### 10.1 三步向导

| 步骤 | 内容 |
| --- | --- |
| 1 选择 Agent | 仅 `available` 状态 |
| 2 选择评测集 | 仅 `published` 且用例数 > 0 的版本 |
| 3 评测配置 | 名称、权重模板、评审模型、抽检策略、失败阈值、Pass@k、基线 Run |

### 10.2 关键配置

| 字段 | 说明 |
| --- | --- |
| 权重模板 | 默认模板，可切换；页面实时预览维度与权重 |
| 评审模型 | 留空跟随评测集绑定；选择即任务级覆盖 |
| 人工抽检策略 | `skip` / `sample`(低置信度必抽+其余 50% 随机) / `full` |
| 失败占比阈值 | 默认 0.5；超过则任务判失败，单用例失败不阻断执行 |
| 重复次数(Pass@k) | 1–20；>1 时按用例统计成功率与 Pass@k |
| 基线 Run ID | 可选，报告中展示与历史任务的质量分/效率分差值 |

### 10.3 创建方式

- **创建草稿**：只落库不执行；草稿仍可编辑配置。
- **创建并提交**：进入 `queued`，调度器执行；提交后权重模板与根因库版本已固化，不可修改。

### 10.4 状态机

```
draft → queued → running → scoring → attributing → reviewing → completed
                                                        ↘ failed / canceled
```

- 同一 Agent 的任务串行，不同 Agent 并行。
- 非终态任务列表每 3 秒自动轮询。
- 运行中可「取消」；失败用例可「重试」；复核阶段可「放弃复核」或「完成复核」。

### 10.5 执行阶段做了什么

1. 还原仓库快照到临时目录。
2. Docker 模式：启动常驻容器并挂载目录到 `/work`。
3. 执行环境准备命令（host 直接执行 / docker 通过 `docker exec`）。
4. 启动 Agent 会话，按任务脚本逐轮发送消息，采集六类事件。
5. 执行自动化验收命令。
6. 清理会话、容器与临时目录。

## 11. 看报告、轨迹与对比

### 11.1 评测报告

- **质量分**：`Σ(选中维度权重 × 得分) / Σ权重 × 20`（仅 D1–D5、D7、D8）。
- **效率分**：选中 D6 时单独计算，不并入质量分。
- **维度明细**：每维得分、权重、分布、已评用例数、评分方法(`rule/rule_approx/llm`)。
- **失败 TOP-N**：扣分、主根因、证据信号、轨迹入口。
- **数据完整性**：缺失数据会在报告显式声明。
- **导出与分享**：HTML 自包含、PDF、7 天有效可撤销的免登录链接。

### 11.2 轨迹回放

按六类事件展示时间线，同时展示：

- 模型收到的 Prompt、模型回答、期望输出(验收标准)；
- 工具调用参数与结果、文件变更、终端命令与退出码；
- Docker 模式下的「容器已启动(镜像/容器 ID)」与「Docker 容器内执行」标签。

轨迹 ID 可直接用于问题排查与 Issue 反馈。

### 11.3 双任务对比与趋势

- **双任务对比**：口径一致时逐维对比；版本/权重不一致时降级为总分与维度对比并提示差异。
- **趋势看板**：质量分、效率分双折线；归因误报率 ≥30% 时给出信号调整建议。

## 12. 人工复核与归因

- 归因结果包含主/次根因、置信度、责任归属(Agent / 用例 / 平台 / 环境)与证据事件序号。
- `sample` 策略：低置信度与「未确定」必抽，其余 50% 随机。
- 复核操作需填写结论与备注，系统留痕：操作人、时间、前后结论。
- 「完成复核」后任务进入 `completed`；「放弃复核」同样结束流程但标记未复核。

**归因误报的处理**：如果规则把正确行为判成失败，在复核中标记误报，趋势看板会统计误报率，达到阈值后提示调整信号规则。

## 13. 运维与数据维护

| 事项 | 操作 |
| --- | --- |
| 数据库升级 | `alembic upgrade head` |
| 轨迹保留 | 默认 180 天；被证据链引用的片段豁免 |
| 手动清理 | `POST /api/admin/retention/purge` 或「系统管理」页 |
| 通知 | 任务状态、校验失败、Agent 异常、群体失败；列表 15 秒轮询 |
| 备份 | 备份 MySQL 即可；报告 HTML 可独立归档 |
| 凭证密钥 | `CREDENTIAL_KEY` 变更后旧凭证无法解密，需重新录入 |

**清理顺序**(手工维护时)：

```
attribution_comments → attributions → reports → dimension_scores → trace_events
→ notifications → case_executions → evaluation_runs → caseset_items
→ caseset_versions → casesets → testcases → agents
```

根因库、权重模板、用户属于系统配置，不参与清理。

## 14. 常见问题排查

| 问题 | 排查与解决 |
| --- | --- |
| 任务一直 `queued` | 同 Agent 有任务在跑(串行)；检查调度进程是否存活 |
| 所有用例立即失败 | Agent 校验是否通过、凭证是否过期、额度是否耗尽 |
| `Qoder CLI 本轮失败: 未知原因` | 升级后已改为输出原始错误；优先看额度、凭证、沙箱设置 |
| Docker 报「未配置镜像」 | 用例「镜像」必填；镜像需与语言匹配 |
| `node: not found` | Node 用例镜像填成了 Python 镜像 |
| Docker 下依赖找不到 | 平台已按用例复用同一容器；若仍出现，检查命令是否自行 `docker run` |
| Maven 每次都很慢 | 挂载 `~/.capeval-cache/m2:/root/.m2` 缓存卷 |
| 创建任务提示执行方式不兼容 | 用例声明改为「任意」，或换用对应隔离模式的 Agent |
| 报告某维度为空 | 权重模板未选中该维度；换模板或新建任务 |
| D6 未评分 | Agent 未上报 token 且用例未配效率基线 |
| 评测集无法删除 | 被任务引用；先处理引用任务或改为归档 |
| 删除 Agent 报错 | 被任务引用；可将 Agent 置为不可用 |
| 页面数据突然为空 | 后端未重启导致新字段序列化失败；重启后端并刷新 |
| 分享链接打不开 | 超过 7 天有效期或已被撤销 |

## 15. 最佳实践

1. **先自检，再评测**：用例自检不通过时，不要急着怀疑 Agent。
2. **一条用例一个目标**：避免多功能混合导致归因不可分离。
3. **固定版本口径**：横向对比必须使用同一评测集版本、同一权重模板、同一根因库版本。
4. **基线 Run 选同口径**：基线任务最好与当前任务的评测集版本一致，否则只比总分。
5. **D6 要配基线**：不配 `efficiency_baseline` 时效率分永远为空。
6. **语义评审配 rubrics**：rubric 越具体，D4/D7/D8 区分度越高；建议温度 0.2。
7. **缓存卷常驻**：Maven/npm/pip 缓存卷能显著降低长任务耗时。
8. **失败阈值按场景设**：探索期可设 0.8 容忍个别失败，验收期建议 0.2–0.5。
9. **Pass@k 衡量稳定性**：关键用例重复 3–5 次，避免「偶然通过」被当成能力。
10. **保留证据链**：报告与轨迹是交付凭证，对外分享前确认无敏感信息。

## 16. API 速查

完整定义见 `http://127.0.0.1:8000/api/docs`。

| 用途 | 接口 |
| --- | --- |
| 健康检查 | `GET /api/health` |
| Agent 列表/详情/校验 | `GET/POST /api/agents`、`GET/PATCH/DELETE /api/agents/{id}`、`POST /api/agents/{id}/verify` |
| 评测用例 | `GET/POST /api/testcases`、`GET/PATCH/DELETE /api/testcases/{id}`、`POST /api/testcases/{id}/self-check`、`POST /api/testcases/detect-build` |
| 评测集与版本 | `GET/POST /api/casesets`、`GET/PATCH/DELETE /api/casesets/{id}`、`GET/POST /api/casesets/{id}/versions`、`PATCH /api/casesets/{id}/versions/{vid}/items`、`POST .../publish` |
| 任务 | `GET/POST /api/runs`、`GET/PATCH/DELETE /api/runs/{id}`、`POST /api/runs/{id}/submit`、`/cancel`、`/retry` |
| 评分与归因 | `GET /api/runs/{id}/scores`、`GET /api/runs/{id}/attributions`、`GET /api/attributions/{id}`、`POST /api/attributions/{id}/comments` |
| 报告 | `GET /api/runs/{id}/report`、`POST /api/runs/{id}/report/export`、`POST /api/runs/{id}/report/share`、`GET /api/reports/shared/{token}` |
| 复核 | `POST /api/runs/{id}/review/complete`、`POST /api/runs/{id}/review/waive` |
| 轨迹 | `GET /api/executions/{id}`、`GET /api/executions/{id}/events` |
| 评审模型 | `GET/POST /api/judge-configs`、`PATCH/DELETE /api/judge-configs/{id}`、`POST /api/judge-configs/test` |
| 根因库/权重模板 | `GET/POST /api/root-causes`、`GET/POST /api/weight-templates` |

---

有问题或建议，欢迎在 [GitHub Issues](https://github.com/libin617/code-agent-evaluation-Platform/issues) 反馈，并附上轨迹 ID 与相关命令退出码。
