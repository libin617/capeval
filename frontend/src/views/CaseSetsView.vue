<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  createCaseSet, createDraftVersion, deleteCaseSet, listCaseSets, listJudgeConfigs, listTestCases,
  publishVersion, replaceDraftItems, setVersionJudgeConfig, updateCaseSet,
} from '../api'
import type { CaseSet, CaseSetVersion, JudgeConfig, TestCase } from '../api/types'

const sets = ref<CaseSet[]>([])
const allCases = ref<TestCase[]>([])
const judgeConfigs = ref<JudgeConfig[]>([])
const loading = ref(false)
const createVisible = ref(false)
const detailVisible = ref(false)
const current = ref<CaseSet | null>(null)
const keyword = ref('')

// ---------- 前端分页(默认每页 10 条) ----------
const page = ref(1)
const PAGE_SIZE = 10
const pagedSets = computed<CaseSet[]>(() => {
  const start = (page.value - 1) * PAGE_SIZE
  return sets.value.slice(start, start + PAGE_SIZE)
})

const createForm = reactive({ name: '', description: '', testcase_ids: [] as number[] })

// ---------- 编辑名称/描述 ----------
const infoEditVisible = ref(false)
const infoForm = reactive({ name: '', description: '' })

function openInfoEdit(row: CaseSet) {
  current.value = row
  infoForm.name = row.name
  infoForm.description = row.description
  infoEditVisible.value = true
}

async function submitInfoEdit() {
  try {
    await updateCaseSet(current.value!.id, { name: infoForm.name, description: infoForm.description })
    ElMessage.success('评测集信息已更新')
    infoEditVisible.value = false
    await refresh()
  } catch (e) {
    ElMessage.error((e as Error).message)
  }
}

async function onDelete(row: CaseSet) {
  try {
    await ElMessageBox.confirm(
      `确认删除评测集「${row.name}」?已被评测任务引用的评测集禁止删除。`, '删除确认', { type: 'warning' },
    )
  } catch { return }
  try {
    await deleteCaseSet(row.id)
    ElMessage.success('已删除')
    if (current.value?.id === row.id) { detailVisible.value = false; current.value = null }
    await refresh()
  } catch (e) {
    ElMessage.error((e as Error).message)
  }
}

async function refresh() {
  loading.value = true
  try {
    ;[sets.value, allCases.value] = await Promise.all([
      listCaseSets(keyword.value.trim() ? { q: keyword.value.trim() } : undefined),
      listTestCases({ status: 'active' }),
    ])
    try {
      judgeConfigs.value = await listJudgeConfigs()
    } catch { /* 评审模型读取失败不影响评测集管理 */ }
    if (current.value) {
      current.value = sets.value.find((s) => s.id === current.value!.id) ?? null
    }
    page.value = 1
  } finally {
    loading.value = false
  }
}

async function submitCreate() {
  try {
    await createCaseSet({ ...createForm })
    ElMessage.success('评测集已创建(草稿 v1),发布后内容冻结')
    createVisible.value = false
    Object.assign(createForm, { name: '', description: '', testcase_ids: [] })
    await refresh()
  } catch (e) {
    ElMessage.error((e as Error).message)
  }
}

function openDetail(row: CaseSet) {
  current.value = row
  detailVisible.value = true
}

const draftVersion = computed<CaseSetVersion | undefined>(() =>
  current.value?.versions.find((v) => v.status === 'draft'),
)

async function onPublish(version: CaseSetVersion) {
  try {
    await publishVersion(current.value!.id, version.id)
    ElMessage.success(`v${version.version_no} 已发布并冻结`)
    await refresh()
  } catch (e) {
    ElMessage.error((e as Error).message)
  }
}

const judgeBinding = reactive<Record<number, number | null>>({})

async function onJudgeChange(version: CaseSetVersion, value: number | null) {
  try {
    await setVersionJudgeConfig(current.value!.id, version.id, value)
    ElMessage.success(`v${version.version_no} 评审模型已更新`)
    await refresh()
  } catch (e) {
    ElMessage.error((e as Error).message)
    judgeBinding[version.id] = version.judge_config_id
  }
}

const editIds = ref<number[]>([])
const editVisible = ref(false)
const editTarget = ref<'draft' | 'new'>('draft')

function openEditItems(target: 'draft' | 'new') {
  editTarget.value = target
  const base = target === 'draft'
    ? draftVersion.value
    : [...(current.value?.versions ?? [])].reverse().find((v) => v.status === 'published')
  editIds.value = base?.items.map((i) => i.testcase_id) ?? []
  editVisible.value = true
}

async function submitEditItems() {
  try {
    if (editTarget.value === 'draft' && draftVersion.value) {
      await replaceDraftItems(current.value!.id, draftVersion.value.id, editIds.value)
      ElMessage.success('草稿内容已更新')
    } else {
      await createDraftVersion(current.value!.id, editIds.value)
      ElMessage.success('已创建新草稿版本')
    }
    editVisible.value = false
    await refresh()
  } catch (e) {
    ElMessage.error((e as Error).message)
  }
}

onMounted(refresh)
</script>

