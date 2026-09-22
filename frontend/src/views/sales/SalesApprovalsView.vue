<template>
  <PageSection :title="texts.title" :description="texts.description">
    <template #actions>
      <el-button @click="load">{{ texts.actions.refresh }}</el-button>
      <el-button v-if="canApproveOrders" type="success" :disabled="!selection.length" @click="batchApprove">{{ texts.actions.batchApprove }}</el-button>
    </template>

    <el-table :data="pagedRows" stripe :row-class-name="rowClassName" @selection-change="selection = $event">
      <el-table-column v-if="canApproveOrders" type="selection" width="55" />
      <el-table-column prop="order_no" :label="texts.columns.orderNo" width="150" />
      <el-table-column prop="customer_name" :label="texts.columns.customer" min-width="150" />
      <el-table-column prop="receipt_account_label" :label="texts.columns.receiptAccount" min-width="220" show-overflow-tooltip />
      <el-table-column prop="payment_type_label" :label="texts.columns.paymentType" min-width="160" />
      <el-table-column prop="sales_user" :label="texts.columns.salesUser" width="130" />
      <el-table-column prop="receipt_amount" :label="texts.columns.receiptAmount" width="120">
        <template #default="scope">{{ formatNumber(scope.row.receipt_amount) }}</template>
      </el-table-column>
      <el-table-column prop="total_amount" :label="texts.columns.totalAmount" width="120">
        <template #default="scope">{{ formatNumber(scope.row.total_amount) }}</template>
      </el-table-column>
      <el-table-column :label="texts.columns.actions" width="320" fixed="right">
        <template #default="scope">
          <el-button size="small" @click="showDetail(scope.row.id)">{{ texts.actions.detail }}</el-button>
          <el-button v-if="canApproveOrders" size="small" type="success" @click="openApprove(scope.row)">{{ texts.actions.approve }}</el-button>
          <el-button v-if="canApproveOrders" size="small" type="warning" plain @click="convertToReceivable(scope.row)">转为应收</el-button>
          <el-button v-if="canApproveOrders" size="small" type="danger" @click="openReject(scope.row)">{{ texts.actions.reject }}</el-button>
        </template>
      </el-table-column>
    </el-table>
    <AppPagination
      v-model:page="pagination.page"
      v-model:page-size="pagination.pageSize"
      :total="total"
      :page-sizes="pageSizes"
    />

    <el-drawer v-model="detailVisible" :title="texts.detail.title" size="50%">
      <el-descriptions v-if="detail" :column="2" border>
        <el-descriptions-item :label="texts.detail.orderNo">{{ detail.order_no }}</el-descriptions-item>
        <el-descriptions-item :label="texts.detail.status">{{ detail.status }}</el-descriptions-item>
        <el-descriptions-item :label="texts.detail.customer">{{ detail.customer?.name }}</el-descriptions-item>
        <el-descriptions-item :label="texts.detail.paymentType">{{ detail.payment_type }}</el-descriptions-item>
        <el-descriptions-item :label="texts.detail.consignee">{{ detail.consignee }}</el-descriptions-item>
        <el-descriptions-item :label="texts.detail.phone">{{ detail.consignee_phone }}</el-descriptions-item>
      </el-descriptions>
      <el-table :data="detail?.items || []" stripe class="mt-16">
        <el-table-column prop="product_name" :label="texts.detail.product" />
        <el-table-column prop="quantity" :label="texts.detail.quantity">
          <template #default="scope">{{ formatNumber(scope.row.quantity) }}</template>
        </el-table-column>
        <el-table-column prop="unit_price" :label="texts.detail.unitPrice">
          <template #default="scope">{{ formatNumber(scope.row.unit_price) }}</template>
        </el-table-column>
        <el-table-column prop="total_amount" :label="texts.detail.totalAmount">
          <template #default="scope">{{ formatNumber(scope.row.total_amount) }}</template>
        </el-table-column>
      </el-table>
      <AttachmentUploader v-if="detail" class="mt-16" business-type="sales_order" :business-id="detail.id" :readonly="true" />
    </el-drawer>

    <el-dialog v-model="decisionVisible" :title="decisionForm.decision === 'approved' ? texts.dialog.approveTitle : texts.dialog.rejectTitle" width="520px">
      <el-form label-width="90px">
        <el-form-item :label="texts.dialog.orderNo">
          <span>{{ activeRow?.order_no || '-' }}</span>
        </el-form-item>
        <el-form-item :label="texts.dialog.depositAmount">
          <span>{{ formatNumber(activeRow?.deposit_amount || 0) }}</span>
        </el-form-item>
        <el-alert
          type="info"
          :closable="false"
          show-icon
          class="mb-12"
          :title="approvalHint"
        />
        <el-form-item :label="texts.dialog.note" :required="decisionForm.decision === 'rejected'">
          <el-input
            v-model="decisionForm.note"
            type="textarea"
            :rows="4"
            :placeholder="decisionForm.decision === 'rejected' ? '请填写驳回原因，销售需据此修改后重新提交' : '可填写审批意见'"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="decisionVisible = false">{{ texts.actions.cancel }}</el-button>
        <el-button :type="decisionForm.decision === 'approved' ? 'success' : 'danger'" @click="submitDecision">
          {{ decisionForm.decision === 'approved' ? texts.actions.confirmApprove : texts.actions.confirmReject }}
        </el-button>
      </template>
    </el-dialog>
  </PageSection>
