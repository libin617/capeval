<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  createRootCause, deleteRootCause, getLibraryVersion, getSignalCatalog, listLibraryVersions,
  listRootCauses, updateRootCause,
} from '../api'
import type {
  DetectionRule, LibraryVersionInfo, Responsibility, RootCause, RootCauseCreatePayload,
  RootCauseUpdatePayload,
} from '../api/types'

const causes = ref<RootCause[]>([])
const signals = ref<string[]>([])
const libraryVersion = ref(1)
const versions = ref<LibraryVersionInfo[]>([])
const loading = ref(false)
const saving = ref(false)
const keyword = ref('')

const tree = computed<RootCause[]>(() => {
  const byParent = new Map<number | null, RootCause[]>()
  for (const rc of causes.value) {
    const key = rc.parent_id
    if (!byParent.has(key)) byParent.set(key, [])
    byParent.get(key)!.push(rc)
  }
  const build = (parentId: number | null): RootCause[] =>
    (byParent.get(parentId) ?? [])
      .sort((a, b) => a.code.localeCompare(b.code, undefined, { numeric: true }))
      .map((rc) => ({ ...rc, children: build(rc.id) }))
  return build(null)
})

/** 模糊查询:命中编码/名称/描述时平铺展示匹配条目,否则展示三级树 */
const displayRows = computed<RootCause[]>(() => {
  const kw = keyword.value.trim().toLowerCase()
  if (!kw) return tree.value
  return causes.value
    .filter((rc) =>
      rc.code.toLowerCase().includes(kw)
      || rc.name.toLowerCase().includes(kw)
      || (rc.description ?? '').toLowerCase().includes(kw))
    .sort((a, b) => a.code.localeCompare(b.code, undefined, { numeric: true }))
    .map((rc) => ({ ...rc, children: [] }))
})

// ---------- 前端分页(每页 10 条;树模式按一级大类分页,子节点随父行) ----------
const page = ref(1)
const PAGE_SIZE = 10
const pagedRows = computed<RootCause[]>(() => {
  const start = (page.value - 1) * PAGE_SIZE
  return displayRows.value.slice(start, start + PAGE_SIZE)
})

function onSearch() {
  page.value = 1
}

const DIM_NAMES: Record<string, string> = {
  D1: '代码正确性', D2: '流程遵从', D3: '工具调用', D4: '会话一致',
  D5: '安全性', D6: '成本效率', D7: '代码风格', D8: '工程化',
}
const LEVEL_LABEL: Record<number, string> = { 1: '一级大类', 2: '二级类别', 3: '三级根因' }

const respMeta: Record<string, { label: string; type: 'primary' | 'success' | 'warning' | 'danger' | 'info' }> = {
  agent: { label: 'Agent', type: 'primary' },
  platform: { label: '平台', type: 'warning' },
  third_party: { label: '第三方', type: 'warning' },
  case_maintainer: { label: '用例维护方', type: 'info' },
}

/** 兼容后端两种写法:["signal"] 或 [{signal, primary}] */
function ruleList(rc: RootCause): DetectionRule[] {
  return (rc.detection_signals?.rules ?? []).map((r) =>
    typeof r === 'string' ? { signal: r, primary: false } : { signal: r.signal, primary: !!r.primary })
}

// ---------- 编辑对话框 ----------
const dialogVisible = ref(false)
const editing = ref<RootCause | null>(null)   // null = 新建
const parentForCreate = ref<RootCause | null>(null)
const form = reactive({
  name: '', description: '', responsibility: 'agent' as Responsibility,
  rules: [] as DetectionRule[], llm_prompt: '', related_dimensions: [] as string[],
  enabled: true, note: '',
})

const dialogTitle = computed(() => {
  if (editing.value) return `编辑 ${editing.value.code}`
  const level = parentForCreate.value ? parentForCreate.value.level + 1 : 1
  return `新建${LEVEL_LABEL[level] ?? ''}`
})

function resetForm() {
  form.name = ''
  form.description = ''
  form.responsibility = 'agent'
  form.rules = []
  form.llm_prompt = ''
  form.related_dimensions = []
  form.enabled = true
  form.note = ''
}

function openCreate(parent: RootCause | null) {
  editing.value = null
  parentForCreate.value = parent
  resetForm()
  dialogVisible.value = true
}

function openEdit(rc: RootCause) {
  editing.value = rc
  parentForCreate.value = null
  form.name = rc.name
  form.description = rc.description
  form.responsibility = rc.responsibility
  form.rules = ruleList(rc)
  form.llm_prompt = String(rc.detection_signals?.llm_prompt ?? '')
  form.related_dimensions = [...(rc.detection_signals?.related_dimensions ?? [])]
  form.enabled = rc.enabled
  form.note = ''
  dialogVisible.value = true
}

