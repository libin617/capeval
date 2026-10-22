<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import {
  addAttributionComment, createRootCause, getExecution, getExecutionAttributions,
  getExecutionScores, getSignalCatalog, listAttributionComments, listExecutionEvents,
  listRootCauses, reviewAttribution,
} from '../api'
import type {
  AttributionCommentInfo, AttributionInfo, CaseStatus, DimensionScoreInfo, ExecutionDetail,
  Responsibility, RootCause, TraceEventInfo,
} from '../api/types'

const route = useRoute()
const router = useRouter()
const execId = Number(route.params.id)

const detail = ref<ExecutionDetail | null>(null)
const events = ref<TraceEventInfo[]>([])
const dimScores = ref<DimensionScoreInfo[]>([])
const attributions = ref<AttributionInfo[]>([])
const rootCauses = ref<RootCause[]>([])
const loading = ref(true)
const reviewingId = ref<number | null>(null)
const correctTarget = ref<number | null>(null)
// ---- M6 归因异议评论(FR-08 异常):任意角色(含只读成员)可提交 ----
const comments = ref<Record<number, AttributionCommentInfo[]>>({})
const commentDraft = reactive<Record<number, string>>({})
const expandedComments = ref<number[]>([])
let timer: ReturnType<typeof setInterval> | null = null

const DIM_NAMES: Record<string, string> = {
  D1: '代码正确性', D2: '流程遵从', D3: '工具调用', D4: '会话一致性',
  D5: '安全性', D6: '成本效率', D7: '代码风格', D8: '工程化',
}

function dimTagType(score: number): 'success' | 'warning' | 'danger' {
  if (score >= 4) return 'success'
  if (score >= 3) return 'warning'
  return 'danger'
}

const VERDICT_META: Record<string, { label: string; type: 'success' | 'warning' | 'danger' | 'info' }> = {
  pass: { label: '通过', type: 'success' },
  warn: { label: '警告', type: 'warning' },
  fail: { label: '未通过', type: 'danger' },
  info: { label: '记录', type: 'info' },
}

const METHOD_LABEL: Record<string, string> = {
  rule: '规则判定', rule_approx: '规则近似(LLM Judge 未启用)', llm: 'LLM 评审',
}

const CASE_STATUS_META: Record<CaseStatus, { label: string; type: 'info' | 'warning' | 'primary' | 'success' | 'danger' }> = {
  pending: { label: '待执行', type: 'info' },
  running: { label: '执行中', type: 'primary' },
  succeeded: { label: '成功', type: 'success' },
  failed: { label: '失败', type: 'danger' },
  canceled: { label: '已取消', type: 'warning' },
}

const EVENT_TYPES = [
  { value: 'message', label: '消息' },
  { value: 'tool_call', label: '工具调用' },
  { value: 'file_change', label: '文件变更' },
  { value: 'terminal', label: '终端' },
  { value: 'resource', label: '资源' },
  { value: 'system', label: '系统' },
]
const typeFilter = ref<string[]>(EVENT_TYPES.map((t) => t.value))
const qaOpen = ref(['qa'])

const filteredEvents = computed(() => events.value.filter((e) => typeFilter.value.includes(e.event_type)))

/** 按轮次聚合:给模型的 Prompt + 模型回答 + 工具调用/耗时,供问答对照布局 */
const turnViews = computed(() => {
  const script = detail.value?.task_script ?? []
  const turns = script.map((t, i) => ({
    turn: Number(t.turn ?? i + 1),
    prompt: String(t.message ?? ''),
    intent: String(t.intent_tag ?? ''),
  }))
  const known = new Set(turns.map((t) => t.turn))
  // 历史执行可能无任务脚本快照,按轨迹中出现的轮次补齐
  events.value.forEach((e) => {
    if (e.turn > 0 && !known.has(e.turn)) {
      known.add(e.turn)
      turns.push({ turn: e.turn, prompt: '', intent: '' })
    }
  })
  return turns
    .sort((a, b) => a.turn - b.turn)
    .map((t) => {
      const turnEvents = events.value.filter((e) => e.turn === t.turn)
      const answers = turnEvents
        .filter((e) => e.event_type === 'message' && e.payload.role === 'agent')
        .map((e) => String(e.payload.content ?? ''))
        .filter((s) => s.trim().length > 0)
      const toolCalls = turnEvents.filter((e) => e.event_type === 'tool_call').length
      const resource = turnEvents.find((e) => e.event_type === 'resource')
      const durationMs = resource ? Number(resource.payload.duration_ms ?? 0) : 0
      return { ...t, answers, toolCalls, durationMs }
    })
})

const acceptancePoints = computed(() => {
  const a = detail.value?.acceptance ?? {}
  return {
    commands: a.auto_commands ?? [],
    semantic: (a.semantic_points ?? []) as string[],
    process: (a.process_expectations ?? []) as string[],
    manual: (a.manual_checks ?? []) as string[],
  }
})
const hasExpected = computed(() => {
  const p = acceptancePoints.value
  return !!(p.commands.length || p.semantic.length || p.process.length || p.manual.length)
})
const detailsTurnCount = computed(() => events.value.filter((e) => e.event_type === 'tool_call').length)
// 长文本(Prompt/模型回答)折叠展开,避免直接截断
const expandedTexts = reactive<Record<string, boolean>>({})
const isLongText = (s: string) => s.length > 260
const isExpanded = (key: string) => !!expandedTexts[key]
function toggleExpanded(key: string) { expandedTexts[key] = !expandedTexts[key] }

