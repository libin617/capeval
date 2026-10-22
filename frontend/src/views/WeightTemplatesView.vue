<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  createWeightTemplate, deleteWeightTemplate, listWeightTemplates, updateWeightTemplate,
} from '../api'
import type { WeightTemplate } from '../api/types'

const templates = ref<WeightTemplate[]>([])
const loading = ref(false)
const saving = ref(false)
const keyword = ref('')
const dialogVisible = ref(false)
const editing = ref<WeightTemplate | null>(null)   // null = 新建

// ---------- 前端分页(默认每页 10 条) ----------
const page = ref(1)
const PAGE_SIZE = 10

const filtered = computed(() => {
  const k = keyword.value.trim().toLowerCase()
  if (!k) return templates.value
  return templates.value.filter((t) => t.name.toLowerCase().includes(k))
})
const pagedTemplates = computed(() => {
  const start = (page.value - 1) * PAGE_SIZE
  return filtered.value.slice(start, start + PAGE_SIZE)
})

const DIMENSIONS = [
  { key: 'D1', label: 'D1 任务完成度' },
  { key: 'D2', label: 'D2 正确性' },
  { key: 'D3', label: 'D3 工具使用' },
  { key: 'D4', label: 'D4 多轮一致性' },
  { key: 'D5', label: 'D5 安全性' },
  { key: 'D6', label: 'D6 效率' },
  { key: 'D7', label: 'D7 代码风格' },
  { key: 'D8', label: 'D8 工程化' },
]

const form = reactive({
  name: '',
  is_default: false,
  weights: { D1: 0.3, D2: 0.12, D3: 0.12, D4: 0.12, D5: 0.12, D6: 0.08, D7: 0.08, D8: 0.06 } as Record<string, number>,
})

/** 选中的维度:只有出现在 weights 中的维度才参与评分 */
const selectedKeys = computed(() => DIMENSIONS.map((d) => d.key).filter((k) => k in form.weights))

const total = computed(() =>
  selectedKeys.value.reduce((s, k) => s + (Number(form.weights[k]) || 0), 0),
)
const totalOk = computed(() => selectedKeys.value.length > 0 && Math.abs(total.value - 1.0) <= 0.001)

function isChecked(key: string): boolean {
  return key in form.weights
}

function setWeight(key: string, v: number | undefined) {
  if (isChecked(key)) form.weights[key] = Number(v ?? 0)
}

/** 勾选维度:按剩余权重补位;取消勾选:移除该维度(剩余权重保持不变,可用「均分」快速归一) */
function toggleDim(key: string, checked: boolean) {
  if (checked) {
    const rest = Math.max(0, 1 - total.value)
    form.weights[key] = Math.round(rest * 100) / 100
  } else if (selectedKeys.value.length > 1) {
    delete form.weights[key]
  } else {
    ElMessage.warning('至少保留一个评测维度')
  }
}

/** 选中维度均分(保留两位小数),保证总和为 1.0 */
function evenSplit() {
  const keys = selectedKeys.value
  if (!keys.length) return
  const each = Math.floor((1 / keys.length) * 100) / 100
  keys.forEach((k) => { form.weights[k] = each })
  const last = Math.round((1 - each * (keys.length - 1)) * 100) / 100
  form.weights[keys[keys.length - 1]] = last
}

async function refresh() {
  loading.value = true
  try {
    templates.value = await listWeightTemplates()
    page.value = 1
  } catch (e) {
    ElMessage.error((e as Error).message)
  } finally {
    loading.value = false
  }
}

function openCreate() {
  editing.value = null
  Object.assign(form, {
    name: '', is_default: templates.value.length === 0,
    weights: { D1: 0.3, D2: 0.12, D3: 0.12, D4: 0.12, D5: 0.12, D6: 0.08, D7: 0.08, D8: 0.06 },
  })
  dialogVisible.value = true
}

function openEdit(row: WeightTemplate) {
  editing.value = row
  Object.assign(form, {
    name: row.name, is_default: row.is_default, weights: { ...row.weights },
  })
  dialogVisible.value = true
}

function validate(): string | null {
  if (!form.name.trim()) return '请填写模板名称'
  if (!selectedKeys.value.length) return '请至少勾选一个评测维度'
  for (const k of selectedKeys.value) {
    const v = Number(form.weights[k])
    if (Number.isNaN(v) || v < 0 || v > 1) return `${k} 权重必须为 0~1 的数字`
  }
  if (!totalOk.value) return `所选维度权重总和必须为 1.0,当前为 ${total.value.toFixed(4)}`
  return null
}

