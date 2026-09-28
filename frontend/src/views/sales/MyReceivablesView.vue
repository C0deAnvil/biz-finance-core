<template>
  <PageSection title="我的应收款" description="仅展示本人负责、已转为应收的客户订单；本页只读，回款由财务登记。">
    <template #actions><el-button @click="load">刷新</el-button></template>
    <div class="filters">
      <el-input v-model="keyword" clearable placeholder="搜索客户订单号或客户名称" class="filter-keyword" @keyup.enter="search" />
      <el-select v-model="status" class="filter-status" @change="search">
        <el-option label="全部状态" value="all" />
        <el-option label="待回款" value="pending" />
        <el-option label="已完成" value="completed" />
      </el-select>
      <el-date-picker v-model="dateRange" type="daterange" value-format="YYYY-MM-DD" start-placeholder="订单开始日期" end-placeholder="订单结束日期" />
      <el-button type="primary" @click="search">查询</el-button>
      <el-button @click="resetFilters">重置</el-button>
    </div>
    <el-alert v-if="errorMessage" :title="errorMessage" type="error" :closable="false" class="load-error" />
    <p class="result-count">共 {{ total }} 条本人已转应收订单</p>
    <el-table :data="rows" stripe v-loading="loading">
      <el-table-column prop="order_no" label="客户订单" min-width="160" />
      <el-table-column prop="customer_name" label="客户" min-width="180" show-overflow-tooltip />
      <el-table-column label="订单金额" min-width="130"><template #default="scope">{{ formatMoney(scope.row.order_amount) }}</template></el-table-column>
      <el-table-column label="应收金额" min-width="130"><template #default="scope">{{ formatMoney(scope.row.receivable_amount) }}</template></el-table-column>
      <el-table-column label="已收金额" min-width="130"><template #default="scope">{{ formatMoney(scope.row.received_amount) }}</template></el-table-column>
      <el-table-column label="剩余应收" min-width="130"><template #default="scope">{{ formatMoney(scope.row.remaining_amount) }}</template></el-table-column>
      <el-table-column label="状态" width="100"><template #default="scope"><el-tag :type="scope.row.status === 'completed' ? 'success' : 'warning'">{{ scope.row.status === 'completed' ? '已完成' : '待回款' }}</el-tag></template></el-table-column>
      <el-table-column label="转应收时间" min-width="175"><template #default="scope">{{ formatDateTime(scope.row.receivable_created_at) }}</template></el-table-column>
      <el-table-column label="订单时间" min-width="175"><template #default="scope">{{ formatDateTime(scope.row.created_at) }}</template></el-table-column>
      <template #empty><el-empty description="暂无本人已转为应收的客户订单；请确认财务审批已完成转应收" /></template>
    </el-table>
    <AppPagination v-model:page="page" v-model:page-size="pageSize" :total="total" :page-sizes="[20, 50, 100]" @change="handlePageChange" />
  </PageSection>
</template>

<script setup>
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/message/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/tag/style/css'
import 'element-plus/es/components/alert/style/css'
import 'element-plus/es/components/date-picker/style/css'
import 'element-plus/es/components/empty/style/css'
import 'element-plus/es/components/input/style/css'
import 'element-plus/es/components/select/style/css'
import { ElMessage } from 'element-plus/es/components/message/index.mjs'
import { onMounted, ref } from 'vue'
import { get } from '../../api/client'
import AppPagination from '../../components/AppPagination.vue'
import PageSection from '../../components/PageSection.vue'
import { formatDateTime, formatMoney } from '../../utils/format'

const rows = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const loading = ref(false)
const keyword = ref('')
const status = ref('all')
const dateRange = ref([])
const errorMessage = ref('')

async function load() {
  loading.value = true
  errorMessage.value = ''
  try {
    const params = new URLSearchParams({ page: String(page.value), page_size: String(pageSize.value), status: status.value })
    if (keyword.value.trim()) params.set('keyword', keyword.value.trim())
    if (dateRange.value?.[0]) params.set('date_from', dateRange.value[0])
    if (dateRange.value?.[1]) params.set('date_to', dateRange.value[1])
    const data = await get(`/api/finance/my-receivables?${params.toString()}`)
    rows.value = data.rows || []
    total.value = data.total || 0
  } catch (error) {
    rows.value = []
    total.value = 0
    errorMessage.value = error.message || '加载应收款失败'
    ElMessage.error(errorMessage.value)
  } finally {
    loading.value = false
  }
}

function search() {
  page.value = 1
  load()
}

function resetFilters() {
  keyword.value = ''
  status.value = 'all'
  dateRange.value = []
  search()
}

function handlePageChange(next) {
  page.value = next.page
  pageSize.value = next.pageSize
  load()
}

onMounted(load)
</script>

<style scoped>
.filters { display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 12px; align-items: center; }
.filter-keyword { width: 260px; }
.filter-status { width: 125px; }
.load-error { margin-bottom: 12px; }
.result-count { margin: 0 0 10px; color: #606266; font-size: 13px; }
</style>