const signalNames = computed({
  get: () => form.rules.map((r) => r.signal),
  set: (vals: string[] | null) => {
    const prev = new Map(form.rules.map((r) => [r.signal, r.primary]))
    form.rules = (vals ?? []).map((s) => ({ signal: s, primary: prev.get(s) ?? false }))
  },
})

function buildSignals() {
  return {
    rules: form.rules.map((r) => ({ signal: r.signal, primary: !!r.primary })),
    llm_prompt: form.llm_prompt,
    related_dimensions: form.related_dimensions,
  }
}

async function save() {
  if (!form.name.trim()) { ElMessage.warning('请填写名称'); return }
  saving.value = true
  try {
    if (editing.value) {
      const payload: RootCauseUpdatePayload = {
        name: form.name, description: form.description, responsibility: form.responsibility,
        detection_signals: buildSignals(), enabled: form.enabled, note: form.note,
      }
      const res = await updateRootCause(editing.value.id, payload)
      if (res.warning) ElMessage.warning(res.warning)
      else ElMessage.success(`已保存,根因库升级至 v${res.library_version}`)
    } else {
      const level = (parentForCreate.value ? parentForCreate.value.level + 1 : 1) as 1 | 2 | 3
      const payload: RootCauseCreatePayload = {
        level, parent_id: parentForCreate.value?.id ?? null, name: form.name,
        description: form.description, responsibility: form.responsibility,
        detection_signals: buildSignals(), note: form.note,
      }
      const res = await createRootCause(payload)
      ElMessage.success(`已创建 ${res.rootcause.code},根因库升级至 v${res.library_version}`)
    }
    dialogVisible.value = false
    await refresh()
  } catch (e) {
    ElMessage.error((e as Error).message)
  } finally {
    saving.value = false
  }
}

async function toggleEnabled(rc: RootCause) {
  try {
    const res = await updateRootCause(rc.id, { enabled: !rc.enabled, note: rc.enabled ? '停用' : '启用' })
    ElMessage.success(`${rc.enabled ? '已停用' : '已启用'} ${rc.code}(库版本 v${res.library_version})`)
    await refresh()
  } catch (e) {
    ElMessage.error((e as Error).message)
  }
}

