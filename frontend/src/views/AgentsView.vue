<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  createAgent, deleteAgent, listAdapters, listAgents, updateAgent, verifyAgent,
} from '../api'
import type { AdapterInfo, AgentInfo } from '../api/types'

const agents = ref<AgentInfo[]>([])
const adapters = ref<AdapterInfo[]>([])
const loading = ref(false)
const verifyingId = ref<number | null>(null)
const dialogVisible = ref(false)
const editing = ref<AgentInfo | null>(null)   // null = 新建
const keyword = ref('')
const detailVisible = ref(false)
const detailRow = ref<AgentInfo | null>(null)

// ---------- 前端分页(默认每页 10 条) ----------
const page = ref(1)
const PAGE_SIZE = 10
const pagedAgents = computed<AgentInfo[]>(() => {
  const start = (page.value - 1) * PAGE_SIZE
  return agents.value.slice(start, start + PAGE_SIZE)
})

const form = reactive({
  name: '',
  version_label: '',
  access_type: 'cli' as 'cli' | 'api',
  adapter_key: 'echo_cli',
  start_command: '',
  endpoint: '',
  auth_type: 'bearer' as 'none' | 'bearer' | 'api_key',
  expose_tool_calls: false,
  expose_tokens: false,
  input_price: '',
  output_price: '',
  credential: '',
  // ---- 资源与隔离配置 ----
  isolation_mode: 'host' as 'host' | 'docker',
  max_memory_mb: 2048,
  max_cpu_seconds: 600,
  max_turns: 100,
  max_case_minutes: 30,
  max_case_cost: 0,
  network: 'allow' as 'allow' | 'deny',
})

const statusMeta: Record<string, { label: string; type: 'success' | 'info' | 'warning' | 'danger' }> = {
  unverified: { label: '未校验', type: 'info' },
  available: { label: '可用', type: 'success' },
  verify_failed: { label: '校验失败', type: 'danger' },
  unavailable: { label: '不可用', type: 'warning' },
}

async function refresh() {
  loading.value = true
  try {
    agents.value = await listAgents(keyword.value.trim() ? { q: keyword.value.trim() } : undefined)
    page.value = 1
  } finally {
    loading.value = false
  }
}

function openCreate() {
  editing.value = null
  Object.assign(form, {
    name: '', version_label: '', access_type: 'cli',
    adapter_key: 'echo_cli', start_command: '', endpoint: '',
    auth_type: 'bearer', expose_tool_calls: false, expose_tokens: false,
    input_price: '', output_price: '', credential: '',
    isolation_mode: 'host', max_memory_mb: 2048, max_cpu_seconds: 600,
    max_turns: 100, max_case_minutes: 30, max_case_cost: 0,
    network: 'allow',
  })
  dialogVisible.value = true
}

function openEdit(row: AgentInfo) {
  editing.value = row
  const cfg = row.adapter_config || {}
  const cost = row.cost_params || {}
  const rl = row.resource_limits || {}
  Object.assign(form, {
    name: row.name, version_label: row.version_label, access_type: row.access_type,
    adapter_key: row.adapter_key,
    start_command: Array.isArray(cfg.start_command) ? (cfg.start_command as string[]).join(' ') : '',
    endpoint: String(cfg.endpoint ?? ''),
    auth_type: (cfg.auth_type as 'none' | 'bearer' | 'api_key') ?? 'bearer',
    expose_tool_calls: !!cfg.expose_tool_calls,
    expose_tokens: !!cfg.expose_tokens,
    input_price: cost.input_price_per_1k != null ? String(cost.input_price_per_1k) : '',
    output_price: cost.output_price_per_1k != null ? String(cost.output_price_per_1k) : '',
    credential: '',   // 凭证留空表示不修改
    isolation_mode: (rl.isolation_mode as 'host' | 'docker') ?? 'host',
    max_memory_mb: Number(rl.max_memory_mb ?? 2048),
    max_cpu_seconds: Number(rl.max_cpu_seconds ?? 600),
    max_turns: Number(rl.max_turns ?? 100),
    max_case_minutes: Number(rl.max_case_minutes ?? 30),
    max_case_cost: Number(rl.max_case_cost ?? 0),
    network: (rl.network as 'allow' | 'deny') ?? 'allow',
  })
  dialogVisible.value = true
}

function openDetail(row: AgentInfo) {
  detailRow.value = row
  detailVisible.value = true
}

function fmtJson(obj: Record<string, unknown> | null | undefined) {
  return JSON.stringify(obj ?? {}, null, 2)
}