async function onSave() {
  const err = validate()
  if (err) { ElMessage.warning(err); return }
  saving.value = true
  try {
    const payload = {
      name: form.name.trim(),
      is_default: form.is_default,
      weights: Object.fromEntries(selectedKeys.value.map((k) => [k, Number(form.weights[k])])),
    }
    if (editing.value) {
      await updateWeightTemplate(editing.value.id, payload)
      ElMessage.success('权重模板已保存')
    } else {
      await createWeightTemplate(payload)
      ElMessage.success('权重模板已创建')
    }
    dialogVisible.value = false
    await refresh()
  } catch (e) {
    ElMessage.error((e as Error).message)
  } finally {
    saving.value = false
  }
}

async function onDelete(row: WeightTemplate) {
  try {
    await ElMessageBox.confirm(
      `确认删除权重模板「${row.name}」?默认模板与被评测任务引用的模板禁止删除。`,
      '删除确认', { type: 'warning' },
    )
  } catch { return }
  try {
    await deleteWeightTemplate(row.id)
    ElMessage.success('已删除')
    await refresh()
  } catch (e) {
    await ElMessageBox.alert((e as Error).message, '无法删除', { type: 'warning' })
  }
}

onMounted(refresh)
</script>

<template>
  <div class="weights-page">
    <div class="toolbar">
      <el-button type="primary" @click="openCreate">新建权重模板</el-button>
      <el-input v-model="keyword" placeholder="搜索模板名称" clearable style="width: 220px" @clear="page = 1" />
      <span class="hint">可只勾选部分维度(如仅 D1)单独评测某项能力;所选维度权重总和须为 1.0,任务创建时快照固化</span>
    </div>

    <el-table :data="pagedTemplates" v-loading="loading" border>
      <el-table-column label="名称" min-width="180">
        <template #default="{ row }">
          <span class="name">{{ row.name }}</span>
          <el-tag v-if="row.is_default" size="small" type="warning" effect="plain" class="tag">默认</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="权重分布" min-width="420">
        <template #default="{ row }">
          <el-tag v-for="(w, k) in row.weights" :key="k" size="small" effect="plain" class="tag">
            {{ k }} {{ w }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="160" fixed="right">
        <template #default="{ row }">
          <el-button size="small" type="primary" plain @click="openEdit(row)">编辑</el-button>
          <el-button size="small" type="danger" plain :disabled="row.is_default" @click="onDelete(row)">删除</el-button>
        </template>
      </el-table-column>
      <template #empty>
        <el-empty description="暂无权重模板,点击「新建权重模板」开始" />
      </template>
    </el-table>

    <div class="pager-row">
      <el-pagination
        v-model:current-page="page" :page-size="PAGE_SIZE" :total="filtered.length"
        layout="total, prev, pager, next" background
      />
    </div>

    <el-dialog v-model="dialogVisible" :title="editing ? `编辑权重模板:${editing.name}` : '新建权重模板'" width="640px">
      <el-form label-width="110px">
        <el-form-item label="模板名称" required>
          <el-input v-model="form.name" placeholder="如 质量优先 / 效率优先" maxlength="128" />
        </el-form-item>
        <el-form-item label="设为默认">
          <el-switch v-model="form.is_default" :disabled="editing?.is_default" />
          <span class="hint inline-hint">新建任务默认选中;全系统仅一个默认模板</span>
        </el-form-item>
        <el-form-item label="评测维度">
          <div class="dim-picker">
            <div v-for="d in DIMENSIONS" :key="d.key" class="dim-row">
              <el-checkbox :model-value="isChecked(d.key)" @change="(v: string | number | boolean) => toggleDim(d.key, !!v)">
                {{ d.label }}
              </el-checkbox>
              <el-input-number
                :model-value="form.weights[d.key] ?? 0" :disabled="!isChecked(d.key)"
                :min="0" :max="1" :step="0.01" :precision="2"
                @update:model-value="(v: number | undefined) => setWeight(d.key, v)"
              />
            </div>
            <div>
              <el-button link type="primary" size="small" @click="evenSplit">选中维度均分</el-button>
              <span class="hint inline-hint">只测单项能力时,可仅勾选该维度并把权重设为 1.0</span>
            </div>
          </div>
        </el-form-item>
        <el-form-item label="权重总和">
          <el-tag :type="totalOk ? 'success' : 'danger'" effect="plain">{{ total.toFixed(4) }}</el-tag>
          <span class="hint inline-hint">仅统计已勾选维度,必须等于 1.0(容差 ±0.001)</span>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" :disabled="!totalOk" @click="onSave">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.weights-page { max-width: 1200px; }
.toolbar { display: flex; align-items: center; gap: 10px; margin-bottom: 14px; }
.hint { font-size: 12px; color: var(--el-text-color-secondary); }
.name { font-weight: 500; }
.tag { margin-right: 4px; }
.inline-hint { margin-left: 10px; }
.dim-picker { display: flex; flex-direction: column; gap: 8px; width: 100%; }
.dim-row { display: flex; align-items: center; gap: 18px; }
.pager-row { display: flex; justify-content: flex-end; margin-top: 12px; }
</style>
