<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  createJudgeConfig, deleteJudgeConfig, listJudgeConfigs, testJudgeConfig, updateJudgeConfig,
} from '../api'
import type { JudgeConfig } from '../api/types'

const configs = ref<JudgeConfig[]>([])
const loading = ref(false)
const saving = ref(false)
const testing = ref(false)
const testingId = ref<number | null>(null)
const keyword = ref('')
const dialogVisible = ref(false)
const editing = ref<JudgeConfig | null>(null)   // null = 新建
const testResult = ref<{ passed: boolean; message: string; latency_ms: number } | null>(null)

// ---------- 前端分页(默认每页 10 条) ----------
const page = ref(1)
const PAGE_SIZE = 10

const filtered = computed(() => {
  const k = keyword.value.trim().toLowerCase()
  if (!k) return configs.value
  return configs.value.filter((c) =>
    [c.name, c.model, c.base_url, c.api_key_masked]
      .some((v) => String(v || '').toLowerCase().includes(k)))
})
const pagedConfigs = computed(() => {
  const start = (page.value - 1) * PAGE_SIZE
  return filtered.value.slice(start, start + PAGE_SIZE)
})

const DIMENSION_OPTIONS = [
  { value: 'D4', label: 'D4 多轮会话一致性', desc: '跨轮上下文承接、是否遗忘既定要求/前后矛盾' },
  { value: 'D7', label: 'D7 代码风格', desc: '命名、结构、冗余、注释与项目既有风格一致性' },
  { value: 'D8', label: 'D8 工程化', desc: '错误处理、边界条件、验证手段、改动克制程度' },
]

const form = reactive({
  name: '',
  enabled: false,
  is_default: false,
  base_url: '',
  model: '',
  api_key: '',
  temperature: 0.2,
  max_tokens: 800,
  timeout_seconds: 60,
  dimensions: [] as string[],
  rubrics: {} as Record<string, string>,
})

async function refresh() {
  loading.value = true
  try {
    configs.value = await listJudgeConfigs()
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
    name: '', enabled: false, is_default: configs.value.length === 0,
    base_url: '', model: '', api_key: '', temperature: 0.2,
    max_tokens: 800, timeout_seconds: 60,
    dimensions: ['D4', 'D7', 'D8'],
    rubrics: { D4: '', D7: '', D8: '' },
  })
  testResult.value = null
  dialogVisible.value = true
}

function openEdit(row: JudgeConfig) {
  editing.value = row
  Object.assign(form, {
    name: row.name, enabled: row.enabled, is_default: row.is_default,
    base_url: row.base_url, model: row.model, api_key: '',
    temperature: row.temperature, max_tokens: row.max_tokens,
    timeout_seconds: row.timeout_seconds, dimensions: [...row.dimensions],
    rubrics: { ...row.rubrics },
  })
  testResult.value = null
  dialogVisible.value = true
}

function validate(): string | null {
  if (!form.name.trim()) return '请填写配置名称'
  if (!form.base_url.trim()) return '请填写接口地址(base_url)'
  if (!form.model.trim()) return '请填写模型名'
  if (form.enabled && !form.api_key && !editing.value?.has_api_key) return '启用前需填写 API Key'
  if (!form.dimensions.length) return '至少选择一个评审维度'
  for (const d of form.dimensions) {
    if (!String(form.rubrics[d] || '').trim()) return `${d} 维度需填写评分标准(rubric)`
  }
  return null
}

function buildPayload() {
  return {
    name: form.name.trim(),
    enabled: form.enabled,
    is_default: form.is_default,
    provider: 'openai_compatible',
    base_url: form.base_url.trim(),
    model: form.model.trim(),
    api_key: form.api_key,
    temperature: form.temperature,
    max_tokens: form.max_tokens,
    timeout_seconds: form.timeout_seconds,
    dimensions: form.dimensions,
    rubrics: { ...form.rubrics },
  }
}

