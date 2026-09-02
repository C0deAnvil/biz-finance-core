<template>
  <div class="page-stack">
    <el-row :gutter="12" class="summary-row">
      <el-col :span="8"><el-card shadow="hover"><div class="summary-label">任务总数</div><div class="summary-value">{{ rows.length }}</div></el-card></el-col>
      <el-col :span="8"><el-card shadow="hover"><div class="summary-label">已完成</div><div class="summary-value text-success">{{ completedCount }}</div></el-card></el-col>
      <el-col :span="8"><el-card shadow="hover"><div class="summary-label">存在差异</div><div class="summary-value text-warning">{{ differenceCount }}</div></el-card></el-col>
    </el-row>

    <PageSection title="盘点任务" description="盘点创建时直接录入明细并完成差异入账，经理和董事长可查看结果。">
      <template #actions>
        <el-button @click="load">刷新</el-button>
        <el-button v-if="canCreate" type="primary" @click="openCreate">新建盘点</el-button>
      </template>

      <el-table :data="pagedRows" stripe :row-class-name="rowClassName">
        <el-table-column prop="task_no" label="任务单号" width="150" />
        <el-table-column prop="warehouse_name" label="仓库" min-width="160" />
        <el-table-column prop="status_label" label="状态" width="120">
          <template #default="scope">
            <el-tag :type="statusType(scope.row.status)">{{ scope.row.status_label }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="item_count" label="明细数" width="100" />
        <el-table-column prop="difference_count" label="差异项" width="100" />
        <el-table-column prop="created_at" label="创建时间" min-width="180">
          <template #default="scope">{{ formatDateTime(scope.row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="120" fixed="right">
          <template #default="scope">
            <el-button size="small" @click="openDetail(scope.row)">详情</el-button>
          </template>
        </el-table-column>
      </el-table>
      <AppPagination
        v-model:page="pagination.page"
        v-model:page-size="pagination.pageSize"
        :total="total"
        :page-sizes="pageSizes"
      />
    </PageSection>

    <el-dialog v-model="dialogVisible" title="新建盘点任务" width="900px">
      <el-form label-width="100px">
        <el-form-item label="盘点仓库">
          <el-select v-model="createForm.warehouse_id" style="width: 100%" @change="loadTemplate">
            <el-option v-for="item in warehouses" :key="item.id" :label="item.name" :value="item.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="差异说明">
          <el-input v-model="createForm.difference_note" type="textarea" :rows="2" placeholder="可填写本次盘点说明" />
        </el-form-item>
      </el-form>

      <el-table :data="createForm.items" stripe max-height="420">
        <el-table-column prop="item_code" label="编码" width="140" />
        <el-table-column prop="item_name" label="产品/原料" min-width="180" />
        <el-table-column prop="system_quantity" label="账面数量" width="120">
          <template #default="scope">{{ formatNumber(scope.row.system_quantity) }}</template>
        </el-table-column>
        <el-table-column label="实盘数量" width="140">
          <template #default="scope">
            <el-input-number v-model="scope.row.counted_quantity" :min="0" :precision="2" style="width: 100%" />
          </template>
        </el-table-column>
        <el-table-column label="差异数量" width="120">
          <template #default="scope">{{ formatNumber(Number(scope.row.counted_quantity || 0) - Number(scope.row.system_quantity || 0)) }}</template>
        </el-table-column>
        <el-table-column label="差异原因" min-width="180">
          <template #default="scope">
            <el-input v-model="scope.row.reason" placeholder="可填写差异原因" />
          </template>
        </el-table-column>
      </el-table>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="submitCreate">提交并入账</el-button>
      </template>
    </el-dialog>

    <el-drawer v-model="detailVisible" size="62%" title="盘点任务详情">
      <el-descriptions v-if="detail" :column="2" border>
        <el-descriptions-item label="任务单号">{{ detail.task_no }}</el-descriptions-item>
        <el-descriptions-item label="仓库">{{ detail.warehouse_name }}</el-descriptions-item>
        <el-descriptions-item label="状态">{{ detail.status_label || detail.status }}</el-descriptions-item>
        <el-descriptions-item label="完成时间">{{ formatDateTime(detail.approved_at) }}</el-descriptions-item>
        <el-descriptions-item label="差异说明" :span="2">{{ detail.difference_note || '-' }}</el-descriptions-item>
      </el-descriptions>
      <el-table :data="detail?.items || []" stripe class="mt-16">
        <el-table-column prop="item_code" label="编码" width="140" />
        <el-table-column prop="item_name" label="产品/原料" min-width="180" />
        <el-table-column prop="system_quantity" label="账面数量" width="120">
          <template #default="scope">{{ formatNumber(scope.row.system_quantity) }}</template>
        </el-table-column>
        <el-table-column prop="counted_quantity" label="实盘数量" width="120">
          <template #default="scope">{{ formatNumber(scope.row.counted_quantity) }}</template>
        </el-table-column>
        <el-table-column prop="reason" label="差异原因" min-width="180" show-overflow-tooltip />
      </el-table>
    </el-drawer>
  </div>
</template>

<script setup>
import 'element-plus/es/components/message/style/css'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/card/style/css'
import 'element-plus/es/components/col/style/css'
import 'element-plus/es/components/descriptions/style/css'
import 'element-plus/es/components/dialog/style/css'
import 'element-plus/es/components/drawer/style/css'
import 'element-plus/es/components/form/style/css'
import 'element-plus/es/components/input-number/style/css'
import 'element-plus/es/components/input/style/css'
import 'element-plus/es/components/row/style/css'
import 'element-plus/es/components/select/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/tag/style/css'

import { ElMessage } from 'element-plus/es/components/message/index.mjs'

import ElButton from 'element-plus/es/components/button/index.mjs'
import ElCard from 'element-plus/es/components/card/index.mjs'
import ElCol from 'element-plus/es/components/col/index.mjs'
import ElDescriptions, { ElDescriptionsItem } from 'element-plus/es/components/descriptions/index.mjs'
import ElDialog from 'element-plus/es/components/dialog/index.mjs'
import ElDrawer from 'element-plus/es/components/drawer/index.mjs'
import ElForm, { ElFormItem } from 'element-plus/es/components/form/index.mjs'
import ElInputNumber from 'element-plus/es/components/input-number/index.mjs'
import ElInput from 'element-plus/es/components/input/index.mjs'
import ElRow from 'element-plus/es/components/row/index.mjs'
import ElSelect, { ElOption } from 'element-plus/es/components/select/index.mjs'
import ElTable, { ElTableColumn } from 'element-plus/es/components/table/index.mjs'
import ElTag from 'element-plus/es/components/tag/index.mjs'

import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRouteFocus } from '../../composables/useRouteFocus'

