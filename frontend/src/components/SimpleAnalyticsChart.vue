<template>
  <div class="chart-shell">
    <div class="chart-toolbar">
      <div class="chart-legend">
        <span v-for="item in legendItems" :key="item.name" class="legend-item">
          <span class="legend-dot" :style="{ backgroundColor: item.color }"></span>
          <span>{{ item.name }}</span>
        </span>
      </div>
    </div>

    <div v-if="isLineLike" class="line-chart">
      <div class="line-grid">
        <div v-for="tick in yTicks" :key="tick" class="grid-row">
          <span class="grid-label">{{ formatNumber(tick) }}</span>
          <div class="grid-line"></div>
        </div>
      </div>
      <svg viewBox="0 0 760 320" class="chart-svg">
        <defs>
          <linearGradient v-for="item in seriesWithPalette" :id="`fill-${item.name}`" :key="`fill-${item.name}`" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" :stop-color="item.color" stop-opacity="0.35" />
            <stop offset="100%" :stop-color="item.color" stop-opacity="0.04" />
          </linearGradient>
        </defs>
        <g>
          <line x1="56" y1="280" x2="720" y2="280" class="axis-line" />
          <line x1="56" y1="20" x2="56" y2="280" class="axis-line" />
        </g>
        <g v-for="(tick, index) in yTicks" :key="`y-${tick}`">
          <line :x1="56" :y1="scaleY(tick)" :x2="720" :y2="scaleY(tick)" class="grid-stroke" />
        </g>
        <template v-for="item in seriesWithPalette" :key="item.name">
          <path v-if="chartType === 'area'" :d="areaPath(item.values)" :fill="`url(#fill-${item.name})`" stroke="none" />
          <path :d="linePath(item.values)" fill="none" :stroke="item.color" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" />
          <circle
            v-for="(value, index) in item.values"
            :key="`${item.name}-${index}`"
            :cx="scaleX(index)"
            :cy="scaleY(value)"
            r="4"
            :fill="item.color"
            stroke="#fff"
            stroke-width="2"
          />
        </template>
        <g v-for="(label, index) in categories" :key="`x-${index}`">
          <text :x="scaleX(index)" y="304" class="x-label">{{ shortLabel(label) }}</text>
        </g>
      </svg>
    </div>

    <div v-else-if="chartType === 'bar'" class="bar-chart">
      <div v-for="(row, index) in normalizedRowsWithWidth" :key="`bar-${index}`" class="bar-row">
        <div class="bar-label" :title="row.label">{{ row.label }}</div>
        <div class="bar-track">
          <div class="stack-list" v-if="hasMultiSeries">
            <div
              v-for="(segment, segmentIndex) in row.segments"
              :key="`${row.label}-${segmentIndex}`"
              class="bar-fill"
              :style="{ width: `${segment.width}%`, backgroundColor: segment.color }"
            ></div>
          </div>
          <div
            v-else
            class="bar-fill"
            :style="{ width: `${row.primaryWidth}%`, backgroundColor: seriesWithPalette[0]?.color || '#4fb3e8' }"
          ></div>
        </div>
        <div class="bar-value">{{ formatNumber(row.total) }}</div>
      </div>
    </div>

    <div v-else-if="chartType === 'hbar'" class="hbar-chart">
      <div class="hbar-stage">
        <div v-for="(row, index) in normalizedRowsWithWidth" :key="`hbar-${index}`" class="hbar-col">
          <div class="hbar-track">
            <div
              v-if="hasMultiSeries"
              class="hbar-stack"
            >
              <div
                v-for="(segment, segmentIndex) in row.segments"
                :key="`${row.label}-${segmentIndex}`"
                class="hbar-fill"
                :style="{ height: `${segment.width}%`, backgroundColor: segment.color }"
              ></div>
            </div>
            <div
              v-else
              class="hbar-fill"
              :style="{ height: `${row.primaryWidth}%`, backgroundColor: seriesWithPalette[0]?.color || '#4fb3e8' }"
            ></div>
          </div>
          <div class="hbar-value">{{ formatNumber(row.total) }}</div>
          <div class="hbar-label" :title="row.label">{{ shortLabel(row.label, 8) }}</div>
        </div>
      </div>
    </div>

    <div v-else class="pie-chart">
      <svg viewBox="0 0 360 240" class="pie-svg">
        <g transform="translate(120,120)">
          <template v-for="(slice, index) in pieSlices" :key="`slice-${index}`">
            <path :d="slice.path" :fill="slice.color"></path>
            <circle
              v-if="chartType === 'donut'"
              cx="0"
              cy="0"
              r="48"
              fill="#fff"
            ></circle>
          </template>
        </g>
      </svg>
      <div class="pie-summary">
        <div class="pie-total-label">合计</div>
        <div class="pie-total-value">{{ formatNumber(totalValue) }}</div>
        <div class="pie-unit">{{ unit || '数值' }}</div>
      </div>
      <div class="pie-list">
        <div v-for="(row, index) in normalizedRows" :key="`pie-row-${index}`" class="pie-row">
          <span class="legend-dot" :style="{ backgroundColor: seriesPalette[index % seriesPalette.length] }"></span>
          <span class="pie-name">{{ row.label }}</span>
          <span class="pie-number">{{ formatNumber(row.total) }}</span>
          <span class="pie-percent">{{ formatPercent(row.total, totalValue) }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  categories: { type: Array, default: () => [] },
  series: { type: Array, default: () => [] },
  chartType: { type: String, default: 'bar' },
  unit: { type: String, default: '' }
})