function cmdText(item: unknown): string {
  if (typeof item === 'string') return item
  const obj = item as { cmd?: string }
  return obj?.cmd ?? JSON.stringify(item)
}

function cmdExpect(item: unknown): number {
  if (typeof item === 'string') return 0
  const obj = item as { expect_exit_code?: number }
  return Number(obj?.expect_exit_code ?? 0)
}

const TIMELINE_COLOR: Record<string, string> = {
  message: '#409eff', tool_call: '#e6a23c', file_change: '#67c23a',
  terminal: '#909399', resource: '#b88230', system: '#f56c6c',
}

const SYSTEM_KIND_LABEL: Record<string, string> = {
  case_started: '用例开始', repo_restored: '仓库还原', session_started: '会话启动',
  container_started: '容器已启动', container_removed: '容器已销毁',
  env_setup: '环境准备', completion_declared: '任务宣告完成', case_succeeded: '用例成功',
  case_failed: '用例失败', platform_error: '平台异常', max_turns_exceeded: '超出最大轮次',
  case_timeout: '用例超时', no_response: 'Agent 无响应', process_exited: '进程退出',
  probe: '探针',
}

function systemLabel(e: TraceEventInfo) {
  const kind = String(e.payload.kind ?? '')
  return SYSTEM_KIND_LABEL[kind] ?? kind
}

function isFailureEvent(e: TraceEventInfo) {
  if (e.event_type === 'terminal') return Number(e.payload.exit_code) !== Number(e.payload.expect_exit_code ?? 0)
  if (e.event_type === 'system') return ['case_failed', 'platform_error', 'case_timeout', 'max_turns_exceeded', 'no_response'].includes(String(e.payload.kind))
  return false
}

function jumpToFirstFailure() {
  const idx = filteredEvents.value.findIndex(isFailureEvent)
  if (idx === -1) return
  document.getElementById(`ev-${filteredEvents.value[idx].id}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
}

const hasFailure = computed(() => filteredEvents.value.some(isFailureEvent))

function fmtTime(t: string) {
  return t.replace('T', ' ').slice(11, 19)
}

async function refresh() {
  try {
    ;[detail.value, events.value] = await Promise.all([getExecution(execId), listExecutionEvents(execId)])
    if (detail.value.status !== 'running') stopPolling()
    // 终态后评分就绪;404/空数组表示未评分,静默忽略
    if (detail.value.status === 'succeeded' || detail.value.status === 'failed') {
      try { dimScores.value = await getExecutionScores(execId) } catch { dimScores.value = [] }
      try { attributions.value = await getExecutionAttributions(execId) } catch { attributions.value = [] }
    }
  } finally {
    loading.value = false
  }
}

const RC_RESP: Record<string, string> = {
  agent: 'Agent', platform: '平台', third_party: '第三方', case_maintainer: '用例维护方',
}
const REVIEW_META: Record<string, { label: string; type: 'success' | 'warning' | 'info' | 'danger' }> = {
  pending: { label: '待复核', type: 'warning' },
  confirmed: { label: '已确认', type: 'success' },
  corrected: { label: '已改判', type: 'success' },
  rejected: { label: '已驳回', type: 'info' },
  not_sampled: { label: '未抽中(自动通过)', type: 'info' },
}
const HISTORY_ACTION: Record<string, string> = {
  confirm: '认可', correct: '改判', reject: '驳回', create_rootcause: '转为新根因', waive: '放弃复核',
}
const SOURCE_LABEL: Record<string, string> = { rule: '规则归因', llm: 'LLM 归因', human: '人工改判' }

const pendingCount = computed(() => attributions.value.filter((a) => a.review_status === 'pending').length)

/** 失败但无任何归因结论 → 展示「未确定」沉淀入口 */
const needsSink = computed(() =>
  !!detail.value && detail.value.status === 'failed' && attributions.value.length === 0)

/** 证据链事件序号 → 对应轨迹事件(用于定位与跳转) */
function evidenceEvents(a: AttributionInfo): TraceEventInfo[] {
  const seqs = a.evidence?.event_seqs ?? []
  const bySeq = new Map(events.value.map((e) => [e.seq, e]))
  return seqs.map((s) => bySeq.get(s)).filter((e): e is TraceEventInfo => e !== undefined)
}

function jumpToEvent(ev: TraceEventInfo | undefined) {
  if (!ev) return
  if (!typeFilter.value.includes(ev.event_type)) typeFilter.value = [...typeFilter.value, ev.event_type]
  requestAnimationFrame(() => {
    document.getElementById(`ev-${ev.id}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  })
}

async function onReview(a: AttributionInfo, action: 'confirm' | 'reject' | 'correct') {
  const payload: { action: 'confirm' | 'correct' | 'reject'; rootcause_id?: number | null; note?: string } = { action }
  if (action === 'correct') {
    if (!correctTarget.value) { ElMessage.warning('请选择改判后的根因条目'); return }
    payload.rootcause_id = correctTarget.value
  }
  reviewingId.value = a.id
  try {
    const updated = await reviewAttribution(a.id, { ...payload, reviewed_by: 'evaluator' })
    const idx = attributions.value.findIndex((x) => x.id === a.id)
    if (idx >= 0) attributions.value[idx] = updated
    if (action === 'correct') correctTarget.value = null
    ElMessage.success('复核已保存')
  } catch (e) {
    ElMessage.error((e as Error).message)
  } finally {
    reviewingId.value = null
  }
}

