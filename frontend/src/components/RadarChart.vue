<script setup lang="ts">
import { computed } from 'vue'

/**
 * 纯 SVG 八维雷达图(M3):不引第三方图表库,八角布局,D1 起顺时针排列。
 * 输入 0-5 分制维度分,null 表示未评分(按 0 绘制并以灰色标注)。
 */
const props = defineProps<{
  values: Record<string, number | null> // {D1: 4.5, ...}
  labels: Record<string, string> // {D1: '代码正确性', ...}
  size?: number
}>()

const DIMS = ['D1', 'D2', 'D3', 'D4', 'D5', 'D6', 'D7', 'D8']
const RINGS = [1, 2, 3, 4, 5]

const vb = computed(() => {
  const s = props.size ?? 320
  return { s, cx: s / 2, cy: s / 2, r: s / 2 - 46 }
})

function point(idx: number, value: number): [number, string] {
  const { cx, cy, r } = vb.value
  const angle = -Math.PI / 2 + (idx * 2 * Math.PI) / DIMS.length
  const rr = (value / 5) * r
  const x = cx + rr * Math.cos(angle)
  const y = cy + rr * Math.sin(angle)
  return [idx, `${x.toFixed(1)},${y.toFixed(1)}`]
}

const ringPoints = computed(() =>
  RINGS.map((v) => DIMS.map((_, i) => point(i, v)[1]).join(' ')),
)

const axisEnds = computed(() => DIMS.map((_, i) => point(i, 5)[1]))

const valuePolygon = computed(() =>
  DIMS.map((d, i) => point(i, props.values[d] ?? 0)[1]).join(' '),
)

const labelPos = computed(() => {
  const { cx, cy, r } = vb.value
  return DIMS.map((d, i) => {
    const angle = -Math.PI / 2 + (i * 2 * Math.PI) / DIMS.length
    const lr = r + 30
    const x = cx + lr * Math.cos(angle)
    const y = cy + lr * Math.sin(angle)
    const anchor = Math.abs(Math.cos(angle)) < 0.3 ? 'middle' : Math.cos(angle) > 0 ? 'start' : 'end'
    return { dim: d, x, y, anchor, scored: props.values[d] !== null && props.values[d] !== undefined }
  })
})

const avgScore = computed(() => {
  const vals = DIMS.map((d) => props.values[d]).filter((v): v is number => v !== null && v !== undefined)
  if (!vals.length) return null
  return (vals.reduce((a, b) => a + b, 0) / vals.length).toFixed(1)
})
</script>

<template>
  <svg :width="vb.s" :height="vb.s" :viewBox="`0 0 ${vb.s} ${vb.s}`" class="radar">
    <!-- 同心环 -->
    <polygon v-for="(pts, i) in ringPoints" :key="i" :points="pts" class="ring" />
    <!-- 轴线 -->
    <line v-for="(end, i) in axisEnds" :key="i" :x1="vb.cx" :y1="vb.cy"
      :x2="end.split(',')[0]" :y2="end.split(',')[1]" class="axis" />
    <!-- 数据多边形 -->
    <polygon :points="valuePolygon" class="value-area" />
    <circle v-for="(d, i) in DIMS" :key="d" r="3" class="value-dot"
      :cx="point(i, values[d] ?? 0)[1].split(',')[0]" :cy="point(i, values[d] ?? 0)[1].split(',')[1]" />
    <!-- 维度标签 -->
    <g v-for="lp in labelPos" :key="lp.dim">
      <text :x="lp.x" :y="lp.y" :text-anchor="lp.anchor" class="dim-label">
        {{ lp.dim }} {{ labels[lp.dim] ?? '' }}
      </text>
      <text :x="lp.x" :y="lp.y + 13" :text-anchor="lp.anchor" class="dim-score"
        :class="{ unscored: !lp.scored }">
        {{ lp.scored ? (values[lp.dim] ?? 0).toFixed(1) : '未评' }}
      </text>
    </g>
    <!-- 中心均分 -->
    <text :x="vb.cx" :y="vb.cy - 4" text-anchor="middle" class="avg">{{ avgScore ?? '-' }}</text>
    <text :x="vb.cx" :y="vb.cy + 13" text-anchor="middle" class="avg-label">维度均分</text>
  </svg>
</template>

<style scoped>
.radar { display: block; margin: 0 auto; }
.ring { fill: none; stroke: var(--el-border-color-lighter); stroke-width: 1; }
.axis { stroke: var(--el-border-color-lighter); stroke-width: 1; }
.value-area { fill: rgba(64, 158, 255, 0.18); stroke: var(--el-color-primary); stroke-width: 2; }
.value-dot { fill: var(--el-color-primary); }
.dim-label { font-size: 11px; fill: var(--el-text-color-regular); }
.dim-score { font-size: 11px; font-weight: 600; fill: var(--el-color-primary); }
.dim-score.unscored { fill: var(--el-text-color-placeholder); font-weight: 400; }
.avg { font-size: 20px; font-weight: 700; fill: var(--el-text-color-primary); }
.avg-label { font-size: 10px; fill: var(--el-text-color-secondary); }
</style>
