<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import {
  createRun, listAgents, listCaseSets, listJudgeConfigs, listRootCauses, listWeightTemplates, submitRun,
} from '../api'
import type { AgentInfo, CaseSet, JudgeConfig, RootCause, WeightTemplate } from '../api/types'

const router = useRouter()
const step = ref(0)
const submitting = ref(false)

const agents = ref<AgentInfo[]>([])
const caseSets = ref<CaseSet[]>([])
const weightTemplates = ref<WeightTemplate[]>([])
const rootCauses = ref<RootCause[]>([])
const judgeConfigs = ref<JudgeConfig[]>([])

const form = reactive({
  agent_id: null as number | null,
  caseset_version_id: null as number | null,
  name: '',
  weight_template_id: null as number | null,
  review_policy: 'skip' as 'skip' | 'sample' | 'full',
  fail_threshold: 0.5,
  pass_k: 1,
  baseline_run_id: null as number | null,
  // null 表示跟随评测集版本绑定,创建时由后端继承
  judge_config_id: null as number | null,
})

const availableAgents = computed(() => agents.value.filter((a) => a.status === 'available'))
const selectedAgent = computed(() => agents.value.find((a) => a.id === form.agent_id))

const publishedVersions = computed(() => {
  const out: {
    versionId: number; label: string; setName: string; caseCount: number
    judgeConfigId: number | null; judgeConfigName: string
  }[] = []
  for (const s of caseSets.value) {
    for (const v of s.versions) {
      if (v.status === 'published' && v.items.length > 0) {
        out.push({
          versionId: v.id,
          label: `${s.name} v${v.version_no}`,
          setName: s.name,
          caseCount: v.items.length,
          judgeConfigId: v.judge_config_id,
          judgeConfigName: v.judge_config_name,
        })
      }
    }
  }
  return out
})
const selectedVersion = computed(() => publishedVersions.value.find((v) => v.versionId === form.caseset_version_id))

// null = 跟随评测集版本绑定
const effectiveJudgeName = computed(() => {
  if (form.judge_config_id) {
    return judgeConfigs.value.find((j) => j.id === form.judge_config_id)?.name ?? ''
  }
  return selectedVersion.value?.judgeConfigName || '默认评审模型(或未配置)'
})

const selectedWeights = computed(() =>
  weightTemplates.value.find((w) => w.id === form.weight_template_id)?.weights ?? {},
)

const rootcauseVersion = computed(() =>
  rootCauses.value.length ? Math.max(...rootCauses.value.map((r) => r.introduced_version)) : 1,
)

const canNext = computed(() => {
  if (step.value === 0) return form.agent_id !== null
  if (step.value === 1) return form.caseset_version_id !== null
  return true
})

async function doCreate(andSubmit: boolean) {
  submitting.value = true
  try {
    const run = await createRun({
      name: form.name || undefined,
      agent_id: form.agent_id!,
      caseset_version_id: form.caseset_version_id!,
      weight_template_id: form.weight_template_id,
      rootcause_version: rootcauseVersion.value,
      review_policy: form.review_policy,
      fail_threshold: form.fail_threshold,
      pass_k: form.pass_k,
      baseline_run_id: form.baseline_run_id,
      judge_config_id: form.judge_config_id,
    })
    if (andSubmit) {
      await submitRun(run.id)
      ElMessage.success(`Run #${run.id} 已创建并提交`)
    } else {
      ElMessage.success(`Run #${run.id} 草稿已创建`)
    }
    router.push(`/runs/${run.id}`)
  } catch (e) {
    ElMessage.error((e as Error).message)
  } finally {
    submitting.value = false
  }
}

onMounted(async () => {
  ;[agents.value, caseSets.value, weightTemplates.value, rootCauses.value, judgeConfigs.value] = await Promise.all([
    listAgents(), listCaseSets(), listWeightTemplates(), listRootCauses(), listJudgeConfigs(),
  ])
  const def = weightTemplates.value.find((w) => w.is_default)
  form.weight_template_id = def?.id ?? weightTemplates.value[0]?.id ?? null
})

const enabledJudges = computed(() => judgeConfigs.value.filter((j) => j.enabled))

function onVersionChange(versionId: number | undefined) {
  form.caseset_version_id = versionId ?? null
  // 切换评测集版本时,清除任务级覆盖,回到跟随版本绑定
  form.judge_config_id = null
}
</script>