// ---------- M6 归因异议评论(任意角色含 viewer 可提交) ----------
async function toggleComments(aid: number) {
  const i = expandedComments.value.indexOf(aid)
  if (i >= 0) {
    expandedComments.value.splice(i, 1)
    return
  }
  expandedComments.value.push(aid)
  if (!comments.value[aid]) {
    try {
      comments.value[aid] = await listAttributionComments(aid)
    } catch {
      comments.value[aid] = []
    }
  }
}

async function submitComment(aid: number) {
  const content = (commentDraft[aid] || '').trim()
  if (!content) return
  try {
    const c = await addAttributionComment(aid, content)
    comments.value[aid] = [...(comments.value[aid] || []), c]
    commentDraft[aid] = ''
    ElMessage.success('评论已提交')
  } catch (e) {
    ElMessage.error((e as Error).message)
  }
}

// ---------- 未确定案件 / 误判结论:一键沉淀为新根因 ----------
const rcDialog = ref(false)
const rcSaving = ref(false)
const signalOptions = ref<string[]>([])
const targetAttr = ref<AttributionInfo | null>(null)
const rcForm = reactive({
  parent_id: null as number | null, name: '', description: '',
  responsibility: 'agent' as Responsibility, signals: [] as string[], primary: false, note: '',
})

/** 可作为上级的条目(一/二级),用于挂载三级根因 */
const parentOptions = computed(() =>
  rootCauses.value.filter((rc) => rc.level === 1 || rc.level === 2)
    .map((rc) => ({ value: rc.id, label: `${rc.code} ${rc.name}`, level: rc.level })))

function openNewRootCause(attr: AttributionInfo | null) {
  targetAttr.value = attr
  rcForm.parent_id = null
  rcForm.name = attr ? `${attr.rootcause.name}(细化)` : ''
  rcForm.description = detail.value
    ? `用例「${detail.value.testcase_title}」执行过程沉淀的失败模式`
    : ''
  rcForm.responsibility = (attr?.rootcause.responsibility as Responsibility) ?? 'agent'
  rcForm.signals = []
  rcForm.primary = true
  rcForm.note = '由人工复核沉淀'
  rcDialog.value = true
}

const newLevel = computed(() => {
  const p = parentOptions.value.find((o) => o.value === rcForm.parent_id)
  return (p ? p.level + 1 : 1) as 1 | 2 | 3
})

async function saveNewRootCause() {
  if (!rcForm.name.trim()) { ElMessage.warning('请填写名称'); return }
  if (!rcForm.parent_id && newLevel.value === 1) { ElMessage.warning('请先选择上级条目'); return }
  rcSaving.value = true
  try {
    const res = await createRootCause({
      level: newLevel.value, parent_id: rcForm.parent_id, name: rcForm.name,
      description: rcForm.description, responsibility: rcForm.responsibility,
      detection_signals: {
        rules: rcForm.signals.map((s) => ({ signal: s, primary: rcForm.primary })),
        related_dimensions: [],
      },
      source_execution_id: execId,   // 自动带入证据示例
      note: rcForm.note,
    })
    rcDialog.value = false
    ElMessage.success(`已创建 ${res.rootcause.code},根因库升级至 v${res.library_version}`)
    rootCauses.value = await listRootCauses()
    // 若由某条结论触发,顺带把该结论改判到新根因(留痕)
    const attr = targetAttr.value
    if (attr) {
      try {
        const updated = await reviewAttribution(attr.id, {
          action: 'correct', rootcause_id: res.rootcause.id,
          note: `转为新根因 ${res.rootcause.code}`, reviewed_by: 'evaluator',
        })
        const idx = attributions.value.findIndex((x) => x.id === attr.id)
        if (idx >= 0) attributions.value[idx] = updated
      } catch (e) { ElMessage.warning(`新根因已创建,但改判失败:${(e as Error).message}`) }
    }
  } catch (e) {
    ElMessage.error((e as Error).message)
  } finally {
    rcSaving.value = false
  }
}

function setupPolling() {
  stopPolling()
  timer = setInterval(() => {
    if (detail.value?.status === 'running' || detail.value?.status === 'pending') refresh()
  }, 3000)
}

function stopPolling() {
  if (timer) { clearInterval(timer); timer = null }
}

onMounted(async () => {
  await refresh()
  setupPolling()
  // 根因条目用于人工改判下拉(失败静默忽略)
  try { rootCauses.value = await listRootCauses() } catch { rootCauses.value = [] }
  try { signalOptions.value = await getSignalCatalog() } catch { signalOptions.value = [] }
})
onUnmounted(stopPolling)
</script>

