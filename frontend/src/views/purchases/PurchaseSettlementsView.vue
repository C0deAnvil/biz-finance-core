<template>
  <div class="page-stack">
    <el-row :gutter="12" class="summary-row">
      <el-col :span="6"><el-card shadow="hover"><div class="summary-label">未完成订单</div><div class="summary-value">{{ summary.order_count || 0 }}</div></el-card></el-col>
      <el-col :span="6"><el-card shadow="hover"><div class="summary-label">付款申请</div><div class="summary-value">{{ summary.payment_request_count || 0 }}</div></el-card></el-col>
      <el-col :span="6"><el-card shadow="hover"><div class="summary-label">付款记录</div><div class="summary-value">{{ summary.payable_count || 0 }}</div></el-card></el-col>
      <el-col :span="6"><el-card shadow="hover"><div class="summary-label">协同通知</div><div class="summary-value">{{ summary.notification_count || 0 }}</div></el-card></el-col>
    </el-row>

    <PageSection title="采购付款申请" description="采购或财务按未完成采购订单提交付款申请；董事长审批通过后流转至财务出账，财务上传回单后形成付款记录。">
      <template #actions>
        <el-button @click="load">刷新</el-button>
        <el-button v-if="canSubmitRequest" type="primary" @click="openRequestDialog()">提交付款申请</el-button>
        <el-button v-if="canSubmitRequest" type="success" plain @click="openMergedRequestDialog">合并付款申请</el-button>
      </template>
      <el-table :data="pagedOrders" stripe @selection-change="handleOrderSelectionChange">
        <el-table-column v-if="canSubmitRequest" type="selection" width="52" :selectable="paymentRequestSelectable" />
        <el-table-column prop="order_no" label="采购单号" width="160" />
        <el-table-column prop="supplier_name" label="供应商" min-width="180" />
        <el-table-column prop="status_label" label="采购状态" width="120">
          <template #default="scope"><el-tag :type="statusType(scope.row.status)">{{ scope.row.status_label }}</el-tag></template>
        </el-table-column>
        <el-table-column prop="arrival_status_label" label="到货状态" width="120">
          <template #default="scope"><el-tag :type="scope.row.arrival_status === 'arrived' ? 'success' : 'warning'">{{ scope.row.arrival_status_label || statusLabel(scope.row.arrival_status) }}</el-tag></template>
        </el-table-column>
        <el-table-column prop="fund_status_label" label="结算状态" width="120">
          <template #default="scope"><el-tag :type="statusType(scope.row.fund_status)">{{ scope.row.fund_status_label }}</el-tag></template>
        </el-table-column>
        <el-table-column prop="total_amount" label="订单金额" width="120"><template #default="scope">{{ formatNumber(scope.row.total_amount) }}</template></el-table-column>
        <el-table-column prop="remaining_amount" label="未结算订单金额" width="140"><template #default="scope">{{ formatNumber(scope.row.remaining_amount) }}</template></el-table-column>
        <el-table-column prop="item_summary" label="订单摘要" min-width="220" show-overflow-tooltip />
        <el-table-column label="操作" width="180" fixed="right">
          <template #default="scope">
            <el-button size="small" text type="primary" @click="goOrder(scope.row.id)">查看订单</el-button>
            <el-button v-if="canSubmitRequest && scope.row.can_submit_payment_request" size="small" text type="success" @click="openRequestDialog(scope.row)">申请付款</el-button>
            <el-tag v-else-if="canSubmitRequest && scope.row.payment_request_block_reason" type="info" size="small">
              {{ scope.row.payment_request_block_reason }}
            </el-tag>
            <el-button
              v-if="canCompleteRequest && completableRequestForOrder(scope.row.id)"
              size="small"
              text
              type="warning"
              @click="completeOrderRequest(scope.row)"
            >确认完成</el-button>
          </template>
        </el-table-column>
      </el-table>
      <AppPagination v-model:page="orderPagination.page" v-model:page-size="orderPagination.pageSize" :total="orderTotal" :page-sizes="pageSizes" />
    </PageSection>

    <PageSection title="申请记录" description="展示采购付款申请的审批与完成状态。">
      <el-table :data="pagedRequests" stripe>
        <el-table-column prop="merge_no" label="合并批次" width="140">
          <template #default="scope">{{ scope.row.merge_no || '-' }}</template>
        </el-table-column>
        <el-table-column prop="counterparty_account_label" label="供应商收款账户" min-width="220" show-overflow-tooltip />
        <el-table-column prop="request_no" label="申请单号" width="160" />
        <el-table-column prop="purchase_order_no" label="采购单号" width="160" />
        <el-table-column prop="requested_amount" label="申请总金额" width="130"><template #default="scope">{{ formatNumber(scope.row.requested_amount) }}</template></el-table-column>
        <el-table-column prop="order_amount" label="订单金额" width="120"><template #default="scope">{{ formatNumber(scope.row.order_amount) }}</template></el-table-column>
        <el-table-column prop="other_fee_amount" label="其他费用" width="120"><template #default="scope">{{ formatNumber(scope.row.other_fee_amount) }}</template></el-table-column>
        <el-table-column prop="arrival_status_label" label="到货状态" width="120" />
        <el-table-column label="发票" width="180">
          <template #default="scope">
            <template v-if="scope.row.invoice_no">{{ scope.row.invoice_no }}</template>
            <el-tag v-else type="info" effect="plain" size="small">未登记</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="status_label" label="申请状态" width="120">
          <template #default="scope"><el-tag :type="statusType(scope.row.status)">{{ scope.row.status_label }}</el-tag></template>
        </el-table-column>
        <el-table-column prop="requested_by_name" label="申请人" width="120" />
        <el-table-column prop="approved_by_name" label="审批人" width="120" />
        <el-table-column prop="created_at" label="创建时间" min-width="170"><template #default="scope">{{ formatDateTime(scope.row.created_at) }}</template></el-table-column>
        <el-table-column label="操作" width="110" fixed="right">
          <template #default="scope">
            <el-button size="small" text type="primary" @click="openInvoiceDialog(scope.row)">登记发票</el-button>
          </template>
        </el-table-column>
      </el-table>
      <AppPagination v-model:page="requestPagination.page" v-model:page-size="requestPagination.pageSize" :total="requestTotal" :page-sizes="pageSizes" />
    </PageSection>

    <PageSection title="付款记录" description="经理审批通过后自动生成付款记录和资金流水，财务侧同步可见。">
      <el-table :data="pagedPayables" stripe>
        <el-table-column prop="merge_no" label="合并批次" width="140">
          <template #default="scope">{{ scope.row.merge_no || '-' }}</template>
        </el-table-column>
        <el-table-column prop="purchase_order_no" label="采购单号" width="160" />
        <el-table-column prop="amount" label="付款总额" width="120"><template #default="scope">{{ formatNumber(scope.row.amount) }}</template></el-table-column>
        <el-table-column prop="status_label" label="状态" width="120">
          <template #default="scope"><el-tag :type="statusType(scope.row.status)">{{ scope.row.status_label }}</el-tag></template>
        </el-table-column>
        <el-table-column prop="paid_by_name" label="登记人" width="120" />
        <el-table-column prop="paid_at" label="时间" min-width="170"><template #default="scope">{{ formatDateTime(scope.row.paid_at) }}</template></el-table-column>
        <el-table-column prop="remark_display" label="备注" min-width="220" show-overflow-tooltip />
      </el-table>
      <AppPagination v-model:page="payablePagination.page" v-model:page-size="payablePagination.pageSize" :total="payableTotal" :page-sizes="pageSizes" />
    </PageSection>

    <PageSection title="协同通知" description="采购、经理、财务之间的同步通知统一展示在最下方，不区分已读未读。">
      <el-table :data="pagedNotifications" stripe>
        <el-table-column prop="title" label="标题" min-width="200" />
        <el-table-column prop="content" label="内容" min-width="320" show-overflow-tooltip />
        <el-table-column prop="module" label="模块" width="100" />
        <el-table-column prop="created_at" label="时间" min-width="170"><template #default="scope">{{ formatDateTime(scope.row.created_at) }}</template></el-table-column>
      </el-table>
      <AppPagination v-model:page="notificationPagination.page" v-model:page-size="notificationPagination.pageSize" :total="notificationTotal" :page-sizes="pageSizes" />
    </PageSection>

    <el-dialog v-model="requestVisible" title="提交采购付款申请" width="920px">
      <el-form :model="requestForm" label-width="110px">
        <SettlementAccounts v-model:counterparty-account-id="requestForm.counterparty_account_id" :party-id="selectedOrder?.supplier_id" direction="payment" :show-company="false" counterparty-required />
        <el-form-item label="采购订单">
          <el-select v-model="requestForm.purchase_order_id" filterable style="width: 100%">
            <el-option v-for="item in paymentRequestableOrders" :key="item.id" :label="`${item.order_no} / ${item.supplier_name || '-'}`" :value="item.id" />
          </el-select>
        </el-form-item>
        <div v-if="selectedOrder" class="order-summary-card">
          <el-descriptions :column="2" border>
            <el-descriptions-item label="采购单号">{{ selectedOrder.order_no }}</el-descriptions-item>
            <el-descriptions-item label="供应商">{{ selectedOrder.supplier_name || '-' }}</el-descriptions-item>
            <el-descriptions-item label="到货状态">{{ selectedOrder.arrival_status_label || statusLabel(selectedOrder.arrival_status) }}</el-descriptions-item>
            <el-descriptions-item label="合同附件">{{ selectedOrder.contract_attachment_count || 0 }} 份</el-descriptions-item>
            <el-descriptions-item label="订单金额">{{ formatNumber(selectedOrder.total_amount) }}</el-descriptions-item>
            <el-descriptions-item label="未结算订单金额">{{ formatNumber(selectedOrder.remaining_amount) }}</el-descriptions-item>
            <el-descriptions-item label="订单摘要">{{ selectedOrder.item_summary || '-' }}</el-descriptions-item>
          </el-descriptions>
        </div>
        <el-table v-if="selectedOrder?.items?.length" :data="selectedOrder.items" stripe class="mt-16">
          <el-table-column prop="item_code" label="编码" width="140" />
          <el-table-column prop="item_name" label="物料/产品" min-width="180" />
          <el-table-column prop="display_specification" label="规格" width="140" />
          <el-table-column prop="quantity" label="订单数量" width="120">
            <template #default="scope">{{ formatNumber(scope.row.quantity) }}</template>
          </el-table-column>
          <el-table-column prop="received_quantity" label="已到货" width="120">
            <template #default="scope">{{ formatNumber(scope.row.received_quantity) }}</template>
          </el-table-column>
          <el-table-column prop="unit_price" label="单价" width="120">
            <template #default="scope">{{ formatNumber(scope.row.unit_price) }}</template>
          </el-table-column>
          <el-table-column prop="total_amount" label="金额" width="120">
            <template #default="scope">{{ formatNumber(scope.row.total_amount) }}</template>
          </el-table-column>
        </el-table>
        <el-form-item class="mt-16" label="订单金额">
          <el-input-number v-model="requestForm.order_amount" :min="0" :precision="4" style="width: 100%" />
        </el-form-item>
        <el-form-item label="其他费用">
          <el-input-number v-model="requestForm.other_fee_amount" :min="0" :precision="4" style="width: 100%" />
        </el-form-item>
        <el-form-item label="申请总金额">
          <el-input-number v-model="requestForm.requested_amount" :min="0.0001" :precision="4" style="width: 100%" />
        </el-form-item>
        <el-form-item label="付款说明">
          <el-input v-model="requestForm.payment_detail_remark" type="textarea" :rows="3" />
        </el-form-item>
        <el-form-item label="指定财务">
          <el-select v-model="requestForm.assigned_finance_user_id" clearable placeholder="不指定则通知全部财务" style="width: 100%">
            <el-option v-for="u in financeUsers" :key="u.id" :label="u.full_name" :value="u.id" />
          </el-select>
        </el-form-item>
        <div v-if="selectedOrder" class="mt-16">
          <AttachmentUploader business-type="purchase_order" :business-id="selectedOrder.id" :preset-types="['合同', '发票', '付款凭证', '到货单']" />
        </div>
      </el-form>
      <template #footer>
        <el-button @click="requestVisible = false">取消</el-button>
        <el-button type="primary" @click="submitRequest">提交申请</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="mergedRequestVisible" title="提交合并付款申请" width="980px">
      <div v-if="mergedSupplierName" class="order-summary-card">
        <el-descriptions :column="2" border>
          <el-descriptions-item label="供应商">{{ mergedSupplierName }}</el-descriptions-item>
          <el-descriptions-item label="订单数量">{{ mergedRequestForm.items.length }}</el-descriptions-item>
          <el-descriptions-item label="申请总金额">{{ formatNumber(mergedRequestedAmount) }}</el-descriptions-item>
          <el-descriptions-item label="说明">仅支持同一供应商的未完成采购订单合并申请</el-descriptions-item>
        </el-descriptions>
      </div>
      <el-table :data="mergedRequestForm.items" stripe border>
        <el-table-column type="expand" width="52">
          <template #default="scope">
            <el-table :data="scope.row.items || []" border size="small">
              <el-table-column prop="item_code" label="编码" width="140" />
              <el-table-column prop="item_name" label="物料/产品" min-width="180" />
              <el-table-column prop="display_specification" label="规格" width="140" />
              <el-table-column prop="quantity" label="订单数量" width="120">
                <template #default="innerScope">{{ formatNumber(innerScope.row.quantity) }}</template>
              </el-table-column>
              <el-table-column prop="received_quantity" label="已到货" width="120">
                <template #default="innerScope">{{ formatNumber(innerScope.row.received_quantity) }}</template>
              </el-table-column>
              <el-table-column prop="unit_price" label="单价" width="120">
                <template #default="innerScope">{{ formatNumber(innerScope.row.unit_price) }}</template>
              </el-table-column>
              <el-table-column prop="total_amount" label="金额" width="120">
                <template #default="innerScope">{{ formatNumber(innerScope.row.total_amount) }}</template>
              </el-table-column>
            </el-table>
          </template>
        </el-table-column>
        <el-table-column prop="order_no" label="采购单号" width="160" />
        <el-table-column prop="supplier_name" label="供应商" min-width="180" />
        <el-table-column prop="item_summary" label="订单摘要" min-width="220" show-overflow-tooltip />
        <el-table-column prop="remaining_amount" label="未结算订单金额" width="150">
          <template #default="scope">{{ formatNumber(scope.row.remaining_amount) }}</template>
        </el-table-column>
        <el-table-column label="本次订单金额" width="150">
          <template #default="scope">
            <el-input-number v-model="scope.row.order_amount" :min="0" :max="scope.row.remaining_amount" :precision="4" style="width: 100%" />
          </template>
        </el-table-column>
        <el-table-column label="其他费用" width="140">
          <template #default="scope">
            <el-input-number v-model="scope.row.other_fee_amount" :min="0" :precision="4" style="width: 100%" />
          </template>
        </el-table-column>
      </el-table>
      <el-form class="mt-16" :model="mergedRequestForm" label-width="100px">
        <SettlementAccounts v-model:counterparty-account-id="mergedRequestForm.counterparty_account_id" :party-id="mergedRequestForm.supplier_id" direction="payment" :show-company="false" counterparty-required />
        <el-form-item label="付款说明">
          <el-input v-model="mergedRequestForm.payment_detail_remark" type="textarea" :rows="3" />
        </el-form-item>
        <el-form-item label="指定财务">
          <el-select v-model="mergedRequestForm.assigned_finance_user_id" clearable placeholder="不指定则通知全部财务" style="width: 100%">
            <el-option v-for="u in financeUsers" :key="u.id" :label="u.full_name" :value="u.id" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="mergedRequestVisible = false">取消</el-button>
        <el-button type="primary" @click="submitMergedRequest">提交申请</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="invoiceVisible" title="登记采购付款发票" width="560px">
      <el-form v-if="activeInvoiceRequest" :model="invoiceForm" label-width="100px">
        <el-form-item label="申请单号">
          <span>{{ activeInvoiceRequest.request_no }}</span>
          <span class="field-label" style="margin-left:12px">采购单 {{ activeInvoiceRequest.purchase_order_no || '-' }}</span>
        </el-form-item>
        <el-form-item label="发票号">
          <el-input v-model="invoiceForm.invoice_no" placeholder="请输入发票号码" />
        </el-form-item>
        <el-form-item label="发票金额">
          <el-input-number v-model="invoiceForm.invoice_amount" :min="0" :precision="4" :controls="false" style="width:100%" placeholder="请输入发票金额" />
        </el-form-item>
        <el-form-item label="发票日期">
          <el-input v-model="invoiceForm.invoice_date" placeholder="格式 YYYY-MM-DD，如 2026-08-31" />
        </el-form-item>
        <el-form-item label="发票附件">
          <AttachmentUploader v-if="invoiceForm.invoice_attachment_id" business-type="purchase_order" :business-id="activeInvoiceRequest.purchase_order_id" :preset-types="['发票']" />
          <span v-else class="field-label">请在付款申请附件中上传发票后填写附件ID</span>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="invoiceVisible = false">取消</el-button>
        <el-button type="primary" :loading="invoiceSubmitting" @click="submitInvoice">保存发票信息</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/card/style/css'
