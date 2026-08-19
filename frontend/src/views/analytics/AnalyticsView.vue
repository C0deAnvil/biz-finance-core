<template>
  <div class="page-stack">
    <PageSection title="统计分析" description="按指标查看系统业务数据的统计分析结果；默认展示更适合该指标的图形，也支持切换为其他常见图型。">
      <template #actions>
        <el-button @click="loadMetrics">刷新指标</el-button>
        <el-button type="primary" :disabled="!selectedMetricCode" @click="loadMetricDetail">刷新图表</el-button>
      </template>

      <el-row :gutter="16">
        <el-col :span="8">
          <el-input v-model="metricKeyword" clearable placeholder="搜索指标名称、说明或关键字" @input="loadMetrics" />
        </el-col>
        <el-col :span="6">
          <el-date-picker
            v-model="filters.dateRange"
            type="daterange"
            value-format="YYYY-MM-DD"
            range-separator="至"
            start-placeholder="开始日期"
            end-placeholder="结束日期"
            style="width: 100%"
            :disabled="!selectedMetric?.supports_date_range"
          />
        </el-col>
        <el-col :span="4">
          <el-select v-model="filters.chartType" placeholder="图表类型" style="width: 100%" :disabled="!selectedMetricCode">
            <el-option
              v-for="item in chartTypeOptions"
              :key="item.value"
              :label="item.label"
              :value="item.value"
            />
          </el-select>
        </el-col>
        <el-col :span="3">
          <el-input-number v-model="filters.topN" :min="3" :max="20" :disabled="!selectedMetric?.supports_top_n" style="width: 100%" />
        </el-col>
        <el-col :span="3">
          <el-button type="primary" style="width: 100%" :disabled="!selectedMetricCode" @click="loadMetricDetail">查看分析</el-button>
        </el-col>
      </el-row>
    </PageSection>

    <el-row :gutter="16">
      <el-col :span="7">
        <PageSection title="指标目录" description="当前用户仅可查看自身职责范围内的统计指标。">
          <el-empty v-if="!metrics.length" description="当前角色暂无可查看指标" />
          <el-scrollbar v-else height="620px">
            <div
              v-for="item in metrics"
              :key="item.code"
              class="metric-card"
              :class="{ active: item.code === selectedMetricCode }"
              @click="selectMetric(item)"
            >
              <div class="metric-module">{{ item.module_label }}</div>
              <div class="metric-name">{{ item.name }}</div>
              <div class="metric-desc">{{ item.description }}</div>
              <div class="metric-formula">计算：{{ item.calculation_logic }}</div>
              <div class="metric-meta">
                <el-tag size="small" type="info">默认图：{{ chartTypeLabel(item.default_chart_type) }}</el-tag>
                <el-tag size="small" :type="item.supports_date_range ? 'success' : 'warning'">
                  {{ item.supports_date_range ? '支持时间筛选' : '实时快照指标' }}
                </el-tag>
              </div>
            </div>
          </el-scrollbar>
        </PageSection>
      </el-col>

      <el-col :span="17">
        <PageSection
          :title="selectedMetric?.name || '指标详情'"
          :description="selectedMetric?.description || '请选择左侧指标查看统计结果。'"
        >
          <el-empty v-if="!selectedMetricCode" description="请选择需要查看的指标" />
          <div v-else-if="loadingDetail" class="loading-box">
            <el-skeleton animated :rows="8" />
          </div>
          <template v-else-if="metricDetail">
            <div class="calculation-box">
              <div class="calculation-title">计算逻辑 / 公式</div>
              <div>{{ metricDetail.metric?.calculation_logic || selectedMetric?.calculation_logic || '-' }}</div>
            </div>
            <el-row :gutter="12" class="summary-row">
              <el-col v-for="item in metricDetail.summary || []" :key="item.label" :span="8">
                <el-statistic :title="item.label" :value="item.value || 0">
                  <template #suffix>{{ item.unit || '' }}</template>
                </el-statistic>
              </el-col>
            </el-row>

            <div class="chart-panel">
              <SimpleAnalyticsChart
                :categories="metricDetail.chart.categories || []"
                :series="metricDetail.chart.series || []"
                :chart-type="metricDetail.chart_type"
                :unit="metricDetail.chart.unit || ''"
              />
            </div>

            <el-table :data="metricDetail.chart.rows || []" stripe class="mt-16">
              <el-table-column prop="label" label="分类" min-width="180" show-overflow-tooltip />
              <template v-if="isSingleSeries">
                <el-table-column prop="value" label="数值" min-width="120" />
                <el-table-column prop="percentage" label="占比" min-width="100">
                  <template #default="scope">{{ scope.row.percentage ?? 0 }}%</template>
                </el-table-column>
              </template>
              <template v-else>
                <el-table-column
                  v-for="item in metricDetail.chart.series || []"
                  :key="item.name"
                  :prop="item.name"
                  :label="item.name"
                  min-width="120"
                />
              </template>
            </el-table>
          </template>
        </PageSection>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/col/style/css'
import 'element-plus/es/components/date-picker/style/css'
import 'element-plus/es/components/empty/style/css'
import 'element-plus/es/components/input/style/css'
import 'element-plus/es/components/input-number/style/css'
import 'element-plus/es/components/row/style/css'
import 'element-plus/es/components/scrollbar/style/css'
import 'element-plus/es/components/select/style/css'
import 'element-plus/es/components/skeleton/style/css'
import 'element-plus/es/components/statistic/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/tag/style/css'

