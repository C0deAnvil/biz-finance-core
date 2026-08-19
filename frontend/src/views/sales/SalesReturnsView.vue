<template>
  <PageSection title="销售退换货" description="销售提交退换货申请后，经理/董事长审核并回库，财务再登记退款。">
    <template #actions>
      <el-button @click="load">刷新</el-button>
      <el-button @click="exportTable">导出</el-button>
      <el-button v-if="canCreate" type="primary" @click="openCreate">新建退换货</el-button>
    </template>

    <el-table :data="pagedRows" stripe :row-class-name="rowClassName">
      <el-table-column prop="return_no" label="退换货单号" width="160" />
      <el-table-column prop="sales_order_no" label="销售订单号" width="160" />
      <el-table-column prop="return_type_label" label="类型" width="100" />
      <el-table-column prop="reason" label="申请原因" min-width="220" show-overflow-tooltip />
      <el-table-column label="状态" width="130">
        <template #default="scope">
          <el-tag :type="statusType(scope.row.status)">{{ scope.row.status_label || statusLabel(scope.row.status) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="复检" width="130">
        <template #default="scope">
          <el-tag :type="scope.row.requires_reinspection ? statusType(scope.row.reinspection_status) : 'info'">
            {{ scope.row.requires_reinspection ? (scope.row.reinspection_status_label || statusLabel(scope.row.reinspection_status)) : '无需复检' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="refund_amount" label="退款金额" width="140">
        <template #default="scope">{{ formatNumber(scope.row.refund_amount) }}</template>
      </el-table-column>
      <el-table-column prop="refunded_amount" label="已退款" width="140">
        <template #default="scope">{{ formatNumber(scope.row.refunded_amount) }}</template>
      </el-table-column>
      <el-table-column prop="review_note" label="审批备注" min-width="180" show-overflow-tooltip />
      <el-table-column prop="created_at" label="申请时间" min-width="170">
        <template #default="scope">{{ formatDateTime(scope.row.created_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="280" fixed="right">
        <template #default="scope">
          <el-button size="small" text type="primary" @click="openDetail(scope.row)">详情</el-button>
          <el-button v-if="canApprove(scope.row)" size="small" text type="success" @click="openApproval(scope.row, 'approved')">通过</el-button>
          <el-button v-if="canApprove(scope.row)" size="small" text type="danger" @click="openApproval(scope.row, 'rejected')">驳回</el-button>
          <el-button v-if="canRefund(scope.row)" size="small" text type="warning" @click="refund(scope.row)">登记退款</el-button>
        </template>
      </el-table-column>
    </el-table>
    <AppPagination
      v-model:page="pagination.page"
      v-model:page-size="pagination.pageSize"
      :total="total"
      :page-sizes="pageSizes"
    />

    <el-dialog v-model="dialogVisible" title="新建退换货申请" width="980px">
      <el-form :model="form" label-width="100px">
        <el-form-item label="销售订单">
          <el-select v-model="form.sales_order_id" filterable style="width: 100%" @change="handleOrderChange">
            <el-option v-for="item in salesOrders" :key="item.id" :label="`${item.order_no} / ${item.customer_name}`" :value="item.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="申请原因">
          <el-input v-model="form.reason" type="textarea" :rows="3" placeholder="请输入退货、换货或退款原因" />
        </el-form-item>
        <el-form-item label="类型">
          <el-select v-model="form.return_type" style="width: 100%">
            <el-option label="退货" value="return" />
            <el-option label="换货" value="exchange" />
          </el-select>
        </el-form-item>
        <el-form-item label="退款金额">
          <el-input-number v-model="form.refund_amount" :min="0" :precision="2" style="width: 100%" />
        </el-form-item>
        <el-form-item label="是否复检">
          <el-switch v-model="form.requires_reinspection" active-text="需要复检" inactive-text="无需复检" />
        </el-form-item>
        <el-alert title="请填写本次需要回库的产品数量；未填写数量的明细不会提交。" type="info" show-icon :closable="false" class="mb-12" />
        <el-table :data="items" border>
          <el-table-column prop="product_name" label="产品" min-width="220" />
          <el-table-column prop="quantity" label="原订单数量" width="140">
            <template #default="scope">{{ formatNumber(scope.row.quantity) }}</template>
          </el-table-column>
          <el-table-column label="本次退换数量" width="160">
            <template #default="scope">
              <el-input-number v-model="scope.row.return_quantity" :min="0" :max="scope.row.quantity" :precision="2" style="width: 100%" />
            </template>
          </el-table-column>
        </el-table>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="submit">提交申请</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="approvalVisible" :title="approvalForm.decision === 'approved' ? '退换货通过' : '退换货驳回'" width="560px">
      <el-form :model="approvalForm" label-width="90px">
        <el-form-item label="审批说明">
          <el-input v-model="approvalForm.note" type="textarea" :rows="4" placeholder="可填写审批说明" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="approvalVisible = false">取消</el-button>
        <DelayedActionButton :type="approvalForm.decision === 'approved' ? 'success' : 'danger'" @click="submitApproval">确认</DelayedActionButton>
      </template>
    </el-dialog>

    <el-drawer v-model="detailVisible" title="退换货详情" size="50%">
      <el-descriptions v-if="detail" :column="2" border>
        <el-descriptions-item label="退换货单号">{{ detail.return_no }}</el-descriptions-item>
        <el-descriptions-item label="销售订单号">{{ detail.sales_order_no || '-' }}</el-descriptions-item>
        <el-descriptions-item label="类型">{{ detail.return_type_label || returnTypeLabel(detail.return_type) }}</el-descriptions-item>
        <el-descriptions-item label="状态">
          <el-tag :type="statusType(detail.status)">{{ detail.status_label || statusLabel(detail.status) }}</el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="退款金额">{{ formatNumber(detail.refund_amount) }}</el-descriptions-item>
        <el-descriptions-item label="复检状态">{{ detail.requires_reinspection ? (detail.reinspection_status_label || statusLabel(detail.reinspection_status)) : '无需复检' }}</el-descriptions-item>
        <el-descriptions-item label="申请人">{{ detail.requested_by_name || '-' }}</el-descriptions-item>
        <el-descriptions-item label="审批人">{{ detail.approved_by_name || '-' }}</el-descriptions-item>
        <el-descriptions-item label="申请时间">{{ formatDateTime(detail.created_at) }}</el-descriptions-item>
        <el-descriptions-item label="审核时间">{{ formatDateTime(detail.approved_at) }}</el-descriptions-item>
        <el-descriptions-item label="申请原因" :span="2">{{ detail.reason || '-' }}</el-descriptions-item>
        <el-descriptions-item label="审批备注" :span="2">{{ detail.review_note || '-' }}</el-descriptions-item>
      </el-descriptions>

      <div class="section-title mt-16">退货明细</div>
      <el-table :data="detail?.items || []" stripe class="mt-16">
        <el-table-column prop="product_name" label="产品" min-width="220" />
        <el-table-column prop="quantity" label="退换数量" width="140">
          <template #default="scope">{{ formatNumber(scope.row.quantity) }}</template>
        </el-table-column>
        <el-table-column prop="batch_suffix" label="批次后缀" width="140" />
      </el-table>

      <div class="section-title mt-16">回库流水</div>
      <el-table :data="detail?.inventory_transactions || []" stripe>
        <el-table-column prop="transaction_no" label="流水号" min-width="150" />
        <el-table-column prop="warehouse_name" label="仓库" min-width="120" />
        <el-table-column prop="product_name" label="产品" min-width="160" />
        <el-table-column prop="quantity" label="数量" width="120">
          <template #default="scope">{{ formatNumber(scope.row.quantity) }}</template>
        </el-table-column>
        <el-table-column prop="unit_cost" label="单位成本" width="120">
          <template #default="scope">{{ formatNumber(scope.row.unit_cost) }}</template>
        </el-table-column>
        <el-table-column prop="total_cost" label="总成本" width="120">
          <template #default="scope">{{ formatNumber(scope.row.total_cost) }}</template>
        </el-table-column>
        <el-table-column prop="batch_no" label="批次号" min-width="160" />
        <el-table-column prop="operator_name" label="操作人" min-width="120" />
        <el-table-column prop="created_at" label="时间" min-width="170">
          <template #default="scope">{{ formatDateTime(scope.row.created_at) }}</template>
        </el-table-column>
      </el-table>
      <div v-if="detail && !(detail.inventory_transactions || []).length" class="empty-tip">
        当前退换货单还没有生成回库流水。
      </div>

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

      <AttachmentUploader v-if="detail" class="mt-16" business-type="sales_return" :business-id="detail.id" :readonly="!canUploadAttachments" />
    </el-drawer>
  </PageSection>
</template>

<script setup>
import 'element-plus/es/components/message/style/css'
import 'element-plus/es/components/alert/style/css'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/descriptions/style/css'
import 'element-plus/es/components/dialog/style/css'
import 'element-plus/es/components/drawer/style/css'
import 'element-plus/es/components/form/style/css'
import 'element-plus/es/components/input-number/style/css'
import 'element-plus/es/components/input/style/css'
import 'element-plus/es/components/select/style/css'
import 'element-plus/es/components/switch/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/tag/style/css'

import { ElMessage } from 'element-plus/es/components/message/index.mjs'

import ElAlert from 'element-plus/es/components/alert/index.mjs'
import ElButton from 'element-plus/es/components/button/index.mjs'
import ElDescriptions, { ElDescriptionsItem } from 'element-plus/es/components/descriptions/index.mjs'
import ElDialog from 'element-plus/es/components/dialog/index.mjs'
import ElDrawer from 'element-plus/es/components/drawer/index.mjs'
import ElForm, { ElFormItem } from 'element-plus/es/components/form/index.mjs'
import ElInputNumber from 'element-plus/es/components/input-number/index.mjs'
import ElInput from 'element-plus/es/components/input/index.mjs'
import ElSelect, { ElOption } from 'element-plus/es/components/select/index.mjs'
import ElSwitch from 'element-plus/es/components/switch/index.mjs'
import ElTable, { ElTableColumn } from 'element-plus/es/components/table/index.mjs'
import ElTag from 'element-plus/es/components/tag/index.mjs'

import { computed, onMounted, reactive, ref } from 'vue'

import { get, post } from '../../api/client'
import { useRouteFocus } from '../../composables/useRouteFocus'
import AttachmentUploader from '../../components/AttachmentUploader.vue'
import AppPagination from '../../components/AppPagination.vue'
import DelayedActionButton from '../../components/DelayedActionButton.vue'
import PageSection from '../../components/PageSection.vue'
import { useLocalPagination } from '../../composables/useLocalPagination'
import { usePagePermission } from '../../composables/usePagePermission'
import { useAuthStore } from '../../stores/auth'
import { exportRowsToCsv } from '../../utils/export'
import { formatDateTime, formatNumber, returnTypeLabel, statusLabel, statusType } from '../../utils/format'

const auth = useAuthStore()
const pagePermission = usePagePermission()
const { focusedId: focusedReturnId, applyFocus: applyRouteFocus } = useRouteFocus()
const rows = ref([])
const salesOrders = ref([])
const items = ref([])
const detail = ref(null)
const dialogVisible = ref(false)
const approvalVisible = ref(false)
const detailVisible = ref(false)
const activeRow = ref(null)

const form = reactive({
  sales_order_id: null,
  return_type: 'return',
  reason: '',
  refund_amount: 0,
  requires_reinspection: false
})
const approvalForm = reactive({
  decision: 'approved',
  note: ''
})

const canCreate = computed(() => auth.user?.role_code === 'sales' && pagePermission.can('create'))
const canUploadAttachments = computed(() => auth.user?.role_code === 'sales' && pagePermission.can('upload'))
const { pagination, total, pageSizes, pagedItems: pagedRows } = useLocalPagination(rows)

function rowClassName({ row }) {
  return focusedReturnId.value === row.id ? 'is-focused-row' : ''
}

function resetForm() {
  form.sales_order_id = null
  form.return_type = 'return'
  form.reason = ''
  form.refund_amount = 0
  form.requires_reinspection = false
  items.value = []
}

function openCreate() {
  resetForm()
  dialogVisible.value = true
}

function canApprove(row) {
  return ['manager', 'chairman'].includes(auth.user?.role_code) && pagePermission.can('approve') && row.status === 'pending'
}

function canRefund(row) {
  return auth.user?.role_code === 'finance' && pagePermission.can('refund') && row.status === 'approved' && Number(row.refund_amount || 0) > 0
}

function openApproval(row, decision) {
  activeRow.value = row
  approvalForm.decision = decision
  approvalForm.note = row.review_note || ''
  approvalVisible.value = true
}

async function openDetail(row) {
  detail.value = await get(`/api/sales/returns/${row.id}`)
  detailVisible.value = true
}

async function applyFocus() {
  await applyRouteFocus(
    (focusId) => rows.value.find((item) => item.id === focusId),
    async (matched) => {
      await openDetail(matched)
    }
  )
}

async function load() {
  rows.value = await get('/api/sales/returns')
  await applyFocus()
}

async function handleOrderChange(orderId) {
  if (!orderId) {
    items.value = []
    return
  }
  const order = await get(`/api/sales/orders/${orderId}`)
  items.value = (order.items || []).map((item) => ({
    sales_order_item_id: item.id,
    product_name: item.product_name,
    quantity: item.quantity,
    return_quantity: 0
  }))
}

async function submit() {
  const selectedItems = items.value
    .filter((item) => Number(item.return_quantity) > 0)
    .map((item) => ({
      sales_order_item_id: item.sales_order_item_id,
      quantity: item.return_quantity
    }))

  if (!form.sales_order_id) {
    ElMessage.warning('请选择销售订单')
    return
  }
  if (!selectedItems.length) {
    ElMessage.warning('请至少填写一条退换货明细')
    return
  }

  const result = await post('/api/sales/returns', {
    sales_order_id: form.sales_order_id,
    return_type: form.return_type,
    reason: form.reason,
    refund_amount: form.refund_amount,
    requires_reinspection: form.requires_reinspection,
    items: selectedItems
  })
  ElMessage.success(`退换货申请已提交：${result.return_no}`)
  dialogVisible.value = false
  await load()
}

async function submitApproval() {
  await post(`/api/sales/returns/${activeRow.value.id}/approve`, {
    decision: approvalForm.decision,
    note: approvalForm.note
  })
  ElMessage.success('退换货审批已处理')
  approvalVisible.value = false
  await load()
}

async function refund(row) {
  await post('/api/finance/refunds', {
    business_type: 'sales_return',
    sales_return_id: row.id,
    amount: row.refund_amount,
    refunded_at: new Date().toISOString(),
    remark: `销售退换货退款：${row.return_no}`
  })
  ElMessage.success('退款已登记')
  await load()
}

function exportTable() {
  exportRowsToCsv('sales_returns', rows.value)
}

onMounted(async () => {
  if (canCreate.value) {
    salesOrders.value = await get('/api/sales/orders')
  }
  await load()
})
</script>

<style scoped>
.mb-12 { margin-bottom: 12px; }
.mt-16 { margin-top: 16px; }
.section-title { font-weight: 600; color: #303133; }
.empty-tip { margin-top: 12px; color: #909399; }
:deep(.is-focused-row) > td { background: #fff8e1 !important; }
</style>
