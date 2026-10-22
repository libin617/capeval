<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  cancelRun, completeRunReview, getRun, getRunAttributions, getRunScores, retryRun, submitRun, waiveRunReview,
} from '../api'
import type { CaseStatus, RunAttributions, RunDetail, RunScores, RunStatus } from '../api/types'
import RadarChart from '../components/RadarChart.vue'
import AttributionReviewPanel from '../components/AttributionReviewPanel.vue'

const route = useRoute()
const router = useRouter()
const runId = Number(route.params.id)
const run = ref<RunDetail | null>(null)
const scores = ref<RunScores | null>(null)
const attributions = ref<RunAttributions | null>(null)
const loading = ref(true)
let timer: ReturnType<typeof setInterval> | null = null

const TERMINAL: RunStatus[] = ['completed', 'failed', 'canceled']

const DIM_ORDER = ['D1', 'D2', 'D3', 'D4', 'D5', 'D6', 'D7', 'D8']

const dimLabels = computed<Record<string, string>>(() => {
  const out: Record<string, string> = {}
  for (const d of DIM_ORDER) out[d] = scores.value?.dimension_summary?.[d]?.name ?? ''
  return out
})

const dimValues = computed<Record<string, number | null>>(() => {
  const out: Record<string, number | null> = {}
  for (const d of DIM_ORDER) out[d] = scores.value?.dimension_summary?.[d]?.score ?? null
  return out
})

const caseScoreMap = computed(() => {
  const m = new Map<number, { quality: number | null; efficiency: number | null }>()
  for (const c of scores.value?.cases ?? []) {
    m.set(c.execution_id, { quality: c.quality_score, efficiency: c.efficiency_score })
  }
  return m
})

/** 维度表仅展示权重模板选中的维度;雷达图仍保留八轴,未选维度标注未评 */
const dimRows = computed(() =>
  DIM_ORDER.map((d) => ({ dim: d, name: '', weight: 0, score: null, scored_cases: 0,
    ...(scores.value?.dimension_summary?.[d] ?? {}) }))
    .filter((r) => (r as { selected?: boolean }).selected !== false && r.weight > 0),
)

function scoreColor(v: number | null | undefined): string {
  if (v === null || v === undefined) return 'info'
  if (v >= 80) return 'success'
  if (v >= 60) return 'warning'
  return 'danger'
}

const STATUS_META: Record<RunStatus, { label: string; type: 'info' | 'warning' | 'primary' | 'success' | 'danger' }> = {
  draft: { label: '草稿', type: 'info' },
  queued: { label: '排队中', type: 'warning' },
  running: { label: '执行中', type: 'primary' },
  scoring: { label: '评分中', type: 'primary' },
  attributing: { label: '归因中', type: 'primary' },
  reviewing: { label: '待复核', type: 'primary' },
  completed: { label: '已完成', type: 'success' },
  failed: { label: '失败', type: 'danger' },
  canceled: { label: '已取消', type: 'info' },
}

const CASE_STATUS_META: Record<CaseStatus, { label: string; type: 'info' | 'warning' | 'primary' | 'success' | 'danger' }> = {
  pending: { label: '待执行', type: 'info' },
  running: { label: '执行中', type: 'primary' },
  succeeded: { label: '成功', type: 'success' },
  failed: { label: '失败', type: 'danger' },
  canceled: { label: '已取消', type: 'warning' },
}

const STAGE_FLOW = ['draft', 'queued', 'running', 'scoring', 'attributing', 'reviewing', 'completed']

const RESP_META: Record<string, { label: string; type: 'primary' | 'warning' | 'info' | 'success' | 'danger' }> = {
  agent: { label: 'Agent', type: 'primary' },
  platform: { label: '平台', type: 'warning' },
  third_party: { label: '第三方', type: 'warning' },
  case_maintainer: { label: '用例维护方', type: 'info' },
}

const stageActive = computed(() => {
  if (!run.value) return 0
  const s = run.value.status
  if (s === 'failed' || s === 'canceled') {
    // 定位到失败/取消发生前的阶段
    if (run.value.progress.running > 0 || run.value.progress.succeeded > 0 || run.value.progress.failed > 0) return 2
    return 1
  }
  return STAGE_FLOW.indexOf(s)
})

async function refresh() {
  try {
    run.value = await getRun(runId)
    if (TERMINAL.includes(run.value.status) || run.value.status === 'draft') stopPolling()
    // 评分/归因在完成后生成;404 表示尚未生成,静默忽略
    if (run.value.status === 'completed' || run.value.status === 'failed' || run.value.status === 'reviewing') {
      try { scores.value = await getRunScores(runId) } catch { scores.value = null }
      try { attributions.value = await getRunAttributions(runId) } catch { attributions.value = null }
    }
  } finally {
    loading.value = false
  }
}

