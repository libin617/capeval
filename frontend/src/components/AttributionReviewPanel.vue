<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  createBadCase, getExecutionAttributions, listRootCauses, reviewAttribution,
} from '../api'
import type {
  AttributionInfo, AttributionReviewPayload, BadCaseOwnership, BadCaseReason, BadCaseSeverity,
  CaseAttribution, RootCause, RunAttributions,
} from '../api/types'

const props = defineProps<{
  runId: number
  runAttributions: RunAttributions
}>()

// 全量根因库（给"改判根因"选择器用）
const rootCauses = ref<RootCause[]>([])
// 逐 case 的完整 attr 列表（key=execution_id）
const caseAttrs = ref<Record<number, AttributionInfo[]>>({})
const loading = ref(true)
// 展开的 case
const expandedCases = ref<Set<number>>(new Set())
// 证据链展开的 attr
const expandedEvidence = ref<Set<number>>(new Set())
// 改判对话框
const correctDialog = ref(false)
const correctingAttr = ref<AttributionInfo | null>(null)
const correctingNote = ref('')
const rootCauseFilter = ref('')

// BadCase 对话框
const badCaseDialog = ref(false)
const badCaseTargetExecId = ref<number | null>(null)
const badCaseReason = ref<BadCaseReason>('agent_bug')
const badCaseSeverity = ref<BadCaseSeverity>('high')
const badCaseOwnership = ref<BadCaseOwnership>('platform')
const badCaseDetail = ref('')

const BADCASE_REASON_META: Record<BadCaseReason, { label: string; hint: string }> = {
  agent_bug: { label: 'Agent 能力缺陷', hint: 'Agent 在该场景下本身能力不足或行为异常,回 Agent 方改进' },
  case_bug: { label: '评测用例设计缺陷', hint: '验收命令错/gold_patch 错/语义要点错,回用例维护方修正' },
  rootcause_gap: { label: '根因库缺口', hint: '归因引擎未命中正确根因,回根因库增补条目' },
  scoring_bug: { label: '评分口径问题', hint: '评分规则给错分,回评分规则修正' },
  false_positive: { label: '归因误报', hint: '复核工作台已标记误报的 attr 汇总' },
  other: { label: '其他', hint: '以上都不是,请在详细说明里描述' },
}

const BADCASE_SEVERITY_META: Record<BadCaseSeverity, { label: string; type: 'success' | 'warning' | 'danger' | 'info' }> = {
  critical: { label: '严重', type: 'danger' },
  high: { label: '高', type: 'warning' },
  medium: { label: '中', type: 'info' },
  low: { label: '低', type: 'success' },
}

const BADCASE_OWNERSHIP_META: Record<BadCaseOwnership, string> = {
  agent_provider: 'Agent 方',
  case_maintainer: '用例维护方',
  platform: '平台方',
}

function openBadCase(cg: { execution_id: number; testcase_title: string; attrs: AttributionInfo[] }) {
  badCaseTargetExecId.value = cg.execution_id
  // 预填说明:列出关联的根因判定
  const rcList = cg.attrs.map((a) => `${a.rootcause.code} ${a.rootcause.name}(conf=${(a.confidence * 100).toFixed(0)}%)`).join('; ')
  badCaseDetail.value = `用例「${cg.testcase_title}」关联根因:${rcList}\n\n问题描述:`
  badCaseReason.value = 'agent_bug'
  badCaseSeverity.value = 'high'
  badCaseOwnership.value = 'platform'
  badCaseDialog.value = true
}

async function submitBadCase() {
  if (!badCaseTargetExecId.value) return
  const detail = badCaseDetail.value.trim()
  if (!detail || detail.length < 5) {
    ElMessage.warning('请填写至少 5 个字符的详细说明')
    return
  }
  try {
    await createBadCase({
      run_id: props.runId,
      // testcase_id 从 runAttributions.cases 里找
      testcase_id: props.runAttributions.cases.find(
        (c) => c.execution_id === badCaseTargetExecId.value,
      )?.testcase_id ?? 0,
      case_execution_id: badCaseTargetExecId.value,
      attribution_ids: caseAttrs.value[badCaseTargetExecId.value]?.map((a) => a.id) || null,
      reason: badCaseReason.value,
      reason_detail: detail,
      severity: badCaseSeverity.value,
      ownership: badCaseOwnership.value,
    })
    ElMessage.success('已提为 BadCase')
    badCaseDialog.value = false
  } catch (e) {
    ElMessage.error((e as Error).message)
  }
}