import 'element-plus/es/components/col/style/css'
import 'element-plus/es/components/descriptions/style/css'
import 'element-plus/es/components/dialog/style/css'
import 'element-plus/es/components/form/style/css'
import 'element-plus/es/components/input-number/style/css'
import 'element-plus/es/components/input/style/css'
import 'element-plus/es/components/message/style/css'
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
import ElForm, { ElFormItem } from 'element-plus/es/components/form/index.mjs'
import ElInputNumber from 'element-plus/es/components/input-number/index.mjs'
import ElInput from 'element-plus/es/components/input/index.mjs'
import ElRow from 'element-plus/es/components/row/index.mjs'
import ElSelect, { ElOption } from 'element-plus/es/components/select/index.mjs'
import ElTable, { ElTableColumn } from 'element-plus/es/components/table/index.mjs'
import ElTag from 'element-plus/es/components/tag/index.mjs'

import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import { get, post, put } from '../../api/client'
import AttachmentUploader from '../../components/AttachmentUploader.vue'
import AppPagination from '../../components/AppPagination.vue'
import PageSection from '../../components/PageSection.vue'
import SettlementAccounts from '../../components/SettlementAccounts.vue'
import { useLocalPagination } from '../../composables/useLocalPagination'
import { useAuthStore } from '../../stores/auth'
import { formatDateTime, formatNumber, statusLabel, statusType } from '../../utils/format'

