<template>
  <div class="approval-page">
  <PageSection :title="texts.title" :description="texts.description">
    <template #actions>
      <el-button @click="load">{{ texts.actions.refresh }}</el-button>
    </template>

    <el-table :data="pagedRows" stripe :row-class-name="rowClassName">
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
      <el-table-column :label="texts.columns.actions" width="400" fixed="right">
        <template #default="scope">
          <el-button size="small" @click="showDetail(scope.row.id)">{{ texts.actions.detail }}</el-button>
          <el-button size="small" @click="handlePrintSales(scope.row)">打印</el-button>
          <el-button size="small" @click="handleDownloadSales(scope.row)">下载</el-button>
          <el-button v-if="canApproveOrders" size="small" type="success" @click="openApprove(scope.row)">{{ texts.actions.approve }}</el-button>
          <el-button v-if="canApproveOrders" size="small" type="warning" plain @click="openConvert(scope.row)">转为应收</el-button>
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
      <div v-if="detailDiscounts.length" class="discount-title mt-16">改价审批明细</div>
      <el-table v-if="detailDiscounts.length" :data="detailDiscounts" stripe border>
        <el-table-column prop="product_name" label="产品" min-width="140" />
        <el-table-column prop="specification" label="规格" min-width="100" />
        <el-table-column label="标准价" width="100"><template #default="scope">{{ formatNumber(scope.row.standard_price) }}</template></el-table-column>
        <el-table-column label="订单价" width="100"><template #default="scope">{{ formatNumber(scope.row.actual_price) }}</template></el-table-column>
        <el-table-column prop="remark" label="改价说明" min-width="160" />
        <el-table-column prop="status_label" label="审批结果" width="100" />
      </el-table>
      <AttachmentUploader v-if="detail" class="mt-16" business-type="sales_order" :business-id="detail.id" :readonly="true" />
    </el-drawer>

    <el-dialog v-model="decisionVisible" :title="decisionTitle" width="920px">
      <el-form label-width="90px">
        <el-form-item :label="texts.dialog.orderNo">
          <span>{{ activeRow?.order_no || '-' }}</span>
        </el-form-item>
        <el-form-item label="提交人">
          <span>{{ activeRow?.sales_user || '-' }}</span>
        </el-form-item>
        <el-form-item label="订单金额">{{ formatNumber(activeRow?.total_amount || 0) }} 元</el-form-item>
        <el-alert
          type="info"
          :closable="false"
          show-icon
          class="mb-12"
          :title="approvalHint"
        />
        <div class="discount-title">低于标准价的改价明细</div>
        <el-table v-if="pendingDiscounts.length" :data="pendingDiscounts" border size="small" class="mb-12">
          <el-table-column prop="product_name" label="产品名称" min-width="150" />
          <el-table-column prop="specification" label="产品规格" min-width="110" />
          <el-table-column label="标准价格" width="105"><template #default="scope">{{ formatNumber(scope.row.standard_price) }}</template></el-table-column>
          <el-table-column label="本订单价格" width="110"><template #default="scope">{{ formatNumber(scope.row.actual_price) }}</template></el-table-column>
          <el-table-column label="数量" width="95"><template #default="scope">{{ formatNumber(scope.row.quantity) }}</template></el-table-column>
          <el-table-column prop="remark" label="改价说明" min-width="160" />
          <el-table-column prop="submitter" label="订单提交人" width="110" />
        </el-table>
        <p v-else class="no-discount">无低于标准价的改价（高于标准价无需改价审批）</p>
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
        <el-button :type="decisionForm.decision === 'rejected' ? 'danger' : 'success'" :loading="submitting" @click="submitDecision">
          {{ decisionForm.decision === 'rejected' ? texts.actions.confirmReject : decisionForm.convert_to_receivable ? '确认转应收' : texts.actions.confirmApprove }}
        </el-button>
      </template>
    </el-dialog>
  </PageSection>
  <PageSection title="审批记录" description="查看已通过、转应收及驳回的客户订单确认记录。">
    <template #actions>
      <el-input v-model="historyKeyword" placeholder="搜索订单号" clearable style="width: 220px" @keyup.enter="loadHistory" @clear="loadHistory" />
      <el-button @click="loadHistory">搜索</el-button>
    </template>
    <el-table :data="historyRows" stripe>
      <el-table-column prop="order_no" label="订单号" min-width="160" />
      <el-table-column label="结果" width="100"><template #default="scope">{{ scope.row.decision === 'approved' ? '已通过' : '已驳回' }}</template></el-table-column>
      <el-table-column prop="approval_mode" label="处理方式" width="120" />
      <el-table-column label="订单金额" width="130"><template #default="scope">{{ formatNumber(scope.row.order_amount) }}</template></el-table-column>
      <el-table-column prop="approver_name" label="审批人" width="120" />
      <el-table-column prop="approved_at" label="审批时间" min-width="180" />
      <el-table-column prop="note" label="审批说明" min-width="160" show-overflow-tooltip />
      <el-table-column label="操作" width="90"><template #default="scope"><el-button link type="primary" @click="showDetail(scope.row.order_id)">详情</el-button></template></el-table-column>
    </el-table>
    <AppPagination v-model:page="historyPage" v-model:page-size="historyPageSize" :total="historyTotal" :page-sizes="pageSizes" />
  </PageSection>
  </div>
