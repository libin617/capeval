<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Delete, VideoPlay } from '@element-plus/icons-vue'
import {
  autoFixBadCase, deleteBadCase, getRun, listBadCases, updateBadCase,
} from '../api'
import type { BadCaseInfo, BadCaseList, BadCaseOwnership, BadCaseReason, BadCaseSeverity, BadCaseStatus, BadCaseUpdatePayload } from '../api/types'

const router = useRouter()

const REASON_META: Record<string, { label: string; type: any }> = {
  agent_bug: { label: 'Agent 缺陷', type: 'danger' },
  case_bug: { label: '用例缺陷', type: 'warning' },
  rootcause_gap: { label: '根因库缺口', type: 'primary' },
  scoring_bug: { label: '评分口径问题', type: 'warning' },
  false_positive: { label: '归因误报', type: 'info' },
  other: { label: '其他', type: 'info' },
}
const SEVERITY_META: Record<string, { label: string; type: 'success' | 'warning' | 'danger' | 'info' }> = {
  critical: { label: '严重', type: 'danger' },
  high: { label: '高', type: 'warning' },
  medium: { label: '中', type: 'info' },
  low: { label: '低', type: 'success' },
}
const OWNERSHIP_META: Record<string, { label: string; type: any }> = {
  agent_provider: { label: 'Agent 方', type: 'primary' },
  case_maintainer: { label: '用例方', type: 'info' },
  platform: { label: '平台方', type: 'warning' },
}
const STATUS_META: Record<string, { label: string; type: 'info' | 'warning' | 'primary' | 'success' | 'danger' }> = {
  open: { label: '待处理', type: 'danger' },
  triaged: { label: '已指派', type: 'warning' },
  fixed: { label: '已修复', type: 'success' },
  wontfix: { label: '不修复', type: 'info' },
  resolved: { label: '已闭环', type: 'primary' },
}

const loading = ref(false)
const list = ref<BadCaseList | null>(null)
const allItems = ref<BadCaseInfo[]>([])

// 筛选
const filterStatus = ref<BadCaseStatus | ''>('')
const filterReason = ref<BadCaseReason | ''>('')
const filterSeverity = ref<BadCaseSeverity | ''>('')
const filterOwnership = ref<BadCaseOwnership | ''>('')

// 分页
const page = ref(1)
const pageSize = ref(50)
const total = ref(0)

// 详情/编辑对话框
const detailDialog = ref(false)
const detail = ref<BadCaseInfo | null>(null)
const editDialog = ref(false)
const editing = ref<BadCaseInfo | null>(null)
const editStatus = ref<BadCaseStatus>('open')
const editOwner = ref('')
const editSolution = ref('')

// 数据聚合(顶部统计卡片)
const statusAgg = computed<Record<string, number>>(() => list.value?.status_dist ?? {})
const reasonAgg = computed<Record<string, number>>(() => list.value?.reason_dist ?? {})

async function load() {
  loading.value = true
  try {
    const params: Record<string, string | number> = {
      limit: pageSize.value, offset: (page.value - 1) * pageSize.value,
    }
    if (filterStatus.value) params.status = filterStatus.value
    if (filterReason.value) params.reason = filterReason.value
    if (filterSeverity.value) params.severity = filterSeverity.value
    if (filterOwnership.value) params.ownership = filterOwnership.value
    list.value = await listBadCases(params)
    allItems.value = list.value.items
    total.value = list.value.total
  } finally {
    loading.value = false
  }
}

function resetFilters() {
  filterStatus.value = ''
  filterReason.value = ''
  filterSeverity.value = ''
  filterOwnership.value = ''
  page.value = 1
  load()
}

watch([filterStatus, filterReason, filterSeverity, filterOwnership], () => {
  page.value = 1
  load()
})

function openDetail(bc: BadCaseInfo) { detail.value = bc; detailDialog.value = true }

function openEdit(bc: BadCaseInfo) {
  editing.value = bc
  editStatus.value = bc.status
  editOwner.value = bc.owner ?? ''
  editSolution.value = bc.solution ?? ''
  editDialog.value = true
}