// 已加载标记：避免重复请求
const loaded = ref(false)

async function load() {
  if (loaded.value) return
  loading.value = true
  try {
    rootCauses.value = await listRootCauses()
    // 遍历 run 的每个 case（从 RunAttributions.cases 拿 execution_id）
    const promises = props.runAttributions.cases.map(async (c: CaseAttribution) => {
      try {
        caseAttrs.value[c.execution_id] = await getExecutionAttributions(c.execution_id)
      } catch {
        caseAttrs.value[c.execution_id] = []
      }
    })
    await Promise.all(promises)
    // 默认展开第一个有 pending 的 case
    const firstPending = props.runAttributions.cases.find(
      (c: CaseAttribution) => (caseAttrs.value[c.execution_id] || []).some((a) => a.review_status === 'pending'),
    )
    if (firstPending) expandedCases.value.add(firstPending.execution_id)
    loaded.value = true
  } finally {
    loading.value = false
  }
}

onMounted(load)
watch(() => props.runAttributions, load, { deep: false })

// ---- 统计 ----
const allAttrs = computed<AttributionInfo[]>(() =>
  Object.values(caseAttrs.value).flat(),
)
const pendingCount = computed(() =>
  allAttrs.value.filter((a) => a.review_status === 'pending').length,
)
const confirmedCount = computed(() =>
  allAttrs.value.filter((a) => a.review_status === 'confirmed').length,
)
const correctedCount = computed(() =>
  allAttrs.value.filter((a) => a.review_status === 'corrected').length,
)
const rejectedCount = computed(() =>
  allAttrs.value.filter((a) => a.review_status === 'rejected').length,
)
const notSampledCount = computed(() =>
  allAttrs.value.filter((a) => a.review_status === 'not_sampled').length,
)

const REVIEW_STATUS_META: Record<string, { label: string; type: 'info' | 'warning' | 'primary' | 'success' | 'danger' }> = {
  pending: { label: '待复核', type: 'warning' },
  confirmed: { label: '已确认', type: 'success' },
  corrected: { label: '已改判', type: 'primary' },
  rejected: { label: '已误报', type: 'danger' },
  not_sampled: { label: '未抽检', type: 'info' },
}

const SOURCE_META: Record<string, { label: string; type: 'primary' | 'success' | 'warning' | 'info' }> = {
  rule: { label: '规则', type: 'info' },
  llm: { label: 'LLM', type: 'warning' },
  human: { label: '人工', type: 'primary' },
}

// ---- Case 分组展示 ----
const caseGroups = computed(() => {
  return props.runAttributions.cases.map((c: CaseAttribution) => ({
    ...c,
    attrs: caseAttrs.value[c.execution_id] || [],
  }))
})

function toggleEvidence(attrId: number) {
  if (expandedEvidence.value.has(attrId)) expandedEvidence.value.delete(attrId)
  else expandedEvidence.value.add(attrId)
}

// ---- 操作 ----
async function doConfirm(attr: AttributionInfo) {
  try {
    const res = await ElMessageBox.prompt(
      `确认根因「${attr.rootcause.code} ${attr.rootcause.name}」正确?`,
      '确认复核结论',
      {
        confirmButtonText: '确认',
        cancelButtonText: '取消',
        inputPlaceholder: '备注(可选)',
        inputValue: attr.review_note,
      },
    )
    const payload: AttributionReviewPayload = { action: 'confirm', note: res.value }
    await reviewAttribution(attr.id, payload)
    ElMessage.success('已确认')
    await refreshOne(attr.execution_id)
  } catch { /* 用户取消 */ }
}

async function doReject(attr: AttributionInfo) {
  try {
    const res = await ElMessageBox.prompt(
      '标记该归因结论为误报(根因库条目命中错误)?',
      '标记误报',
      {
        type: 'warning',
        confirmButtonText: '确认误报',
        cancelButtonText: '取消',
        inputPlaceholder: '备注(可选,说明为什么是误报)',
        inputValue: attr.review_note,
      },
    )
    const payload: AttributionReviewPayload = { action: 'reject', note: res.value }
    await reviewAttribution(attr.id, payload)
    ElMessage.success('已标记误报')
    await refreshOne(attr.execution_id)
  } catch { /* 用户取消 */ }
}

function openCorrect(attr: AttributionInfo) {
  correctingAttr.value = attr
  correctingNote.value = attr.review_note
  rootCauseFilter.value = ''
  correctDialog.value = true
}