const auth = useAuthStore()
const router = useRouter()
const summary = reactive({})
const notifications = ref([])
const orders = ref([])
const paymentRequests = ref([])
const payables = ref([])
const requestVisible = ref(false)
const mergedRequestVisible = ref(false)
const selectedOrderRows = ref([])

const requestForm = reactive({
  counterparty_account_id: null,
  purchase_order_id: null,
  order_amount: 0,
  other_fee_amount: 0,
  requested_amount: 0,
  payment_detail_remark: '',
  assigned_finance_user_id: null
})
const financeUsers = ref([])
const mergedRequestForm = reactive({
  counterparty_account_id: null,
  supplier_id: null,
  payment_detail_remark: '',
  assigned_finance_user_id: null,
  items: []
})

const canSubmitRequest = computed(() => ['purchase', 'finance', 'system_admin'].includes(auth.user?.role_code))
const canCompleteRequest = computed(() => ['purchase', 'system_admin'].includes(auth.user?.role_code))
const paymentRequestableOrders = computed(() => orders.value.filter((item) => item.can_submit_payment_request))
const selectedOrder = computed(() => orders.value.find((item) => item.id === requestForm.purchase_order_id) || null)
const mergedSupplierName = computed(() => mergedRequestForm.items[0]?.supplier_name || '')
const mergedRequestedAmount = computed(() =>
  mergedRequestForm.items.reduce((sum, item) => sum + Number(item.order_amount || 0) + Number(item.other_fee_amount || 0), 0)
)
const completableRequestMap = computed(() => {
  const mapping = new Map()
  for (const row of paymentRequests.value || []) {
    if (row.status !== 'pending_completion') continue
    if (!mapping.has(row.purchase_order_id)) {
      mapping.set(row.purchase_order_id, row)
    }
  }
  return mapping
})