<template>
  <div v-loading="loading">
    <template v-if="detail">
      <div class="head-row">
        <el-button @click="router.push(`/runs/${detail.run_id}`)">← 返回任务详情</el-button>
        <el-button type="danger" plain :disabled="!hasFailure" @click="jumpToFirstFailure">跳到首个失败</el-button>
      </div>

      <el-descriptions :column="4" border style="margin: 14px 0">
        <el-descriptions-item label="评测用例" :span="2">#{{ detail.order_no }} {{ detail.testcase_title }}</el-descriptions-item>
        <el-descriptions-item label="状态">
          <el-tag :type="CASE_STATUS_META[detail.status].type" size="small">{{ CASE_STATUS_META[detail.status].label }}</el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="难度">{{ detail.difficulty }}</el-descriptions-item>
        <el-descriptions-item label="标签" :span="2">
          <el-tag v-for="t in detail.tags" :key="t" size="small" effect="plain" style="margin-right: 4px">{{ t }}</el-tag>
          <span v-if="!detail.tags.length">-</span>
        </el-descriptions-item>
        <el-descriptions-item label="资源消耗">
          <template v-if="Object.keys(detail.resource_usage ?? {}).length">
            {{ detail.resource_usage.input_tokens ?? 0 }} in / {{ detail.resource_usage.output_tokens ?? 0 }} out tok
            · {{ Number(detail.resource_usage.wall_seconds ?? 0).toFixed(1) }}s
            · {{ detail.resource_usage.turns ?? '-' }} 轮
          </template>
          <template v-else>-</template>
        </el-descriptions-item>
        <el-descriptions-item label="重跑次数">{{ detail.rerun_count }}</el-descriptions-item>
        <el-descriptions-item v-if="detail.error" label="错误" :span="4">
          <span class="error-text">{{ detail.error }}</span>
        </el-descriptions-item>
      </el-descriptions>

      <!-- 模型问答对照 + 期望输出:左列为每轮 Prompt/回答,右列为验收标准 -->
      <div v-if="turnViews.length || hasExpected" class="qa-panel">
        <el-collapse v-model="qaOpen">
          <el-collapse-item name="qa">
            <template #title>
              模型输入 / 回答 / 期望输出
              <span class="hint" style="margin-left: 8px">共 {{ turnViews.length }} 轮 · {{ detailsTurnCount }} 次工具调用</span>
            </template>
            <div class="qa-grid">
              <div class="qa-left">
                <div v-for="t in turnViews" :key="`turn-${t.turn}`" class="turn-card">
                  <div class="turn-head">
                    <el-tag size="small" type="primary" effect="dark">第 {{ t.turn }} 轮</el-tag>
                    <el-tag v-if="t.intent" size="small" effect="plain" type="success">{{ t.intent }}</el-tag>
                    <span class="hint">
                      {{ t.toolCalls }} 次工具调用<template v-if="t.durationMs"> · {{ (t.durationMs / 1000).toFixed(1) }}s</template>
                    </span>
                  </div>
                  <div class="qa-block prompt">
                    <div class="qa-label">给模型的 Prompt</div>
                    <div class="qa-text" :class="{ clamped: isLongText(t.prompt) && !isExpanded(`p-${t.turn}`) }">{{ t.prompt || '(历史执行未记录该轮 Prompt)' }}</div>
                    <el-button v-if="isLongText(t.prompt)" link type="primary" size="small"
                      @click="toggleExpanded(`p-${t.turn}`)">{{ isExpanded(`p-${t.turn}`) ? '收起' : '展开全文' }}</el-button>
                  </div>
                  <div class="qa-block answer">
                    <div class="qa-label">模型回答</div>
                    <template v-if="t.answers.length">
                      <div v-for="(a, i) in t.answers" :key="`a-${i}`" class="qa-text-wrap">
                        <div class="qa-text" :class="{ clamped: isLongText(a) && !isExpanded(`a-${t.turn}-${i}`) }">{{ a }}</div>
                        <el-button v-if="isLongText(a)" link type="primary" size="small"
                          @click="toggleExpanded(`a-${t.turn}-${i}`)">{{ isExpanded(`a-${t.turn}-${i}`) ? '收起' : '展开全文' }}</el-button>
                      </div>
                    </template>
                    <div v-else class="hint">本轮无文本回答(仅工具调用)</div>
                  </div>
                </div>
              </div>

              <div class="qa-right">
                <div class="expect-card">
                  <div class="expect-title">期望输出(验收标准)</div>
                  <div v-if="acceptancePoints.commands.length" class="expect-section">
                    <div class="qa-label">验收命令 · 期望退出码</div>
                    <div v-for="(c, i) in acceptancePoints.commands" :key="`c-${i}`" class="cmd-row">
                      <code class="cmd-code">{{ cmdText(c) }}</code>
                      <el-tag size="small" type="success" effect="plain">exit {{ cmdExpect(c) }}</el-tag>
                    </div>
                  </div>
                  <div v-if="acceptancePoints.semantic.length" class="expect-section">
                    <div class="qa-label">语义验收点(供 LLM Judge 比对)</div>
                    <ol class="expect-list">
                      <li v-for="(s, i) in acceptancePoints.semantic" :key="`s-${i}`">{{ s }}</li>
                    </ol>
                  </div>
                  <div v-if="acceptancePoints.process.length" class="expect-section">
                    <div class="qa-label">过程期望</div>
                    <ul class="expect-list">
                      <li v-for="(s, i) in acceptancePoints.process" :key="`p-${i}`">{{ s }}</li>
                    </ul>
                  </div>
                  <div v-if="acceptancePoints.manual.length" class="expect-section">
                    <div class="qa-label">人工检查项</div>
                    <ul class="expect-list">
                      <li v-for="(s, i) in acceptancePoints.manual" :key="`m-${i}`">{{ s }}</li>
                    </ul>
                  </div>
                  <div v-if="!hasExpected" class="hint">该用例未配置验收标准</div>
                </div>
              </div>
            </div>
          </el-collapse-item>
        </el-collapse>
      </div>

      <!-- M3:八维评分 chips(点击展开判定信号明细) -->
      <div v-if="dimScores.length" class="dim-panel">
        <el-collapse>
          <el-collapse-item title="八维评分(点击展开判定信号)" name="scores">
            <div class="dim-chips">
              <el-tag v-for="s in dimScores" :key="s.dimension" size="large"
                :type="dimTagType(s.score)" effect="plain" class="dim-chip">
                {{ s.dimension }} {{ DIM_NAMES[s.dimension] }} · {{ s.score.toFixed(1) }}
              </el-tag>
            </div>
            <div v-for="s in dimScores" :key="`detail-${s.dimension}`" class="dim-detail">
              <div class="dim-detail-head">
                <span class="dim-detail-title">{{ s.dimension }} {{ DIM_NAMES[s.dimension] }}</span>
                <el-tag size="small" :type="dimTagType(s.score)">{{ s.score.toFixed(2) }} / 5</el-tag>
                <span class="hint">{{ METHOD_LABEL[s.details.method] ?? s.details.method }}</span>
              </div>
              <div v-if="s.details.note" class="hint" style="margin: 2px 0 4px">{{ s.details.note }}</div>
              <div v-for="(sig, i) in s.details.signals" :key="i" class="signal-row">
                <el-tag size="small" :type="VERDICT_META[sig.verdict]?.type ?? 'info'" effect="plain">
                  {{ VERDICT_META[sig.verdict]?.label ?? sig.verdict }}
                </el-tag>
                <span class="signal-detail">{{ sig.detail }}</span>
                <span v-if="sig.deduction > 0" class="signal-ded">-{{ sig.deduction }}</span>
              </div>
            </div>
          </el-collapse-item>
        </el-collapse>
      </div>

      <!-- M4:归因结论 + 证据链 + 人工复核 -->
      <div v-if="attributions.length" class="attr-panel">
        <el-collapse>
          <el-collapse-item name="attr">
            <template #title>
              根因归因
              <el-tag v-if="pendingCount" type="warning" size="small" effect="plain" style="margin-left: 8px">
                {{ pendingCount }} 条待复核
              </el-tag>
            </template>

            <div v-for="a in attributions" :key="a.id" class="attr-card" :class="{ 'attr-primary': a.is_primary }">
              <div class="attr-head">
                <el-tag size="small" :type="a.is_primary ? 'danger' : 'info'" effect="dark">
                  {{ a.is_primary ? '主根因' : '次要根因' }}
                </el-tag>
                <span class="rc-code">{{ a.rootcause.code }}</span>
                <span class="attr-name">{{ a.rootcause.name }}</span>
                <el-tag size="small" effect="plain">{{ RC_RESP[a.rootcause.responsibility] ?? a.rootcause.responsibility }}</el-tag>
                <span class="hint">{{ SOURCE_LABEL[a.source] ?? a.source }} · 置信度 {{ a.confidence.toFixed(2) }}</span>
                <el-tag size="small" :type="REVIEW_META[a.review_status]?.type ?? 'info'" effect="plain">
                  {{ REVIEW_META[a.review_status]?.label ?? a.review_status }}
                </el-tag>
              </div>

              <div class="attr-evidence">
                <span class="hint">证据链</span>
                <el-tag v-for="sig in a.evidence?.signals ?? []" :key="sig" size="small" effect="plain" type="warning">
                  {{ sig }}
                </el-tag>
                <el-button v-for="ev in evidenceEvents(a)" :key="ev.id" size="small" link type="primary"
                  @click="jumpToEvent(ev)">→ 事件 #{{ ev.seq }}</el-button>
                <span v-if="!evidenceEvents(a).length" class="hint">(无关联事件)</span>
              </div>
              <div v-for="(d, i) in a.evidence?.details ?? []" :key="`d-${i}`" class="hint attr-detail">{{ d }}</div>
              <div v-if="a.review_note" class="hint attr-detail">复核备注:{{ a.review_note }}<span v-if="a.reviewed_by"> ({{ a.reviewed_by }})</span></div>

              <div v-if="a.review_history?.length" class="review-history">
                <span class="hint">复核留痕</span>
                <div v-for="(h, i) in a.review_history" :key="`h-${i}`" class="hint history-row">
                  {{ h.at.replace('T', ' ') }} · {{ HISTORY_ACTION[h.action] ?? h.action }} · {{ h.by || '-' }}
                  <span v-if="h.note"> · {{ h.note }}</span>
                  <span v-if="h.from_rootcause_id !== h.to_rootcause_id">
                    ({{ h.from_rootcause_id ?? '-' }} → {{ h.to_rootcause_id ?? '-' }})
                  </span>
                </div>
              </div>

              <!-- M6 异议评论:任意角色可对归因结论留言(FR-08 异常) -->
              <div class="objection-box">
                <div class="objection-head">
                  <span class="hint">异议评论({{ (comments[a.id] || []).length }})</span>
                  <el-button link type="primary" size="small" @click="toggleComments(a.id)">
                    {{ expandedComments.includes(a.id) ? '收起' : '展开' }}
                  </el-button>
                </div>
                <template v-if="expandedComments.includes(a.id)">
                  <div v-for="c in comments[a.id] || []" :key="c.id" class="comment-row">
                    <div class="comment-meta">
                      <span class="comment-author">{{ c.author }}</span>
                      <el-tag size="small" effect="plain">{{ c.role }}</el-tag>
                      <span class="hint">{{ c.created_at?.replace('T', ' ').slice(0, 16) }}</span>
                    </div>
                    <div class="comment-content">{{ c.content }}</div>
                  </div>
                  <div v-if="!(comments[a.id] || []).length" class="hint" style="padding: 4px 0">
                    暂无评论,如有异议请留言(复核人会看到)
                  </div>
                  <div class="comment-input">
                    <el-input
                      v-model="commentDraft[a.id]" type="textarea" :rows="2" maxlength="2000" show-word-limit
                      placeholder="对归因结论提出异议或补充说明(任意角色可提交)…"
                    />
                    <el-button type="primary" size="small" :disabled="!commentDraft[a.id]?.trim()"
                      @click="submitComment(a.id)">提交</el-button>
                  </div>
                </template>
              </div>

              <div class="attr-actions">
                <el-button size="small" :loading="reviewingId === a.id" @click="onReview(a, 'confirm')">认可</el-button>
                <el-button size="small" type="info" plain :loading="reviewingId === a.id"
                  @click="onReview(a, 'reject')">驳回</el-button>
                <el-select v-model="correctTarget" placeholder="改判为…" size="small" style="width: 220px" filterable>
                  <el-option v-for="rc in rootCauses" :key="rc.id" :label="`${rc.code} ${rc.name}`" :value="rc.id" />
                </el-select>
                <el-button size="small" type="warning" :loading="reviewingId === a.id"
                  @click="onReview(a, 'correct')">改判</el-button>
                <el-button size="small" type="primary" plain @click="openNewRootCause(a)">转为新根因</el-button>
              </div>
            </div>
          </el-collapse-item>
        </el-collapse>
      </div>

      <!-- 未确定/无归因:人工沉淀新根因入口 -->
      <el-alert v-else-if="needsSink" type="warning" :closable="false" style="margin: 12px 0">
        <template #title>
          该用例规则未命中根因(未确定),复核时可沉淀为新根因条目
        </template>
        <el-button size="small" type="primary" plain style="margin-top: 6px"
          @click="openNewRootCause(null)">转为新根因</el-button>
      </el-alert>

      <div class="filter-row">
        <el-checkbox-group v-model="typeFilter">
          <el-checkbox v-for="t in EVENT_TYPES" :key="t.value" :value="t.value">{{ t.label }}</el-checkbox>
        </el-checkbox-group>
        <span class="hint">共 {{ filteredEvents.length }} 条事件</span>
      </div>

      <el-timeline style="margin-top: 14px; padding-left: 4px">
        <el-timeline-item
          v-for="e in filteredEvents" :key="e.id" :id="`ev-${e.id}`"
          :timestamp="`${fmtTime(e.created_at)}${e.turn > 0 ? ` · 第 ${e.turn} 轮` : ''}`"
          :color="isFailureEvent(e) ? '#f56c6c' : TIMELINE_COLOR[e.event_type]"
          :hollow="!isFailureEvent(e)"
        >
          <!-- 消息:Prompt / 模型回答气泡 -->
          <div v-if="e.event_type === 'message'" class="msg-bubble" :class="String(e.payload.role)">
            <div class="msg-role">
              {{ e.payload.role === 'user' ? '给模型的 Prompt' : '模型回答' }}
              <el-tag v-if="e.payload.intent_tag" size="small" type="success" effect="plain" style="margin-left: 6px">
                {{ e.payload.intent_tag }}
              </el-tag>
            </div>
            <div class="msg-content">{{ e.payload.content }}</div>
          </div>

          <!-- 工具调用(字段兼容:name/args 为现行契约,tool/input 为历史数据) -->
          <div v-else-if="e.event_type === 'tool_call'" class="ev-card">
            <div class="ev-head">
              <el-tag size="small" type="warning" effect="plain">工具调用</el-tag>
              <code class="ev-code">{{ e.payload.name ?? e.payload.tool }}</code>
              <el-tag v-if="e.payload.result" size="small" type="success" effect="plain">{{ e.payload.result }}</el-tag>
            </div>
            <pre class="ev-pre">{{ JSON.stringify(e.payload.args ?? e.payload.input ?? {}, null, 2) }}</pre>
          </div>

          <!-- 文件变更 -->
          <div v-else-if="e.event_type === 'file_change'" class="ev-card">
            <div class="ev-head">
              <el-tag size="small" type="success" effect="plain">文件变更</el-tag>
              <code class="ev-code">{{ e.payload.path }}</code>
              <span class="hint">({{ e.payload.change }})</span>
            </div>
            <pre v-if="e.payload.after_summary" class="ev-pre">{{ e.payload.after_summary }}</pre>
          </div>

          <!-- 终端 -->
          <div v-else-if="e.event_type === 'terminal'" class="ev-card" :class="{ failed: isFailureEvent(e) }">
            <div class="ev-head">
              <el-tag size="small" :type="e.payload.kind === 'acceptance' ? 'primary' : 'info'" effect="plain">
                {{ e.payload.kind === 'acceptance' ? '验收命令' : '环境命令' }}
              </el-tag>
              <el-tag v-if="e.payload.isolation" size="small" effect="plain"
                :type="e.payload.isolation === 'docker' ? 'warning' : 'info'">
                {{ e.payload.isolation === 'docker' ? 'Docker 容器内执行' : '宿主机执行' }}
              </el-tag>
              <code class="ev-code">{{ e.payload.command }}</code>
              <el-tag size="small" :type="isFailureEvent(e) ? 'danger' : 'success'">
                exit {{ e.payload.exit_code }}
              </el-tag>
            </div>
            <div v-if="e.payload.image" class="hint">镜像:{{ e.payload.image }}</div>
            <pre v-if="e.payload.stdout_tail" class="ev-pre stdout">{{ e.payload.stdout_tail }}</pre>
            <pre v-if="e.payload.stderr_tail" class="ev-pre stderr">{{ e.payload.stderr_tail }}</pre>
          </div>

          <!-- 资源 -->
          <div v-else-if="e.event_type === 'resource'" class="ev-resource">
            <el-icon><span>⚡</span></el-icon>
            本轮消耗:input {{ e.payload.input_tokens }} / output {{ e.payload.output_tokens }} tokens
            <template v-if="Number(e.payload.cost) > 0"> · ${{ e.payload.cost }}</template>
          </div>

          <!-- 系统 -->
          <div v-else class="ev-system" :class="{ failed: isFailureEvent(e) }">
            <el-tag size="small" :type="isFailureEvent(e) ? 'danger' : 'info'" effect="dark">
              {{ systemLabel(e) }}
            </el-tag>
            <span v-if="e.payload.error" class="error-text">{{ e.payload.error }}</span>
            <span v-else-if="e.payload.detail" class="hint">{{ e.payload.detail }}</span>
            <span v-else-if="e.payload.image" class="hint">镜像:{{ e.payload.image }}<template v-if="e.payload.container_id"> · 容器:{{ String(e.payload.container_id).slice(0, 12) }}</template></span>
            <span v-else-if="e.payload.session_id" class="hint">{{ e.payload.adapter }} · {{ e.payload.session_id }}</span>
            <span v-else-if="e.payload.file_count !== undefined" class="hint">{{ e.payload.file_count }} 个文件</span>
            <pre v-if="e.payload.trace" class="ev-pre stderr">{{ e.payload.trace }}</pre>
          </div>
        </el-timeline-item>
      </el-timeline>

      <el-empty v-if="!filteredEvents.length" description="暂无事件(用例尚未执行或过滤条件为空)" />
    </template>

    <el-dialog v-model="rcDialog" title="沉淀为新根因条目" width="600px" :close-on-click-modal="false">
      <el-form label-width="96px">
        <el-form-item label="上级条目">
          <el-select v-model="rcForm.parent_id" placeholder="选择所属二级类别(自动生成三级编码)" filterable
            style="width: 100%">
            <el-option v-for="o in parentOptions" :key="o.value" :label="o.label" :value="o.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="名称" required>
          <el-input v-model="rcForm.name" maxlength="128" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="rcForm.description" type="textarea" :rows="3" />
          <div class="hint">保存时会自动追加「证据示例」(本用例轨迹事件序号、命中信号)</div>
        </el-form-item>
        <el-form-item label="责任归属">
          <el-select v-model="rcForm.responsibility" style="width: 200px">
            <el-option v-for="(label, k) in RC_RESP" :key="k" :label="label" :value="k" />
          </el-select>
        </el-form-item>
        <el-form-item label="检测信号">
          <el-select v-model="rcForm.signals" multiple filterable clearable style="width: 100%"
            placeholder="可选:绑定评分信号,便于后续规则自动归因">
            <el-option v-for="s in signalOptions" :key="s" :label="s" :value="s" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="rcDialog = false">取消</el-button>
        <el-button type="primary" :loading="rcSaving" @click="saveNewRootCause">创建并改判</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.head-row { display: flex; justify-content: space-between; align-items: center; }