async function doCorrect(targetRc: RootCause) {
  if (!correctingAttr.value) return
  try {
    const payload: AttributionReviewPayload = {
      action: 'correct',
      rootcause_id: targetRc.id,
      note: correctingNote.value || `改判为 ${targetRc.code} ${targetRc.name}`,
    }
    await reviewAttribution(correctingAttr.value.id, payload)
    ElMessage.success(`已改判为 ${targetRc.code}`)
    correctDialog.value = false
    await refreshOne(correctingAttr.value.execution_id)
  } catch (e) {
    ElMessage.error((e as Error).message)
  }
}

async function refreshOne(execId: number) {
  try {
    caseAttrs.value[execId] = await getExecutionAttributions(execId)
  } catch { /* ignore */ }
}

async function batchConfirmAllPending() {
  const pending = allAttrs.value.filter((a) => a.review_status === 'pending')
  if (!pending.length) { ElMessage.info('没有待复核项'); return }
  try {
    await ElMessageBox.confirm(
      `批量确认全部 ${pending.length} 条待复核结论为正确?此操作不可撤销(可在复核历史里追溯)。`,
      '批量确认', { type: 'warning' },
    )
  } catch { return }
  let ok = 0, fail = 0
  for (const a of pending) {
    try {
      await reviewAttribution(a.id, { action: 'confirm' })
      ok++
    } catch { fail++ }
  }
  ElMessage.success(`批量确认完成:成功 ${ok},失败 ${fail}`)
  // 重新加载所有
  for (const execId of Object.keys(caseAttrs.value)) {
    await refreshOne(Number(execId))
  }
}

// 根因库按 level1 分组
const rootCauseGroups = computed(() => {
  const enabled = rootCauses.value.filter((r) => r.enabled)
  const groups: Record<string, RootCause[]> = {}
  for (const rc of enabled) {
    const key = rc.level === 1 ? `${rc.code} ${rc.name}` : `· ${rc.code} ${rc.name}`
    if (!groups[key.split(' ')[0].replace('·', '').trim()]) {
      groups[key.split(' ')[0].replace('·', '').trim()] = []
    }
  }
  // 简化:按 level1 分组
  const level1 = enabled.filter((r) => r.level === 1)
  const out: { key: string; level1: RootCause; children: RootCause[] }[] = level1.map((l1) => ({
    key: `${l1.code}-${l1.id}`,
    level1: l1,
    children: enabled.filter((r) => r.parent_id === l1.id),
  }))
  // 三级也挂到对应二级
  const level2 = enabled.filter((r) => r.level === 2)
  for (const l2 of level2) {
    const parent = out.find((g) => g.level1.id === l2.parent_id)
    if (parent) {
      const l3s = enabled.filter((r) => r.parent_id === l2.id)
      parent.children.push(l2, ...l3s)
    }
  }
  return out
})

const filteredRootCauseGroups = computed(() => {
  if (!rootCauseFilter.value.trim()) return rootCauseGroups.value
  const q = rootCauseFilter.value.trim().toLowerCase()
  return rootCauseGroups.value
    .map((g) => ({
      ...g,
      children: g.children.filter((c) =>
        `${c.code} ${c.name}`.toLowerCase().includes(q),
      ),
    }))
    .filter((g) => g.children.length > 0 ||
      `${g.level1.code} ${g.level1.name}`.toLowerCase().includes(q))
})

function fmtConfidence(c: number) {
  return `${(c * 100).toFixed(0)}%`
}

function confidenceTag(c: number): 'success' | 'warning' | 'danger' {
  if (c >= 0.8) return 'success'
  if (c >= 0.6) return 'warning'
  return 'danger'
}
</script>

