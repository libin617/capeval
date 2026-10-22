<script setup lang="ts">
import { computed } from 'vue'
import RadarChart from './RadarChart.vue'
import type { ReportPayload } from '../api/types'

const props = defineProps<{ report: ReportPayload }>()

const DIM_ORDER = ['D1', 'D2', 'D3', 'D4', 'D5', 'D6', 'D7', 'D8']
const RESP_LABEL: Record<string, string> = {
  agent: 'Agent', platform: '平台', third_party: '第三方', case_maintainer: '用例维护方',
}

const ov = computed(() => props.report.overview)

const radarValues = computed<Record<string, number | null>>(() => {
  const out: Record<string, number | null> = {}
  for (const d of DIM_ORDER) out[d] = props.report.dimensions.find((r) => r.dim === d)?.score ?? null
  return out
})
const radarLabels = computed<Record<string, string>>(() => {
  const out: Record<string, string> = {}
  for (const d of DIM_ORDER) out[d] = props.report.dimensions.find((r) => r.dim === d)?.name ?? ''
  return out
})

/** 维度详情表仅展示本次权重模板选中的维度 */
const dimensionRows = computed(() =>
  props.report.dimensions.filter((r) => r.selected !== false && r.weight > 0),
)

function scoreColor(v: number | null | undefined): string {
  if (v === null || v === undefined) return 'info'
  if (v >= 80) return 'success'
  if (v >= 60) return 'warning'
  return 'danger'
}

/** 根因 × 维度交叉矩阵:行 = 根因编码,列 = 维度 */
const matrixRows = computed(() => {
  const mat = props.report.attribution.matrix ?? {}
  return Object.entries(mat)
    .sort((a, b) => a[0].localeCompare(b[0], undefined, { numeric: true }))
    .map(([code, dims]) => ({ code, dims }))
})
const matrixDims = computed(() =>
  [...new Set(matrixRows.value.flatMap((r) => Object.keys(r.dims)))].sort())

const respRows = computed(() =>
  Object.entries(props.report.attribution.responsibility_summary ?? {})
    .map(([k, v]) => ({ key: k, label: RESP_LABEL[k] ?? k, value: v })))
</script>

