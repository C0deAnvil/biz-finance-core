<template>
  <PageSection title="标准价格表" description="维护企业出售产品的标准销售价格。销售建客户订单选择商品时，单价默认带出此处价格；如订单改价，必须填写备注并同步通知经理和董事长。">
    <template #actions>
      <el-input v-model="keyword" clearable placeholder="搜索产品编码 / 名称" class="w-260" />
      <el-button @click="load">刷新</el-button>
      <el-button @click="exportTable">导出</el-button>
    </template>

    <el-table :data="pagedRows" stripe>
      <el-table-column prop="product_code" label="产品编码" width="150" />
      <el-table-column prop="product_name" label="产品名称" min-width="180" />
      <el-table-column prop="specification" label="规格" min-width="140" />
      <el-table-column prop="unit" label="单位" width="80" />
      <el-table-column prop="standard_price" label="标准售价" width="130">
        <template #default="scope">{{ formatMoney(scope.row.standard_price) }}</template>
      </el-table-column>
      <el-table-column prop="latest_change_remark" label="最近调整说明" min-width="220" show-overflow-tooltip />
      <el-table-column prop="latest_changed_at" label="最近调整时间" min-width="170" />
      <el-table-column label="操作" width="180" fixed="right">
        <template #default="scope">
          <el-button size="small" text type="primary" @click="openHistory(scope.row)">历史</el-button>
          <el-button v-if="canEdit" size="small" text type="primary" @click="openEdit(scope.row)">编辑价格</el-button>
        </template>
      </el-table-column>
    </el-table>
    <AppPagination v-model:page="pagination.page" v-model:page-size="pagination.pageSize" :total="total" :page-sizes="pageSizes" />

    <el-dialog v-model="editVisible" title="编辑标准售价" width="560px">
      <el-form :model="editForm" label-width="110px">
        <el-form-item label="产品">
          <el-input :model-value="activeRow ? `${activeRow.product_code} / ${activeRow.product_name}` : ''" disabled />
        </el-form-item>
        <el-form-item label="原标准售价">
          <el-input :model-value="formatMoney(activeRow?.standard_price)" disabled />
        </el-form-item>
        <el-form-item label="新标准售价">
          <el-input-number v-model="editForm.price" :min="0.01" :precision="2" style="width: 100%" />
        </el-form-item>
        <el-form-item label="调整原因">
          <el-input v-model="editForm.remark" type="textarea" :rows="3" placeholder="请填写调价原因，例如市场价格变化、客户政策调整、成本变化等" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" @click="submitEdit">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="historyVisible" :title="historyTitle" width="760px">
      <el-table :data="historyRows" stripe>
        <el-table-column prop="old_price" label="原价格" width="120">
          <template #default="scope">{{ formatMoney(scope.row.old_price) }}</template>
        </el-table-column>
        <el-table-column prop="new_price" label="新价格" width="120">
          <template #default="scope">{{ formatMoney(scope.row.new_price) }}</template>
        </el-table-column>
        <el-table-column prop="remark" label="调整原因" min-width="220" show-overflow-tooltip />
        <el-table-column prop="changed_by_name" label="调整人" width="120" />
        <el-table-column prop="created_at" label="调整时间" min-width="170" />
      </el-table>
      <el-empty v-if="!historyRows.length" description="暂无标准价格调整记录" />
      <template #footer>
        <el-button @click="historyVisible = false">关闭</el-button>
      </template>
    </el-dialog>
  </PageSection>
</template>

<script setup>
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/dialog/style/css'
import 'element-plus/es/components/empty/style/css'
import 'element-plus/es/components/form/style/css'
import 'element-plus/es/components/input/style/css'
import 'element-plus/es/components/input-number/style/css'
import 'element-plus/es/components/message/style/css'
import 'element-plus/es/components/table/style/css'

import { ElMessage } from 'element-plus/es/components/message/index.mjs'

import ElButton from 'element-plus/es/components/button/index.mjs'
import ElDialog from 'element-plus/es/components/dialog/index.mjs'
import ElEmpty from 'element-plus/es/components/empty/index.mjs'
import ElForm, { ElFormItem } from 'element-plus/es/components/form/index.mjs'
import ElInput from 'element-plus/es/components/input/index.mjs'
import ElInputNumber from 'element-plus/es/components/input-number/index.mjs'
import ElTable, { ElTableColumn } from 'element-plus/es/components/table/index.mjs'

import { computed, onMounted, reactive, ref } from 'vue'

import { get, put } from '../../api/client'
import AppPagination from '../../components/AppPagination.vue'
import PageSection from '../../components/PageSection.vue'
import { useLocalPagination } from '../../composables/useLocalPagination'
import { useAuthStore } from '../../stores/auth'
import { exportRowsToCsv } from '../../utils/export'

const auth = useAuthStore()
const rows = ref([])
const keyword = ref('')
const editVisible = ref(false)
const historyVisible = ref(false)
const activeRow = ref(null)
const historyRows = ref([])
const historyTitle = ref('标准价格历史')
const editForm = reactive({ price: 0, remark: '' })
const canEdit = computed(() => ['sales', 'manager', 'chairman', 'system_admin', 'master_data_admin'].includes(auth.user?.role_code))

const filteredRows = computed(() => {
  const value = keyword.value.trim().toLowerCase()
  if (!value) return rows.value
  return rows.value.filter((item) => [item.product_code, item.product_name, item.specification].filter(Boolean).some((text) => String(text).toLowerCase().includes(value)))
})
const { pagination, total, pageSizes, pagedItems: pagedRows } = useLocalPagination(filteredRows)

function formatMoney(value) {
  const number = Number(value || 0)
  return Number.isFinite(number) ? number.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '-'
}

async function load() {
  rows.value = await get('/api/master-data/standard-prices')
}

function openEdit(row) {
  activeRow.value = row
  editForm.price = Number(row.standard_price || 0)
  editForm.remark = ''
  editVisible.value = true
}

async function submitEdit() {
  if (!activeRow.value) return
  if (!String(editForm.remark || '').trim() || String(editForm.remark || '').trim().length < 2) {
    ElMessage.warning('请填写标准价格调整原因')
    return
  }
  await put(`/api/master-data/standard-prices/${activeRow.value.product_id}`, {
    price: editForm.price,
    remark: editForm.remark.trim()
  })
  ElMessage.success('标准价格已更新')
  editVisible.value = false
  await load()
}

async function openHistory(row) {
  const result = await get(`/api/master-data/standard-prices/${row.product_id}/history`)
  historyRows.value = result.history || []
  historyTitle.value = `${row.product_code} / ${row.product_name} 标准价格历史`
  historyVisible.value = true
}

function exportTable() {
  exportRowsToCsv('standard_prices', filteredRows.value)
}

onMounted(load)
</script>

<style scoped>
.w-260 { width: 260px; }
</style>
