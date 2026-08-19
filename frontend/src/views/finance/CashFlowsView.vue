<template>
  <div class="page-stack">
    <PageSection title="资金流水" description="按收入、支出和业务类型查看流水，支持快速筛选与导出。">
      <template #actions>
        <el-button @click="load">刷新</el-button>
        <el-button @click="exportTable">导出</el-button>
      </template>

      <el-row :gutter="12" class="filters-row">
        <el-col :span="8">
          <el-date-picker
            v-model="filters.dateRange"
            type="daterange"
            range-separator="至"
            start-placeholder="开始日期"
            end-placeholder="结束日期"
            value-format="YYYY-MM-DD"
            style="width: 100%"
            @change="load"
          />
        </el-col>
        <el-col :span="6">
          <el-select v-model="filters.flowType" clearable placeholder="流水方向" style="width: 100%">
            <el-option label="收入" value="incoming" />
            <el-option label="支出" value="outgoing" />
          </el-select>
        </el-col>
        <el-col :span="6">
          <el-select v-model="filters.businessType" clearable placeholder="业务类型" style="width: 100%">
            <el-option v-for="item in businessTypes" :key="item" :label="item" :value="item" />
          </el-select>
        </el-col>
        <el-col :span="4">
          <el-input v-model="filters.keyword" placeholder="搜索单号、备注、业务编号" clearable />
        </el-col>
      </el-row>

      <el-row :gutter="16" class="summary-row">
        <el-col :span="8"><el-statistic title="收入合计" :value="summary.incoming" /></el-col>
        <el-col :span="8"><el-statistic title="支出合计" :value="summary.outgoing" /></el-col>
        <el-col :span="8"><el-statistic title="净现金流" :value="summary.net" /></el-col>
      </el-row>

      <el-table :data="pagedRows" stripe :row-class-name="rowClassName">
        <el-table-column prop="record_no" label="流水号" width="160" />
        <el-table-column prop="flow_type" label="方向" width="100">
          <template #default="scope">
            <el-tag :type="scope.row.flow_type === 'incoming' ? 'success' : 'danger'">
              {{ scope.row.flow_type === 'incoming' ? '收入' : '支出' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="business_type" label="业务类型" width="150" />
        <el-table-column prop="related_no" label="关联单号" min-width="180" />
        <el-table-column prop="amount" label="金额" width="120">
          <template #default="scope">{{ formatNumber(scope.row.amount) }}</template>
        </el-table-column>
        <el-table-column prop="happened_at" label="发生时间" min-width="180">
          <template #default="scope">{{ formatDateTime(scope.row.happened_at) }}</template>
        </el-table-column>
        <el-table-column prop="remark" label="备注" min-width="220" show-overflow-tooltip />
      </el-table>
      <AppPagination
        v-model:page="pagination.page"
        v-model:page-size="pagination.pageSize"
        :total="total"
        :page-sizes="pageSizes"
      />
    </PageSection>
  </div>
</template>

<script setup>
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/col/style/css'
import 'element-plus/es/components/date-picker/style/css'
import 'element-plus/es/components/input/style/css'
import 'element-plus/es/components/row/style/css'
import 'element-plus/es/components/select/style/css'
import 'element-plus/es/components/statistic/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/tag/style/css'

import ElButton from 'element-plus/es/components/button/index.mjs'
import ElCol from 'element-plus/es/components/col/index.mjs'
import ElDatePicker from 'element-plus/es/components/date-picker/index.mjs'
import ElInput from 'element-plus/es/components/input/index.mjs'
import ElRow from 'element-plus/es/components/row/index.mjs'
import ElSelect, { ElOption } from 'element-plus/es/components/select/index.mjs'
import ElStatistic from 'element-plus/es/components/statistic/index.mjs'
import ElTable, { ElTableColumn } from 'element-plus/es/components/table/index.mjs'
import ElTag from 'element-plus/es/components/tag/index.mjs'

import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { parseFocusId } from '../../composables/useRouteFocus'

import { get } from '../../api/client'
import AppPagination from '../../components/AppPagination.vue'
import PageSection from '../../components/PageSection.vue'
import { useLocalPagination } from '../../composables/useLocalPagination'
import { exportRowsToCsv } from '../../utils/export'
import { formatDateTime, formatNumber } from '../../utils/format'

const route = useRoute()
const rows = ref([])
const filters = reactive({
  flowType: '',
  businessType: '',
  keyword: '',
  dateRange: []
})
const focusedRowId = ref(null)

const businessTypes = computed(() => [...new Set(rows.value.map((item) => item.business_type).filter(Boolean))])

function resolveCashFlowRow(focusId) {
  const focusType = String(route.query.focusType || '')
  if (!focusType || focusType === 'cash_flow' || focusType === 'cash_flow_record') {
    return rows.value.find((item) => item.id === focusId) || null
  }
  return rows.value.find((item) => item.business_type === focusType && item.business_id === focusId) || null
}

function rowClassName({ row }) {
  return focusedRowId.value === row.id ? 'is-focused-row' : ''
}

async function applyFocus() {
  focusedRowId.value = null
  const focusId = parseFocusId(route.query.focusId)
  if (!focusId) return
  const matched = resolveCashFlowRow(focusId)
  if (!matched) return
  focusedRowId.value = matched.id
  ensureVisible((item) => item.id === matched.id)
  filters.businessType = matched.business_type || filters.businessType
  filters.flowType = matched.flow_type || filters.flowType
  filters.keyword = matched.related_no || matched.record_no || filters.keyword
  await nextTick()
}

const filteredRows = computed(() => {
  const keyword = filters.keyword.trim().toLowerCase()
  return rows.value.filter((item) => {
    if (filters.flowType && item.flow_type !== filters.flowType) return false
    if (filters.businessType && item.business_type !== filters.businessType) return false
    if (!keyword) return true
    return [item.record_no, item.related_no, item.business_type, item.remark]
      .filter(Boolean)
      .some((value) => String(value).toLowerCase().includes(keyword))
  })
})
const { pagination, total, pageSizes, pagedItems: pagedRows, ensureVisible } = useLocalPagination(filteredRows)

const summary = computed(() => {
  const incoming = filteredRows.value
    .filter((item) => item.flow_type === 'incoming')
    .reduce((total, item) => total + Number(item.amount || 0), 0)
  const outgoing = filteredRows.value
    .filter((item) => item.flow_type === 'outgoing')
    .reduce((total, item) => total + Number(item.amount || 0), 0)
  return {
    incoming,
    outgoing,
    net: incoming - outgoing
  }
})

async function load() {
  const params = new URLSearchParams()
  if (Array.isArray(filters.dateRange) && filters.dateRange.length === 2) {
    if (filters.dateRange[0]) params.set('date_from', filters.dateRange[0])
    if (filters.dateRange[1]) params.set('date_to', filters.dateRange[1])
  }
  const query = params.toString()
  rows.value = await get(`/api/finance/cash-flows${query ? `?${query}` : ''}`)
  await applyFocus()
}

function exportTable() {
  exportRowsToCsv('cash_flows', filteredRows.value)
}

onMounted(load)

watch(
  () => [route.query.focusId, route.query.focusType],
  async () => {
    if (rows.value.length) {
      await applyFocus()
    }
  }
)
</script>

<style scoped>
.filters-row { margin-bottom: 16px; }
.summary-row { margin-bottom: 16px; }
.page-stack { display: flex; flex-direction: column; gap: 16px; }
.page-stack :deep(.is-focused-row) > td { background: #fff8e1 !important; }
</style>