</template>

<script setup>
import 'element-plus/es/components/alert/style/css'
import 'element-plus/es/components/message/style/css'
import 'element-plus/es/components/message-box/style/css'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/descriptions/style/css'
import 'element-plus/es/components/dialog/style/css'
import 'element-plus/es/components/drawer/style/css'
import 'element-plus/es/components/form/style/css'
import 'element-plus/es/components/input/style/css'
import 'element-plus/es/components/table/style/css'

import { ElMessage } from 'element-plus/es/components/message/index.mjs'
import { ElMessageBox } from 'element-plus/es/components/message-box/index.mjs'

import ElAlert from 'element-plus/es/components/alert/index.mjs'
import ElButton from 'element-plus/es/components/button/index.mjs'
import ElDescriptions, { ElDescriptionsItem } from 'element-plus/es/components/descriptions/index.mjs'
import ElDialog from 'element-plus/es/components/dialog/index.mjs'
import ElDrawer from 'element-plus/es/components/drawer/index.mjs'
import ElForm, { ElFormItem } from 'element-plus/es/components/form/index.mjs'
import ElInput from 'element-plus/es/components/input/index.mjs'
import ElTable, { ElTableColumn } from 'element-plus/es/components/table/index.mjs'

import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { useRouteFocus } from '../../composables/useRouteFocus'

import { get, post } from '../../api/client'
import AttachmentUploader from '../../components/AttachmentUploader.vue'
import AppPagination from '../../components/AppPagination.vue'
import PageSection from '../../components/PageSection.vue'
import { useLocalPagination } from '../../composables/useLocalPagination'
import { usePagePermission } from '../../composables/usePagePermission'
import { useAuthStore } from '../../stores/auth'
import { formatNumber } from '../../utils/format'

const auth = useAuthStore()
const pagePermission = usePagePermission()
const texts = {
  title: '销售订单审批',
  description: '财务审批确认收款或应收放行；通过后订单直接进入库管置货，无需销售再次提交收款确认。',
  columns: {
    orderNo: '订单号',
    customer: '客户',
    paymentType: '收款方式',
    receiptAccount: '收款账户',
    salesUser: '提交人',
    receiptAmount: '收款金额',
    totalAmount: '订单金额',
    depositAmount: '定金',
    actions: '操作'
  },
  detail: {
    title: '订单详情',
    orderNo: '订单号',
    status: '状态',
    customer: '客户',
    paymentType: '收款方式',
    consignee: '收货人',
    phone: '电话',
    product: '产品',
    quantity: '数量',
    unitPrice: '单价',
    totalAmount: '金额'
  },
  dialog: {
    approveTitle: '审批通过',
    rejectTitle: '审批驳回',
    orderNo: '订单号',
    depositAmount: '定金',
    voucherAmount: '到账金额',
    note: '审批说明'
  },
  actions: {
    refresh: '刷新',
    detail: '详情',
    approve: '通过',
    reject: '驳回',
    batchApprove: '批量通过',
    cancel: '取消',
    confirmApprove: '确认通过',
    confirmReject: '确认驳回'
  },
  messages: {
    success: '审批完成',
    batchSuccess: '批量审批完成'
  }
}