<template>
  <div class="wizard">
    <el-steps :active="step" align-center style="margin-bottom: 26px">
      <el-step title="选择 Agent" />
      <el-step title="选择评测集" />
      <el-step title="评测配置" />
    </el-steps>

    <div v-if="step === 0" class="step-body">
      <el-alert v-if="!availableAgents.length" type="warning" :closable="false"
        title="暂无可用 Agent,请先在「Agent 管理」中创建并通过连通性验证" style="margin-bottom: 14px" />
      <el-table :data="availableAgents" border highlight-current-row @current-change="(r: AgentInfo) => (form.agent_id = r?.id ?? null)">
        <el-table-column width="50">
          <template #default="{ row }">
            <el-radio :model-value="form.agent_id" :value="row.id" @change="form.agent_id = row.id"><span /></el-radio>
          </template>
        </el-table-column>
        <el-table-column prop="name" label="名称" min-width="160" />
        <el-table-column prop="version_label" label="版本" width="80" />
        <el-table-column prop="adapter_key" label="适配器" width="120" />
        <el-table-column prop="access_type" label="接入方式" width="90" />
        <el-table-column label="最近验证" min-width="150">
          <template #default="{ row }">{{ row.last_verify_at ? row.last_verify_at.replace('T', ' ').slice(0, 19) : '-' }}</template>
        </el-table-column>
      </el-table>
    </div>

    <div v-if="step === 1" class="step-body">
      <el-alert v-if="!publishedVersions.length" type="warning" :closable="false"
        title="暂无已发布且非空的评测集版本,请先在「评测集管理」中发布" style="margin-bottom: 14px" />
      <el-table :data="publishedVersions" border highlight-current-row
        @current-change="(r: { versionId: number; judgeConfigName: string } | undefined) => onVersionChange(r?.versionId)">
        <el-table-column width="50">
          <template #default="{ row }">
            <el-radio :model-value="form.caseset_version_id" :value="row.versionId" @change="onVersionChange(row.versionId)"><span /></el-radio>
          </template>
        </el-table-column>
        <el-table-column prop="label" label="评测集版本" min-width="200" />
        <el-table-column prop="caseCount" label="评测用例数" width="110" />
        <el-table-column prop="judgeConfigName" label="绑定评审模型" min-width="160">
          <template #default="{ row }">{{ row.judgeConfigName || '未绑定(使用默认)' }}</template>
        </el-table-column>
      </el-table>
    </div>

    <div v-if="step === 2" class="step-body">
      <el-form label-width="120px" style="max-width: 640px">
        <el-form-item label="任务名称">
          <el-input v-model="form.name" placeholder="留空自动生成:Agent(版本) × 评测集 vN" />
        </el-form-item>
        <el-form-item label="权重模板">
          <el-select v-model="form.weight_template_id" style="width: 320px">
            <el-option v-for="w in weightTemplates" :key="w.id" :value="w.id"
              :label="w.is_default ? `${w.name}(默认)` : w.name" />
          </el-select>
          <div class="weights-view">
            <el-tag v-for="(w, k) in selectedWeights" :key="k" size="small" effect="plain" style="margin-right: 6px">
              {{ k }} {{ w }}
            </el-tag>
            <span class="hint">创建时快照固化;评分仅使用模板所选维度(可在「权重模板」中只选单项能力)</span>
          </div>
        </el-form-item>
        <el-form-item label="评审模型">
          <el-select v-model="form.judge_config_id" clearable placeholder="跟随评测集版本绑定" style="width: 320px">
            <el-option v-for="j in enabledJudges" :key="j.id" :label="j.name" :value="j.id" />
          </el-select>
          <span class="hint" style="margin-left: 8px">
            清空即跟随评测集:{{ selectedVersion?.judgeConfigName || '未绑定(使用默认)' }}
          </span>
        </el-form-item>
        <el-form-item label="根因库版本">
          <span>v{{ rootcauseVersion }}</span>
        </el-form-item>
        <el-form-item label="人工抽检策略">
          <el-radio-group v-model="form.review_policy">
            <el-radio value="skip">跳过</el-radio>
            <el-radio value="sample">抽检</el-radio>
            <el-radio value="full">全检</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="失败占比阈值">
          <div class="slider-row">
            <el-slider v-model="form.fail_threshold" :min="0" :max="1" :step="0.05" style="flex: 1" />
            <span class="threshold-num">{{ Math.round(form.fail_threshold * 100) }}%</span>
          </div>
          <div class="hint">失败用例占比达到该阈值时,Run 判为 failed</div>
        </el-form-item>
        <el-form-item label="基线 Run ID">
          <el-input-number v-model="form.baseline_run_id" :min="1" placeholder="可选,用于对比" style="width: 200px" />
          <span class="hint" style="margin-left: 8px">可选,M4 用于跨版本对比</span>
        </el-form-item>
        <el-form-item label="Pass@k 重复次数">
          <el-input-number v-model="form.pass_k" :min="1" :max="20" style="width: 120px" />
          <span class="hint" style="margin-left: 8px">每个用例重复执行次数(默认 1),用于统计非确定性 Agent 的通过率</span>
        </el-form-item>
      </el-form>
    </div>

    <div class="footer-btns">
      <el-button :disabled="step === 0" @click="step--">上一步</el-button>
      <el-button @click="router.push('/runs')">取消</el-button>
      <el-button v-if="step < 2" type="primary" :disabled="!canNext" @click="step++">下一步</el-button>
      <template v-else>
        <el-button :loading="submitting" @click="doCreate(false)">创建草稿</el-button>
        <el-button type="primary" :loading="submitting" @click="doCreate(true)">创建并提交</el-button>
      </template>
    </div>

    <el-descriptions v-if="step === 2" title="确认信息" :column="2" border style="margin-top: 18px">
      <el-descriptions-item label="Agent">{{ selectedAgent?.name }}({{ selectedAgent?.version_label }})</el-descriptions-item>
      <el-descriptions-item label="适配器">{{ selectedAgent?.adapter_key }}</el-descriptions-item>
      <el-descriptions-item label="评测集版本">{{ selectedVersion?.label }}</el-descriptions-item>
      <el-descriptions-item label="评测用例数">{{ selectedVersion?.caseCount }}</el-descriptions-item>
      <el-descriptions-item label="评审模型">{{ effectiveJudgeName }}</el-descriptions-item>
      <el-descriptions-item label="权重模板">
        {{ weightTemplates.find((w) => w.id === form.weight_template_id)?.name }}
      </el-descriptions-item>
    </el-descriptions>
  </div>
</template>

<style scoped>
.wizard { max-width: 960px; margin: 0 auto; }
.step-body { min-height: 320px; }
.footer-btns { display: flex; justify-content: center; gap: 10px; margin-top: 20px; }
.weights-view { display: flex; align-items: center; flex-wrap: wrap; gap: 2px; margin-top: 6px; }
.hint { font-size: 12px; color: var(--el-text-color-secondary); }
.slider-row { display: flex; align-items: center; gap: 12px; width: 100%; }
.threshold-num { font-weight: 600; width: 44px; text-align: right; }
</style>