async function onTest() {
  if (!form.base_url.trim() || !form.model.trim()) {
    ElMessage.warning('测试连接需先填写接口地址与模型名')
    return
  }
  testing.value = true
  testResult.value = null
  try {
    testResult.value = await testJudgeConfig({
      id: editing.value?.id,
      base_url: form.base_url.trim(), model: form.model.trim(),
      api_key: form.api_key, timeout_seconds: Math.min(form.timeout_seconds, 60),
    })
  } catch (e) {
    testResult.value = { passed: false, message: (e as Error).message, latency_ms: 0 }
  } finally {
    testing.value = false
  }
}

async function onSave() {
  const err = validate()
  if (err) { ElMessage.warning(err); return }
  saving.value = true
  try {
    const payload = buildPayload()
    if (editing.value) {
      await updateJudgeConfig(editing.value.id, payload)
      ElMessage.success('评审模型已保存')
    } else {
      await createJudgeConfig(payload)
      ElMessage.success('评审模型已创建')
    }
    dialogVisible.value = false
    await refresh()
  } catch (e) {
    ElMessage.error((e as Error).message)
  } finally {
    saving.value = false
  }
}

async function onRowTest(row: JudgeConfig) {
  testingId.value = row.id
  try {
    const r = await testJudgeConfig({
      id: row.id, base_url: row.base_url, model: row.model,
      api_key: '', timeout_seconds: Math.min(row.timeout_seconds, 60),
    })
    if (r.passed) ElMessage.success(`${row.name}:${r.message}(耗时 ${r.latency_ms} ms)`)
    else ElMessageBox.alert(r.message, `${row.name} 连接失败`, { type: 'warning' })
  } catch (e) {
    ElMessage.error((e as Error).message)
  } finally {
    testingId.value = null
  }
}

async function onDelete(row: JudgeConfig) {
  try {
    await ElMessageBox.confirm(
      `确认删除评审模型「${row.name}」?已被评测集版本或评测任务引用时禁止删除。`,
      '删除确认', { type: 'warning' },
    )
  } catch { return }
  try {
    await deleteJudgeConfig(row.id)
    ElMessage.success('已删除')
    await refresh()
  } catch (e) {
    await ElMessageBox.alert((e as Error).message, '无法删除', { type: 'warning' })
  }
}

function fmtTime(t: string | null) {
  return t ? t.replace('T', ' ').slice(0, 16) : '-'
}

onMounted(refresh)
</script>

