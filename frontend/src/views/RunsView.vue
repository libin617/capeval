<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  cancelRun, deleteRun, listAgents, listCaseSets, listJudgeConfigs, listRuns, listWeightTemplates,
  retryRun, submitRun, updateRun,
} from '../api'
import type { AgentInfo, CaseSet, JudgeConfig, RunInfo, RunStatus, WeightTemplate } from '../api/types'

const router = useRouter()
const runs = ref<RunInfo[]>([])
const loading = ref(false)
const statusFilter = ref('')
const keyword = ref('')

// ---------- 前端分页(默认每页 10 条) ----------
const page = ref(1)
const PAGE_SIZE = 10
const pagedRuns = computed<RunInfo[]>(() => {
  const start = (page.value - 1) * PAGE_SIZE
  return runs.value.slice(start, start + PAGE_SIZE)
})
let timer: ReturnType<typeof setInterval> | null = null

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

const TERMINAL: RunStatus[] = ['completed', 'failed', 'canceled']
/** 可删除状态:草稿 + 终态;进行中需先取消 */
const DELETABLE: RunStatus[] = ['draft', 'completed', 'failed', 'canceled']

// ---------- 草稿任务编辑 ----------
const editVisible = ref(false)
const editRow = ref<RunInfo | null>(null)
const editSaving = ref(false)
const editAgents = ref<AgentInfo[]>([])
const editSets = ref<CaseSet[]>([])
const editWeights = ref<WeightTemplate[]>([])
const editJudges = ref<JudgeConfig[]>([])
const editForm = reactive({
  name: '', agent_id: 0, caseset_version_id: 0, weight_template_id: 0, fail_threshold: 0.5,
  pass_k: 1,
  judge_config_id: null as number | null,
})

const editVersionOptions = computed(() =>
  editSets.value.flatMap((s) =>
    s.versions
      .filter((v) => v.status === 'published')
      .map((v) => ({ id: v.id, label: `${s.name} v${v.version_no}(${v.items.length} 个评测用例)` })),
  ),
)

async function openEdit(row: RunInfo) {
  editRow.value = row
  try {
    if (!editAgents.value.length) {
      ;[editAgents.value, editSets.value, editWeights.value, editJudges.value] = await Promise.all([
        listAgents(), listCaseSets(), listWeightTemplates(), listJudgeConfigs(),
      ])
    }
  } catch (e) { ElMessage.error((e as Error).message); return }
  Object.assign(editForm, {
    name: row.name, agent_id: row.agent_id, caseset_version_id: row.caseset_version_id,
    weight_template_id: row.weight_template_id ?? 0, fail_threshold: row.fail_threshold,
    pass_k: row.pass_k ?? 1,
    judge_config_id: row.judge_config_id ?? null,
  })
  editVisible.value = true
}

async function submitEdit() {
  editSaving.value = true
  try {
    await updateRun(editRow.value!.id, {
      name: editForm.name, agent_id: editForm.agent_id,
      caseset_version_id: editForm.caseset_version_id,
      weight_template_id: editForm.weight_template_id, fail_threshold: editForm.fail_threshold,
      pass_k: editForm.pass_k,
      judge_config_id: editForm.judge_config_id ?? null,
    })
    ElMessage.success('已保存')
    editVisible.value = false
    await refresh(false)
  } catch (e) {
    ElMessage.error((e as Error).message)
  } finally {
    editSaving.value = false
  }
}

async function refresh(resetPage = true) {
  try {
    const params: Record<string, string> = {}
    if (statusFilter.value) params.status = statusFilter.value
    if (keyword.value.trim()) params.q = keyword.value.trim()
    runs.value = await listRuns(Object.keys(params).length ? params : undefined)
    if (resetPage) {
      page.value = 1
    } else {
      // 保护:如果当前页超过新数据总页数(比如筛选后 run 变少),自动 clamp
      const maxPage = Math.max(1, Math.ceil(runs.value.length / PAGE_SIZE))
      if (page.value > maxPage) page.value = maxPage
    }
  } finally {
    loading.value = false
  }
}

function setupPolling() {
  stopPolling()
  timer = setInterval(() => {
    if (runs.value.some((r) => !TERMINAL.includes(r.status) && r.status !== 'draft')) refresh(false)
  }, 3000)
}