<template>
  <div class="report-body">
    <!-- ① 总览 -->
    <el-card shadow="never" class="sec">
      <div class="overview">
        <div class="ov-left">
          <div class="ov-name">{{ ov.name }}</div>
          <div class="ov-sub">
            {{ ov.agent_name }}({{ ov.agent_version }}) · 评测集 {{ ov.caseset_name }} v{{ ov.caseset_version_no }}
          </div>
          <div class="ov-sub">
            根因库 v{{ ov.rootcause_version }} · 复核策略 {{ ov.review_policy }} · 权重模板 #{{ ov.weight_template_id }}
          </div>
          <div class="ov-kpis">
            <div class="kpi">
              <div class="kpi-num" :class="`c-${scoreColor(ov.quality_score)}`">{{ ov.quality_score ?? '-' }}</div>
              <div class="kpi-label">质量分(百分制)</div>
            </div>
            <div class="kpi">
              <div class="kpi-num" :class="`c-${scoreColor(ov.efficiency_score)}`">{{ ov.efficiency_score ?? '-' }}</div>
              <div class="kpi-label">效率分(百分制)</div>
            </div>
            <div class="kpi">
              <div class="kpi-num small">{{ ov.case_succeeded }}/{{ ov.case_failed }}</div>
              <div class="kpi-label">评测用例 成功/失败(共 {{ ov.case_total }})</div>
            </div>
          </div>
          <div class="ov-tags">
            <el-tag size="small" :type="ov.review_mark === 'reviewed' ? 'success' : 'info'" effect="plain">
              {{ ov.review_mark === 'reviewed' ? '已人工复核' : '未经人工复核' }}
            </el-tag>
            <el-tag v-if="ov.baseline" size="small" effect="plain" type="warning">
              vs 基线 #{{ ov.baseline.run_id }} · 质量 {{ ov.baseline.quality_delta }} / 效率 {{ ov.baseline.efficiency_delta }}
            </el-tag>
            <span class="hint">生成于 {{ report.generated_at.replace('T', ' ') }}</span>
          </div>
        </div>
        <RadarChart :values="radarValues" :labels="radarLabels" :size="290" />
      </div>
    </el-card>

    <!-- ② 维度详情 -->
    <h3 class="sec-title">② 维度详情</h3>
    <el-table :data="dimensionRows" border size="small">
      <el-table-column prop="dim" label="维度" width="70" />
      <el-table-column prop="name" label="名称" min-width="140" />
      <el-table-column prop="weight" label="权重" width="80" />
      <el-table-column label="得分(0-5)" width="120">
        <template #default="{ row }">
          <el-tag v-if="row.score !== null" size="small"
            :type="row.score >= 4 ? 'success' : row.score >= 3 ? 'warning' : 'danger'">
            {{ row.score.toFixed(2) }}
          </el-tag>
          <span v-else class="hint">未评</span>
        </template>
      </el-table-column>
      <el-table-column label="分布" min-width="200">
        <template #default="{ row }">
          <el-progress :percentage="Math.round(((row.score ?? 0) / 5) * 100)" :stroke-width="8" />
        </template>
      </el-table-column>
      <el-table-column prop="scored_cases" label="已评用例" width="90" />
    </el-table>

    <!-- ③ 失败分析 TOP-N -->
    <h3 class="sec-title">③ 失败分析 TOP-N</h3>
    <el-table :data="report.top_failures" border size="small">
      <el-table-column type="index" label="#" width="46" />
      <el-table-column prop="testcase_title" label="评测用例" min-width="200" show-overflow-tooltip />
      <el-table-column prop="difficulty" label="难度" width="80" />
      <el-table-column prop="quality_score" label="质量分" width="90" />
      <el-table-column prop="deduction" label="扣分" width="80" />
      <el-table-column label="主根因" min-width="200">
        <template #default="{ row }">
          <template v-if="row.primary">
            <span class="rc-code">{{ row.primary.code }}</span> {{ row.primary.name }}
          </template>
          <span v-else class="hint">未确定</span>
        </template>
      </el-table-column>
      <el-table-column label="证据信号" min-width="180">
        <template #default="{ row }">
          <el-tag v-for="s in row.evidence?.signals ?? []" :key="s" size="small" effect="plain"
            type="warning" style="margin-right: 4px">{{ s }}</el-tag>
          <span v-if="!row.evidence?.signals?.length" class="hint">-</span>
        </template>
      </el-table-column>
      <el-table-column label="轨迹" width="80">
        <template #default="{ row }">
          <router-link :to="`/executions/${row.execution_id}`" class="link">查看</router-link>
        </template>
      </el-table-column>
      <template #empty><span class="hint">无失分用例</span></template>
    </el-table>

    <!-- ④ 归因分布 -->
    <h3 class="sec-title">④ 归因分布</h3>
    <div class="attr-kpis">
      <span>Agent 责任占比 <b>{{ report.attribution.agent_responsibility_pct ?? '-' }}%</b></span>
      <span v-for="r in respRows" :key="r.key">{{ r.label }} <b>{{ r.value }}</b></span>
      <span>未确定 <b>{{ report.attribution.undetermined.length }}</b></span>
    </div>
    <el-table :data="report.attribution.distribution" border size="small">
      <el-table-column label="编码 / 名称" min-width="240">
        <template #default="{ row }">
          <span class="rc-code">{{ row.code }}</span> {{ row.name }}
        </template>
      </el-table-column>
      <el-table-column prop="level" label="层级" width="70" />
      <el-table-column label="责任归属" width="110">
        <template #default="{ row }">{{ RESP_LABEL[row.responsibility] ?? row.responsibility }}</template>
      </el-table-column>
      <el-table-column prop="primary" label="主根因" width="90" />
      <el-table-column prop="secondary" label="次要" width="80" />
      <template #empty><span class="hint">无归因结论</span></template>
    </el-table>

    <div v-if="matrixRows.length" class="matrix-wrap">
      <div class="matrix-title">根因 × 维度交叉矩阵</div>
      <el-table :data="matrixRows" border size="small">
        <el-table-column prop="code" label="根因" width="130" />
        <el-table-column v-for="d in matrixDims" :key="d" :label="d" width="70" align="center">
          <template #default="{ row }">{{ row.dims[d] ?? 0 }}</template>
        </el-table-column>
      </el-table>
    </div>

    <div v-if="report.attribution.undetermined.length" class="und-wrap">
      <div class="matrix-title">未确定清单</div>
      <el-table :data="report.attribution.undetermined" border size="small">
        <el-table-column prop="testcase_title" label="评测用例" min-width="200" />
        <el-table-column label="信号" min-width="200">
          <template #default="{ row }">
            <el-tag v-for="s in row.signals" :key="s" size="small" effect="plain" style="margin-right: 4px">{{ s }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="reason" label="原因" min-width="200" show-overflow-tooltip />
        <el-table-column label="轨迹" width="80">
          <template #default="{ row }">
            <router-link :to="`/executions/${row.execution_id}`" class="link">查看</router-link>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <!-- ⑤ 多维下钻 -->
    <h3 class="sec-title">⑤ 多维下钻</h3>
    <div class="drill-grid">
      <div>
        <div class="matrix-title">按难度</div>
        <el-table :data="report.drilldown.by_difficulty" border size="small">
          <el-table-column prop="key" label="难度" />
          <el-table-column prop="cases" label="评测用例数" width="110" />
          <el-table-column prop="avg_quality" label="平均质量" width="100" />
          <el-table-column prop="avg_efficiency" label="平均效率" width="100" />
        </el-table>
      </div>
      <div>
        <div class="matrix-title">按标签</div>
        <el-table :data="report.drilldown.by_tag" border size="small" max-height="260">
          <el-table-column prop="key" label="标签" />
          <el-table-column prop="cases" label="评测用例数" width="110" />
          <el-table-column prop="avg_quality" label="平均质量" width="100" />
          <el-table-column prop="avg_efficiency" label="平均效率" width="100" />
        </el-table>
      </div>
    </div>

    <!-- ⑥ 附录 -->
    <h3 class="sec-title">⑥ 附录</h3>
    <el-card shadow="never" class="sec">
      <div class="appendix-row">
        <span class="hint">权重快照</span>
        <el-tag v-for="(w, k) in ov.weight_snapshot" :key="k" size="small" effect="plain">{{ k }} {{ w }}</el-tag>
      </div>
      <div class="appendix-row">
        <span class="hint">评审引擎</span><span>{{ report.appendix.judge }}</span>
        <span class="hint" style="margin-left: 16px">引擎标识</span><span>{{ report.engine }}</span>
      </div>
      <div class="appendix-row" style="align-items: flex-start">
        <span class="hint">数据完整性声明</span>
        <ul class="integrity">
          <li v-for="(t, i) in report.appendix.data_integrity" :key="i">{{ t }}</li>
        </ul>
      </div>
    </el-card>
  </div>
</template>

<style scoped>
.report-body { max-width: 1180px; }
.sec-title { font-size: 15px; margin: 20px 0 10px; }
.sec { margin: 14px 0; }
.overview { display: flex; align-items: center; gap: 24px; flex-wrap: wrap; }
.ov-left { flex: 1; min-width: 320px; }
.ov-name { font-size: 18px; font-weight: 700; }
.ov-sub { font-size: 12px; color: var(--el-text-color-secondary); margin-top: 4px; }
.ov-kpis { display: flex; gap: 14px; margin: 14px 0 8px; flex-wrap: wrap; }
.kpi { background: var(--el-fill-color-light); border-radius: 8px; padding: 10px 18px; }
.kpi-num { font-size: 28px; font-weight: 700; line-height: 1.1; }
.kpi-num.small { font-size: 20px; }
.kpi-num.c-success { color: var(--el-color-success); }
.kpi-num.c-warning { color: var(--el-color-warning); }
.kpi-num.c-danger { color: var(--el-color-danger); }
.kpi-num.c-info { color: var(--el-text-color-placeholder); }
.kpi-label { font-size: 12px; color: var(--el-text-color-secondary); margin-top: 2px; }
.ov-tags { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.attr-kpis { display: flex; gap: 18px; flex-wrap: wrap; font-size: 13px; margin-bottom: 10px; color: var(--el-text-color-secondary); }
.attr-kpis b { color: var(--el-text-color-primary); font-size: 15px; }
.matrix-wrap, .und-wrap { margin-top: 14px; }
.matrix-title { font-size: 13px; font-weight: 600; margin: 8px 0 6px; }
.drill-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.appendix-row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 8px; font-size: 13px; }
.integrity { margin: 0; padding-left: 18px; font-size: 12px; color: var(--el-text-color-secondary); }
.hint { font-size: 12px; color: var(--el-text-color-secondary); }
.rc-code { font-family: monospace; color: var(--el-color-primary); font-weight: 600; }
.link { color: var(--el-color-primary); text-decoration: none; }
</style>
