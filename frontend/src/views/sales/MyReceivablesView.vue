<template>
  <PageSection title="我的应收款" description="仅展示本人负责、已转为应收的客户订单；本页只读，回款由财务登记。">
    <template #actions><el-button @click="load">刷新</el-button></template>
    <el-table :data="rows" stripe v-loading="loading">
      <el-table-column prop="order_no" label="客户订单" min-width="160" />
      <el-table-column prop="customer_name" label="客户" min-width="180" show-overflow-tooltip />
      <el-table-column label="订单金额" min-width="130"><template #default="scope">{{ formatMoney(scope.row.order_amount) }}</template></el-table-column>
      <el-table-column label="应收金额" min-width="130"><template #default="scope">{{ formatMoney(scope.row.receivable_amount) }}</template></el-table-column>
      <el-table-column label="已收金额" min-width="130"><template #default="scope">{{ formatMoney(scope.row.received_amount) }}</template></el-table-column>
      <el-table-column label="剩余应收" min-width="130"><template #default="scope">{{ formatMoney(scope.row.remaining_amount) }}</template></el-table-column>
      <el-table-column label="状态" width="100"><template #default="scope"><el-tag :type="scope.row.status === 'completed' ? 'success' : 'warning'">{{ scope.row.status === 'completed' ? '已完成' : '待回款' }}</el-tag></template></el-table-column>
      <el-table-column label="订单时间" min-width="175"><template #default="scope">{{ formatDateTime(scope.row.created_at) }}</template></el-table-column>
    </el-table>
    <AppPagination v-model:page="page" v-model:page-size="pageSize" :total="total" :page-sizes="[20, 50, 100]" @change="handlePageChange" />
  </PageSection>
</template>

<script setup>
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/message/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/tag/style/css'
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

async function load() {
  loading.value = true
  try {
    const data = await get(`/api/finance/my-receivables?page=${page.value}&page_size=${pageSize.value}`)
    rows.value = data.rows || []
    total.value = data.total || 0
  } catch (error) {
    ElMessage.error(error.message || '加载应收款失败')
  } finally {
    loading.value = false
  }
}

function handlePageChange(next) {
  page.value = next.page
  pageSize.value = next.pageSize
  load()
}

onMounted(load)
</script>