async function saveEdit() {
  if (!editing.value) return
  const payload: BadCaseUpdatePayload = {
    status: editStatus.value,
    owner: editOwner.value || null,
    solution: editSolution.value || null,
  }
  try {
    await updateBadCase(editing.value.id, payload)
    ElMessage.success('已更新')
    editDialog.value = false
    editing.value = null
    await load()
  } catch (e) { ElMessage.error((e as Error).message) }
}

async function doAutoFix(bc: BadCaseInfo) {
  try {
    await ElMessageBox.confirm(
      `自动修复将对 BadCase「${bc.reason}」执行:\n`
      + `• 如果是 agent_bug → 禁用关联 Agent\n`
      + `• 如果是 case_bug  → 归档关联用例\n`
      + `• 严重度非 critical 则仅标记 triaged\n\n确认执行?`,
      '自动修复', { type: 'warning' })
  } catch { return }
  try {
    await autoFixBadCase(bc.id)
    ElMessage.success('已执行自动修复')
    await load()
  } catch (e) { ElMessage.error((e as Error).message) }
}

async function doDelete(bc: BadCaseInfo) {
  try {
    await ElMessageBox.confirm(`确认删除 BadCase #${bc.id}?此操作不可撤销`, '删除', { type: 'warning' })
  } catch { return }
  try {
    await deleteBadCase(bc.id)
    ElMessage.success('已删除')
    await load()
  } catch (e) { ElMessage.error((e as Error).message) }
}

async function openRun(bc: BadCaseInfo) {
  try {
    const run = await getRun(bc.run_id)
    router.push(`/runs/${run.id}`)
  } catch { /* ignore */ }
}

const REASON_SUMMARY_LABEL = computed(() =>
  (Object.entries(reasonAgg.value) as [BadCaseReason, number][])
    .sort((a, b) => b[1] - a[1])
    .map(([k, v]) => `${REASON_META[k]?.label ?? k}: ${v}`)
    .join('  | ') || '暂无',
)

onMounted(load)
</script>