async function remove(rc: RootCause) {
  try {
    await ElMessageBox.confirm(
      `确认删除 ${rc.code} ${rc.name}?被归因引用或含子节点的条目不可删除,仅可停用。`,
      '删除根因条目', { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' })
  } catch { return }
  try {
    await deleteRootCause(rc.id)
    ElMessage.success('已删除')
    await refresh()
  } catch (e) {
    ElMessage.error((e as Error).message)
  }
}

// ---------- 版本历史 ----------
const versionDrawer = ref(false)

async function openVersions() {
  versions.value = await listLibraryVersions()
  versionDrawer.value = true
}

async function refresh() {
  loading.value = true
  try {
    const [list, ver] = await Promise.all([listRootCauses(), getLibraryVersion()])
    causes.value = list
    libraryVersion.value = ver.version_no
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  try {
    signals.value = await getSignalCatalog()
  } catch {
    signals.value = []
  }
  await refresh()
})
</script>

<template>
  <div v-loading="loading">
    <el-card shadow="never">
      <template #header>
        <div class="head-row">
          <b>三级根因库</b>
          <el-tag size="small" effect="plain" style="margin-left: 8px">库版本 v{{ libraryVersion }}</el-tag>
          <span class="footnote" style="margin-left: 10px">共 {{ causes.length }} 条 · 任何增改都会生成新版本,仅影响新版本之后的评测任务</span>
          <span style="flex: 1" />
          <el-input
            v-model="keyword" placeholder="搜索编码 / 名称 / 描述" clearable
            size="small" style="width: 200px; margin-right: 8px" @input="onSearch" @clear="onSearch"
          />
          <el-button size="small" @click="openVersions">版本历史</el-button>
          <el-button size="small" type="primary" @click="openCreate(null)">新建一级大类</el-button>
        </div>
      </template>
      <el-table :data="pagedRows" row-key="id" border :tree-props="{ children: 'children' }">
        <el-table-column label="编码 / 名称" min-width="280">
          <template #default="{ row }">
            <span class="code">{{ row.code }}</span> {{ row.name }}
            <el-tag size="small" effect="plain" type="info" style="margin-left: 6px">{{ LEVEL_LABEL[row.level] }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="description" label="描述" min-width="260" show-overflow-tooltip />
        <el-table-column label="责任归属" width="110">
          <template #default="{ row }">
            <el-tag size="small" :type="respMeta[row.responsibility]?.type" effect="plain">
              {{ respMeta[row.responsibility]?.label ?? row.responsibility }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="检测信号" min-width="220">
          <template #default="{ row }">
            <el-tag v-for="r in ruleList(row)" :key="r.signal" size="small"
              :type="r.primary ? 'danger' : 'info'" effect="plain" style="margin: 2px 4px 2px 0">
              {{ r.signal }}{{ r.primary ? ' · 主' : '' }}
            </el-tag>
            <span v-if="!ruleList(row).length" class="footnote">-</span>
          </template>
        </el-table-column>
        <el-table-column label="关联维度" width="120">
          <template #default="{ row }">
            <el-tag v-for="d in row.detection_signals?.related_dimensions ?? []" :key="d"
              size="small" effect="plain" style="margin-right: 4px">{{ d }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="引用" width="70" align="center">
          <template #default="{ row }">
            <span :class="{ 'ref-count': row.reference_count > 0 }">{{ row.reference_count }}</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="70">
          <template #default="{ row }">
            <el-tag size="small" :type="row.enabled ? 'success' : 'info'">{{ row.enabled ? '启用' : '停用' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="230" fixed="right">
          <template #default="{ row }">
            <el-button v-if="row.level < 3" link type="primary" size="small" @click="openCreate(row)">新增子级</el-button>
            <el-button link type="primary" size="small" @click="openEdit(row)">编辑</el-button>
            <el-button link :type="row.enabled ? 'warning' : 'success'" size="small" @click="toggleEnabled(row)">
              {{ row.enabled ? '停用' : '启用' }}
            </el-button>
            <el-button link type="danger" size="small" @click="remove(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div class="pager-row">
        <el-pagination
          v-model:current-page="page" :page-size="PAGE_SIZE" :total="displayRows.length"
          layout="total, prev, pager, next" background
        />
      </div>
      <div class="footnote" style="margin-top: 10px">
        RC-9(环境与接入)/ RC-10(用例缺陷)主导的案件展示但不计入 Agent 能力扣分。
        被归因引用或有子节点的条目禁止删除,请使用「停用」。
      </div>
    </el-card>

    <el-dialog v-model="dialogVisible" :title="dialogTitle" width="640px" :close-on-click-modal="false">
      <el-form label-width="96px">
        <el-form-item v-if="parentForCreate" label="上级">
          <span class="code">{{ parentForCreate.code }}</span> {{ parentForCreate.name }}
        </el-form-item>
        <el-form-item label="名称" required>
          <el-input v-model="form.name" maxlength="128" placeholder="如:验收命令执行失败" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="form.description" type="textarea" :rows="2" placeholder="该根因的判定说明" />
        </el-form-item>
        <el-form-item label="责任归属">
          <el-select v-model="form.responsibility" style="width: 200px">
            <el-option v-for="(m, k) in respMeta" :key="k" :label="m.label" :value="k" />
          </el-select>
        </el-form-item>
        <el-form-item label="检测信号">
          <el-select v-model="signalNames" multiple filterable clearable style="width: 100%"
            placeholder="选择绑定的规则信号(来自评分信号目录)">
            <el-option v-for="s in signals" :key="s" :label="s" :value="s" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="form.rules.length" label="主信号">
          <div class="primary-list">
            <el-checkbox v-for="r in form.rules" :key="r.signal" v-model="r.primary">
              {{ r.signal }}
            </el-checkbox>
          </div>
          <div class="footnote">命中主信号时该根因优先作为主根因输出</div>
        </el-form-item>
        <el-form-item label="关联维度">
          <el-checkbox-group v-model="form.related_dimensions">
            <el-checkbox v-for="(n, d) in DIM_NAMES" :key="d" :value="d">{{ d }} {{ n }}</el-checkbox>
          </el-checkbox-group>
        </el-form-item>
        <el-form-item label="LLM 提示词">
          <el-input v-model="form.llm_prompt" type="textarea" :rows="2"
            placeholder="可选:接入 LLM 归因后使用的判定提示词模板" />
        </el-form-item>
        <el-form-item v-if="editing" label="启用">
          <el-switch v-model="form.enabled" />
        </el-form-item>
        <el-form-item label="版本说明">
          <el-input v-model="form.note" placeholder="本次变更说明(写入版本记录)" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>

    <el-drawer v-model="versionDrawer" title="根因库版本历史" size="420px">
      <el-timeline>
        <el-timeline-item v-for="v in versions" :key="v.version_no" :timestamp="`v${v.version_no}`" placement="top">
          <div>{{ v.note || '未填写说明' }}</div>
        </el-timeline-item>
      </el-timeline>
      <el-empty v-if="!versions.length" description="暂无版本记录" />
    </el-drawer>
  </div>
</template>

<style scoped>
.code { font-family: monospace; color: var(--el-color-primary); font-weight: 600; }
.footnote { font-size: 12px; color: var(--el-text-color-secondary); margin-top: 8px; }
.head-row { display: flex; align-items: center; gap: 4px; }
.primary-list { display: flex; flex-wrap: wrap; gap: 4px 14px; }
.ref-count { color: var(--el-color-danger); font-weight: 600; }
.pager-row { display: flex; justify-content: flex-end; margin-top: 12px; }
</style>