</template>

<script setup>
import { downloadOrderPdf, printOrderPdf } from '../../utils/printPdf'
import 'element-plus/es/components/alert/style/css'
import 'element-plus/es/components/message/style/css'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/descriptions/style/css'
import 'element-plus/es/components/dialog/style/css'
import 'element-plus/es/components/drawer/style/css'
import 'element-plus/es/components/form/style/css'
import 'element-plus/es/components/input/style/css'
import 'element-plus/es/components/table/style/css'

import { ElMessage } from 'element-plus/es/components/message/index.mjs'

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
async function handlePrintSales(row) {
  try { await printOrderPdf('sales', row.id) } catch (error) { ElMessage.error(error.message || '打印销售单失败') }
}
async function handleDownloadSales(row) {
  try { await downloadOrderPdf('sales', row.id, row.order_no) } catch (error) { ElMessage.error(error.message || '下载销售单失败') }
}
const pagePermission = usePagePermission()
const texts = {
  title: '客户订单收款确认',
  description: '确认收款或转为应收时，同时审核低于标准价的改价；确认后订单进入库管置货。',
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
    cancel: '取消',
    confirmApprove: '确认通过',
    confirmReject: '确认驳回'
  },
  messages: {
    success: '审批完成',
  }
}

const { route, focusedId: focusedOrderId, applyFocus: applyRouteFocus } = useRouteFocus()
const rows = ref([])
const historyRows = ref([])
const historyKeyword = ref('')
const historyPage = ref(1)
const historyPageSize = ref(10)
const historyTotal = ref(0)
const submitting = ref(false)
const decisionDetail = ref(null)
const detailVisible = ref(false)
const decisionVisible = ref(false)
const detail = ref(null)
const activeRow = ref(null)
const decisionForm = reactive({ decision: 'approved', note: '', convert_to_receivable: false })
const canApproveOrders = computed(() => ['finance', 'finance_super', 'chairman', 'system_admin'].includes(auth.user?.role_code) && pagePermission.can('approve'))
const { pagination, total, pageSizes, pagedItems: pagedRows, ensureVisible } = useLocalPagination(rows)
const decisionTitle = computed(() => decisionForm.decision === 'rejected' ? '驳回客户订单' : decisionForm.convert_to_receivable ? '确认转为应收并审核改价' : '确认收款并审核改价')
const pendingDiscounts = computed(() => {
  const items = new Map((decisionDetail.value?.items || []).map(item => [item.id, item]))
  return (decisionDetail.value?.price_change_logs || [])
    .filter(log => log.status === 'pending' && Number(log.actual_price) < Number(log.standard_price))
    .map(log => ({ ...log, specification: items.get(log.sales_order_item_id)?.display_specification || '-',
      quantity: items.get(log.sales_order_item_id)?.quantity || 0,
      submitter: activeRow.value?.sales_user || decisionDetail.value?.operator_name || '-' }))
})
const detailDiscounts = computed(() => {
  const items = new Map((detail.value?.items || []).map(item => [item.id, item]))
  return (detail.value?.price_change_logs || [])
    .filter(log => Number(log.actual_price) < Number(log.standard_price))
    .map(log => ({ ...log, specification: items.get(log.sales_order_item_id)?.display_specification || '-' }))
})
const approvalHint = computed(() => {
  if (decisionForm.decision === 'rejected') return '驳回后订单返回销售修改；如有待审核改价，也一并驳回。'
  if (decisionForm.convert_to_receivable) return `本次不登记已收款，订单全额 ${formatNumber(activeRow.value?.total_amount || 0)} 元转入应收，并放行后续置货。`
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
  await loadHistory()
  await applyFocus()
}

async function loadHistory() {
  const params = new URLSearchParams({ page: String(historyPage.value), page_size: String(historyPageSize.value) })
  if (historyKeyword.value.trim()) params.set('keyword', historyKeyword.value.trim())
  const data = await get(`/api/sales/approvals/history?${params}`)
  historyRows.value = data.items || []
  historyTotal.value = data.total || 0
}

async function showDetail(id) {
  detail.value = await get(`/api/sales/orders/${id}`)
  detailVisible.value = true
}

async function openApprove(row) {
  try { decisionDetail.value = await get(`/api/sales/orders/${row.id}`) } catch (error) { ElMessage.error(error.message || '无法读取改价明细'); return }
  activeRow.value = row
  decisionForm.decision = 'approved'
  decisionForm.convert_to_receivable = false
  decisionForm.note = '确认收款并通过'
  decisionVisible.value = true
}

async function openConvert(row) {
  try { decisionDetail.value = await get(`/api/sales/orders/${row.id}`) } catch (error) { ElMessage.error(error.message || '无法读取改价明细'); return }
  activeRow.value = row
  decisionForm.decision = 'approved'
  decisionForm.convert_to_receivable = true
  decisionForm.note = ''
  decisionVisible.value = true
}

async function openReject(row) {
  try { decisionDetail.value = await get(`/api/sales/orders/${row.id}`) } catch (error) { ElMessage.error(error.message || '无法读取改价明细'); return }
  activeRow.value = row
  decisionForm.decision = 'rejected'
  decisionForm.convert_to_receivable = false
  decisionForm.note = ''
  decisionVisible.value = true
}

async function submitDecision() {
  // 驳回必须说明原因,否则销售不知道该改什么
  if (decisionForm.decision === 'rejected' && !String(decisionForm.note || '').trim()) {
    ElMessage.warning('请填写驳回原因')
    return
  }
  if (submitting.value) return
  submitting.value = true
  try {
    await post(`/api/sales/orders/${activeRow.value.id}/approve`, {
      decision: decisionForm.decision,
      note: decisionForm.note,
      convert_to_receivable: decisionForm.convert_to_receivable
    })
    ElMessage.success(texts.messages.success)
    decisionVisible.value = false
    await load()
  } catch (error) {
    ElMessage.error(error.message || '审批失败，请确认该订单是否已指定其他财务处理')
  } finally {
    submitting.value = false
  }
}

onMounted(load)

watch([historyPage, historyPageSize], loadHistory)

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
.approval-page { display: grid; gap: 16px; }
.discount-title { color: #111; font-weight: 700; margin: 8px 0; }
.no-discount { color: #606266; margin: 6px 0 16px; }
.mt-16 { margin-top: 16px; }
:deep(.is-focused-row) > td { background: #fff8e1 !important; }
</style>
