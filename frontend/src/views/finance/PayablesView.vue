<template>
  <div class="page-stack">
    <el-row :gutter="12" class="summary-row">
      <el-col :span="6"><el-card shadow="hover"><div class="summary-label">待出账申请</div><div class="summary-value">{{ pendingRequestCount }}</div></el-card></el-col>
      <el-col :span="6"><el-card shadow="hover"><div class="summary-label">付款记录</div><div class="summary-value">{{ summary.record_count || 0 }}</div></el-card></el-col>
      <el-col :span="6"><el-card shadow="hover"><div class="summary-label">待出账金额</div><div class="summary-value">{{ formatMoney(pendingRequestAmount) }}</div></el-card></el-col>
      <el-col :span="6"><el-card shadow="hover"><div class="summary-label">已付总额</div><div class="summary-value">{{ formatMoney(totalPaidAmount) }}</div></el-card></el-col>
    </el-row>

    <div ref="recordSectionRef">
      <PageSection title="采购付款申请" description="仅展示采购人员已提交的付款申请。财务从申请中登记出账，未申请的采购订单不会进入应付。">
        <template #actions>
          <el-date-picker v-model="filters.dateRange" type="daterange" range-separator="至" start-placeholder="开始日期" end-placeholder="结束日期" value-format="YYYY-MM-DD" @change="load" />
          <el-button @click="load">刷新</el-button>
        </template>
        <el-table :data="pagedPaymentRequests" stripe :row-class-name="paymentRequestRowClassName">
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
                  <template #default="innerScope">{{ formatMoney(innerScope.row.unit_price) }}</template>
                </el-table-column>
                <el-table-column prop="total_amount" label="金额" width="120">
                  <template #default="innerScope">{{ formatMoney(innerScope.row.total_amount) }}</template>
                </el-table-column>
              </el-table>
            </template>
          </el-table-column>
          <el-table-column prop="merge_no" label="合并批次" width="140">
            <template #default="scope">{{ scope.row.merge_no || '-' }}</template>
          </el-table-column>
          <el-table-column prop="company_account_label" label="企业账户" min-width="240" show-overflow-tooltip />
          <el-table-column prop="request_no" label="申请单号" width="160" />
          <el-table-column prop="purchase_order_no" label="采购单号" width="160" />
          <el-table-column prop="supplier_name" label="供应商" min-width="180" />
          <el-table-column prop="arrival_status_label" label="到货状态" width="120">
            <template #default="scope">
              <el-tag :type="scope.row.arrival_status === 'arrived' ? 'success' : 'warning'">{{ scope.row.arrival_status_label || statusLabel(scope.row.arrival_status) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="status_label" label="申请状态" width="120">
            <template #default="scope">
              <el-tag :type="statusType(scope.row.status)">{{ scope.row.status_label || statusLabel(scope.row.status) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="purchase_total_amount" label="采购单总额" width="130">
            <template #default="scope">{{ formatMoney(scope.row.purchase_total_amount) }}</template>
          </el-table-column>
          <el-table-column prop="order_amount" label="本次申请订单款" width="150">
            <template #default="scope">{{ formatMoney(scope.row.order_amount) }}</template>
          </el-table-column>
          <el-table-column prop="other_fee_amount" label="其他费用" width="120">
            <template #default="scope">{{ formatMoney(scope.row.other_fee_amount) }}</template>
          </el-table-column>
          <el-table-column prop="requested_amount" label="申请总额" width="120">
            <template #default="scope">{{ formatMoney(scope.row.requested_amount) }}</template>
          </el-table-column>
          <el-table-column prop="actual_amount" label="实际出账" width="120">
            <template #default="scope">{{ formatMoney(scope.row.actual_amount) }}</template>
          </el-table-column>
          <el-table-column prop="requested_by_name" label="申请人" width="120" />
          <el-table-column prop="approved_by_name" label="审批人" width="120" />
          <el-table-column prop="created_at" label="申请时间" min-width="170">
            <template #default="scope">{{ formatDateTime(scope.row.created_at) }}</template>
          </el-table-column>
          <el-table-column prop="payment_detail_remark_display" label="申请说明" min-width="220" show-overflow-tooltip />
          <el-table-column label="操作" width="180" fixed="right">
            <template #default="scope">
              <el-button size="small" text type="primary" @click="goToPurchaseOrder(scope.row.purchase_order_id)">订单</el-button>
              <el-button
                v-if="canRegisterPayment && scope.row.status === 'pending_finance_payment'"
                size="small"
                text
                type="success"
                @click="openPaymentRegister(scope.row)"
              >
                登记出账
              </el-button>
            </template>
          </el-table-column>
        </el-table>
        <AppPagination
          v-model:page="requestPagination.page"
          v-model:page-size="requestPagination.pageSize"
          :total="requestTotal"
          :page-sizes="pageSizes"
        />
      </PageSection>
    </div>

    <div ref="paymentRecordSectionRef">
      <PageSection title="付款记录" description="付款按采购订单累计；小于订单总额记为部分结算，达到或超过订单总额记为已结算。">
        <template #actions>
          <el-button @click="exportRecords">导出付款记录</el-button>
        </template>

        <el-table :data="pagedRecords" stripe :row-class-name="recordRowClassName">
          <el-table-column prop="company_account_label" label="企业付款账户" min-width="240" show-overflow-tooltip />
          <el-table-column prop="counterparty_account_label" label="供应商收款账户" min-width="240" show-overflow-tooltip />
          <el-table-column prop="merge_no" label="合并批次" width="140">
            <template #default="scope">{{ scope.row.merge_no || '-' }}</template>
          </el-table-column>
          <el-table-column prop="purchase_order_no" label="采购单号" width="160" />
          <el-table-column prop="status_label" label="付款状态" width="120">
            <template #default="scope">
              <el-tag :type="statusType(scope.row.status)">{{ scope.row.status_label || statusLabel(scope.row.status) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="amount" label="付款金额" width="120">
            <template #default="scope">{{ formatMoney(scope.row.amount) }}</template>
          </el-table-column>
          <el-table-column prop="paid_at" label="付款时间" min-width="180">
            <template #default="scope">{{ formatDateTime(scope.row.paid_at) }}</template>
          </el-table-column>
          <el-table-column prop="paid_by_name" label="处理人" width="120" />
          <el-table-column prop="remark_display" label="备注" min-width="220" show-overflow-tooltip>
            <template #default="scope">{{ scope.row.remark_display || formatRemark(scope.row.remark) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="120" fixed="right">
            <template #default="scope">
              <el-button size="small" text type="primary" @click="goToPurchaseOrder(scope.row.purchase_order_id)">订单</el-button>
            </template>
          </el-table-column>
        </el-table>
        <AppPagination
          v-model:page="recordPagination.page"
          v-model:page-size="recordPagination.pageSize"
          :total="recordTotal"
          :page-sizes="pageSizes"
        />
      </PageSection>
    </div>

    <el-dialog v-model="paymentRegisterVisible" title="登记采购出账" width="760px">
      <el-descriptions v-if="activePaymentRequest" :column="2" border class="mb-16">
        <el-descriptions-item label="申请单号">{{ activePaymentRequest.request_no }}</el-descriptions-item>
        <el-descriptions-item label="采购订单">{{ activePaymentRequest.purchase_order_no }}</el-descriptions-item>
        <el-descriptions-item label="供应商">{{ activePaymentRequest.supplier_name || '-' }}</el-descriptions-item>
        <el-descriptions-item label="到货状态">{{ activePaymentRequest.arrival_status_label || statusLabel(activePaymentRequest.arrival_status) }}</el-descriptions-item>
        <el-descriptions-item label="采购单总额">{{ formatMoney(activePaymentRequest.purchase_total_amount) }}</el-descriptions-item>
        <el-descriptions-item label="本次申请订单款">{{ formatMoney(activePaymentRequest.order_amount) }}</el-descriptions-item>
        <el-descriptions-item label="其他费用">{{ formatMoney(activePaymentRequest.other_fee_amount) }}</el-descriptions-item>
        <el-descriptions-item label="申请总额">{{ formatMoney(activePaymentRequest.requested_amount) }}</el-descriptions-item>
        <el-descriptions-item label="申请人">{{ activePaymentRequest.requested_by_name || '-' }}</el-descriptions-item>
        <el-descriptions-item label="申请说明" :span="2">{{ activePaymentRequest.payment_detail_remark_display || activePaymentRequest.payment_detail_remark || '-' }}</el-descriptions-item>
      </el-descriptions>
      <el-table v-if="activePaymentRequest?.items?.length" :data="activePaymentRequest.items" border size="small" class="mb-16">
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
          <template #default="scope">{{ formatMoney(scope.row.unit_price) }}</template>
        </el-table-column>
        <el-table-column prop="total_amount" label="金额" width="120">
          <template #default="scope">{{ formatMoney(scope.row.total_amount) }}</template>
        </el-table-column>
      </el-table>
      <el-table v-if="activePaymentRequest?.attachments?.length" :data="activePaymentRequest.attachments" border size="small" class="mb-16">
        <el-table-column prop="attachment_type" label="附件类型" width="140" />
        <el-table-column prop="original_name" label="文件名" min-width="220" />
        <el-table-column label="操作" width="100">
          <template #default="scope">
            <el-button size="small" text type="primary" @click="openAttachment(scope.row.id)">查看</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-form :model="paymentRegisterForm" label-width="110px">
        <SettlementAccounts v-model:company-account-id="paymentRegisterForm.company_account_id" v-model:counterparty-account-id="paymentRegisterForm.counterparty_account_id" :party-id="activePaymentRequest?.supplier_id" :counterparty-readonly="Boolean(activePaymentRequest?.counterparty_account_id)" direction="payment" />
        <el-form-item label="实际出账金额">
          <el-input-number v-model="paymentRegisterForm.actual_amount" :min="0.0001" :precision="4" style="width: 100%" />
        </el-form-item>
        <el-form-item label="付款时间">
          <el-date-picker v-model="paymentRegisterForm.paid_at" type="datetime" value-format="YYYY-MM-DDTHH:mm:ss" style="width: 100%" />
        </el-form-item>
        <el-form-item label="银行回单">
          <input type="file" accept=".jpg,.jpeg,.png,.pdf,.xls,.xlsx" @change="(event) => handlePaymentFileChange(event, 'single')" />
          <el-button v-if="paymentRegisterForm.bank_receipt_attachment_id" class="ml-8" text type="primary" @click="openAttachment(paymentRegisterForm.bank_receipt_attachment_id)">查看回单</el-button>
        </el-form-item>
        <el-form-item label="处理说明">
          <el-input v-model="paymentRegisterForm.remark" type="textarea" :rows="3" placeholder="可填写付款凭证、差异说明或审批意见" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="paymentRegisterVisible = false">取消</el-button>
        <DelayedActionButton type="primary" :loading="paymentRegisterSubmitting" @click="submitPaymentRegister">确认登记</DelayedActionButton>
      </template>
    </el-dialog>

    <el-dialog v-model="mergedPaymentRequestVisible" title="确认合并付款申请" width="1040px">
      <el-descriptions v-if="mergedPaymentRequestSupplierName" :column="2" border class="mb-16">
        <el-descriptions-item label="供应商">{{ mergedPaymentRequestSupplierName }}</el-descriptions-item>
        <el-descriptions-item label="申请数量">{{ mergedPaymentRequestForm.items.length }}</el-descriptions-item>
        <el-descriptions-item label="申请总额">{{ formatNumber(mergedPaymentRequestRequestedTotal) }}</el-descriptions-item>
        <el-descriptions-item label="实际总额">{{ formatNumber(mergedPaymentRequestActualTotal) }}</el-descriptions-item>
      </el-descriptions>
      <el-table :data="mergedPaymentRequestForm.items" stripe border>
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
                <template #default="innerScope">{{ formatMoney(innerScope.row.unit_price) }}</template>
              </el-table-column>
              <el-table-column prop="total_amount" label="金额" width="120">
                <template #default="innerScope">{{ formatMoney(innerScope.row.total_amount) }}</template>
              </el-table-column>
            </el-table>
          </template>
        </el-table-column>
        <el-table-column prop="request_no" label="申请单号" width="160" />
        <el-table-column prop="purchase_order_no" label="采购单号" width="160" />
        <el-table-column prop="requested_amount" label="申请金额" width="120">
          <template #default="scope">{{ formatMoney(scope.row.requested_amount) }}</template>
        </el-table-column>
        <el-table-column prop="order_amount" label="订单金额" width="120">
          <template #default="scope">{{ formatMoney(scope.row.order_amount) }}</template>
        </el-table-column>
        <el-table-column prop="other_fee_amount" label="其他费用" width="120">
          <template #default="scope">{{ formatMoney(scope.row.other_fee_amount) }}</template>
        </el-table-column>
        <el-table-column label="实际出账" width="160">
          <template #default="scope">
            <el-input-number v-model="scope.row.actual_amount" :min="0.0001" :precision="4" style="width: 100%" />
          </template>
        </el-table-column>
      </el-table>
      <el-form class="mt-16" :model="mergedPaymentRequestForm" label-width="100px">
        <SettlementAccounts v-model:company-account-id="mergedPaymentRequestForm.company_account_id" v-model:counterparty-account-id="mergedPaymentRequestForm.counterparty_account_id" :party-id="mergedPaymentRequestForm.items[0]?.supplier_id" :counterparty-readonly="Boolean(mergedPaymentRequestForm.items[0]?.counterparty_account_id)" direction="payment" />
        <el-form-item label="付款时间">
          <el-date-picker v-model="mergedPaymentRequestForm.paid_at" type="datetime" value-format="YYYY-MM-DDTHH:mm:ss" style="width: 100%" />
        </el-form-item>
        <el-form-item label="银行回单">
          <input type="file" accept=".jpg,.jpeg,.png,.pdf,.xls,.xlsx" @change="(event) => handlePaymentFileChange(event, 'mergedRequest')" />
          <el-button v-if="mergedPaymentRequestForm.bank_receipt_attachment_id" class="ml-8" text type="primary" @click="openAttachment(mergedPaymentRequestForm.bank_receipt_attachment_id)">查看回单</el-button>
        </el-form-item>
        <el-form-item label="处理说明">
          <el-input v-model="mergedPaymentRequestForm.remark" type="textarea" :rows="3" placeholder="可填写批次付款说明、差异说明或银行流水信息" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="mergedPaymentRequestVisible = false">取消</el-button>
        <DelayedActionButton type="primary" :loading="paymentRegisterSubmitting" @click="submitMergedPaymentRequestRegister">确认登记</DelayedActionButton>
      </template>
    </el-dialog>


  </div>
</template>

<script setup>
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/card/style/css'
import 'element-plus/es/components/col/style/css'
import 'element-plus/es/components/date-picker/style/css'
import 'element-plus/es/components/descriptions/style/css'
import 'element-plus/es/components/dialog/style/css'
import 'element-plus/es/components/form/style/css'
import 'element-plus/es/components/input/style/css'
import 'element-plus/es/components/input-number/style/css'
import 'element-plus/es/components/message/style/css'
import 'element-plus/es/components/row/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/tag/style/css'

import { ElMessage } from 'element-plus/es/components/message/index.mjs'
import ElButton from 'element-plus/es/components/button/index.mjs'
import ElCard from 'element-plus/es/components/card/index.mjs'
import ElCol from 'element-plus/es/components/col/index.mjs'
import ElDatePicker from 'element-plus/es/components/date-picker/index.mjs'
import ElDescriptions, { ElDescriptionsItem } from 'element-plus/es/components/descriptions/index.mjs'
import ElDialog from 'element-plus/es/components/dialog/index.mjs'
import ElForm, { ElFormItem } from 'element-plus/es/components/form/index.mjs'
import ElInput from 'element-plus/es/components/input/index.mjs'
import ElInputNumber from 'element-plus/es/components/input-number/index.mjs'
import ElRow from 'element-plus/es/components/row/index.mjs'
import ElTable, { ElTableColumn } from 'element-plus/es/components/table/index.mjs'
import ElTag from 'element-plus/es/components/tag/index.mjs'

import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { get, post, upload } from '../../api/client'
import AppPagination from '../../components/AppPagination.vue'
import DelayedActionButton from '../../components/DelayedActionButton.vue'
import PageSection from '../../components/PageSection.vue'
import SettlementAccounts from '../../components/SettlementAccounts.vue'
import { useLocalPagination } from '../../composables/useLocalPagination'
import { parseFocusId } from '../../composables/useRouteFocus'
import { useAuthStore } from '../../stores/auth'
import { exportRowsToCsv } from '../../utils/export'
import { formatDateTime, formatNumber, formatRemark, statusLabel, statusType, formatMoney } from '../../utils/format'

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()

const recordSectionRef = ref(null)
const paymentRecordSectionRef = ref(null)
const summary = reactive({})
const paymentRequests = ref([])
const records = ref([])
const focusedRecordId = ref(null)
const focusedPaymentRequestId = ref(null)
const paymentRegisterVisible = ref(false)
const mergedPaymentRequestVisible = ref(false)
const activePaymentRequest = ref(null)
const paymentRegisterForm = reactive({
  company_account_id: null,
  counterparty_account_id: null,
  actual_amount: 0,
  remark: '',
  bank_receipt_attachment_id: null,
  paid_at: ''
})
const mergedPaymentRequestForm = reactive({
  company_account_id: null,
  counterparty_account_id: null,
  items: [],
  remark: '',
  bank_receipt_attachment_id: null,
  paid_at: ''
})
const paymentRegisterSubmitting = ref(false)
const selectedPaymentFiles = reactive({ single: null, mergedRequest: null })
const canRegisterPayment = computed(() => ['finance', 'finance_super', 'system_admin'].includes(auth.user?.role_code))
const pendingRequestCount = computed(() => paymentRequests.value.filter((item) => item.status === 'pending_finance_payment').length)
const pendingRequestAmount = computed(() => paymentRequests.value.filter((item) => item.status === 'pending_finance_payment').reduce((sum, item) => sum + Number(item.requested_amount || 0), 0))
const totalPaidAmount = computed(() => records.value.reduce((sum, item) => sum + Number(item.amount || 0), 0))
const mergedPaymentRequestSupplierName = computed(() => mergedPaymentRequestForm.items[0]?.supplier_name || '')
const mergedPaymentRequestRequestedTotal = computed(() =>
  mergedPaymentRequestForm.items.reduce((sum, item) => sum + Number(item.requested_amount || 0), 0)
)
const mergedPaymentRequestActualTotal = computed(() =>
  mergedPaymentRequestForm.items.reduce((sum, item) => sum + Number(item.actual_amount || 0), 0)
)

const {
  pagination: requestPagination,
  total: requestTotal,
  pageSizes,
  pagedItems: pagedPaymentRequests,
  ensureVisible: ensureRequestVisible
} = useLocalPagination(paymentRequests)
const {
  pagination: recordPagination,
  total: recordTotal,
  pagedItems: pagedRecords,
  ensureVisible: ensureRecordVisible
} = useLocalPagination(records)

function recordRowClassName({ row }) {
  return focusedRecordId.value === row.id ? 'is-focused-row' : ''
}

function paymentRequestRowClassName({ row }) {
  return focusedPaymentRequestId.value === row.id ? 'is-focused-row' : ''
}

async function scrollToSection(sectionRef) {
  await nextTick()
  const target = sectionRef.value?.$el || sectionRef.value
  target?.scrollIntoView?.({ behavior: 'smooth', block: 'start' })
}

async function applyFocus() {
  focusedRecordId.value = null
  focusedPaymentRequestId.value = null
  const focusId = parseFocusId(route.query.focusId)
  const focusType = String(route.query.focusType || '')
  if (!focusId) return
  if (focusType === 'purchase_payment_request' && paymentRequests.value.some((item) => item.id === focusId)) {
    focusedPaymentRequestId.value = focusId
    ensureRequestVisible((item) => item.id === focusId)
    await scrollToSection(recordSectionRef)
    const target = paymentRequests.value.find((item) => item.id === focusId)
    if (target && canRegisterPayment.value && target.status === 'pending_finance_payment') {
      openPaymentRegister(target)
    }
    return
  }
  if ((focusType === 'payable_record' || !focusType) && records.value.some((item) => item.id === focusId)) {
    focusedRecordId.value = focusId
    ensureRecordVisible((item) => item.id === focusId)
    await scrollToSection(paymentRecordSectionRef)
    return
  }
  if (focusType === 'purchase_order') {
    const target = paymentRequests.value.find((item) => item.purchase_order_id === focusId)
    if (target) {
      focusedPaymentRequestId.value = target.id
      ensureRequestVisible((item) => item.id === target.id)
      await scrollToSection(recordSectionRef)
    }
  }
}

// 日期区间筛选(后端 /api/finance/payables 已支持 date_from/date_to)
const filters = reactive({ dateRange: [] })

function payablesUrl() {
  const params = new URLSearchParams()
  if (Array.isArray(filters.dateRange) && filters.dateRange.length === 2) {
    if (filters.dateRange[0]) params.set('date_from', filters.dateRange[0])
    if (filters.dateRange[1]) params.set('date_to', filters.dateRange[1])
  }
  const query = params.toString()
  return `/api/finance/payables${query ? `?${query}` : ''}`
}

async function load() {
  const data = await get(payablesUrl())
  Object.assign(summary, data.summary || {})
  paymentRequests.value = data.payment_requests || []
  records.value = data.records || []

  await applyFocus()
}

function goToPurchaseOrder(orderId) {
  if (!orderId) return
  router.push({ path: '/purchases/orders', query: { focusId: String(orderId) } })
}

function openAttachment(id) {
  if (!id) return
  window.open(`/api/files/${id}`, '_blank')
}

function handlePaymentFileChange(event, target) {
  selectedPaymentFiles[target] = event.target.files?.[0] || null
}

async function uploadPaymentFileIfNeeded(target, businessType, businessId) {
  const file = selectedPaymentFiles[target]
  if (!file) return null
  const formData = new FormData()
  formData.append('business_type', businessType)
  formData.append('business_id', String(businessId || 0))
  formData.append('attachment_type', '银行回单')
  formData.append('file', file)
  const result = await upload('/api/files/upload', formData)
  selectedPaymentFiles[target] = null
  return result.id
}

function openPaymentRegister(row) {
  if (row.merge_no) {
    const relatedRows = paymentRequests.value.filter((item) => item.merge_no === row.merge_no && item.status === 'pending_finance_payment')
    if (relatedRows.length > 1) {
      mergedPaymentRequestForm.items = relatedRows.map((item) => ({
        id: item.id,
        request_no: item.request_no,
        purchase_order_id: item.purchase_order_id,
        purchase_order_no: item.purchase_order_no,
        supplier_id: item.supplier_id,
        counterparty_account_id: item.counterparty_account_id,
        supplier_name: item.supplier_name,
        requested_amount: Number(item.requested_amount || 0),
        order_amount: Number(item.order_amount || 0),
        other_fee_amount: Number(item.other_fee_amount || 0),
        actual_amount: Number(item.requested_amount || item.actual_amount || 0),
        items: item.items || []
      }))
      mergedPaymentRequestForm.remark = row.payment_detail_remark_display || row.payment_detail_remark || ''
      mergedPaymentRequestForm.company_account_id = row.company_account_id || null
      mergedPaymentRequestForm.counterparty_account_id = row.counterparty_account_id || null
      mergedPaymentRequestVisible.value = true
      return
    }
  }
  activePaymentRequest.value = row
  paymentRegisterForm.actual_amount = Number(row.requested_amount || row.actual_amount || 0)
  paymentRegisterForm.remark = row.payment_detail_remark_display || row.payment_detail_remark || ''
  paymentRegisterForm.company_account_id = row.company_account_id || null
  paymentRegisterForm.counterparty_account_id = row.counterparty_account_id || null
  paymentRegisterForm.bank_receipt_attachment_id = row.bank_receipt_attachment_id || null
  paymentRegisterForm.paid_at = row.paid_at || currentDateTime()
  selectedPaymentFiles.single = null
  paymentRegisterVisible.value = true
}

function currentDateTime() {
  const date = new Date()
  const pad = (value) => String(value).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}:00`
}

async function submitPaymentRegister() {
  if (!paymentRegisterForm.company_account_id) { ElMessage.warning('请选择企业收付款账户'); return }
  if (paymentRegisterSubmitting.value) return
  if (!activePaymentRequest.value || Number(paymentRegisterForm.actual_amount || 0) <= 0) {
    ElMessage.warning('请填写实际出账金额')
    return
  }
  paymentRegisterSubmitting.value = true
  try {
    const uploadedId = await uploadPaymentFileIfNeeded('single', 'purchase_payment_request', activePaymentRequest.value.id)
    await post(`/api/finance/payables/payment-requests/${activePaymentRequest.value.id}/register`, {
      purchase_payment_request_id: activePaymentRequest.value.id,
      actual_amount: paymentRegisterForm.actual_amount,
      company_account_id: paymentRegisterForm.company_account_id,
      counterparty_account_id: paymentRegisterForm.counterparty_account_id,
      remark: paymentRegisterForm.remark,
      bank_receipt_attachment_id: uploadedId || paymentRegisterForm.bank_receipt_attachment_id || null,
      paid_at: paymentRegisterForm.paid_at || null
    })
    ElMessage.success('采购出账已登记')
    paymentRegisterVisible.value = false
    await load()
  } catch (error) {
    ElMessage.error(error.message || '登记出账失败')
  } finally {
    paymentRegisterSubmitting.value = false
  }
}

async function submitMergedPaymentRequestRegister() {
  if (!mergedPaymentRequestForm.company_account_id) { ElMessage.warning('请选择企业收付款账户'); return }
  if (paymentRegisterSubmitting.value) return
  if (!mergedPaymentRequestForm.items.length) {
    ElMessage.warning('当前没有可登记的付款申请')
    return
  }
  paymentRegisterSubmitting.value = true
  try {
    const uploadedId = await uploadPaymentFileIfNeeded('mergedRequest', 'purchase_payment_request', mergedPaymentRequestForm.items[0]?.id)
    await post('/api/finance/payables/payment-requests/merged-register', {
      company_account_id: mergedPaymentRequestForm.company_account_id,
      counterparty_account_id: mergedPaymentRequestForm.counterparty_account_id,
      remark: mergedPaymentRequestForm.remark,
      bank_receipt_attachment_id: uploadedId || mergedPaymentRequestForm.bank_receipt_attachment_id || null,
      paid_at: mergedPaymentRequestForm.paid_at || null,
      items: mergedPaymentRequestForm.items.map((item) => ({
        purchase_payment_request_id: item.id,
        actual_amount: Number(item.actual_amount || 0)
      }))
    })
    ElMessage.success('合并付款申请已登记出账')
    mergedPaymentRequestVisible.value = false
    mergedPaymentRequestForm.items = []
    mergedPaymentRequestForm.remark = ''
    await load()
  } catch (error) {
    ElMessage.error(error.message || '合并付款申请登记失败')
  } finally {
    paymentRegisterSubmitting.value = false
  }
}

function exportRecords() {
  exportRowsToCsv('finance_payable_records', records.value)
}

onMounted(load)

watch(
  () => [route.query.focusId, route.query.focusType, route.query.mode],
  async () => {
    await load()
  }
)
</script>

<style scoped>
.page-stack { display: flex; flex-direction: column; gap: 16px; }
.summary-row { margin-bottom: 0; }
.filters-row { margin-bottom: 16px; }
.summary-label { color: #909399; margin-bottom: 8px; }
.summary-value { font-size: 28px; font-weight: 700; color: #0f5c90; }
.order-summary-card { margin-bottom: 16px; }
.mb-16 { margin-bottom: 16px; }
.mt-16 { margin-top: 16px; }
.page-stack :deep(.is-focused-row) > td { background: #fff8e1 !important; }
</style>