const { pagination: notificationPagination, total: notificationTotal, pageSizes, pagedItems: pagedNotifications } = useLocalPagination(notifications)
const { pagination: orderPagination, total: orderTotal, pagedItems: pagedOrders } = useLocalPagination(orders)
const { pagination: requestPagination, total: requestTotal, pagedItems: pagedRequests } = useLocalPagination(paymentRequests)
const { pagination: payablePagination, total: payableTotal, pagedItems: pagedPayables } = useLocalPagination(payables)

function resetRequestForm() {
  requestForm.purchase_order_id = null
  requestForm.order_amount = 0
  requestForm.other_fee_amount = 0
  requestForm.requested_amount = 0
  requestForm.assigned_finance_user_id = null
  requestForm.payment_detail_remark = ''
  requestForm.counterparty_account_id = null
}

function resetMergedRequestForm() {
  mergedRequestForm.supplier_id = null
  mergedRequestForm.payment_detail_remark = ''
  mergedRequestForm.counterparty_account_id = null
  mergedRequestForm.assigned_finance_user_id = null
  mergedRequestForm.items = []
}

async function load() {
  const data = await get('/api/purchases/settlements')
  Object.assign(summary, data.summary || {})
  if (data.finance_users && data.finance_users.length) {
    financeUsers.value = data.finance_users
  }
  notifications.value = data.notifications || []
  orders.value = data.orders || []
  paymentRequests.value = data.payment_requests || []
  payables.value = data.payables || []
  selectedOrderRows.value = []
}