const { route, focusedId: focusedOrderId, applyFocus: applyRouteFocus } = useRouteFocus()
const rows = ref([])
const selection = ref([])
const detailVisible = ref(false)
const decisionVisible = ref(false)
const detail = ref(null)
const activeRow = ref(null)
const decisionForm = reactive({ decision: 'approved', note: '' })
const canApproveOrders = computed(() => ['finance', 'chairman', 'system_admin'].includes(auth.user?.role_code) && pagePermission.can('approve'))
const { pagination, total, pageSizes, pagedItems: pagedRows, ensureVisible } = useLocalPagination(rows)
const approvalHint = computed(() => {
  const amount = Number(activeRow.value?.receivable_amount || 0)
  return amount > 0
    ? `本次通过将确认收款 ${formatNumber(activeRow.value?.receipt_amount || 0)} 元，并生成 ${formatNumber(amount)} 元应收；确认后订单将进入置货流程。`
    : `本次通过将确认收款 ${formatNumber(activeRow.value?.receipt_amount || 0)} 元，订单将进入置货流程。`
})

function rowClassName({ row }) {
  return focusedOrderId.value === row.id ? 'is-focused-row' : ''
}

async function applyFocus() {
  await applyRouteFocus(
    (focusId) => {
      const matched = rows.value.find((item) => item.id === focusId)
      if (matched) {
        ensureVisible((item) => item.id === matched.id)
      }
      return matched
    },
    async (matched) => {
      await showDetail(matched.id)
    }
  )
}

async function load() {
  rows.value = await get('/api/sales/approvals/pending')
  await applyFocus()
}

async function showDetail(id) {
  detail.value = await get(`/api/sales/orders/${id}`)
  detailVisible.value = true
}

function openApprove(row) {
  activeRow.value = row
  decisionForm.decision = 'approved'
  decisionForm.note = '审核通过'
  decisionVisible.value = true
}

function openReject(row) {
  activeRow.value = row
  decisionForm.decision = 'rejected'
  decisionForm.note = ''
  decisionVisible.value = true
}

async function submitDecision() {
  // 驳回必须说明原因,否则销售不知道该改什么
  if (decisionForm.decision === 'rejected' && !String(decisionForm.note || '').trim()) {
    ElMessage.warning('请填写驳回原因')
    return
  }
  if (decisionForm.decision === 'approved' && Number(activeRow.value?.receivable_amount || 0) > 0) {
    try {
      await ElMessageBox.confirm(`将生成 ${formatNumber(activeRow.value.receivable_amount)} 元应收，是否确认通过？`, '确认转应收', { type: 'warning', confirmButtonText: '确认通过', cancelButtonText: '取消' })
    } catch { return }
  }
  await post(`/api/sales/orders/${activeRow.value.id}/approve`, {
    decision: decisionForm.decision,
    note: decisionForm.note
  })
  ElMessage.success(texts.messages.success)
  decisionVisible.value = false
  await load()
}

async function convertToReceivable(row) {
  try {
    await ElMessageBox.confirm(`将订单全额 ${formatNumber(row.total_amount)} 元转为应收并放行后续置货/发货，是否继续？`, '转为应收', { type: 'warning', confirmButtonText: '确认转应收', cancelButtonText: '取消' })
  } catch { return }
  await post(`/api/sales/orders/${row.id}/convert-to-receivable`, {})
  ElMessage.success('订单已转为应收并放行')
  await load()
}

async function batchApprove() {
  // 原先按"有无定金"拆两组并跳过有定金的单(因为那时要手填到账金额)。
  // 现在审批不再登记金额,这个区分已无意义,选中的单可一次批完。
  for (const row of selection.value) {
    await post(`/api/sales/orders/${row.id}/approve`, {
      decision: 'approved',
      note: '批量审批通过'
    })
  }
  if (selection.value.length) {
    ElMessage.success(texts.messages.batchSuccess)
  }
  await load()
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
.mt-16 { margin-top: 16px; }
:deep(.is-focused-row) > td { background: #fff8e1 !important; }
</style>