function buildPayload() {
  const config: Record<string, unknown> = {}
  if (form.access_type === 'cli' && form.start_command) {
    config.start_command = form.start_command.split(' ').filter(Boolean)
  }
  if (form.access_type === 'api') {
    if (form.endpoint) config.endpoint = form.endpoint
    // M6 HTTP API 契约:鉴权方式与数据暴露声明(未暴露的维度评分将按「数据缺失」降级)
    config.auth_type = form.auth_type
    config.expose_tool_calls = form.expose_tool_calls
    config.expose_tokens = form.expose_tokens
  }
  const cost: Record<string, unknown> = {}
  if (form.access_type === 'api') {
    if (form.input_price) cost.input_price_per_1k = Number(form.input_price)
    if (form.output_price) cost.output_price_per_1k = Number(form.output_price)
  }
  const resource_limits: Record<string, unknown> = {
    isolation_mode: form.isolation_mode,
    max_memory_mb: Number(form.max_memory_mb),
    max_cpu_seconds: Number(form.max_cpu_seconds),
    max_turns: Number(form.max_turns),
    max_case_minutes: Number(form.max_case_minutes),
    max_case_cost: Number(form.max_case_cost),
    network: form.network,
  }
  return { config, cost, resource_limits }
}

async function submit() {
  const { config, cost, resource_limits } = buildPayload()
  if (editing.value) {
    // 编辑:PATCH;凭证留空则不修改
    await updateAgent(editing.value.id, {
      name: form.name, version_label: form.version_label,
      adapter_key: form.adapter_key, adapter_config: config,
      ...(form.credential ? { credential: form.credential } : {}),
      cost_params: cost,
      resource_limits,
    })
    ElMessage.success('已保存,配置变更后需重新校验')
  } else {
    await createAgent({
      name: form.name, version_label: form.version_label, access_type: form.access_type,
      adapter_key: form.adapter_key, adapter_config: config, credential: form.credential,
      resource_limits, cost_params: cost,
    })
    ElMessage.success('已创建,请执行连通性校验')
  }
  dialogVisible.value = false
  await refresh()
}

async function onVerify(row: AgentInfo) {
  verifyingId.value = row.id
  try {
    const result = await verifyAgent(row.id)
    if (result.passed) ElMessage.success(result.message)
    else ElMessage.error(result.message)
  } catch (e) {
    ElMessage.error((e as Error).message)
  } finally {
    verifyingId.value = null
    await refresh()
  }
}

async function onDelete(row: AgentInfo) {
  try {
    await ElMessageBox.confirm(`确认删除 Agent「${row.name} ${row.version_label}」?`, '删除确认', { type: 'warning' })
  } catch { return }
  try {
    await deleteAgent(row.id)
    ElMessage.success('已删除')
    await refresh()
  } catch (e) {
    // 后端 409:已被评测任务引用禁止删除(可置为不可用) —— 弹窗明确告知
    ElMessageBox.alert((e as Error).message, '无法删除', { type: 'warning' })
  }
}

function onAccessTypeChange() {
  const matched = adapters.value.find((a) => a.access_type === form.access_type)
  if (matched) form.adapter_key = matched.key
}

onMounted(async () => {
  adapters.value = await listAdapters()
  await refresh()
})
</script>