<template>
  <div v-loading="loading" class="badcase-page">
    <!-- 顶部统计卡片 -->
    <div class="bc-stats">
      <div class="stat-card total">
        <div class="stat-num">{{ total }}</div>
        <div class="stat-label">BadCase 总数</div>
      </div>
      <div v-for="(cnt, status) in statusAgg" :key="status"
        class="stat-card" :class="`s-${status}`">
        <div class="stat-num">{{ cnt }}</div>
        <div class="stat-label">{{ STATUS_META[status as BadCaseStatus]?.label ?? status }}</div>
      </div>
      <div class="stat-card stat-reason">
        <div class="stat-label">问题分布</div>
        <div class="stat-detail">{{ REASON_SUMMARY_LABEL }}</div>
      </div>
    </div>

    <!-- 筛选条 -->
    <div class="bc-filter">
      <el-select v-model="filterStatus" placeholder="全部状态" clearable size="default" style="width: 140px">
        <el-option v-for="(m, k) in STATUS_META" :key="k" :label="m.label" :value="k" />
      </el-select>
      <el-select v-model="filterReason" placeholder="全部原因" clearable size="default" style="width: 140px">
        <el-option v-for="(m, k) in REASON_META" :key="k" :label="m.label" :value="k" />
      </el-select>
      <el-select v-model="filterSeverity" placeholder="全部严重度" clearable size="default" style="width: 140px">
        <el-option v-for="(m, k) in SEVERITY_META" :key="k" :label="m.label" :value="k" />
      </el-select>
      <el-select v-model="filterOwnership" placeholder="全部归属方" clearable size="default" style="width: 140px">
        <el-option v-for="(m, k) in OWNERSHIP_META" :key="k" :label="m.label" :value="k" />
      </el-select>
      <el-button plain @click="resetFilters">重置</el-button>
    </div>

    <!-- 列表表格 -->
    <el-table :data="allItems" border stripe size="small" style="margin-top: 12px">
      <el-table-column prop="id" label="#" width="70" />
      <el-table-column label="问题分类" width="130">
        <template #default="{ row }">
          <el-tag size="small" :type="REASON_META[row.reason]?.type">{{ REASON_META[row.reason]?.label }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="严重度" width="90">
        <template #default="{ row }">
          <el-tag size="small" :type="SEVERITY_META[row.severity]?.type">{{ SEVERITY_META[row.severity]?.label }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="归属方" width="100">
        <template #default="{ row }">
          <el-tag size="small" :type="OWNERSHIP_META[row.ownership]?.type">{{ OWNERSHIP_META[row.ownership]?.label }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="110">
        <template #default="{ row }">
          <el-tag size="small" :type="STATUS_META[row.status]?.type">{{ STATUS_META[row.status]?.label }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="run_name" label="来源评测任务" min-width="200">
        <template #default="{ row }">
          <el-button size="small" text type="primary" @click="openRun(row)">
            <el-icon><VideoPlay /></el-icon> #{{ row.run_id }} {{ row.run_name }}
          </el-button>
        </template>
      </el-table-column>
      <el-table-column prop="testcase_title" label="评测用例" min-width="180" show-overflow-tooltip />
      <el-table-column label="详细说明" min-width="260" show-overflow-tooltip>
        <template #default="{ row }">
          <span class="detail-text">{{ row.reason_detail?.slice(0, 80) }}{{ row.reason_detail?.length > 80 ? '...' : '' }}</span>
        </template>
      </el-table-column>
      <el-table-column prop="owner" label="处理人" width="110" />
      <el-table-column prop="created_at" label="创建时间" width="170"
        :formatter="(r: any) => r.created_at?.replace('T', ' ').slice(0, 19)" />
      <el-table-column label="操作" width="280" fixed="right">
        <template #default="{ row }">
          <el-button size="small" text type="primary" @click="openDetail(row)">详情</el-button>
          <el-button size="small" text type="warning" @click="openEdit(row)">处理</el-button>
          <el-button size="small" text type="success" :disabled="row.status !== 'open' && row.status !== 'triaged'"
            @click="doAutoFix(row)">自动修复</el-button>
          <el-button size="small" text type="danger" @click="doDelete(row)">
            <el-icon><Delete /></el-icon>
          </el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-pagination v-if="total > pageSize" style="margin-top: 12px"
      v-model:current-page="page" v-model:page-size="pageSize"
      :total="total" :page-sizes="[20, 50, 100]" background
      layout="total, sizes, prev, pager, next, jumper"
      @size-change="load" @current-change="load" />

    <!-- 详情对话框 -->
    <el-dialog v-model="detailDialog" width="720px" :title="`BadCase #${detail?.id} · ${REASON_META[detail?.reason as string]?.label}`">
      <template v-if="detail">
        <el-descriptions :column="2" border size="small">
          <el-descriptions-item label="问题分类">
            <el-tag :type="REASON_META[detail.reason]?.type">{{ REASON_META[detail.reason]?.label }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="严重度">
            <el-tag :type="SEVERITY_META[detail.severity]?.type">{{ SEVERITY_META[detail.severity]?.label }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="归属方">
            <el-tag :type="OWNERSHIP_META[detail.ownership]?.type">{{ OWNERSHIP_META[detail.ownership]?.label }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="状态">
            <el-tag :type="STATUS_META[detail.status]?.type">{{ STATUS_META[detail.status]?.label }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="处理人">{{ detail.owner || '未指派' }}</el-descriptions-item>
          <el-descriptions-item label="状态创建">{{ detail.created_at?.replace('T', ' ').slice(0, 19) }}</el-descriptions-item>
          <el-descriptions-item label="评测任务" :span="2">
            <el-button size="small" text @click="openRun(detail)">
              #{{ detail.run_id }} {{ detail.run_name }}
            </el-button>
          </el-descriptions-item>
          <el-descriptions-item label="评测用例" :span="2">
            {{ detail.testcase_title }} (#{{ detail.testcase_id }})
          </el-descriptions-item>
          <el-descriptions-item label="关联归因 IDs" :span="2">
            <span v-if="detail.attribution_ids?.length">{{ detail.attribution_ids.join(', ') }}</span>
            <span v-else class="hint">无</span>
          </el-descriptions-item>
        </el-descriptions>
        <div style="margin-top: 14px">
          <h4 class="detail-sub">问题描述</h4>
          <div class="detail-block">{{ detail.reason_detail }}</div>
        </div>
        <div v-if="detail.solution" style="margin-top: 12px">
          <h4 class="detail-sub">解决方案</h4>
          <div class="detail-block">
            <el-tag v-if="detail.solution_type" size="small" style="margin-right: 6px" effect="plain">
              {{ detail.solution_type }}
            </el-tag>
            {{ detail.solution }}
          </div>
        </div>
        <div v-if="detail.resolved_at" style="margin-top: 8px" class="detail-hint">
          已闭环于 {{ detail.resolved_at.replace('T', ' ').slice(0, 19) }}
        </div>
      </template>
    </el-dialog>

    <!-- 处理对话框 -->
    <el-dialog v-model="editDialog" width="560px" title="处理 BadCase">
      <template v-if="editing">
        <el-form label-position="top">
          <el-form-item label="处理状态">
            <el-radio-group v-model="editStatus" size="default">
              <el-radio-button v-for="(m, k) in STATUS_META" :key="k" :value="k"
                :disabled="k === 'open' && editing.status !== 'open'">
                {{ m.label }}
              </el-radio-button>
            </el-radio-group>
          </el-form-item>
          <el-form-item label="指派处理人">
            <el-input v-model="editOwner" placeholder="留空表示未指派" />
          </el-form-item>
          <el-form-item label="解决方案">
            <el-input v-model="editSolution" type="textarea" :rows="3"
              placeholder="写一下怎么解决的(如果 status=fixed 或 resolved)" />
          </el-form-item>
          <el-alert v-if="editStatus === 'resolved'" type="warning" :closable="false"
            title="标记为已闭环后将自动记录 resolved_at 时间戳,问题从 BadCase 流水中关闭" />
        </el-form>
      </template>
      <template #footer>
        <el-button @click="editDialog = false">取消</el-button>
        <el-button type="primary" @click="saveEdit">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.bc-stats { display: flex; gap: 12px; margin-bottom: 14px; flex-wrap: wrap; }
.stat-card {
  flex: 0 0 140px;
  padding: 14px 16px;
  border-radius: 8px;
  border: 1px solid var(--el-border-color-lighter);
  background: var(--el-bg-color);
}
.stat-card.stat-reason { flex: 1 1 260px; }
.stat-num { font-size: 28px; font-weight: 700; line-height: 1.2; }
.stat-label { font-size: 12px; color: var(--el-text-color-secondary); margin-top: 4px; }
.stat-detail { font-size: 12px; color: var(--el-text-color-primary); margin-top: 6px; }
.stat-card.s-open .stat-num { color: var(--el-color-danger); }
.stat-card.s-triaged .stat-num { color: var(--el-color-warning); }
.stat-card.s-fixed .stat-num { color: var(--el-color-success); }
.stat-card.s-wontfix .stat-num { color: var(--el-text-color-secondary); }
.stat-card.s-resolved .stat-num { color: var(--el-color-primary); }

.bc-filter { display: flex; gap: 8px; align-items: center; }

.detail-text { font-size: 12px; color: var(--el-text-color-secondary); }
.hint { font-size: 12px; color: var(--el-text-color-placeholder); }

.detail-sub { font-size: 13px; margin: 0 0 6px; font-weight: 600; color: var(--el-text-color-primary); }
.detail-block {
  padding: 10px 12px; border-radius: 6px;
  background: var(--el-fill-color-lighter);
  font-size: 13px; color: var(--el-text-color-primary);
  white-space: pre-wrap; word-break: break-word;
  border-left: 3px solid var(--el-color-primary-light-5);
}
.detail-hint { font-size: 12px; color: var(--el-text-color-secondary); font-style: italic; }
</style>