function goOrder(orderId) {
  router.push({ path: '/purchases/orders', query: { focusId: String(orderId) } })
}

const invoiceVisible = ref(false)
const activeInvoiceRequest = ref(null)
const invoiceSubmitting = ref(false)
const invoiceForm = reactive({ invoice_no: '', invoice_amount: null, invoice_date: '', invoice_attachment_id: null })

function openInvoiceDialog(row) {
  activeInvoiceRequest.value = row
  invoiceForm.invoice_no = row.invoice_no || ''
  invoiceForm.invoice_amount = row.invoice_amount ?? null
  invoiceForm.invoice_date = row.invoice_date || ''
  invoiceForm.invoice_attachment_id = row.invoice_attachment_id || null
  invoiceVisible.value = true
}

async function submitInvoice() {
  if (!activeInvoiceRequest.value || invoiceSubmitting.value) return
  invoiceSubmitting.value = true
  try {
    await put(`/api/purchases/settlements/payment-requests/${activeInvoiceRequest.value.id}/invoice`, {
      invoice_no: invoiceForm.invoice_no || null,
      invoice_amount: invoiceForm.invoice_amount ?? null,
      invoice_date: invoiceForm.invoice_date || null,
      invoice_attachment_id: invoiceForm.invoice_attachment_id || null
    })
    ElMessage.success('发票信息已保存')
    invoiceVisible.value = false
    await load()
  } catch (error) {
    ElMessage.error(error.message || '发票信息保存失败')
  } finally {
    invoiceSubmitting.value = false
  }
}

