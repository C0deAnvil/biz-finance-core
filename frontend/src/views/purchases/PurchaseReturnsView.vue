<template>
  <PageSection :title="texts.title" :description="texts.description">
    <template #actions>
      <el-button @click="load">{{ texts.actions.refresh }}</el-button>
      <el-button @click="exportTable">{{ texts.actions.export }}</el-button>
      <el-button v-if="canCreate" type="primary" @click="openCreate">{{ texts.actions.create }}</el-button>
    </template>

    <el-tabs v-model="activeStatusTab" class="status-tabs">
      <el-tab-pane :label="`退货处理中 (${processingRows.length})`" name="processing" />
      <el-tab-pane :label="`已退货 (${completedRows.length})`" name="completed" />
    </el-tabs>

    <el-table :data="pagedRows" stripe :row-class-name="rowClassName">
      <el-table-column prop="return_no" :label="texts.columns.returnNo" width="160" />
      <el-table-column prop="purchase_order_no" :label="texts.columns.orderNo" width="160" />
      <el-table-column prop="return_type_label" label="类型" width="100" />
      <el-table-column prop="warehouse_name" :label="texts.columns.warehouse" width="160" />
      <el-table-column prop="item_count" :label="texts.columns.itemCount" width="100" />
      <el-table-column prop="total_return_quantity" :label="texts.columns.totalQty" width="120">
        <template #default="scope">{{ formatNumber(scope.row.total_return_quantity) }}</template>
      </el-table-column>
      <el-table-column prop="status" :label="texts.columns.status" width="120">
        <template #default="scope"><el-tag :type="statusType(scope.row.status)">{{ scope.row.status_label || statusLabel(scope.row.status) }}</el-tag></template>
      </el-table-column>
      <el-table-column label="复检" width="130">
        <template #default="scope">
          <el-tag :type="scope.row.requires_reinspection ? statusType(scope.row.reinspection_status) : 'info'">
            {{ scope.row.requires_reinspection ? (scope.row.reinspection_status_label || statusLabel(scope.row.reinspection_status)) : '无需复检' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="refund_amount" :label="texts.columns.refundAmount" width="130">
        <template #default="scope">{{ formatNumber(scope.row.refund_amount) }}</template>
      </el-table-column>
      <el-table-column prop="refunded_amount" label="已退款" width="130">
        <template #default="scope">{{ formatNumber(scope.row.refunded_amount) }}</template>
      </el-table-column>
      <el-table-column prop="reason" :label="texts.columns.reason" min-width="220" show-overflow-tooltip />
      <el-table-column prop="created_at" :label="texts.columns.createdAt" min-width="180">
        <template #default="scope">{{ formatDateTime(scope.row.created_at) }}</template>
      </el-table-column>
      <el-table-column :label="texts.columns.actions" width="100" fixed="right">
        <template #default="scope">
          <el-button size="small" text type="primary" @click="openDetail(scope.row)">{{ texts.actions.detail }}</el-button>
        </template>
      </el-table-column>
    </el-table>
    <AppPagination
      v-model:page="pagination.page"
      v-model:page-size="pagination.pageSize"
      :total="total"
      :page-sizes="pageSizes"
    />

    <el-dialog v-model="dialogVisible" :title="texts.createDialog.title" width="980px">
      <el-form :model="form" label-width="100px">
        <el-row :gutter="12">
          <el-col :span="12">
            <el-form-item :label="texts.createDialog.order">
              <el-select v-model="form.purchase_order_id" filterable style="width: 100%" @change="handleOrderChange">
                <el-option v-for="item in purchaseOrders" :key="item.id" :label="`${item.order_no} / ${item.supplier_name}`" :value="item.id" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item :label="texts.createDialog.warehouse">
              <el-select v-model="form.warehouse_id" filterable style="width: 100%">
                <el-option v-for="item in warehouses" :key="item.id" :label="item.name" :value="item.id" />
              </el-select>
            </el-form-item>
          </el-col>
        </el-row>
        <el-form-item label="类型">
          <el-select v-model="form.return_type" style="width: 100%">
            <el-option label="退货" value="return" />
            <el-option label="换货" value="exchange" />
          </el-select>
        </el-form-item>
        <el-form-item :label="texts.createDialog.reason">
          <el-input v-model="form.reason" type="textarea" :rows="3" />
        </el-form-item>
        <el-form-item :label="texts.createDialog.refundAmount">
          <el-input-number v-model="form.refund_amount" :min="0" :precision="2" style="width: 100%" />
        </el-form-item>
        <el-form-item label="是否复检">
          <el-switch v-model="form.requires_reinspection" active-text="需要复检" inactive-text="无需复检" />
        </el-form-item>

        <el-alert :title="texts.createDialog.tip" type="warning" :closable="false" show-icon class="mb-12" />
        <el-table :data="items" border>
          <el-table-column :label="texts.createDialog.itemName" min-width="220">
            <template #default="scope">{{ scope.row.product_name || scope.row.material_name || '-' }}</template>
          </el-table-column>
          <el-table-column prop="quantity" :label="texts.createDialog.orderQty" width="120">
            <template #default="scope">{{ formatNumber(scope.row.quantity) }}</template>
          </el-table-column>
          <el-table-column prop="received_quantity" :label="texts.createDialog.receivedQty" width="120">
            <template #default="scope">{{ formatNumber(scope.row.received_quantity) }}</template>
          </el-table-column>
          <el-table-column prop="returned_quantity" :label="texts.createDialog.returnedQty" width="120">
            <template #default="scope">{{ formatNumber(scope.row.returned_quantity) }}</template>
          </el-table-column>
          <el-table-column prop="available_quantity" :label="texts.createDialog.availableQty" width="120">
            <template #default="scope">{{ formatNumber(scope.row.available_quantity) }}</template>
          </el-table-column>
          <el-table-column :label="texts.createDialog.currentQty" width="160">
            <template #default="scope">
              <el-input-number v-model="scope.row.return_quantity" :min="0" :max="scope.row.available_quantity" :precision="2" style="width: 100%" />
            </template>
          </el-table-column>
        </el-table>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">{{ texts.actions.cancel }}</el-button>
        <el-button type="primary" @click="submit">{{ texts.actions.submit }}</el-button>
      </template>
    </el-dialog>

    <el-drawer v-model="detailVisible" :title="texts.detail.title" size="52%">
      <el-descriptions v-if="detail" :column="2" border>
        <el-descriptions-item :label="texts.detail.returnNo">{{ detail.return_no }}</el-descriptions-item>
        <el-descriptions-item :label="texts.detail.orderNo">{{ detail.purchase_order_no }}</el-descriptions-item>
        <el-descriptions-item label="类型">{{ detail.return_type_label || returnTypeLabel(detail.return_type) }}</el-descriptions-item>
        <el-descriptions-item :label="texts.detail.warehouse">{{ detail.warehouse_name }}</el-descriptions-item>
        <el-descriptions-item :label="texts.detail.status">{{ detail.status_label || statusLabel(detail.status) }}</el-descriptions-item>
        <el-descriptions-item :label="texts.detail.refundAmount">{{ formatNumber(detail.refund_amount) }}</el-descriptions-item>
        <el-descriptions-item label="复检状态">{{ detail.requires_reinspection ? (detail.reinspection_status_label || statusLabel(detail.reinspection_status)) : '无需复检' }}</el-descriptions-item>
        <el-descriptions-item label="退货人">{{ detail.returned_by_name || '-' }}</el-descriptions-item>
        <el-descriptions-item :label="texts.detail.createdAt">{{ formatDateTime(detail.created_at) }}</el-descriptions-item>
        <el-descriptions-item :label="texts.detail.reason" :span="2">{{ detail.reason || '-' }}</el-descriptions-item>
      </el-descriptions>
      <div class="section-title mt-16">库存出库流水</div>
      <el-table :data="detail?.items || []" stripe class="mt-16">
        <el-table-column prop="transaction_no" label="流水号" min-width="150" />
        <el-table-column prop="item_code" :label="texts.detail.itemCode" width="130" />
        <el-table-column prop="item_name" :label="texts.detail.itemName" min-width="180" />
        <el-table-column prop="item_type" :label="texts.detail.itemType" width="100" />
        <el-table-column prop="quantity" :label="texts.detail.quantity" width="120">
          <template #default="scope">{{ formatNumber(scope.row.quantity) }}</template>
        </el-table-column>
        <el-table-column prop="unit_cost" :label="texts.detail.unitCost" width="120">
          <template #default="scope">{{ formatNumber(scope.row.unit_cost) }}</template>
        </el-table-column>
        <el-table-column prop="total_cost" :label="texts.detail.totalCost" width="120">
          <template #default="scope">{{ formatNumber(scope.row.total_cost) }}</template>
        </el-table-column>
        <el-table-column prop="batch_no" label="批次号" min-width="160" />
        <el-table-column prop="operator_name" label="操作人" min-width="120" />
        <el-table-column prop="created_at" label="时间" min-width="170">
          <template #default="scope">{{ formatDateTime(scope.row.created_at) }}</template>
        </el-table-column>
      </el-table>
      <div class="section-title mt-16">退款记录</div>
      <el-table :data="detail?.refund_records || []" stripe class="mt-16">
        <el-table-column prop="amount" label="退款金额" width="120">
          <template #default="scope">{{ formatNumber(scope.row.amount) }}</template>
        </el-table-column>
        <el-table-column prop="processed_by_name" label="处理人" min-width="120" />
        <el-table-column prop="remark" label="备注" min-width="220" show-overflow-tooltip />
        <el-table-column prop="refunded_at" label="退款时间" min-width="170">
          <template #default="scope">{{ formatDateTime(scope.row.refunded_at) }}</template>
        </el-table-column>
      </el-table>
      <AttachmentUploader v-if="detail" class="mt-16" business-type="purchase_return" :business-id="detail.id" :readonly="!canUploadAttachments" />
    </el-drawer>
  </PageSection>
</template>

<script setup>
import 'element-plus/es/components/message/style/css'
import 'element-plus/es/components/alert/style/css'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/col/style/css'
import 'element-plus/es/components/descriptions/style/css'
import 'element-plus/es/components/dialog/style/css'
import 'element-plus/es/components/drawer/style/css'
import 'element-plus/es/components/form/style/css'
import 'element-plus/es/components/input-number/style/css'
import 'element-plus/es/components/input/style/css'
import 'element-plus/es/components/row/style/css'
import 'element-plus/es/components/select/style/css'
import 'element-plus/es/components/switch/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/tabs/style/css'
import 'element-plus/es/components/tag/style/css'

import { ElMessage } from 'element-plus/es/components/message/index.mjs'

import ElAlert from 'element-plus/es/components/alert/index.mjs'
import ElButton from 'element-plus/es/components/button/index.mjs'
import ElCol from 'element-plus/es/components/col/index.mjs'
import ElDescriptions, { ElDescriptionsItem } from 'element-plus/es/components/descriptions/index.mjs'
import ElDialog from 'element-plus/es/components/dialog/index.mjs'
import ElDrawer from 'element-plus/es/components/drawer/index.mjs'
import ElForm, { ElFormItem } from 'element-plus/es/components/form/index.mjs'
import ElInputNumber from 'element-plus/es/components/input-number/index.mjs'
import ElInput from 'element-plus/es/components/input/index.mjs'
import ElRow from 'element-plus/es/components/row/index.mjs'
import ElSelect, { ElOption } from 'element-plus/es/components/select/index.mjs'
import ElSwitch from 'element-plus/es/components/switch/index.mjs'
import ElTable, { ElTableColumn } from 'element-plus/es/components/table/index.mjs'
import ElTabs, { ElTabPane } from 'element-plus/es/components/tabs/index.mjs'
import ElTag from 'element-plus/es/components/tag/index.mjs'

import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { useRouteFocus } from '../../composables/useRouteFocus'

import { get, post } from '../../api/client'
import AttachmentUploader from '../../components/AttachmentUploader.vue'
import AppPagination from '../../components/AppPagination.vue'
import PageSection from '../../components/PageSection.vue'
import { useLocalPagination } from '../../composables/useLocalPagination'
import { usePagePermission } from '../../composables/usePagePermission'
import { useAuthStore } from '../../stores/auth'
import { exportRowsToCsv } from '../../utils/export'
import { formatDateTime, formatNumber, returnTypeLabel, statusLabel, statusType } from '../../utils/format'

const texts = {
  title: '退货处理',
  description: '登记采购退货/换货申请，提交后会同步库管执行手工出库，并由财务后续处理退款。',
  columns: {
    returnNo: '退货单号',
    orderNo: '采购订单',
    warehouse: '退货仓库',
    itemCount: '明细数',
    totalQty: '退货总量',
    status: '状态',
    refundAmount: '退款金额',
    reason: '退货原因',
    createdAt: '创建时间',
    actions: '操作'
  },
  createDialog: {
    title: '新建退货处理',
    order: '采购订单',
    warehouse: '退货仓库',
    reason: '退货原因',
    refundAmount: '退款金额',
    tip: '请按可退数量填写本次退货明细。提交后只生成库管手工出库作业，不会自动扣减库存。',
    itemName: '物料/产品',
    orderQty: '采购数量',
    receivedQty: '已入库',
    returnedQty: '已退货',
    availableQty: '可退数量',
    currentQty: '本次退货'
  },
  detail: {
    title: '退货详情',
    returnNo: '退货单号',
    orderNo: '采购订单',
    warehouse: '仓库',
    status: '状态',
    refundAmount: '退款金额',
    createdAt: '创建时间',
    reason: '原因',
    itemCode: '编码',
    itemName: '物料/产品',
    itemType: '类型',
    quantity: '数量',
    unitCost: '单成本',
    totalCost: '总成本'
  },
  actions: {
    refresh: '刷新',
    export: '导出',
    create: '新建退货',
    detail: '详情',
    cancel: '取消',
    submit: '提交'
  },
  messages: {
    created: '退货处理已提交，并已同步库管待出库作业'
  }
}

const { route, focusedId: focusedReturnId, applyFocus: applyRouteFocus } = useRouteFocus()
const auth = useAuthStore()
const pagePermission = usePagePermission()
const rows = ref([])
const activeStatusTab = ref('processing')
const detailVisible = ref(false)
const detail = ref(null)
const purchaseOrders = ref([])
const warehouses = ref([])
const items = ref([])
const dialogVisible = ref(false)
const canCreate = computed(() => ['purchase', 'inventory'].includes(auth.user?.role_code) && pagePermission.can('create'))
const canUploadAttachments = computed(() => ['purchase', 'inventory'].includes(auth.user?.role_code) && pagePermission.can('upload'))
const processingRows = computed(() => rows.value.filter((item) => !['returned', 'refunded'].includes(item.status)))
const completedRows = computed(() => rows.value.filter((item) => ['returned', 'refunded'].includes(item.status)))
const activeRows = computed(() => (activeStatusTab.value === 'completed' ? completedRows.value : processingRows.value))
const { pagination, total, pageSizes, pagedItems: pagedRows } = useLocalPagination(activeRows)
const form = reactive({
  purchase_order_id: null,
  warehouse_id: null,
  return_type: 'return',
  reason: '',
  refund_amount: 0,
  requires_reinspection: false
})

function resetForm() {
  Object.assign(form, { purchase_order_id: null, warehouse_id: null, return_type: 'return', reason: '', refund_amount: 0, requires_reinspection: false })
  items.value = []
}

function openCreate() {
  resetForm()
  dialogVisible.value = true
}

function rowClassName({ row }) {
  return focusedReturnId.value && row.id === focusedReturnId.value ? 'focused-row' : ''
}

function setActiveStatusTabByRow(row) {
  if (!row) return
  activeStatusTab.value = ['returned', 'refunded'].includes(row.status) ? 'completed' : 'processing'
}

async function openDetail(row) {
  setActiveStatusTabByRow(row)
  detail.value = await get(`/api/purchases/returns/${row.id}`)
  detailVisible.value = true
}

async function applyFocusFromRoute() {
  await applyRouteFocus(
    (focusId) => {
      const matched = rows.value.find((item) => item.id === focusId)
      if (matched) {
        setActiveStatusTabByRow(matched)
      }
      return matched
    },
    async (row) => {
      await openDetail(row)
    }
  )
}

async function load() {
  rows.value = await get('/api/purchases/returns')
  await applyFocusFromRoute()
}

async function handleOrderChange(orderId) {
  if (!orderId) {
    items.value = []
    return
  }
  const orderDetail = await get(`/api/purchases/orders/${orderId}`)
  items.value = (orderDetail.items || []).map((item) => {
    const availableQuantity = Number(item.received_quantity || 0) - Number(item.returned_quantity || 0)
    return {
      ...item,
      available_quantity: availableQuantity > 0 ? availableQuantity : 0,
      return_quantity: 0
    }
  })
}

async function submit() {
  const selectedItems = items.value
    .filter((item) => Number(item.return_quantity) > 0)
    .map((item) => ({
      purchase_order_item_id: item.id,
      returned_quantity: item.return_quantity
    }))

  await post('/api/purchases/returns', {
    purchase_order_id: form.purchase_order_id,
    warehouse_id: form.warehouse_id,
    return_type: form.return_type,
    reason: form.reason,
    refund_amount: form.refund_amount,
    requires_reinspection: form.requires_reinspection,
    items: selectedItems
  })
  ElMessage.success(texts.messages.created)
  dialogVisible.value = false
  resetForm()
  await load()
}

function exportTable() {
  exportRowsToCsv('purchase_returns', rows.value)
}

watch(
  () => route.query.focusId,
  async () => {
    await applyFocusFromRoute()
  }
)

onMounted(async () => {
  if (canCreate.value) {
    const orderData = await get('/api/purchases/orders?tab=all&page=1&page_size=100')
    const orders = Array.isArray(orderData) ? orderData : (orderData.items || [])
    purchaseOrders.value = orders.filter((item) => ['partial_received', 'fully_received'].includes(item.status))
  }
  warehouses.value = await get('/api/master-data/warehouses')
  await load()
})
</script>

<style scoped>
.status-tabs { margin-bottom: 12px; }
.mb-12 { margin-bottom: 12px; }
.mt-16 { margin-top: 16px; }
.section-title { font-size: 14px; font-weight: 600; color: #303133; }
.focused-row > td { background: #f0f9eb !important; }
</style>
