<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { getTrend, getFalsePositiveStats, listAgents } from '../api'
import type { AgentInfo, FalsePositiveStats, TrendData } from '../api/types'

const router = useRouter()
const agents = ref<AgentInfo[]>([])
const agentId = ref<number | undefined>(undefined)
const trend = ref<TrendData | null>(null)
const fp = ref<FalsePositiveStats | null>(null)
const loading = ref(false)

const QUALITY_COLOR = '#409eff'
const EFFICIENCY_COLOR = '#67c23a'

async function loadTrend() {
  loading.value = true
  try {
    trend.value = await getTrend(agentId.value)
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  try {
    agents.value = await listAgents()
  } catch { /* 忽略 */ }
  await Promise.all([loadTrend(), loadFp()])
})

async function loadFp() {
  try {
    fp.value = await getFalsePositiveStats()
  } catch { /* 忽略 */ }
}

// ---------- SVG 折线图(P1 趋势看板) ----------
const W = 860
const H = 300
const PAD = { top: 24, right: 24, bottom: 46, left: 44 }

const points = computed(() => trend.value?.points ?? [])

function xAt(i: number, n: number): number {
  if (n <= 1) return PAD.left + (W - PAD.left - PAD.right) / 2
  return PAD.left + (i / (n - 1)) * (W - PAD.left - PAD.right)
}

function yAt(v: number): number {
  return PAD.top + (1 - Math.max(0, Math.min(100, v)) / 100) * (H - PAD.top - PAD.bottom)
}

function linePoints(key: 'quality_score' | 'efficiency_score'): string {
  const pts = points.value
    .map((p, i) => ({ x: xAt(i, points.value.length), y: p[key] }))
    .filter((p) => p.y !== null) as { x: number; y: number }[]
  return pts.map((p) => `${p.x.toFixed(1)},${yAt(p.y).toFixed(1)}`).join(' ')
}

const yTicks = [0, 20, 40, 60, 80, 100]

function rateColor(rate: number | null): string {
  if (rate === null) return 'info'
  if (rate >= 0.3) return 'danger'
  if (rate >= 0.15) return 'warning'
  return 'success'
}

function goRun(runId: number) {
  router.push(`/runs/${runId}`)
}
</script>

