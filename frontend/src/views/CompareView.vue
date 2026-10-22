<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { compareRuns, listRuns } from '../api'
import type { CompareResult, RunInfo } from '../api/types'

const runs = ref<RunInfo[]>([])
const left = ref<number | null>(null)
const right = ref<number | null>(null)
const result = ref<CompareResult | null>(null)
const loading = ref(false)

const options = computed(() =>
  runs.value.map((r) => ({ value: r.id, label: `#${r.id} ${r.name} · ${r.agent_name}(${r.agent_version_label})` })))

async function loadRuns() {
  const list = await listRuns()
  // 仅已产出报告的任务可参与对比
  runs.value = list.filter((r) => r.status === 'completed' || r.status === 'failed' || r.status === 'reviewing')
  if (runs.value.length >= 2) {
    right.value = runs.value[0].id
    left.value = runs.value[1].id
  }
}

async function run() {
  if (!left.value || !right.value) { ElMessage.warning('请选择两个任务'); return }
  if (left.value === right.value) { ElMessage.warning('请选择两个不同的任务'); return }
  loading.value = true
  try {
    result.value = await compareRuns(left.value, right.value)
  } catch (e) {
    ElMessage.error((e as Error).message)
    result.value = null
  } finally {
    loading.value = false
  }
}

function deltaType(v: number | null) {
  if (v === null) return 'info'
  if (v > 0) return 'success'
  if (v < 0) return 'danger'
  return 'info'
}

const maxAbsDelta = computed(() => {
  const items = result.value?.case_waterfall ?? []
  return Math.max(1, ...items.map((i) => Math.abs(i.delta ?? 0)))
})

onMounted(loadRuns)
</script>