<template>
  <div>
    <div class="toolbar">
      <el-button type="primary" @click="openCreate">新建 Agent</el-button>
      <el-input
        v-model="keyword" placeholder="搜索名称 / 版本 / 适配器" clearable
        style="width: 220px" @keyup.enter="refresh" @clear="refresh"
      >
        <template #append>
          <el-button @click="refresh">查询</el-button>
        </template>
      </el-input>
      <span class="hint">创建后必须通过连通性校验才可被评测任务选中</span>
    </div>

    <el-table :data="pagedAgents" v-loading="loading" border>
      <el-table-column prop="name" label="名称" min-width="140" />
      <el-table-column prop="version_label" label="版本标识" width="100" />
      <el-table-column label="接入类型" width="90">
        <template #default="{ row }">
          <el-tag size="small" effect="plain">{{ row.access_type.toUpperCase() }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="adapter_key" label="适配器" width="130" />
      <el-table-column prop="credential_masked" label="凭证" width="120">
        <template #default="{ row }">{{ row.credential_masked || '—' }}</template>
      </el-table-column>
      <el-table-column label="状态" width="100">
        <template #default="{ row }">
          <el-tag :type="statusMeta[row.status]?.type" size="small">{{ statusMeta[row.status]?.label }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="校验信息" min-width="200">
        <template #default="{ row }">
          <span v-if="row.last_verify_error" class="err-text">{{ row.last_verify_error }}</span>
          <span v-else-if="row.last_verify_at">{{ row.last_verify_at?.replace('T', ' ').slice(0, 19) }} 通过</span>
          <span v-else>—</span>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="300" fixed="right">
        <template #default="{ row }">
          <el-button size="small" @click="openDetail(row)">查看</el-button>
          <el-button size="small" type="primary" plain @click="openEdit(row)">编辑</el-button>
          <el-button size="small" :loading="verifyingId === row.id" @click="onVerify(row)">
            {{ row.status === 'unverified' ? '校验' : '重新校验' }}
          </el-button>
          <el-button size="small" type="danger" plain @click="onDelete(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <div class="pager-row">
      <el-pagination
        v-model:current-page="page" :page-size="PAGE_SIZE" :total="agents.length"
        layout="total, prev, pager, next" background
      />
    </div>

    <el-dialog v-model="dialogVisible" :title="editing ? `编辑 Agent「${editing.name} ${editing.version_label}」` : '新建 Agent'" width="560px">
      <el-form label-width="110px">
        <el-form-item label="名称" required>
          <el-input v-model="form.name" placeholder="如 claude-code" />
        </el-form-item>
        <el-form-item label="版本标识" required>
          <el-input v-model="form.version_label" placeholder="如 v1.2(同一 Agent 多版本分别建档)" />
        </el-form-item>
        <el-form-item label="接入类型" required>
          <el-radio-group v-model="form.access_type" :disabled="!!editing" @change="onAccessTypeChange">
            <el-radio value="cli">CLI 型</el-radio>
            <el-radio value="api">API 型</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="适配器" required>
          <el-select v-model="form.adapter_key" style="width: 100%">
            <el-option
              v-for="a in adapters.filter((x) => x.access_type === form.access_type)"
              :key="a.key" :value="a.key" :label="a.name"
            >
              <div>{{ a.name }}</div>
              <div class="adapter-desc">{{ a.description }}</div>
            </el-option>
          </el-select>
        </el-form-item>
        <el-form-item v-if="form.access_type === 'cli' && form.adapter_key === 'generic_cli'" label="启动命令">
          <el-input v-model="form.start_command" placeholder="如 aider --model deepseek" />
        </el-form-item>
        <el-form-item v-if="form.access_type === 'api'" label="Endpoint" required>
          <el-input v-model="form.endpoint" placeholder="https://agent.example.com/v1" />
        </el-form-item>
        <el-form-item v-if="form.access_type === 'api'" label="鉴权方式">
          <el-radio-group v-model="form.auth_type">
            <el-radio value="none">无鉴权</el-radio>
            <el-radio value="bearer">Bearer Token</el-radio>
            <el-radio value="api_key">X-API-Key</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-if="form.access_type === 'api'" label="数据暴露">
          <div class="expose-box">
            <el-checkbox v-model="form.expose_tool_calls">上报工具调用明细(未勾选则 D3 按「数据缺失」降级)</el-checkbox>
            <el-checkbox v-model="form.expose_tokens">上报 token 用量(未勾选则 D6 按「数据缺失」降级)</el-checkbox>
          </div>
        </el-form-item>
        <el-form-item v-if="form.access_type === 'api'" label="token 单价">
          <div class="price-box">
            <el-input v-model="form.input_price" placeholder="输入价(元/1k tokens)" style="width: 210px" />
            <el-input v-model="form.output_price" placeholder="输出价(元/1k tokens)" style="width: 210px" />
          </div>
          <div class="field-hint">用于成本计量(FR-04 资源限制);Agent 未上报 cost 时按单价折算</div>
        </el-form-item>
        <el-form-item label="凭证">
          <el-input v-model="form.credential" type="password" show-password
                    :placeholder="editing ? '留空表示不修改凭证' : '仅存储掩码,界面不回显明文'" />
        </el-form-item>

        <el-divider content-position="left">资源与隔离</el-divider>
        <el-form-item label="执行隔离">
          <el-radio-group v-model="form.isolation_mode">
            <el-radio value="host">宿主机直跑(默认)</el-radio>
            <el-radio value="docker">Docker 容器</el-radio>
          </el-radio-group>
          <div class="field-hint">
            host:构建/验收命令在临时目录直接执行,通过 setrlimit 限制内存与 CPU;
            docker:命令在容器内执行(--memory/--network),需本机已安装 Docker。
            隔离模式只在此处配置,评测用例不再单独覆盖;容器镜像由各评测用例的「镜像覆盖」指定。
          </div>
        </el-form-item>
        <el-form-item label="网络访问">
          <el-radio-group v-model="form.network">
            <el-radio value="allow">允许</el-radio>
            <el-radio value="deny">禁止(docker: --network=none)</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="内存上限(MB)">
          <el-input-number v-model="form.max_memory_mb" :min="128" :max="16384" :step="256" />
          <div class="field-hint">host:RLIMIT_AS;docker:--memory。0 表示不限制</div>
        </el-form-item>
        <el-form-item label="CPU 时间上限(秒)">
          <el-input-number v-model="form.max_cpu_seconds" :min="0" :max="7200" :step="60" />
          <div class="field-hint">host:RLIMIT_CPU;docker:--cpu-shares。0 表示不限制</div>
        </el-form-item>
        <el-form-item label="最大交互轮次">
          <el-input-number v-model="form.max_turns" :min="1" :max="500" :step="10" />
        </el-form-item>
        <el-form-item label="单 case 时长(分)">
          <el-input-number v-model="form.max_case_minutes" :min="1" :max="240" :step="5" />
        </el-form-item>
        <el-form-item label="单 case 费用上限(元)">
          <el-input-number v-model="form.max_case_cost" :min="0" :max="1000" :step="1" :precision="2" />
          <div class="field-hint">0 表示不限制;触限立即终止该 case</div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :disabled="!form.name || !form.version_label" @click="submit">
          {{ editing ? '保存' : '创建' }}
        </el-button>
      </template>
    </el-dialog>

    <el-drawer v-model="detailVisible" :title="detailRow ? `Agent 详情:${detailRow.name} ${detailRow.version_label}` : ''" size="520px">
      <el-descriptions v-if="detailRow" :column="1" border>
        <el-descriptions-item label="ID">{{ detailRow.id }}</el-descriptions-item>
        <el-descriptions-item label="名称">{{ detailRow.name }}</el-descriptions-item>
        <el-descriptions-item label="版本标识">{{ detailRow.version_label }}</el-descriptions-item>
        <el-descriptions-item label="接入类型">{{ detailRow.access_type.toUpperCase() }}</el-descriptions-item>
        <el-descriptions-item label="适配器">{{ detailRow.adapter_key }}</el-descriptions-item>
        <el-descriptions-item label="凭证">{{ detailRow.credential_masked || '—' }}</el-descriptions-item>
        <el-descriptions-item label="状态">
          <el-tag :type="statusMeta[detailRow.status]?.type" size="small">
            {{ statusMeta[detailRow.status]?.label }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="最近校验">
          <span v-if="detailRow.last_verify_error" class="err-text">{{ detailRow.last_verify_error }}</span>
          <span v-else-if="detailRow.last_verify_at">{{ detailRow.last_verify_at.replace('T', ' ').slice(0, 19) }} 通过</span>
          <span v-else>—</span>
        </el-descriptions-item>
        <el-descriptions-item label="适配器配置">
          <pre class="json-pre">{{ fmtJson(detailRow.adapter_config) }}</pre>
        </el-descriptions-item>
        <el-descriptions-item label="资源限制">
          <pre class="json-pre">{{ fmtJson(detailRow.resource_limits) }}</pre>
        </el-descriptions-item>
        <el-descriptions-item label="成本参数">
          <pre class="json-pre">{{ fmtJson(detailRow.cost_params) }}</pre>
        </el-descriptions-item>
      </el-descriptions>
    </el-drawer>
  </div>
</template>

<style scoped>
.toolbar { display: flex; align-items: center; gap: 12px; margin-bottom: 14px; }
.hint { font-size: 12px; color: var(--el-text-color-secondary); }
.err-text { color: var(--el-color-danger); font-size: 12px; }
.adapter-desc { font-size: 12px; color: var(--el-text-color-secondary); }
.expose-box { display: flex; flex-direction: column; gap: 2px; }
.price-box { display: flex; gap: 8px; }
.field-hint { font-size: 12px; color: var(--el-text-color-placeholder); line-height: 1.6; margin-top: 4px; }
.pager-row { display: flex; justify-content: flex-end; margin-top: 12px; }
.json-pre {
  margin: 0; font-size: 12px; line-height: 1.5; white-space: pre-wrap; word-break: break-all;
  background: var(--el-fill-color-light); padding: 8px; border-radius: 4px; width: 100%;
}
</style>