function stopPolling() {
  if (timer) { clearInterval(timer); timer = null }
}

function fmtTime(t: string | null) {
  return t ? t.replace('T', ' ').slice(0, 19) : '-'
}

function progressPercent(row: RunInfo) {
  const p = row.progress
  return p.total ? Math.round(((p.succeeded + p.failed + p.canceled) / p.total) * 100) : 0
}

function progressStatus(row: RunInfo) {
  if (row.status === 'failed') return 'exception'
  if (row.status === 'completed') return 'success'
  return ''
}

async function onSubmit(row: RunInfo) {
  try {
    await submitRun(row.id)
    ElMessage.success(`Run #${row.id} 已提交`)
    await refresh(false)
  } catch (e) { ElMessage.error((e as Error).message) }
}

async function onCancel(row: RunInfo) {
  try {
    await ElMessageBox.confirm(`确认取消 Run #${row.id}「${row.name}」?执行中的用例将在当前用例结束后停止。`, '取消确认', { type: 'warning' })
  } catch { return }
  try {
    await cancelRun(row.id)
    ElMessage.success('已取消')
    await refresh(false)
  } catch (e) { ElMessage.error((e as Error).message) }
}

async function onRetry(row: RunInfo) {
  try {
    await retryRun(row.id)
    ElMessage.success(`Run #${row.id} 已重新排队(仅重跑失败用例)`)
    await refresh(false)
  } catch (e) { ElMessage.error((e as Error).message) }
}

async function onDelete(row: RunInfo) {
  const tip = row.status === 'draft'
    ? `确认删除草稿任务 Run #${row.id}「${row.name}」?`
    : `确认删除任务 Run #${row.id}「${row.name}」?其执行记录、评分、归因与报告将一并删除,不可恢复。`
  try {
    await ElMessageBox.confirm(tip, '删除确认', { type: 'warning' })
  } catch { return }
  try {
    await deleteRun(row.id)
    ElMessage.success('已删除')
    await refresh(false)
  } catch (e) { ElMessage.error((e as Error).message) }
}

onMounted(async () => {
  loading.value = true
  await refresh()
  setupPolling()
})
onUnmounted(stopPolling)
</script>