const seriesPalette = ['#4fb3e8', '#6ecf7d', '#f4b55f', '#9d8cff', '#f58b8b', '#48c2c2', '#7d9ff5', '#f1d36d']

const seriesWithPalette = computed(() =>
  (props.series || []).map((item, index) => ({
    ...item,
    color: seriesPalette[index % seriesPalette.length]
  }))
)

const hasMultiSeries = computed(() => seriesWithPalette.value.length > 1)
const isLineLike = computed(() => ['line', 'area'].includes(props.chartType))

const normalizedRows = computed(() =>
  (props.categories || []).map((label, index) => {
    const segments = seriesWithPalette.value.map((item) => ({
      name: item.name,
      value: Number(item.values?.[index] || 0),
      color: item.color
    }))
    const total = segments.reduce((sum, item) => sum + item.value, 0)
    return {
      label,
      total,
      primary: segments[0]?.value || 0,
      segments
    }
  })
)

const maxPrimaryValue = computed(() => {
  const values = normalizedRows.value.map((item) => (hasMultiSeries.value ? item.total : item.primary))
  return Math.max(...values, 0)
})

const normalizedRowsWithWidth = computed(() =>
  normalizedRows.value.map((row) => {
    const base = maxPrimaryValue.value || 1
    const primaryWidth = Math.max((row.primary / base) * 100, row.primary > 0 ? 6 : 0)
    return {
      ...row,
      primaryWidth,
      segments: row.segments.map((segment) => ({
        ...segment,
        width: Math.max((segment.value / base) * 100, segment.value > 0 ? 4 : 0)
      }))
    }
  })
)

const totalValue = computed(() => normalizedRows.value.reduce((sum, row) => sum + row.total, 0))
const legendItems = computed(() => seriesWithPalette.value)

const yMax = computed(() => {
  const values = []
  seriesWithPalette.value.forEach((item) => {
    values.push(...(item.values || []).map((value) => Number(value || 0)))
  })
  const max = Math.max(...values, 0)
  if (max <= 0) return 1
  const magnitude = 10 ** Math.floor(Math.log10(max))
  return Math.ceil(max / magnitude * 5) / 5 * magnitude
})

const yTicks = computed(() => {
  const max = yMax.value
  return [max, max * 0.75, max * 0.5, max * 0.25, 0]
})

function scaleX(index) {
  if (!props.categories.length) return 56
  if (props.categories.length === 1) return 388
  return 56 + (664 / (props.categories.length - 1)) * index
}

function scaleY(value) {
  const max = yMax.value || 1
  return 280 - (Number(value || 0) / max) * 240
}

function linePath(values = []) {
  if (!values.length) return ''
  return values
    .map((value, index) => `${index === 0 ? 'M' : 'L'} ${scaleX(index)} ${scaleY(value)}`)
    .join(' ')
}

function areaPath(values = []) {
  if (!values.length) return ''
  const line = linePath(values)
  const firstX = scaleX(0)
  const lastX = scaleX(values.length - 1)
  return `${line} L ${lastX} 280 L ${firstX} 280 Z`
}

function polarToCartesian(cx, cy, r, angle) {
  const rad = ((angle - 90) * Math.PI) / 180
  return { x: cx + r * Math.cos(rad), y: cy + r * Math.sin(rad) }
}