<template>
  <div class="review-panel" v-loading="loading">
    <!-- 顶部统计卡片 -->
    <div class="review-summary">
      <div class="review-stat stat-pending">
        <div class="stat-num">{{ pendingCount }}</div>
        <div class="stat-label">待复核</div>
      </div>
      <div class="review-stat stat-confirmed">
        <div class="stat-num">{{ confirmedCount }}</div>
        <div class="stat-label">已确认</div>
      </div>
      <div class="review-stat stat-corrected">
        <div class="stat-num">{{ correctedCount }}</div>
        <div class="stat-label">已改判</div>
      </div>
      <div class="review-stat stat-rejected">
        <div class="stat-num">{{ rejectedCount }}</div>
        <div class="stat-label">已误报</div>
      </div>
      <div class="review-stat stat-not-sampled">
        <div class="stat-num">{{ notSampledCount }}</div>
        <div class="stat-label">未抽检(放行)</div>
      </div>
      <div class="review-actions">
        <el-button type="warning" :disabled="pendingCount === 0" @click="batchConfirmAllPending">
          批量确认全部待复核
        </el-button>
      </div>
    </div>

    <!-- Case 分组列表 -->
    <div v-if="caseGroups.length === 0" class="review-empty">
      该评测任务无归因结论需要复核(可能全部用例成功、或归因引擎未命中)
    </div>

    <el-collapse v-model="expandedCases" accordion style="margin-top: 16px">
      <el-collapse-item v-for="cg in caseGroups" :key="cg.execution_id" :name="cg.execution_id">
        <template #title>
          <div class="case-header">
            <span class="case-title">
              {{ cg.testcase_title }}
              <el-tag size="small" effect="plain" style="margin-left: 6px"
                :type="cg.status === 'failed' ? 'danger' : 'success'">
                {{ cg.status === 'failed' ? '失败' : '成功' }}
              </el-tag>
            </span>
            <span class="case-meta">
              <el-tag v-for="s in ['pending','confirmed','corrected','rejected','not_sampled']" :key="s"
                size="small" style="margin-left: 4px"
                v-show="cg.attrs.filter((a) => a.review_status === s).length > 0"
                :type="REVIEW_STATUS_META[s].type">
                {{ REVIEW_STATUS_META[s].label }} {{ cg.attrs.filter((a) => a.review_status === s).length }}
              </el-tag>
              <el-button size="small" type="danger" plain style="margin-left: 8px"
                @click="openBadCase(cg)">📌 提为 BadCase</el-button>
            </span>
          </div>
        </template>

        <!-- Case 内的每条 attr -->
        <div v-for="attr in cg.attrs" :key="attr.id" class="attr-card">
          <div class="attr-head">
            <el-tag size="small" :type="REVIEW_STATUS_META[attr.review_status].type" effect="dark">
              {{ REVIEW_STATUS_META[attr.review_status].label }}
            </el-tag>
            <span v-if="attr.is_primary" class="primary-badge">主根因</span>
            <span v-else class="secondary-badge">次根因</span>
            <el-tag size="small" :type="confidenceTag(attr.confidence)" effect="plain">
              置信度 {{ fmtConfidence(attr.confidence) }}
            </el-tag>
            <el-tag size="small" :type="SOURCE_META[attr.source]?.type || 'info'" effect="plain">
              {{ SOURCE_META[attr.source]?.label || attr.source }}
            </el-tag>
            <span class="attr-rc">
              <el-link type="primary" :underline="false">
                {{ attr.rootcause.code }} {{ attr.rootcause.name }}
              </el-link>
              <el-tag size="small" type="info" effect="plain" style="margin-left: 4px">
                {{ attr.rootcause.responsibility }}
              </el-tag>
            </span>
            <div class="attr-actions">
              <el-button size="small" text type="success" :disabled="attr.review_status !== 'pending'"
                @click="doConfirm(attr)">✓ 确认正确</el-button>
              <el-button size="small" text type="primary" :disabled="attr.review_status === 'confirmed' || attr.review_status === 'rejected'"
                @click="openCorrect(attr)">↻ 改判根因</el-button>
              <el-button size="small" text type="danger" :disabled="attr.review_status !== 'pending'"
                @click="doReject(attr)">✗ 标记误报</el-button>
            </div>
          </div>

          <!-- 证据链 & 复核历史 -->
          <div class="attr-detail">
            <el-button size="small" text @click="toggleEvidence(attr.id)">
              {{ expandedEvidence.has(attr.id) ? '收起证据链 ▲' : '展开证据链 ▼' }}
            </el-button>
            <template v-if="expandedEvidence.has(attr.id)">
              <div v-if="attr.evidence?.signals?.length" class="evidence-block">
                <div class="ev-label">触发信号:</div>
                <el-tag v-for="s in attr.evidence.signals" :key="s" size="small" style="margin: 2px"
                  type="warning" effect="plain">{{ s }}</el-tag>
              </div>
              <div v-if="attr.evidence?.event_seqs?.length" class="evidence-block">
                <div class="ev-label">关联轨迹事件序号:</div>
                <span class="ev-val">{{ attr.evidence.event_seqs.join(', ') }}</span>
              </div>
              <div v-if="attr.evidence?.details?.length" class="evidence-block">
                <div class="ev-label">详细说明:</div>
                <div v-for="(d, i) in attr.evidence.details" :key="i" class="ev-detail-line">{{ d }}</div>
              </div>
              <div v-if="attr.review_history?.length" class="history-block">
                <div class="ev-label">复核历史:</div>
                <el-timeline>
                  <el-timeline-item v-for="(h, i) in attr.review_history" :key="i"
                    :timestamp="h.at" placement="top" size="small">
                    <template #default>
                      <el-tag size="small" effect="plain">{{ h.action }}</el-tag>
                      <span class="hist-by">{{ h.by }}</span>
                      <span v-if="h.from_rootcause_id !== h.to_rootcause_id" class="hist-rc">
                        根因 {{ h.from_rootcause_id ?? '—' }} → {{ h.to_rootcause_id ?? '—' }}
                      </span>
                      <span v-if="h.note" class="hist-note">· {{ h.note }}</span>
                    </template>
                  </el-timeline-item>
                </el-timeline>
              </div>
              <div v-if="attr.review_note && !attr.review_history?.length" class="ev-note">
                备注: {{ attr.review_note }}
              </div>
              <div v-if="!attr.evidence?.signals?.length && !attr.evidence?.event_seqs?.length && !attr.evidence?.details?.length"
                class="ev-empty">
                无证据链详情
              </div>
            </template>
          </div>
        </div>
      </el-collapse-item>
    </el-collapse>

    <!-- 改判根因对话框 -->
    <el-dialog v-model="correctDialog" width="640px" :title="`改判根因 · ${correctingAttr?.rootcause.code || ''}`">
      <div v-if="correctingAttr" class="correct-dialog">
        <el-alert type="info" :closable="false" style="margin-bottom: 12px">
          原根因: <b>{{ correctingAttr.rootcause.code }} {{ correctingAttr.rootcause.name }}</b>
          · 置信度 {{ fmtConfidence(correctingAttr.confidence) }} · 来源 {{ correctingAttr.source }}
        </el-alert>
        <el-input v-model="rootCauseFilter" placeholder="搜索根因编码或名称" clearable style="margin-bottom: 12px" />
        <div class="rc-tree">
          <div v-for="g in filteredRootCauseGroups" :key="g.key" class="rc-group">
            <div class="rc-level1">
              <el-tag size="small" effect="dark" type="primary">{{ g.level1.code }}</el-tag>
              <span class="rc-l1-name">{{ g.level1.name }}</span>
            </div>
            <div v-for="rc in g.children" :key="rc.id"
              class="rc-item"
              :class="{ 'rc-selected': correctingAttr.rootcause.id === rc.id }">
              <el-button size="small" text type="primary"
                :disabled="correctingAttr.rootcause.id === rc.id"
                @click="doCorrect(rc)">
                {{ rc.code }} · {{ rc.name }}
                <el-tag size="small" effect="plain" style="margin-left: 4px">{{ rc.responsibility }}</el-tag>
              </el-button>
            </div>
          </div>
          <div v-if="filteredRootCauseGroups.length === 0" class="rc-empty">
            无匹配根因
          </div>
        </div>
        <el-input v-model="correctingNote" type="textarea" :rows="2" placeholder="改判说明(可选)"
          style="margin-top: 12px" />
      </div>
      <template #footer>
        <el-button @click="correctDialog = false">取消</el-button>
      </template>
    </el-dialog>

    <!-- BadCase 对话框 -->
    <el-dialog v-model="badCaseDialog" width="600px" title="标记为 BadCase(回流到 Agent/用例/根因库)">
      <el-form label-position="top">
        <el-form-item label="问题分类(决定回流方向)">
          <el-radio-group v-model="badCaseReason" size="default">
            <el-radio-button v-for="(m, k) in BADCASE_REASON_META" :key="k" :value="k as BadCaseReason"
              :title="m.hint">
              {{ m.label }}
            </el-radio-button>
          </el-radio-group>
          <div class="bc-hint">{{ BADCASE_REASON_META[badCaseReason].hint }}</div>
        </el-form-item>
        <div class="bc-row">
          <el-form-item label="严重度" style="flex: 1">
            <el-select v-model="badCaseSeverity" style="width: 100%">
              <el-option v-for="(m, k) in BADCASE_SEVERITY_META" :key="k" :label="m.label"
                :value="k" />
            </el-select>
          </el-form-item>
          <el-form-item label="归属方" style="flex: 1">
            <el-select v-model="badCaseOwnership" style="width: 100%">
              <el-option v-for="(label, k) in BADCASE_OWNERSHIP_META" :key="k" :label="label"
                :value="k" />
            </el-select>
          </el-form-item>
        </div>
        <el-form-item label="详细说明">
          <el-input v-model="badCaseDetail" type="textarea" :rows="5" maxlength="4000"
            show-word-limit placeholder="请描述具体问题现象、期望行为、复现条件..." />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="badCaseDialog = false">取消</el-button>
        <el-button type="primary" @click="submitBadCase">确认提交</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.review-summary {
  display: flex;
  gap: 12px;
  align-items: stretch;
  margin-bottom: 10px;
  flex-wrap: wrap;
}
.review-stat {
  flex: 0 0 110px;
  padding: 12px;
  border-radius: 8px;
  text-align: center;
  border: 1px solid var(--el-border-color-lighter);
  background: var(--el-bg-color);
}
.stat-num { font-size: 24px; font-weight: 700; line-height: 1.2; }
.stat-label { font-size: 12px; color: var(--el-text-color-secondary); margin-top: 4px; }
.stat-pending .stat-num { color: var(--el-color-warning); }
.stat-confirmed .stat-num { color: var(--el-color-success); }
.stat-corrected .stat-num { color: var(--el-color-primary); }
.stat-rejected .stat-num { color: var(--el-color-danger); }
.stat-not-sampled .stat-num { color: var(--el-text-color-secondary); }