<template>
  <div>
    <div class="toolbar">
      <el-button type="primary" @click="createVisible = true">新建评测集</el-button>
      <el-input
        v-model="keyword" placeholder="搜索名称 / 描述" clearable
        style="width: 200px" @keyup.enter="refresh" @clear="refresh"
      >
        <template #append>
          <el-button @click="refresh">查询</el-button>
        </template>
      </el-input>
      <span class="hint">发布版本后内容冻结,修改生成新版本;历史 Run 永远绑定当时版本</span>
    </div>

    <el-table :data="pagedSets" v-loading="loading" border>
      <el-table-column prop="name" label="名称" min-width="180">
        <template #default="{ row }">
          {{ row.name }}
          <el-tag v-if="row.is_builtin" size="small" type="warning" effect="plain" style="margin-left: 6px">官方</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="description" label="描述" min-width="220" show-overflow-tooltip />
      <el-table-column label="版本" min-width="200">
        <template #default="{ row }">
          <el-tag
            v-for="v in row.versions" :key="v.id" size="small"
            :type="v.status === 'published' ? 'success' : 'info'" effect="plain" style="margin-right: 4px"
          >
            v{{ v.version_no }} {{ v.status === 'published' ? '已发布' : '草稿' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="评测用例数" width="100">
        <template #default="{ row }">
          {{[...row.versions].reverse().find((v) => v.status === 'published')?.items.length ?? row.versions[0]?.items.length ?? 0 }}
        </template>
      </el-table-column>
      <el-table-column label="操作" width="260" fixed="right">
        <template #default="{ row }">
          <el-button size="small" @click="openDetail(row)">版本管理</el-button>
          <el-button size="small" type="primary" plain @click="openInfoEdit(row)">编辑</el-button>
          <el-button size="small" type="danger" plain @click="onDelete(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <div class="pager-row">
      <el-pagination
        v-model:current-page="page" :page-size="PAGE_SIZE" :total="sets.length"
        layout="total, prev, pager, next" background
      />
    </div>

    <el-dialog v-model="createVisible" title="新建评测集" width="560px">
      <el-form label-width="90px">
        <el-form-item label="名称" required>
          <el-input v-model="createForm.name" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="createForm.description" type="textarea" :rows="2" />
        </el-form-item>
        <el-form-item label="选择评测用例" required>
          <el-select v-model="createForm.testcase_ids" multiple style="width: 100%" placeholder="至少 1 个评测用例(选择顺序即执行顺序)">
            <el-option v-for="c in allCases" :key="c.id" :value="c.id" :label="c.title" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" :disabled="!createForm.name || !createForm.testcase_ids.length" @click="submitCreate">
          创建
        </el-button>
      </template>
    </el-dialog>

    <el-drawer v-model="detailVisible" :title="current?.name" size="640px">
      <div v-for="v in [...(current?.versions ?? [])].reverse()" :key="v.id" class="version-block">
        <div class="version-head">
          <div>
            <b>v{{ v.version_no }}</b>
            <el-tag size="small" :type="v.status === 'published' ? 'success' : 'info'" style="margin-left: 8px">
              {{ v.status === 'published' ? '已发布(冻结)' : '草稿' }}
            </el-tag>
            <span v-if="v.published_at" class="pub-time">{{ v.published_at.replace('T', ' ').slice(0, 19) }}</span>
          </div>
          <div class="version-actions">
            <el-select
              v-if="v.status === 'draft'" size="small" clearable placeholder="未绑定(使用默认)"
              :model-value="judgeBinding[v.id] ?? v.judge_config_id ?? null"
              style="width: 200px"
              @change="(val: number | null) => onJudgeChange(v, val)"
            >
              <el-option
                v-for="j in judgeConfigs.filter((x) => x.enabled)"
                :key="j.id" :label="j.name" :value="j.id"
              />
            </el-select>
            <span v-else class="judge-label">评审模型:{{ v.judge_config_name || '未绑定(使用默认)' }}</span>
            <el-button v-if="v.status === 'draft'" size="small" @click="openEditItems('draft')">编辑评测用例</el-button>
            <el-button v-if="v.status === 'draft'" size="small" type="primary" @click="onPublish(v)">发布</el-button>
          </div>
        </div>
        <el-table :data="v.items" size="small" border>
          <el-table-column prop="order_no" label="#" width="50" />
          <el-table-column prop="title" label="评测用例" min-width="220" />
          <el-table-column prop="difficulty" label="难度" width="80" />
          <el-table-column label="标签" min-width="140">
            <template #default="{ row }">
              <el-tag v-for="t in row.tags" :key="t" size="small" effect="plain" style="margin-right: 4px">{{ t }}</el-tag>
            </template>
          </el-table-column>
        </el-table>
      </div>
      <el-button v-if="!draftVersion" style="margin-top: 16px" @click="openEditItems('new')">
        + 基于最新发布版创建新草稿
      </el-button>
    </el-drawer>

    <el-dialog v-model="editVisible" :title="editTarget === 'draft' ? '编辑草稿评测用例' : '创建新草稿版本'" width="520px">
      <el-select v-model="editIds" multiple style="width: 100%">
        <el-option v-for="c in allCases" :key="c.id" :value="c.id" :label="c.title" />
      </el-select>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" :disabled="!editIds.length" @click="submitEditItems">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="infoEditVisible" title="编辑评测集信息" width="480px">
      <el-form label-width="90px">
        <el-form-item label="名称" required>
          <el-input v-model="infoForm.name" maxlength="128" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="infoForm.description" type="textarea" :rows="3" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="infoEditVisible = false">取消</el-button>
        <el-button type="primary" :disabled="!infoForm.name" @click="submitInfoEdit">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.toolbar { display: flex; align-items: center; gap: 12px; margin-bottom: 14px; }
.hint { font-size: 12px; color: var(--el-text-color-secondary); }
.version-block { margin-bottom: 22px; }
.version-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
.version-actions { display: flex; align-items: center; gap: 8px; }
.judge-label { font-size: 12px; color: var(--el-text-color-secondary); }
.pub-time { font-size: 12px; color: var(--el-text-color-secondary); margin-left: 8px; }
.pager-row { display: flex; justify-content: flex-end; margin-top: 12px; }
</style>