import ElButton from 'element-plus/es/components/button/index.mjs'
import ElCol from 'element-plus/es/components/col/index.mjs'
import ElDatePicker from 'element-plus/es/components/date-picker/index.mjs'
import ElEmpty from 'element-plus/es/components/empty/index.mjs'
import ElInput from 'element-plus/es/components/input/index.mjs'
import ElInputNumber from 'element-plus/es/components/input-number/index.mjs'
import ElRow from 'element-plus/es/components/row/index.mjs'
import ElScrollbar from 'element-plus/es/components/scrollbar/index.mjs'
import ElSelect, { ElOption } from 'element-plus/es/components/select/index.mjs'
import ElSkeleton from 'element-plus/es/components/skeleton/index.mjs'
import ElStatistic from 'element-plus/es/components/statistic/index.mjs'
import ElTable, { ElTableColumn } from 'element-plus/es/components/table/index.mjs'
import ElTag from 'element-plus/es/components/tag/index.mjs'

import { computed, onMounted, reactive, ref } from 'vue'

import { get } from '../../api/client'
import PageSection from '../../components/PageSection.vue'
import SimpleAnalyticsChart from '../../components/SimpleAnalyticsChart.vue'

const metrics = ref([])
const selectedMetricCode = ref('')
const metricDetail = ref(null)
const loadingDetail = ref(false)
const metricKeyword = ref('')

const filters = reactive({
  dateRange: [],
  chartType: '',
  topN: 8
})

const chartTypeMap = {
  bar: '条形图',
  hbar: '横向柱状图',
  line: '折线图',
  area: '面积图',
  pie: '饼图',
  donut: '环形图'
}

const selectedMetric = computed(() => metrics.value.find((item) => item.code === selectedMetricCode.value) || null)
const chartTypeOptions = computed(() =>
  (selectedMetric.value?.supported_chart_types || []).map((item) => ({ value: item, label: chartTypeLabel(item) }))
)
const isSingleSeries = computed(() => (metricDetail.value?.chart?.series || []).length <= 1)

function chartTypeLabel(value) {
  return chartTypeMap[value] || value
}

async function loadMetrics() {
  const query = metricKeyword.value.trim() ? `?keyword=${encodeURIComponent(metricKeyword.value.trim())}` : ''
  const rows = await get(`/api/analytics/metrics${query}`, { force: true })
  metrics.value = rows || []
  if (!selectedMetricCode.value && metrics.value.length) {
    selectMetric(metrics.value[0])
  } else if (selectedMetricCode.value && !metrics.value.some((item) => item.code === selectedMetricCode.value)) {
    selectedMetricCode.value = ''
    metricDetail.value = null
  }
}

function selectMetric(metric) {
  selectedMetricCode.value = metric.code
  filters.chartType = metric.default_chart_type
  if (!metric.supports_top_n) {
    filters.topN = 8
  }
  if (!metric.supports_date_range) {
    filters.dateRange = []
  }
  loadMetricDetail()
}

async function loadMetricDetail() {
  if (!selectedMetricCode.value) return
  const metric = selectedMetric.value
  if (!metric) return
  loadingDetail.value = true
  try {
    const params = new URLSearchParams()
    if (metric.supports_date_range && filters.dateRange?.length === 2) {
      params.set('date_from', filters.dateRange[0])
      params.set('date_to', filters.dateRange[1])
    }
    if (filters.chartType) {
      params.set('chart_type', filters.chartType)
    }
    if (metric.supports_top_n) {
      params.set('top_n', String(filters.topN || 8))
    }
    const query = params.toString() ? `?${params.toString()}` : ''
    metricDetail.value = await get(`/api/analytics/metrics/${selectedMetricCode.value}${query}`, { force: true })
  } finally {
    loadingDetail.value = false
  }
}

onMounted(loadMetrics)
</script>

<style scoped>
.page-stack {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.metric-card {
  padding: 14px 16px;
  border: 1px solid #e4edf6;
  border-radius: 12px;
  margin-bottom: 12px;
  cursor: pointer;
  background: #fff;
  transition: all 0.18s ease;
}

.metric-card:hover,
.metric-card.active {
  border-color: #4fb3e8;
  background: #eef8ff;
  box-shadow: 0 10px 24px rgba(79, 179, 232, 0.12);
}

.metric-module {
  display: inline-flex;
  align-items: center;
  padding: 2px 8px;
  background: #ecf5ff;
  color: #409eff;
  border-radius: 999px;
  font-size: 12px;
  margin-bottom: 8px;
}

.metric-name {
  font-size: 16px;
  font-weight: 700;
  color: #1f2f3d;
  margin-bottom: 8px;
}

.metric-desc {
  font-size: 13px;
  color: #6b7280;
  line-height: 1.6;
  min-height: 40px;
}

.metric-formula {
  margin-top: 6px;
  font-size: 12px;
  color: #7a8a99;
  line-height: 1.5;
}

.metric-meta {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 12px;
}

.summary-row {
  margin-bottom: 14px;
}

.chart-panel {
  border: 1px solid #e6eef7;
  border-radius: 12px;
  background: #fff;
  padding: 18px;
}

.calculation-box {
  margin-bottom: 14px;
  padding: 12px 14px;
  border: 1px solid #d9ecff;
  border-radius: 10px;
  background: #f5faff;
  color: #475467;
  line-height: 1.6;
}

.calculation-title {
  margin-bottom: 4px;
  font-weight: 600;
  color: #1f4e79;
}

.loading-box {
  padding: 12px 0;
}

.mt-16 {
  margin-top: 16px;
}
</style>