<template>
  <div v-loading="loading">
    <el-card shadow="never">
      <div class="picker">
        <div class="pick">
          <span class="hint">左侧(基线)</span>
          <el-select v-model="left" filterable placeholder="选择任务" style="width: 340px">
            <el-option v-for="o in options" :key="o.value" :label="o.label" :value="o.value" />
          </el-select>
        </div>
        <div class="pick">
          <span class="hint">右侧(对比)</span>
          <el-select v-model="right" filterable placeholder="选择任务" style="width: 340px">
            <el-option v-for="o in options" :key="o.value" :label="o.label" :value="o.value" />
          </el-select>
        </div>
        <el-button type="primary" @click="run">开始对比</el-button>
      </div>
      <div class="hint" style="margin-top: 8px">
        仅已完成评分/归因的任务可对比;两侧评测集版本或根因库版本不一致时,只输出总分与维度分对比。
      </div>
    </el-card>

    <template v-if="result">
      <el-alert v-if="!result.version_matched" type="warning" :closable="false" style="margin-top: 14px">
        <template #title>{{ result.version_note }}</template>
      </el-alert>

      <h3 class="sec-title">总分对比</h3>
      <div class="kpi-row">
        <el-card shadow="never" class="kpi-card">
          <div class="kpi-name">#{{ result.left.run_id }} {{ result.left.name }}</div>
          <div class="kpi-sub">{{ result.left.agent }}</div>
          <div class="kpi-num">{{ result.left.quality_score ?? '-' }}</div>
          <div class="hint">质量分</div>
          <div class="kpi-num small">{{ result.left.efficiency_score ?? '-' }}</div>
          <div class="hint">效率分</div>
        </el-card>
        <el-card shadow="never" class="kpi-card">
          <div class="kpi-name">#{{ result.right.run_id }} {{ result.right.name }}</div>
          <div class="kpi-sub">{{ result.right.agent }}</div>
          <div class="kpi-num">{{ result.right.quality_score ?? '-' }}</div>
          <div class="hint">质量分</div>
          <div class="kpi-num small">{{ result.right.efficiency_score ?? '-' }}</div>
          <div class="hint">效率分</div>
        </el-card>
        <el-card shadow="never" class="kpi-card delta-card">
          <div class="kpi-name">分差(右 - 左)</div>
          <el-tag size="large" :type="deltaType(result.delta.quality)">
            质量 {{ result.delta.quality ?? '-' }}
          </el-tag>
          <el-tag size="large" :type="deltaType(result.delta.efficiency)" style="margin-top: 8px">
            效率 {{ result.delta.efficiency ?? '-' }}
          </el-tag>
        </el-card>
      </div>

      <h3 class="sec-title">八维分差</h3>
      <el-table :data="result.dimension_diff" border size="small">
        <el-table-column prop="dim" label="维度" width="70" />
        <el-table-column prop="name" label="名称" min-width="140" />
        <el-table-column prop="left" label="左(基线)" width="110" />
        <el-table-column prop="right" label="右(对比)" width="110" />
        <el-table-column label="分差" width="110">
          <template #default="{ row }">
            <el-tag size="small" :type="deltaType(row.delta)">{{ row.delta ?? '-' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="变化" min-width="240">
          <template #default="{ row }">
            <div class="bar-wrap">
              <div class="bar-zero" />
              <div class="bar" :class="(row.delta ?? 0) >= 0 ? 'up' : 'down'"
                :style="{ width: `${Math.min(50, Math.abs(row.delta ?? 0) / 5 * 50)}%` }" />
            </div>
          </template>
        </el-table-column>
      </el-table>

      <template v-if="result.version_matched">
        <h3 class="sec-title">逐用例分差瀑布</h3>
        <el-table :data="result.case_waterfall" border size="small" max-height="420">
          <el-table-column prop="testcase_title" label="评测用例" min-width="220" show-overflow-tooltip />
          <el-table-column prop="left" label="左" width="80" />
          <el-table-column prop="right" label="右" width="80" />
          <el-table-column label="分差" width="90">
            <template #default="{ row }">
              <el-tag size="small" :type="deltaType(row.delta)">{{ row.delta ?? '-' }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="瀑布" min-width="220">
            <template #default="{ row }">
              <div class="bar-wrap">
                <div class="bar-zero" />
                <div class="bar" :class="(row.delta ?? 0) >= 0 ? 'up' : 'down'"
                  :style="{ width: `${Math.abs(row.delta ?? 0) / maxAbsDelta * 50}%` }" />
              </div>
            </template>
          </el-table-column>
        </el-table>

        <h3 class="sec-title">
          退化用例清单
          <el-tag size="small" type="danger" effect="plain" style="margin-left: 8px">
            {{ result.regressions.length }} 项
          </el-tag>
        </h3>
        <el-table :data="result.regressions" border size="small">
          <el-table-column prop="testcase_title" label="评测用例" min-width="240" show-overflow-tooltip />
          <el-table-column prop="left" label="左" width="80" />
          <el-table-column prop="right" label="右" width="80" />
          <el-table-column label="分差" width="90">
            <template #default="{ row }">
              <el-tag size="small" type="danger">{{ row.delta }}</el-tag>
            </template>
          </el-table-column>
          <template #empty><span class="hint">无退化用例</span></template>
        </el-table>

        <h3 class="sec-title">根因分布迁移</h3>
        <el-table :data="result.rootcause_migration" border size="small" max-height="360">
          <el-table-column prop="code" label="根因编码" width="140" />
          <el-table-column prop="left" label="左" width="90" />
          <el-table-column prop="right" label="右" width="90" />
          <el-table-column label="变化" width="110">
            <template #default="{ row }">
              <el-tag size="small" :type="deltaType(row.delta)">
                {{ row.delta > 0 ? `+${row.delta}` : row.delta }}
              </el-tag>
            </template>
          </el-table-column>
        </el-table>
      </template>
    </template>

    <el-empty v-else-if="!loading" description="选择两个任务后开始对比" />
  </div>
</template>

<style scoped>
.picker { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; }
.pick { display: flex; align-items: center; gap: 8px; }
.sec-title { font-size: 15px; margin: 20px 0 10px; }
.hint { font-size: 12px; color: var(--el-text-color-secondary); }
.kpi-row { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 14px; }
.kpi-card { text-align: left; }
.kpi-name { font-size: 14px; font-weight: 600; }
.kpi-sub { font-size: 12px; color: var(--el-text-color-secondary); margin-bottom: 6px; }
.kpi-num { font-size: 26px; font-weight: 700; }
.kpi-num.small { font-size: 18px; margin-top: 6px; }
.delta-card { display: flex; flex-direction: column; gap: 6px; }
.bar-wrap { position: relative; height: 12px; background: var(--el-fill-color-light); border-radius: 4px; }
.bar-zero { position: absolute; left: 50%; top: 0; bottom: 0; width: 1px; background: var(--el-border-color); }
.bar { position: absolute; top: 0; bottom: 0; border-radius: 4px; }
.bar.up { left: 50%; background: var(--el-color-success); }
.bar.down { right: 50%; background: var(--el-color-danger); }
</style>