<template>
  <div>
    <div class="toolbar">
      <el-button type="primary" @click="router.push('/runs/new')">新建评测</el-button>
      <el-input
        v-model="keyword" placeholder="搜索任务 / Agent / 评测集名称" clearable
        style="width: 240px" @keyup.enter="refresh" @clear="refresh"
      >
        <template #append>
          <el-button @click="refresh">查询</el-button>
        </template>
      </el-input>
      <el-select v-model="statusFilter" placeholder="全部状态" clearable style="width: 140px" @change="refresh">
        <el-option v-for="(meta, s) in STATUS_META" :key="s" :value="s" :label="meta.label" />
      </el-select>
      <span class="hint">提交后由调度器自动执行;同一 Agent 的 Run 串行,不同 Agent 并行</span>
    </div>

    <el-table :data="pagedRuns" v-loading="loading" border>
      <el-table-column prop="id" label="ID" width="70" />
      <el-table-column label="名称" min-width="240" show-overflow-tooltip>
        <template #default="{ row }">
          <el-link type="primary" @click="router.push(`/runs/${row.id}`)">{{ row.name }}</el-link>
        </template>
      </el-table-column>
      <el-table-column label="Agent" min-width="140">
        <template #default="{ row }">{{ row.agent_name }}({{ row.agent_version_label }})</template>
      </el-table-column>
      <el-table-column label="评测集" min-width="150">
        <template #default="{ row }">{{ row.caseset_name }} v{{ row.caseset_version_no }}</template>
      </el-table-column>
      <el-table-column label="评审模型" min-width="130">
        <template #default="{ row }">{{ row.judge_config_name || '默认' }}</template>
      </el-table-column>
      <el-table-column label="状态" width="90">
        <template #default="{ row }">
          <el-tag :type="STATUS_META[row.status as RunStatus].type" size="small">
            {{ STATUS_META[row.status as RunStatus].label }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="进度(成功/总数)" width="200">
        <template #default="{ row }">
          <div class="progress-cell">
            <el-progress :percentage="progressPercent(row)" :status="progressStatus(row)" style="flex: 1" />
            <span class="progress-num">{{ row.progress.succeeded }}/{{ row.progress.total }}</span>
          </div>
        </template>
      </el-table-column>
      <el-table-column label="失败阈值" width="80">
        <template #default="{ row }">{{ Math.round(row.fail_threshold * 100) }}%</template>
      </el-table-column>
      <el-table-column label="创建时间" width="160">
        <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="270" fixed="right">
        <template #default="{ row }">
          <el-button size="small" @click="router.push(`/runs/${row.id}`)">详情</el-button>
          <el-button v-if="row.status === 'draft'" size="small" type="primary" plain @click="openEdit(row)">编辑</el-button>
          <el-button v-if="row.status === 'draft'" size="small" type="primary" @click="onSubmit(row)">提交</el-button>
          <el-button v-if="!TERMINAL.includes(row.status)" size="small" type="danger" plain @click="onCancel(row)">取消</el-button>
          <el-button v-if="row.status === 'failed'" size="small" type="warning" @click="onRetry(row)">重试</el-button>
          <el-button v-if="DELETABLE.includes(row.status)" size="small" type="danger" plain @click="onDelete(row)">删除</el-button>
        </template>
      </el-table-column>
      <template #empty>
        <el-empty description="暂无评测任务,点击「新建评测」开始" />
      </template>
    </el-table>

    <el-dialog v-model="editVisible" :title="editRow ? `编辑任务 Run #${editRow.id}(草稿)` : ''" width="520px">
      <el-form label-width="100px">
        <el-form-item label="任务名称">
          <el-input v-model="editForm.name" placeholder="留空按组合自动生成" maxlength="256" />
        </el-form-item>
        <el-form-item label="Agent" required>
          <el-select v-model="editForm.agent_id" style="width: 100%">
            <el-option
              v-for="a in editAgents.filter((x) => x.status === 'available')"
              :key="a.id" :value="a.id" :label="`${a.name} ${a.version_label}`"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="评测集版本" required>
          <el-select v-model="editForm.caseset_version_id" style="width: 100%">
            <el-option v-for="v in editVersionOptions" :key="v.id" :value="v.id" :label="v.label" />
          </el-select>
        </el-form-item>
        <el-form-item label="权重模板" required>
          <el-select v-model="editForm.weight_template_id" style="width: 100%">
            <el-option v-for="w in editWeights" :key="w.id" :value="w.id" :label="w.name" />
          </el-select>
        </el-form-item>
        <el-form-item label="评审模型">
          <el-select v-model="editForm.judge_config_id" clearable placeholder="跟随评测集版本绑定" style="width: 100%">
            <el-option
              v-for="j in editJudges.filter((x) => x.enabled)"
              :key="j.id" :value="j.id" :label="j.name"
            />
          </el-select>
          <div class="hint">清空表示跟随评测集版本的默认绑定;当前:{{ editRow?.judge_config_name || '未绑定(使用默认)' }}</div>
        </el-form-item>
        <el-form-item label="失败阈值">
          <el-input-number v-model="editForm.fail_threshold" :min="0" :max="1" :step="0.05" />
          <span class="hint" style="margin-left: 8px">失败用例占比超过该值则任务判为失败</span>
        </el-form-item>
        <el-form-item label="Pass@k 重复次数">
          <el-input-number v-model="editForm.pass_k" :min="1" :max="20" />
          <span class="hint" style="margin-left: 8px">每个用例重复执行次数(仅草稿可改)</span>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button
          type="primary" :loading="editSaving"
          :disabled="!editForm.agent_id || !editForm.caseset_version_id || !editForm.weight_template_id"
          @click="submitEdit"
        >保存</el-button>
      </template>
    </el-dialog>

    <div class="pager-row">
      <el-pagination
        v-model:current-page="page" :page-size="PAGE_SIZE" :total="runs.length"
        layout="total, prev, pager, next" background
      />
    </div>
  </div>
</template>

<style scoped>
.toolbar { display: flex; align-items: center; gap: 12px; margin-bottom: 14px; }
.hint { font-size: 12px; color: var(--el-text-color-secondary); }
.progress-cell { display: flex; align-items: center; gap: 8px; }
.progress-num { font-size: 12px; color: var(--el-text-color-secondary); white-space: nowrap; }
.pager-row { display: flex; justify-content: flex-end; margin-top: 12px; }
</style>