const pieSlices = computed(() => {
  const rows = normalizedRows.value.filter((row) => row.total > 0)
  const total = rows.reduce((sum, row) => sum + row.total, 0) || 1
  let startAngle = 0
  return rows.map((row, index) => {
    const angle = (row.total / total) * 360
    const endAngle = startAngle + angle
    const start = polarToCartesian(0, 0, 90, endAngle)
    const end = polarToCartesian(0, 0, 90, startAngle)
    const largeArcFlag = angle > 180 ? 1 : 0
    const path = [`M 0 0`, `L ${end.x} ${end.y}`, `A 90 90 0 ${largeArcFlag} 1 ${start.x} ${start.y}`, 'Z'].join(' ')
    const slice = {
      path,
      color: seriesPalette[index % seriesPalette.length]
    }
    startAngle = endAngle
    return slice
  })
})

function formatNumber(value) {
  const numeric = Number(value || 0)
  return new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 2 }).format(numeric)
}

function formatPercent(value, total) {
  if (!total) return '0%'
  return `${((Number(value || 0) / Number(total || 1)) * 100).toFixed(1)}%`
}

function shortLabel(value, max = 10) {
  const text = String(value || '')
  return text.length > max ? `${text.slice(0, max)}…` : text
}
</script>

<style scoped>
.chart-shell {
  width: 100%;
  min-height: 320px;
}

.chart-toolbar {
  display: flex;
  justify-content: flex-end;
  margin-bottom: 12px;
}

.chart-legend {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
}

.legend-item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: #607080;
  font-size: 13px;
}

.legend-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  display: inline-block;
}

.line-chart {
  position: relative;
  min-height: 320px;
}

.line-grid {
  position: absolute;
  inset: 0;
  pointer-events: none;
}

.grid-row {
  display: flex;
  align-items: center;
  height: 64px;
}

.grid-label {
  width: 54px;
  font-size: 12px;
  color: #94a3b8;
}

.grid-line {
  flex: 1;
  border-top: 1px dashed #d9e4ef;
}

.chart-svg {
  width: 100%;
  height: 320px;
}

.axis-line {
  stroke: #9fb3c8;
  stroke-width: 1.5;
}

.grid-stroke {
  stroke: #eef3f8;
  stroke-width: 1;
}

.x-label {
  font-size: 12px;
  fill: #6b7280;
  text-anchor: middle;
}

.bar-chart,
.pie-chart {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.bar-row {
  display: grid;
  grid-template-columns: minmax(140px, 220px) 1fr 110px;
  align-items: center;
  gap: 14px;
}

.bar-label,
.hbar-label,
.pie-name {
  font-size: 13px;
  color: #334155;
}

.bar-track {
  height: 20px;
  border-radius: 999px;
  background: #edf4fa;
  overflow: hidden;
}

.bar-fill {
  height: 100%;
  border-radius: 999px;
}

.stack-list {
  display: flex;
  height: 100%;
  width: 100%;
}

.bar-value,
.hbar-value,
.pie-number,
.pie-percent {
  font-size: 13px;
  color: #64748b;
  text-align: right;
}

.hbar-chart {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.hbar-stage {
  display: flex;
  align-items: flex-end;
  gap: 16px;
  min-height: 260px;
  padding: 12px 0 0;
}

.hbar-col {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
}

.hbar-track {
  width: 100%;
  max-width: 52px;
  height: 220px;
  background: #edf4fa;
  border-radius: 14px 14px 10px 10px;
  display: flex;
  align-items: flex-end;
  overflow: hidden;
}

.hbar-fill,
.hbar-stack {
  width: 100%;
}

.hbar-fill {
  border-radius: 14px 14px 0 0;
}

.hbar-stack {
  display: flex;
  flex-direction: column;
  justify-content: flex-end;
  height: 100%;
}

.pie-chart {
  display: grid;
  grid-template-columns: 240px 120px 1fr;
  gap: 18px;
  align-items: center;
}

.pie-svg {
  width: 240px;
  height: 240px;
}

.pie-summary {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
}

.pie-total-label,
.pie-unit {
  font-size: 13px;
  color: #64748b;
}

.pie-total-value {
  font-size: 24px;
  font-weight: 700;
  color: #1f2f3d;
}

.pie-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.pie-row {
  display: grid;
  grid-template-columns: 16px 1fr 100px 70px;
  align-items: center;
  gap: 8px;
}
</style>