function openRequestDialog(row = null) {
  if (row && !row.can_submit_payment_request) {
    ElMessage.warning(row.payment_request_block_reason || '当前采购订单尚未生效，不能申请付款')
    return
  }
  resetRequestForm()
  if (row) {
    requestForm.purchase_order_id = row.id
    requestForm.order_amount = Number(row.remaining_amount || row.total_amount || 0)
    requestForm.requested_amount = requestForm.order_amount
  }
  requestVisible.value = true
}

function paymentRequestSelectable(row) {
  return Boolean(row?.can_submit_payment_request)
}

function handleOrderSelectionChange(selection) {
  selectedOrderRows.value = selection || []
}

function openMergedRequestDialog() {
  if (selectedOrderRows.value.length < 2) {
    ElMessage.warning('请至少选择两张同一供应商的采购订单')
    return
  }
  const supplierIds = new Set(selectedOrderRows.value.map((item) => item.supplier_id).filter(Boolean))
  if (supplierIds.size !== 1) {
    ElMessage.warning('合并付款仅支持同一供应商的采购订单')
    return
  }
  mergedRequestForm.supplier_id = selectedOrderRows.value[0].supplier_id
  mergedRequestForm.payment_detail_remark = ''
  mergedRequestForm.counterparty_account_id = null
  mergedRequestForm.assigned_finance_user_id = null
  mergedRequestForm.items = selectedOrderRows.value.map((item) => ({
    purchase_order_id: item.id,
    order_no: item.order_no,
    supplier_name: item.supplier_name,
    item_summary: item.item_summary,
    items: item.items || [],
    remaining_amount: Number(item.remaining_amount || 0),
    order_amount: Number(item.remaining_amount || 0),
    other_fee_amount: 0
  }))
  mergedRequestVisible.value = true
}