function setupPolling() {
  stopPolling()
  timer = setInterval(() => {
    if (run.value && !TERMINAL.includes(run.value.status) && run.value.status !== 'draft') refresh()
  }, 3000)
}

function stopPolling() {
  if (timer) { clearInterval(timer); timer = null }
}

function fmtTime(t: string | null) {
  return t ? t.replace('T', ' ').slice(0, 19) : '-'
}

function fmtResource(usage: Record<string, unknown>) {
  if (!usage || !Object.keys(usage).length) return '-'
  const tokens = Number(usage.input_tokens ?? 0) + Number(usage.output_tokens ?? 0)
  const secs = usage.wall_seconds as number | undefined
  return `${tokens} tok · ${secs !== undefined ? secs.toFixed(1) : '-'}s`
}

async function onSubmit() {
  try {
    await submitRun(runId)
    ElMessage.success('已提交,等待调度')
    await refresh()
    setupPolling()
  } catch (e) { ElMessage.error((e as Error).message) }
}

async function onCancel() {
  try {
    await ElMessageBox.confirm('确认取消该任务?执行中的用例将在当前用例结束后停止。', '取消确认', { type: 'warning' })
  } catch { return }
  try {
    await cancelRun(runId)
    ElMessage.success('已取消')
    await refresh()
  } catch (e) { ElMessage.error((e as Error).message) }
}

async function onRetry() {
  try {
    await retryRun(runId)
    ElMessage.success('已重新排队(仅重跑失败用例)')
    await refresh()
    setupPolling()
  } catch (e) { ElMessage.error((e as Error).message) }
}

async function onWaiveReview() {
  try {
    await ElMessageBox.confirm(
      '放弃复核将直接发布结果,报告会标注「未经人工复核」。确认继续?', '放弃复核', { type: 'warning' })
  } catch { return }
  try {
    await waiveRunReview(runId)
    ElMessage.success('已放弃复核并发布')
    await refresh()
  } catch (e) { ElMessage.error((e as Error).message) }
}

async function onCompleteReview() {
  try {
    await ElMessageBox.confirm('确认完成人工复核?完成后该任务将进入终态,评分与归因结果冻结。', '复核完成', { type: 'warning' })
  } catch { return }
  try {
    await completeRunReview(runId)
    ElMessage.success('复核已完成')
    await refresh()
  } catch (e) { ElMessage.error((e as Error).message) }
}

onMounted(async () => {
  await refresh()
  setupPolling()
})
onUnmounted(stopPolling)
</script>