<template>
  <div class="trend-page">
    <el-card shadow="never">
      <template #header>
        <div class="card-head">
          <span>能力趋势(P1:同一 Agent 跨时间质量/效率曲线)</span>
          <div class="filters">
            <el-select v-model="agentId" placeholder="全部 Agent" clearable style="width: 240px" @change="loadTrend">
              <el-option v-for="a in agents" :key="a.id" :label="`${a.name}(${a.version_label})`" :value="a.id" />
            </el-select>
            <el-button :loading="loading" @click="loadTrend">刷新</el-button>
          </div>
        </div>
      </template>

      <el-empty v-if="points.length === 0" description="暂无已完成且已生成报告的评测任务" :image-size="80" />
      <template v-else>
        <svg :viewBox="`0 0 ${W} ${H}`" class="trend-svg">
          <!-- Y 轴刻度 -->
          <g v-for="t in yTicks" :key="t">
            <line :x1="PAD.left" :x2="W - PAD.right" :y1="yAt(t)" :y2="yAt(t)" stroke="#e4e7ed" stroke-width="1" />
            <text :x="PAD.left - 8" :y="yAt(t) + 4" text-anchor="end" class="tick-text">{{ t }}</text>
          </g>
          <!-- 质量分折线 -->
          <polyline :points="linePoints('quality_score')" fill="none" :stroke="QUALITY_COLOR" stroke-width="2.5" />
          <!-- 效率分折线 -->
          <polyline :points="linePoints('efficiency_score')" fill="none" :stroke="EFFICIENCY_COLOR" stroke-width="2.5" />
          <!-- 数据点 -->
          <g v-for="(p, i) in points" :key="p.run_id">
            <circle
              v-if="p.quality_score !== null" :cx="xAt(i, points.length)" :cy="yAt(p.quality_score)"
              r="4" :fill="QUALITY_COLOR"
            >
              <title>{{ p.name }}\n质量分 {{ p.quality_score }}</title>
            </circle>
            <circle
              v-if="p.efficiency_score !== null" :cx="xAt(i, points.length)" :cy="yAt(p.efficiency_score)"
              r="4" :fill="EFFICIENCY_COLOR"
            >
              <title>{{ p.name }}\n效率分 {{ p.efficiency_score }}</title>
            </circle>
            <!-- 点击热区 -->
            <rect
              :x="xAt(i, points.length) - 18" :y="PAD.top" width="36" :height="H - PAD.top - PAD.bottom"
              fill="transparent" class="hitzone" @click="goRun(p.run_id)"
            />
            <text
              :x="xAt(i, points.length)" :y="H - PAD.bottom + 18" text-anchor="middle"
              class="tick-text" :transform="points.length > 8 ? `rotate(24 ${xAt(i, points.length)} ${H - PAD.bottom + 18})` : ''"
            >
              #{{ p.run_id }}
            </text>
          </g>
        </svg>
        <div class="legend">
          <span class="legend-item"><i class="dot" :style="{ background: QUALITY_COLOR }" />质量分</span>
          <span class="legend-item"><i class="dot" :style="{ background: EFFICIENCY_COLOR }" />效率分</span>
          <span class="legend-hint">横轴为完成任务序号(#RunID),点击曲线区域可跳转任务详情</span>
        </div>
        <el-table :data="points" size="small" class="trend-table">
          <el-table-column label="完成时间" prop="completed_at" width="170" />
          <el-table-column label="任务" min-width="200">
            <template #default="{ row }">
              <el-link type="primary" @click="goRun(row.run_id)">{{ row.name }}</el-link>
            </template>
          </el-table-column>
          <el-table-column label="Agent" prop="agent_name" min-width="150" />
          <el-table-column label="评测集" prop="caseset_name" min-width="150" />
          <el-table-column label="质量分" width="90">
            <template #default="{ row }">{{ row.quality_score ?? '—' }}</template>
          </el-table-column>
          <el-table-column label="效率分" width="90">
            <template #default="{ row }">{{ row.efficiency_score ?? '—' }}</template>
          </el-table-column>
        </el-table>
      </template>
    </el-card>

    <el-card shadow="never" class="mt">
      <template #header>
        <div class="card-head">
          <span>归因误报率统计(P1:人工复核修正数据沉淀,误差率 ≥30% 生成信号调整建议)</span>
          <el-tag v-if="fp" :type="rateColor(fp.overall.error_rate)" size="small">
            整体误报率 {{ fp.overall.error_rate === null ? '暂无数据' : `${(fp.overall.error_rate * 100).toFixed(1)}%` }}
            (已复核 {{ fp.overall.reviewed }} 条)
          </el-tag>
        </div>
      </template>
      <el-empty v-if="!fp || fp.items.length === 0" description="暂无人工复核数据" :image-size="80" />
      <el-table v-else :data="fp.items" size="small">
        <el-table-column label="根因" min-width="220">
          <template #default="{ row }">
            <span class="rc-code">{{ row.code }}</span>
            <span class="rc-name">{{ row.name }}</span>
            <el-tag v-if="!row.enabled" size="small" type="info" class="rc-tag">已停用</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="已复核" prop="reviewed" width="80" align="center" />
        <el-table-column label="确认" prop="confirmed" width="70" align="center" />
        <el-table-column label="修正" prop="corrected" width="70" align="center" />
        <el-table-column label="误评" prop="rejected" width="70" align="center" />
        <el-table-column label="误报率" width="110" align="center">
          <template #default="{ row }">
            <el-tag :type="rateColor(row.error_rate)" size="small">
              {{ row.error_rate === null ? '—' : `${(row.error_rate * 100).toFixed(1)}%` }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="调整建议" min-width="260">
          <template #default="{ row }">
            <span v-if="row.suggestion" class="suggestion">{{ row.suggestion }}</span>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<style scoped>
.trend-page { max-width: 1200px; }
.mt { margin-top: 16px; }
.card-head { display: flex; align-items: center; justify-content: space-between; }
.filters { display: flex; gap: 8px; align-items: center; }
.trend-svg { width: 100%; height: auto; }
.tick-text { font-size: 11px; fill: var(--el-text-color-secondary); }
.hitzone { cursor: pointer; }
.legend { display: flex; align-items: center; gap: 16px; margin: 4px 0 12px; font-size: 13px; color: var(--el-text-color-regular); }
.legend-item { display: inline-flex; align-items: center; gap: 6px; }
.dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; }
.legend-hint { color: var(--el-text-color-placeholder); font-size: 12px; }
.trend-table { margin-top: 4px; }
.rc-code { font-family: monospace; margin-right: 8px; color: var(--el-color-primary); }
.rc-name { margin-right: 6px; }
.rc-tag { margin-left: 4px; }
.suggestion { color: var(--el-color-danger); font-size: 12px; }
.muted { color: var(--el-text-color-placeholder); }
</style>