<template>
  <div class="judge-page">
    <div class="toolbar">
      <el-button type="primary" @click="openCreate">新建评审模型</el-button>
      <el-input
        v-model="keyword" placeholder="搜索名称 / 模型 / 接口地址" clearable
        style="width: 240px" @clear="page = 1"
      />
      <span class="hint">可维护多套评审模型;评测集版本绑定默认模型,创建任务时可覆盖。未启用时回退规则评分。</span>
    </div>

    <el-table :data="pagedConfigs" v-loading="loading" border>
      <el-table-column label="名称" min-width="180">
        <template #default="{ row }">
          <span class="name">{{ row.name }}</span>
          <el-tag v-if="row.is_default" size="small" type="warning" effect="plain" class="tag">默认</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="model" label="模型" min-width="150" show-overflow-tooltip />
      <el-table-column prop="base_url" label="接口地址" min-width="220" show-overflow-tooltip />
      <el-table-column label="评审维度" min-width="140">
        <template #default="{ row }">
          <el-tag v-for="d in row.dimensions" :key="d" size="small" effect="plain" class="tag">{{ d }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="密钥" width="110">
        <template #default="{ row }">{{ row.has_api_key ? row.api_key_masked : '未配置' }}</template>
      </el-table-column>
      <el-table-column label="状态" width="90">
        <template #default="{ row }">
          <el-tag :type="row.enabled ? 'success' : 'info'" size="small" effect="plain">
            {{ row.enabled ? '已启用' : '未启用' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="更新时间" width="150">
        <template #default="{ row }">{{ fmtTime(row.updated_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="220" fixed="right">
        <template #default="{ row }">
          <el-button size="small" type="primary" plain @click="openEdit(row)">编辑</el-button>
          <el-button size="small" :loading="testingId === row.id" @click="onRowTest(row)">测试</el-button>
          <el-button size="small" type="danger" plain @click="onDelete(row)">删除</el-button>
        </template>
      </el-table-column>
      <template #empty>
        <el-empty description="暂无评审模型,点击「新建评审模型」开始" />
      </template>
    </el-table>

    <div class="pager-row">
      <el-pagination
        v-model:current-page="page" :page-size="PAGE_SIZE" :total="filtered.length"
        layout="total, prev, pager, next" background
      />
    </div>

    <el-dialog v-model="dialogVisible" :title="editing ? `编辑评审模型:${editing.name}` : '新建评审模型'" width="720px">
      <el-form label-width="118px">
        <el-form-item label="配置名称" required>
          <el-input v-model="form.name" placeholder="如 DeepSeek-V3 评审 / GPT-4o 评审" />
        </el-form-item>
        <el-form-item label="启用语义评审">
          <el-switch v-model="form.enabled" active-text="启用" inactive-text="关闭" />
          <el-checkbox v-model="form.is_default" class="default-check">设为默认评审模型</el-checkbox>
        </el-form-item>
        <el-form-item label="接口地址" required>
          <el-input v-model="form.base_url" placeholder="如 https://api.deepseek.com/v1(OpenAI 兼容)" />
          <div class="hint">平台按 OpenAI Chat Completions 协议调用:POST {base_url}/chat/completions</div>
        </el-form-item>
        <el-form-item label="模型名" required>
          <el-input v-model="form.model" placeholder="如 deepseek-chat / gpt-4o-mini / qwen-plus" />
        </el-form-item>
        <el-form-item label="API Key">
          <el-input
            v-model="form.api_key" type="password" show-password
            :placeholder="editing?.has_api_key ? `已保存 ${editing.api_key_masked},留空则不修改` : '填写访问密钥'"
          />
        </el-form-item>
        <el-form-item label="评审维度" required>
          <el-checkbox-group v-model="form.dimensions" class="dim-group">
            <el-checkbox v-for="d in DIMENSION_OPTIONS" :key="d.value" :value="d.value">
              {{ d.label }}<span class="hint dim-desc">{{ d.desc }}</span>
            </el-checkbox>
          </el-checkbox-group>
        </el-form-item>
        <el-form-item
          v-for="d in DIMENSION_OPTIONS.filter((x) => form.dimensions.includes(x.value))"
          :key="`rubric-${d.value}`" :label="`${d.value} 评分标准`" required
        >
          <el-input
            v-model="form.rubrics[d.value]" type="textarea" :rows="3"
            :placeholder="`描述 ${d.value} 维度的评分标准,模型将据此打分`"
          />
        </el-form-item>
        <el-form-item label="温度">
          <el-input-number v-model="form.temperature" :min="0" :max="2" :step="0.1" :precision="1" />
          <span class="hint inline-hint">评审建议 0 ~ 0.3,越低越稳定</span>
        </el-form-item>
        <el-form-item label="最大输出 tokens">
          <el-input-number v-model="form.max_tokens" :min="64" :max="8192" :step="64" />
        </el-form-item>
        <el-form-item label="单次超时(秒)">
          <el-input-number v-model="form.timeout_seconds" :min="5" :max="600" :step="5" />
          <span class="hint inline-hint">超时即回退规则近似,不阻断评分</span>
        </el-form-item>
      </el-form>

      <el-alert
        v-if="testResult" :closable="false" class="test-alert"
        :type="testResult.passed ? 'success' : 'error'"
        :title="`${testResult.message}${testResult.latency_ms ? `(耗时 ${testResult.latency_ms} ms)` : ''}`"
      />

      <template #footer>
        <el-button :loading="testing" @click="onTest">测试连接</el-button>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="onSave">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.judge-page { max-width: 1200px; }
.toolbar { display: flex; align-items: center; gap: 10px; margin-bottom: 14px; }
.hint { font-size: 12px; color: var(--el-text-color-secondary); line-height: 1.6; }
.name { font-weight: 500; }
.tag { margin-right: 4px; }
.pager-row { display: flex; justify-content: flex-end; margin-top: 12px; }
.default-check { margin-left: 18px; }
.dim-group { display: flex; flex-direction: column; gap: 2px; }
.dim-desc { margin-left: 8px; }
.inline-hint { margin-left: 10px; }
.test-alert { margin-top: 6px; }
</style>