.review-actions {
  margin-left: auto;
  display: flex;
  align-items: center;
}

.case-header { display: flex; gap: 12px; align-items: center; flex: 1; }
.case-title { font-weight: 600; font-size: 14px; }
.case-meta { margin-left: auto; display: flex; gap: 4px; }

.attr-card {
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  padding: 10px 12px;
  margin-bottom: 8px;
  background: var(--el-bg-color-page);
}
.attr-head { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.primary-badge {
  background: var(--el-color-primary-light-9);
  color: var(--el-color-primary);
  font-size: 12px;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 10px;
}
.secondary-badge {
  background: var(--el-text-color-placeholder);
  color: #fff;
  font-size: 12px;
  padding: 2px 8px;
  border-radius: 10px;
}
.attr-rc { margin-left: auto; display: flex; align-items: center; gap: 4px; }
.attr-actions { display: flex; gap: 4px; }

.attr-detail { margin-top: 8px; }
.evidence-block { margin-top: 6px; }
.ev-label { font-size: 12px; color: var(--el-text-color-secondary); margin-bottom: 4px; }
.ev-val { font-family: monospace; font-size: 12px; }
.ev-detail-line { font-size: 12px; padding: 2px 0; color: var(--el-text-color-primary); }
.ev-note { font-size: 12px; color: var(--el-text-color-secondary); margin-top: 4px; }
.ev-empty { font-size: 12px; color: var(--el-text-color-placeholder); font-style: italic; }

.history-block { margin-top: 10px; }
.hist-by { font-size: 12px; color: var(--el-text-color-secondary); margin-left: 4px; }
.hist-rc { font-size: 12px; color: var(--el-text-color-secondary); margin-left: 6px; }
.hist-note { font-size: 12px; color: var(--el-text-color-secondary); margin-left: 6px; }

/* 改判对话框 */
.rc-tree { max-height: 360px; overflow-y: auto; border: 1px solid var(--el-border-color-lighter); border-radius: 6px; padding: 8px; }
.rc-group { margin-bottom: 8px; }
.rc-level1 {
  font-size: 13px; font-weight: 600; padding: 4px 0;
  display: flex; align-items: center; gap: 8px;
}
.rc-l1-name { color: var(--el-text-color-primary); }
.rc-item { padding: 2px 0 2px 24px; }
.rc-selected { background: var(--el-color-primary-light-9); }
.rc-empty { font-size: 13px; color: var(--el-text-color-placeholder); padding: 12px; text-align: center; }

.review-empty { text-align: center; padding: 32px; color: var(--el-text-color-placeholder); font-size: 14px; }
.bc-row { display: flex; gap: 12px; }
.bc-hint { font-size: 12px; color: var(--el-text-color-secondary); margin-top: 4px; padding: 4px 8px; background: var(--el-color-info-light-9); border-radius: 4px; }
</style>