async function submitRequest() {
  if (!requestForm.counterparty_account_id) { ElMessage.warning('请选择供应商收款账户'); return }
  if (!requestForm.purchase_order_id || Number(requestForm.requested_amount || 0) <= 0) {
    ElMessage.warning('请填写完整的付款申请信息')
    return
  }
  try {
    await post('/api/purchases/settlements/payment-requests', {
      purchase_order_id: requestForm.purchase_order_id,
      order_amount: Number(requestForm.order_amount || 0),
      other_fee_amount: Number(requestForm.other_fee_amount || 0),
      requested_amount: Number(requestForm.requested_amount || 0),
      counterparty_account_id: requestForm.counterparty_account_id,
      payment_detail_remark: requestForm.payment_detail_remark,
      assigned_finance_user_id: requestForm.assigned_finance_user_id || null
    })
    ElMessage.success('采购付款申请已提交')
    requestVisible.value = false
    resetRequestForm()
    await load()
  } catch (error) {
    ElMessage.error(error.message || '采购付款申请提交失败')
  }
}

async function submitMergedRequest() {
  if (!mergedRequestForm.counterparty_account_id) { ElMessage.warning('请选择供应商收款账户'); return }
  if (mergedRequestForm.items.length < 2 || !mergedRequestForm.supplier_id) {
    ElMessage.warning('请先选择同一供应商的多张采购订单')
    return
  }
  const payload = {
    supplier_id: mergedRequestForm.supplier_id,
    counterparty_account_id: mergedRequestForm.counterparty_account_id,
      payment_detail_remark: mergedRequestForm.payment_detail_remark,
    assigned_finance_user_id: mergedRequestForm.assigned_finance_user_id || null,
    items: mergedRequestForm.items.map((item) => ({
      purchase_order_id: item.purchase_order_id,
      order_amount: Number(item.order_amount || 0),
      other_fee_amount: Number(item.other_fee_amount || 0)
    }))
  }
  try {
    await post('/api/purchases/settlements/payment-requests/merged', payload)
    ElMessage.success('合并付款申请已提交')
    mergedRequestVisible.value = false
    resetMergedRequestForm()
    await load()
  } catch (error) {
    ElMessage.error(error.message || '合并付款申请提交失败')
  }
}

async function completeRequest(row) {
  await post(`/api/purchases/settlements/payment-requests/${row.id}/complete`, { completion_remark: '采购侧确认流程完成' })
  ElMessage.success('付款申请已确认完成')
  await load()
}

function completableRequestForOrder(orderId) {
  return completableRequestMap.value.get(orderId) || null
}

async function completeOrderRequest(orderRow) {
  const requestRow = completableRequestForOrder(orderRow.id)
  if (!requestRow) {
    ElMessage.warning('当前订单暂无可确认完成的付款申请')
    return
  }
  await completeRequest(requestRow)
}

watch(selectedOrder, (row) => {
  if (!row) return
  if (!requestForm.order_amount) {
    requestForm.order_amount = Number(row.remaining_amount || row.total_amount || 0)
  }
  requestForm.requested_amount = Number(requestForm.order_amount || 0) + Number(requestForm.other_fee_amount || 0)
})

watch(() => requestForm.other_fee_amount, () => {
  requestForm.requested_amount = Number(requestForm.order_amount || 0) + Number(requestForm.other_fee_amount || 0)
})

watch(() => requestForm.order_amount, () => {
  requestForm.requested_amount = Number(requestForm.order_amount || 0) + Number(requestForm.other_fee_amount || 0)
})

onMounted(load)
</script>

<style scoped>
.page-stack { display: flex; flex-direction: column; gap: 16px; }
.summary-row { margin-bottom: 0; }
.summary-label { color: #909399; margin-bottom: 8px; }
.summary-value { font-size: 28px; font-weight: 700; color: #0f5c90; }
.order-summary-card { margin-bottom: 16px; }
.mt-16 { margin-top: 16px; }
</style>