<template>
  <div v-loading="loading">
    <template v-if="run">
      <div class="head-row">
        <el-button @click="router.push('/runs')">← 返回列表</el-button>
        <div class="ops">
          <el-button v-if="run.status === 'draft'" type="primary" @click="onSubmit">提交</el-button>
          <el-button v-if="run.status === 'reviewing'" @click="onWaiveReview">放弃复核</el-button>
          <el-button v-if="run.status === 'reviewing'" type="primary" @click="onCompleteReview">完成复核</el-button>
          <el-button v-if="scores" type="success" plain
            @click="router.push(`/runs/${runId}/report`)">查看报告</el-button>
          <el-button v-if="!TERMINAL.includes(run.status)" type="danger" plain @click="onCancel">取消</el-button>
          <el-button v-if="run.status === 'failed'" type="warning" @click="onRetry">重试失败用例</el-button>
        </div>
      </div>

      <el-steps :active="stageActive" align-center :process-status="run.status === 'failed' ? 'error' : 'process'"
        :finish-status="run.status === 'completed' ? 'success' : run.status === 'failed' ? 'error' : run.status === 'canceled' ? 'wait' : 'success'"
        style="margin: 18px 0 22px">
        <el-step title="草稿" />
        <el-step title="排队" />
        <el-step title="执行" />
        <el-step title="评分" />
        <el-step title="归因" />
        <el-step title="复核" />
        <el-step :title="run.status === 'failed' ? '失败' : run.status === 'canceled' ? '已取消' : '完成'" />
      </el-steps>

      <el-alert v-if="run.error" type="error" :title="run.error" :closable="false" style="margin-bottom: 14px" />
      <!-- M6:API 型 Agent 数据缺失/成本计量参数缺失预警(FR-04 异常) -->
      <el-alert
        v-if="run.warning" type="warning" :title="run.warning" :closable="false"
        show-icon style="margin-bottom: 14px"
      />

      <el-descriptions :column="3" border style="margin-bottom: 18px">
        <el-descriptions-item label="名称" :span="2">{{ run.name }}</el-descriptions-item>
        <el-descriptions-item label="状态">
          <el-tag :type="STATUS_META[run.status].type" size="small">{{ STATUS_META[run.status].label }}</el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="Agent">{{ run.agent_name }}({{ run.agent_version_label }})</el-descriptions-item>
        <el-descriptions-item label="评测集">{{ run.caseset_name }} v{{ run.caseset_version_no }}</el-descriptions-item>
        <el-descriptions-item label="失败阈值">{{ Math.round(run.fail_threshold * 100) }}%</el-descriptions-item>
        <el-descriptions-item label="权重快照" :span="2">
          <el-tag v-for="(w, k) in run.weight_snapshot" :key="k" size="small" effect="plain" style="margin-right: 4px">
            {{ k }} {{ w }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="根因库版本">v{{ run.rootcause_version }}</el-descriptions-item>
        <el-descriptions-item label="评审模型">{{ run.judge_config_name || '默认' }}</el-descriptions-item>
        <el-descriptions-item label="创建时间">{{ fmtTime(run.created_at) }}</el-descriptions-item>
        <el-descriptions-item label="提交时间">{{ fmtTime(run.submitted_at) }}</el-descriptions-item>
        <el-descriptions-item label="完成时间">{{ fmtTime(run.completed_at) }}</el-descriptions-item>
      </el-descriptions>

      <!-- M3 评分结果:质量/效率分 + 八维雷达 + 维度明细 -->
      <template v-if="scores">
        <h3 class="section-title">评分结果(FR-05 八维)</h3>
        <div class="score-panel">
          <div class="score-cards">
            <div class="score-card">
              <div class="score-num" :class="`c-${scoreColor(scores.quality_score)}`">
                {{ scores.quality_score ?? '-' }}
              </div>
              <div class="score-label">质量分(百分制)</div>
              <div class="score-sub">按所选维度加权</div>
            </div>
            <div class="score-card">
              <div class="score-num" :class="`c-${scoreColor(scores.efficiency_score)}`">
                {{ scores.efficiency_score ?? '-' }}
              </div>
              <div class="score-label">效率分(百分制)</div>
              <div class="score-sub">D6 成本与效率折算</div>
            </div>
          </div>
          <RadarChart :values="dimValues" :labels="dimLabels" :size="330" />
        </div>
        <el-table :data="dimRows" border size="small" style="margin-bottom: 18px">
          <el-table-column prop="dim" label="维度" width="70" />
          <el-table-column prop="name" label="名称" min-width="130" />
          <el-table-column label="权重" width="80">
            <template #default="{ row }">{{ row.weight }}</template>
          </el-table-column>
          <el-table-column label="得分(0-5)" width="120">
            <template #default="{ row }">
              <el-tag v-if="row.score !== null" size="small"
                :type="row.score >= 4 ? 'success' : row.score >= 3 ? 'warning' : 'danger'">
                {{ row.score.toFixed(2) }}
              </el-tag>
              <span v-else class="hint">未评</span>
            </template>
          </el-table-column>
          <el-table-column prop="scored_cases" label="已评用例数" width="100" />
        </el-table>
      </template>

      <!-- M4 归因结果:根因分布 + 责任归属 + 未确定清单 -->
      <template v-if="attributions">
        <h3 class="section-title">
          归因分析(FR-06 三级根因库 v{{ attributions.rootcause_version }})
          <el-tag v-if="run.status === 'reviewing'" type="primary" size="small" effect="plain" style="margin-left: 8px">
            待人工复核 · {{ attributions.review_policy }} · 待办 {{ attributions.pending_review }} 项
          </el-tag>
          <el-tag v-else-if="attributions.review_mark" size="small" effect="plain" style="margin-left: 8px"
            :type="attributions.review_mark === 'reviewed' ? 'success' : 'info'">
            {{ attributions.review_mark === 'reviewed' ? '已人工复核' : '未经人工复核' }}
          </el-tag>
        </h3>
        <div class="attr-summary">
          <span class="attr-k">归因结论 <b>{{ attributions.total_attributions }}</b></span>
          <span class="attr-k">归属案件 <b>{{ attributions.attributed_cases }}</b></span>
          <span v-for="(n, resp) in attributions.responsibility_summary" v-show="n > 0"
            :key="resp" class="attr-k">
            {{ RESP_META[resp as string]?.label ?? resp }} <b>{{ n }}</b>
          </span>
        </div>
        <el-alert v-if="attributions.undetermined.length" type="warning" :closable="false" style="margin: 10px 0">
          <template #title>
            有 {{ attributions.undetermined.length }} 个失败用例规则未命中根因,已标记"未确定"需人工复核
          </template>
        </el-alert>
        <el-table v-if="attributions.distribution.length" :data="attributions.distribution" border size="small"
          style="margin-bottom: 18px">
          <el-table-column label="根因编码 / 名称" min-width="260">
            <template #default="{ row }">
              <span class="rc-code">{{ row.code }}</span> {{ row.name }}
            </template>
          </el-table-column>
          <el-table-column label="责任归属" width="120">
            <template #default="{ row }">
              <el-tag size="small" effect="plain" :type="RESP_META[row.responsibility]?.type ?? 'info'">
                {{ RESP_META[row.responsibility]?.label ?? row.responsibility }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="primary" label="主根因" width="90" />
          <el-table-column prop="secondary" label="次要根因" width="100" />
        </el-table>
        <div v-else class="hint" style="margin-bottom: 18px">
          本次评测未产生根因归因(无明显失分信号)
        </div>
      </template>

      <!-- 人工复核工作台:run 在 reviewing 状态显示完整交互;已完成的 run 也可以复盘查看历史 -->
      <template v-if="attributions && run.review_policy !== 'skip' && attributions.total_attributions > 0">
        <h3 class="section-title">人工复核工作台(FR-08)</h3>
        <AttributionReviewPanel :run-id="runId" :run-attributions="attributions" />
      </template>

      <h3 class="section-title">评测用例执行({{ run.progress.succeeded + run.progress.failed + run.progress.canceled }}/{{ run.progress.total }})</h3>
      <el-table :data="run.cases" border>
        <el-table-column prop="order_no" label="#" width="50" />
        <el-table-column prop="testcase_title" label="评测用例" min-width="240" show-overflow-tooltip />
        <el-table-column prop="difficulty" label="难度" width="80" />
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag :type="CASE_STATUS_META[row.status as CaseStatus].type" size="small">
              {{ CASE_STATUS_META[row.status as CaseStatus].label }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="rerun_count" label="重跑" width="60" />
        <el-table-column v-if="scores" label="质量分" width="90">
          <template #default="{ row }">
            <el-tag v-if="caseScoreMap.get(row.id)?.quality != null" size="small"
              :type="scoreColor(caseScoreMap.get(row.id)?.quality) as 'success' | 'warning' | 'danger' | 'info'">
              {{ caseScoreMap.get(row.id)?.quality }}
            </el-tag>
            <span v-else class="hint">-</span>
          </template>
        </el-table-column>
        <el-table-column v-if="scores" label="效率分" width="90">
          <template #default="{ row }">
            <el-tag v-if="caseScoreMap.get(row.id)?.efficiency != null" size="small"
              :type="scoreColor(caseScoreMap.get(row.id)?.efficiency) as 'success' | 'warning' | 'danger' | 'info'">
              {{ caseScoreMap.get(row.id)?.efficiency }}
            </el-tag>
            <span v-else class="hint">-</span>
          </template>
        </el-table-column>
        <el-table-column label="资源消耗" width="150">
          <template #default="{ row }">{{ fmtResource(row.resource_usage) }}</template>
        </el-table-column>
        <el-table-column label="错误" min-width="180" show-overflow-tooltip>
          <template #default="{ row }">
            <span v-if="row.error" class="error-text">{{ row.error }}</span>
            <span v-else>-</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="90" fixed="right">
          <template #default="{ row }">
            <el-button size="small" :disabled="row.status === 'pending'"
              @click="router.push(`/executions/${row.id}`)">轨迹</el-button>
          </template>
        </el-table-column>
      </el-table>
    </template>
  </div>
</template>

<style scoped>
.head-row { display: flex; justify-content: space-between; align-items: center; }
.ops { display: flex; gap: 10px; }
.section-title { font-size: 15px; margin: 6px 0 10px; }
.error-text { color: var(--el-color-danger); font-size: 12px; }
.hint { font-size: 12px; color: var(--el-text-color-secondary); }
.score-panel { display: flex; align-items: center; gap: 28px; margin-bottom: 14px; flex-wrap: wrap; }
.score-cards { display: flex; flex-direction: column; gap: 12px; min-width: 200px; }
.score-card { background: #fff; border: 1px solid var(--el-border-color); border-radius: 8px; padding: 14px 18px; }
.score-num { font-size: 30px; font-weight: 700; line-height: 1.1; }
.score-num.c-success { color: var(--el-color-success); }
.score-num.c-warning { color: var(--el-color-warning); }
.score-num.c-danger { color: var(--el-color-danger); }
.score-num.c-info { color: var(--el-text-color-placeholder); }
.score-label { font-size: 13px; margin-top: 4px; }
.score-sub { font-size: 11px; color: var(--el-text-color-secondary); margin-top: 2px; }
.attr-summary { display: flex; gap: 18px; flex-wrap: wrap; margin-bottom: 8px; font-size: 13px; }
.attr-k { color: var(--el-text-color-secondary); }
.attr-k b { color: var(--el-text-color-primary); font-size: 15px; }
.rc-code { font-family: monospace; color: var(--el-color-primary); font-weight: 600; }
</style>