.filter-row { display: flex; align-items: center; gap: 16px; }
.hint { font-size: 12px; color: var(--el-text-color-secondary); }
.error-text { color: var(--el-color-danger); font-size: 12px; }
.msg-bubble { max-width: 78%; padding: 10px 14px; border-radius: 10px; }
.msg-bubble.user { background: var(--el-color-primary-light-9); border: 1px solid var(--el-color-primary-light-5); }
.msg-bubble.agent { background: #fff; border: 1px solid var(--el-border-color); margin-left: auto; }
.msg-role { font-size: 11px; color: var(--el-text-color-secondary); margin-bottom: 4px; }
.msg-content { white-space: pre-wrap; word-break: break-word; font-size: 13px; }
.ev-card { background: #fff; border: 1px solid var(--el-border-color); border-radius: 8px; padding: 10px 12px; }
.ev-card.failed { border-color: var(--el-color-danger); }
.ev-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.ev-code { font-family: ui-monospace, monospace; font-size: 12px; background: var(--el-fill-color); padding: 2px 6px; border-radius: 4px; }
.ev-pre { font-family: ui-monospace, monospace; font-size: 12px; background: var(--el-fill-color-light); border-radius: 6px; padding: 8px 10px; margin: 8px 0 0; white-space: pre-wrap; word-break: break-all; max-height: 220px; overflow: auto; }
.ev-pre.stderr { color: var(--el-color-danger); }
.ev-resource { font-size: 12px; color: var(--el-text-color-secondary); display: flex; align-items: center; gap: 6px; }
.ev-system { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.dim-panel { margin-bottom: 14px; }
.dim-chips { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 12px; }
.dim-chip { font-weight: 600; }
.dim-detail { border-top: 1px dashed var(--el-border-color); padding: 8px 0; }
.dim-detail-head { display: flex; align-items: center; gap: 8px; }
.dim-detail-title { font-size: 13px; font-weight: 600; }
.signal-row { display: flex; align-items: center; gap: 8px; margin-top: 4px; font-size: 12px; }
.signal-detail { color: var(--el-text-color-regular); }
.signal-ded { color: var(--el-color-danger); font-weight: 600; }
.attr-panel { margin-bottom: 14px; }
.attr-card { border: 1px solid var(--el-border-color); border-left: 3px solid var(--el-border-color); border-radius: 8px; padding: 10px 14px; margin-bottom: 10px; }
.attr-card.attr-primary { border-left-color: var(--el-color-danger); }
.attr-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.rc-code { font-family: monospace; color: var(--el-color-primary); font-weight: 600; }
.attr-name { font-size: 13px; font-weight: 600; }
.attr-evidence { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-top: 8px; }
.attr-detail { margin-top: 4px; }
.attr-actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-top: 10px; }
.review-history { margin-top: 6px; border-top: 1px dashed var(--el-border-color); padding-top: 6px; }
.history-row { margin-top: 2px; }
.objection-box { margin-top: 8px; border-top: 1px dashed var(--el-border-color); padding-top: 6px; }
.objection-head { display: flex; align-items: center; justify-content: space-between; }
.comment-row { padding: 6px 0 2px; }
.comment-meta { display: flex; align-items: center; gap: 8px; }
.comment-author { font-size: 13px; font-weight: 600; }
.comment-content { font-size: 13px; color: var(--el-text-color-regular); margin-top: 2px; white-space: pre-wrap; }
.comment-input { display: flex; gap: 8px; align-items: flex-start; margin-top: 8px; }
.comment-input .el-button { margin-top: 2px; }

/* 模型问答对照 + 期望输出 */
.qa-panel { margin-bottom: 14px; }
.qa-grid { display: grid; grid-template-columns: minmax(0, 1.6fr) minmax(260px, 1fr); gap: 14px; align-items: start; }
.qa-left { display: flex; flex-direction: column; gap: 12px; min-width: 0; }
.qa-right { position: sticky; top: 12px; min-width: 0; }
.turn-card { border: 1px solid var(--el-border-color); border-radius: 8px; padding: 10px 12px; background: #fff; }
.turn-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 8px; }
.qa-block { border-radius: 8px; padding: 8px 10px; }
.qa-block.prompt { background: var(--el-color-primary-light-9); border: 1px solid var(--el-color-primary-light-7); }
.qa-block.answer { background: var(--el-fill-color-light); border: 1px solid var(--el-border-color-lighter); margin-top: 8px; }
.qa-label { font-size: 11px; font-weight: 600; color: var(--el-text-color-secondary); margin-bottom: 4px; letter-spacing: 0.3px; }
.qa-text { font-size: 13px; line-height: 1.6; white-space: pre-wrap; word-break: break-word; }
.qa-text.clamped { max-height: 210px; overflow: hidden; -webkit-mask-image: linear-gradient(180deg, #000 78%, transparent 100%); mask-image: linear-gradient(180deg, #000 78%, transparent 100%); }
.qa-text-wrap + .qa-text-wrap { margin-top: 8px; padding-top: 8px; border-top: 1px dashed var(--el-border-color-lighter); }
.expect-card { border: 1px solid var(--el-border-color); border-left: 3px solid var(--el-color-success); border-radius: 8px; padding: 12px 14px; background: #fff; }
.expect-title { font-size: 13px; font-weight: 700; margin-bottom: 10px; }
.expect-section { border-top: 1px dashed var(--el-border-color-lighter); padding-top: 8px; margin-top: 8px; }
.expect-section:first-of-type { border-top: none; padding-top: 0; margin-top: 0; }
.cmd-row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 4px; }
.cmd-code { font-family: ui-monospace, monospace; font-size: 12px; background: var(--el-fill-color-light); padding: 3px 6px; border-radius: 4px; word-break: break-all; }
.expect-list { margin: 0; padding-left: 18px; font-size: 12.5px; line-height: 1.7; color: var(--el-text-color-regular); }
.expect-list li + li { margin-top: 2px; }
@media (max-width: 1000px) {
  .qa-grid { grid-template-columns: 1fr; }
  .qa-right { position: static; }
}
</style>