import { get, post } from '../../api/client'
import AppPagination from '../../components/AppPagination.vue'
import PageSection from '../../components/PageSection.vue'
import { useLocalPagination } from '../../composables/useLocalPagination'
import { useAuthStore } from '../../stores/auth'
import { formatDateTime, formatNumber, statusType } from '../../utils/format'

const auth = useAuthStore()
const { route, focusedId: focusedTaskId, applyFocus: applyRouteFocus } = useRouteFocus()
const rows = ref([])
const warehouses = ref([])
const dialogVisible = ref(false)
const detailVisible = ref(false)
const detail = ref(null)
const { pagination, total, pageSizes, pagedItems: pagedRows } = useLocalPagination(rows)

const createForm = reactive({
  warehouse_id: null,
  difference_note: '',
  items: []
})

const canCreate = computed(() => auth.isRole('inventory'))
const completedCount = computed(() => rows.value.filter((item) => item.status === 'completed').length)
const differenceCount = computed(() => rows.value.reduce((sum, item) => sum + Number(item.difference_count || 0), 0))

function rowClassName({ row }) {
  return focusedTaskId.value === row.id ? 'is-focused-row' : ''
}

async function applyFocus() {
  await applyRouteFocus(
    (focusId) => rows.value.find((item) => item.id === focusId),
    async (matchedRow) => {
      await loadDetail(matchedRow.id)
      detailVisible.value = true
    }
  )
}

async function load() {
  rows.value = await get('/api/inventory/stock-takes')
  await applyFocus()
}

function resetCreateForm() {
  createForm.warehouse_id = null
  createForm.difference_note = ''
  createForm.items = []
}

function openCreate() {
  resetCreateForm()
  dialogVisible.value = true
}

async function loadTemplate() {
  if (!createForm.warehouse_id) {
    createForm.items = []
    return
  }
  const template = await get(`/api/inventory/stock-takes/template?warehouse_id=${createForm.warehouse_id}`)
  createForm.items = (template.items || []).map((item) => ({ ...item }))
}

async function submitCreate() {
  if (!createForm.warehouse_id) {
    ElMessage.warning('请先选择仓库')
    return
  }
  await post('/api/inventory/stock-takes', {
    warehouse_id: createForm.warehouse_id,
    difference_note: createForm.difference_note,
    items: createForm.items.map((item) => ({
      inventory_id: item.inventory_id,
      counted_quantity: item.counted_quantity,
      reason: item.reason
    }))
  })
  ElMessage.success('盘点任务已完成并入账')
  dialogVisible.value = false
  resetCreateForm()
  await load()
}

async function loadDetail(id) {
  detail.value = await get(`/api/inventory/stock-takes/${id}`)
}

async function openDetail(row) {
  await loadDetail(row.id)
  detailVisible.value = true
}

onMounted(async () => {
  warehouses.value = await get('/api/master-data/warehouses')
  await load()
})

watch(() => route.query.focusId, async () => {
  if (rows.value.length) {
    await applyFocus()
  }
})
</script>

<style scoped>
.page-stack { display: flex; flex-direction: column; gap: 16px; }
.summary-row { margin-bottom: 0; }
.summary-label { color: #909399; margin-bottom: 8px; }
.summary-value { font-size: 28px; font-weight: 700; color: #0f5c90; }
.text-warning { color: #e6a23c; }
.text-success { color: #67c23a; }
.mt-16 { margin-top: 16px; }
.page-stack :deep(.is-focused-row) > td { background: #fff8e1 !important; }
</style>
