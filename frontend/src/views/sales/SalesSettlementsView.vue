<template>
  <div class="page-stack">
    <el-row :gutter="12" class="summary-row">
      <el-col :span="6"><el-card shadow="hover"><div class="summary-label">未结清订单</div><div class="summary-value">{{ summary.order_count || 0 }}</div></el-card></el-col>
      <el-col :span="6"><el-card shadow="hover"><div class="summary-label">收款确认申请</div><div class="summary-value">{{ summary.receipt_request_count || 0 }}</div></el-card></el-col>
      <el-col :span="6"><el-card shadow="hover"><div class="summary-label">回款记录</div><div class="summary-value">{{ summary.receivable_count || 0 }}</div></el-card></el-col>
      <el-col :span="6"><el-card shadow="hover"><div class="summary-label">协同通知</div><div class="summary-value">{{ summary.notification_count || 0 }}</div></el-card></el-col>
    </el-row>

    <PageSection
      title="销售收款确认申请"
      description="销售在收到定金、尾款、发货前全款、到货后全款或分期款后提交确认；财务核验回款凭证后形成回款记录并同步业务通知。"
    >
      <template #actions>
        <el-button @click="load">刷新</el-button>
        <el-button v-if="canSubmitReceipt" type="primary" :disabled="!canOpenReceiptFromSelection" @click="openReceiptDialog()">提交收款确认</el-button>
        <el-button v-if="canSubmitReceipt" type="success" plain :disabled="mergeableSelection.length < 2" @click="openMergedReceiptDialog">合并收款确认</el-button>
      </template>
      <el-table :data="pagedOrders" stripe @selection-change="handleOrderSelectionChange">
        <el-table-column v-if="canSubmitReceipt" type="selection" width="52" />
        <el-table-column type="expand" width="52">
          <template #default="scope">
            <el-table :data="scope.row.items || []" border size="small">
              <el-table-column prop="product_code" label="编码" width="140" />
              <el-table-column prop="product_name" label="产品" min-width="180" />
              <el-table-column prop="display_specification" label="规格" width="140" />
              <el-table-column prop="packaging_name" label="包装" width="140" />
              <el-table-column prop="bag_count" label="袋数" width="100"><template #default="innerScope">{{ formatNumber(innerScope.row.bag_count) }}</template></el-table-column>
              <el-table-column prop="packaging_fee" label="包装费" width="110"><template #default="innerScope">{{ formatNumber(innerScope.row.packaging_fee) }}</template></el-table-column>
              <el-table-column prop="quantity" label="订单数量" width="120">
                <template #default="innerScope">{{ formatNumber(innerScope.row.quantity) }}</template>
              </el-table-column>
              <el-table-column prop="shipped_quantity" label="已发货" width="120">
                <template #default="innerScope">{{ formatNumber(innerScope.row.shipped_quantity) }}</template>
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
        <el-table-column prop="order_no" label="客户订单号" width="160" />
        <el-table-column prop="parent_order_no" label="母订单号" width="160" />
        <el-table-column prop="customer_name" label="客户" min-width="180" />
        <el-table-column prop="status_label" label="订单状态" width="120">
          <template #default="scope"><el-tag :type="statusType(scope.row.status)">{{ scope.row.status_label }}</el-tag></template>
        </el-table-column>
        <el-table-column prop="settlement_status_label" label="结算状态" width="120">
          <template #default="scope"><el-tag :type="statusType(scope.row.settlement_status)">{{ scope.row.settlement_status_label }}</el-tag></template>
        </el-table-column>
        <el-table-column prop="payment_type_label" label="付款方式" min-width="180" />
        <el-table-column prop="total_amount" label="订单金额" width="120">
          <template #default="scope">{{ formatNumber(scope.row.total_amount) }}</template>
        </el-table-column>
        <el-table-column prop="remaining_amount" label="待收金额" width="120">
          <template #default="scope">{{ formatNumber(scope.row.remaining_amount) }}</template>
        </el-table-column>
        <el-table-column prop="order_remark" label="整单备注" min-width="180" show-overflow-tooltip />
        <el-table-column label="操作" width="180" fixed="right">
          <template #default="scope">
            <el-button size="small" text type="primary" @click="goOrder(scope.row.id)">查看订单</el-button>
            <el-button v-if="canSubmitReceipt" size="small" text type="success" :disabled="!canSubmitForOrder(scope.row)" @click="openReceiptDialog(scope.row)">收款确认</el-button>
            <el-button v-if="canSubmitReceipt && scope.row.can_mark_completed" size="small" text type="warning" @click="completeOrder(scope.row)">结算完成</el-button>
          </template>
        </el-table-column>
      </el-table>
      <AppPagination
        v-model:page="orderPagination.page"
        v-model:page-size="orderPagination.pageSize"
        :total="orderTotal"
        :page-sizes="pageSizes"
      />
    </PageSection>

    <PageSection title="申请记录" description="展示销售收款确认申请的审批结果与异常状态。">
      <el-table :data="pagedRequests" stripe>
        <el-table-column prop="merge_no" label="合并批次" width="140">
          <template #default="scope">{{ scope.row.merge_no || '-' }}</template>
        </el-table-column>
        <el-table-column prop="request_no" label="申请单号" width="160" />
        <el-table-column prop="sales_order_no" label="客户订单号" width="160" />
        <el-table-column prop="payment_stage_label" label="收款阶段" width="120" />
        <el-table-column prop="requested_amount" label="申请金额" width="120">
          <template #default="scope">{{ formatNumber(scope.row.requested_amount) }}</template>
        </el-table-column>
        <el-table-column prop="actual_amount" label="确认金额" width="120">
          <template #default="scope">{{ formatNumber(scope.row.actual_amount) }}</template>
        </el-table-column>
        <el-table-column prop="status_label" label="状态" width="120">
          <template #default="scope"><el-tag :type="statusType(scope.row.status)">{{ scope.row.status_label }}</el-tag></template>
        </el-table-column>
        <el-table-column prop="installment_total" label="总期数" width="100" />
        <el-table-column prop="installment_index" label="当前期数" width="100" />
        <el-table-column prop="requested_by_name" label="申请人" width="120" />
        <el-table-column prop="approved_by_name" label="确认人" width="120" />
        <el-table-column prop="created_at" label="创建时间" min-width="170">
          <template #default="scope">{{ formatDateTime(scope.row.created_at) }}</template>
        </el-table-column>
      </el-table>
      <AppPagination
        v-model:page="requestPagination.page"
        v-model:page-size="requestPagination.pageSize"
        :total="requestTotal"
        :page-sizes="pageSizes"
      />
    </PageSection>

    <PageSection title="回款记录" description="董事长确认通过后自动生成回款记录，并同步财务资金流水。">
      <el-table :data="pagedReceivables" stripe>
        <el-table-column prop="sales_order_no" label="客户订单号" width="160" />
        <el-table-column prop="amount" label="回款金额" width="120">
          <template #default="scope">{{ formatNumber(scope.row.amount) }}</template>
        </el-table-column>
        <el-table-column prop="received_by_name" label="登记人" width="120" />
        <el-table-column prop="received_at" label="时间" min-width="170">
          <template #default="scope">{{ formatDateTime(scope.row.received_at) }}</template>
        </el-table-column>
        <el-table-column prop="remark_display" label="备注" min-width="220" show-overflow-tooltip />
      </el-table>
      <AppPagination
        v-model:page="receivablePagination.page"
        v-model:page-size="receivablePagination.pageSize"
        :total="receivableTotal"
        :page-sizes="pageSizes"
      />
    </PageSection>

    <PageSection title="协同通知" description="审批通过、金额异常与财务同步消息统一沉淀到页面底部。">
      <el-table :data="pagedNotifications" stripe>
        <el-table-column prop="title" label="标题" min-width="200" />
        <el-table-column prop="content" label="内容" min-width="320" show-overflow-tooltip />
        <el-table-column prop="module" label="模块" width="100" />
        <el-table-column prop="created_at" label="时间" min-width="170">
          <template #default="scope">{{ formatDateTime(scope.row.created_at) }}</template>
        </el-table-column>
      </el-table>
      <AppPagination
        v-model:page="notificationPagination.page"
        v-model:page-size="notificationPagination.pageSize"
        :total="notificationTotal"
        :page-sizes="pageSizes"
      />
    </PageSection>

    <el-dialog v-model="receiptVisible" title="提交销售收款确认" width="860px">
      <el-form :model="receiptForm" label-width="110px">
        <el-form-item label="客户订单">
          <el-select v-model="receiptForm.sales_order_id" filterable style="width: 100%">
            <el-option v-for="item in orders" :key="item.id" :label="`${item.order_no} / ${item.customer_name || '-'}`" :value="item.id" />
          </el-select>
        </el-form-item>
        <div v-if="selectedOrder" class="order-summary-card">
          <el-descriptions :column="2" border>
            <el-descriptions-item label="客户订单号">{{ selectedOrder.order_no }}</el-descriptions-item>
            <el-descriptions-item label="客户">{{ selectedOrder.customer_name || '-' }}</el-descriptions-item>
            <el-descriptions-item label="付款方式">{{ selectedOrder.payment_type_label || '-' }}</el-descriptions-item>
            <el-descriptions-item label="订单金额">{{ formatNumber(selectedOrder.total_amount) }}</el-descriptions-item>
            <el-descriptions-item label="定金">{{ formatNumber(selectedOrder.deposit_amount) }}</el-descriptions-item>
            <el-descriptions-item label="已收金额">{{ formatNumber(selectedOrder.received_amount) }}</el-descriptions-item>
            <el-descriptions-item label="待收金额">{{ formatNumber(selectedOrder.remaining_amount) }}</el-descriptions-item>
            <el-descriptions-item label="订单状态">{{ selectedOrder.status_label || '-' }}</el-descriptions-item>
            <el-descriptions-item label="母订单号">{{ selectedOrder.parent_order_no || '-' }}</el-descriptions-item>
          </el-descriptions>
        </div>
        <el-table v-if="selectedOrder?.items?.length" :data="selectedOrder.items" stripe class="mt-16">
          <el-table-column prop="product_code" label="编码" width="140" />
          <el-table-column prop="product_name" label="产品" min-width="180" />
          <el-table-column prop="display_specification" label="规格" width="140" />
          <el-table-column prop="packaging_name" label="包装" width="140" />
          <el-table-column prop="bag_count" label="袋数" width="100"><template #default="scope">{{ formatNumber(scope.row.bag_count) }}</template></el-table-column>
          <el-table-column prop="packaging_fee" label="包装费" width="110"><template #default="scope">{{ formatNumber(scope.row.packaging_fee) }}</template></el-table-column>
          <el-table-column prop="quantity" label="订单数量" width="120"><template #default="scope">{{ formatNumber(scope.row.quantity) }}</template></el-table-column>
          <el-table-column prop="shipped_quantity" label="已发货" width="120"><template #default="scope">{{ formatNumber(scope.row.shipped_quantity) }}</template></el-table-column>
          <el-table-column prop="unit_price" label="单价" width="120"><template #default="scope">{{ formatNumber(scope.row.unit_price) }}</template></el-table-column>
          <el-table-column prop="total_amount" label="金额" width="120"><template #default="scope">{{ formatNumber(scope.row.total_amount) }}</template></el-table-column>
        </el-table>
        <el-table v-if="receivablesForSelectedOrder.length" :data="receivablesForSelectedOrder" stripe class="mt-16">
          <el-table-column prop="payment_stage_label" label="阶段" width="120" />
          <el-table-column prop="amount" label="金额" width="120"><template #default="scope">{{ formatNumber(scope.row.amount) }}</template></el-table-column>
          <el-table-column prop="received_at" label="时间" min-width="170"><template #default="scope">{{ formatDateTime(scope.row.received_at) }}</template></el-table-column>
          <el-table-column prop="remark_display" label="备注" min-width="220" show-overflow-tooltip />
        </el-table>
        <el-form-item class="mt-16" label="收款阶段">
          <el-select v-model="receiptForm.payment_stage" style="width: 100%">
            <el-option label="定金" value="deposit" />
            <el-option label="尾款" value="balance" />
            <el-option label="到货后全款" value="cod_full" />
            <el-option label="发货前全款" value="pre_shipment_full" />
            <el-option label="分期款" value="installment" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="receiptForm.payment_stage === 'installment'" label="总期数">
          <el-input-number v-model="receiptForm.installment_total" :min="1" :max="24" style="width: 100%" />
        </el-form-item>
        <el-form-item v-if="receiptForm.payment_stage === 'installment'" label="当前期数">
          <el-input-number v-model="receiptForm.installment_index" :min="1" :max="24" style="width: 100%" />
        </el-form-item>
        <el-form-item label="申请金额">
          <el-input-number v-model="receiptForm.requested_amount" :min="0.01" :precision="2" style="width: 100%" />
        </el-form-item>
        <el-form-item label="说明">
          <el-input v-model="receiptForm.remark" type="textarea" :rows="3" />
        </el-form-item>
        <el-form-item label="指定财务">
          <el-select v-model="receiptForm.assigned_finance_user_id" clearable placeholder="不指定则系统自动分配" style="width: 100%">
            <el-option v-for="u in financeAssignees" :key="u.id" :label="u.is_super ? `${u.full_name}（超级财务）` : u.full_name" :value="u.id" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="receiptVisible = false">取消</el-button>
        <el-button type="primary" @click="submitReceiptRequest">提交申请</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="mergedReceiptVisible" title="提交合并收款确认" width="1040px">
      <el-descriptions v-if="mergedReceiptCustomerName" :column="2" border class="mb-16">
        <el-descriptions-item label="客户">{{ mergedReceiptCustomerName }}</el-descriptions-item>
        <el-descriptions-item label="订单数量">{{ mergedReceiptForm.items.length }}</el-descriptions-item>
        <el-descriptions-item label="申请总额">{{ formatNumber(mergedReceiptRequestedAmount) }}</el-descriptions-item>
        <el-descriptions-item label="说明">仅支持同一客户的未结清客户订单合并提交收款确认</el-descriptions-item>
      </el-descriptions>
      <el-form :model="mergedReceiptForm" label-width="110px" class="mb-16">
        <el-form-item label="收款阶段">
          <el-select v-model="mergedReceiptForm.payment_stage" style="width: 100%">
            <el-option label="合并收款" value="mixed" />
            <el-option label="定金" value="deposit" />
            <el-option label="尾款" value="balance" />
            <el-option label="到货后全款" value="cod_full" />
            <el-option label="发货前全款" value="pre_shipment_full" />
            <el-option label="分期款" value="installment" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="mergedReceiptForm.payment_stage === 'installment'" label="总期数">
          <el-input-number v-model="mergedReceiptForm.installment_total" :min="1" :max="24" style="width: 100%" />
        </el-form-item>
        <el-form-item v-if="mergedReceiptForm.payment_stage === 'installment'" label="当前期数">
          <el-input-number v-model="mergedReceiptForm.installment_index" :min="1" :max="24" style="width: 100%" />
        </el-form-item>
      </el-form>
      <el-table :data="mergedReceiptForm.items" stripe border>
        <el-table-column type="expand" width="52">
          <template #default="scope">
            <el-table :data="scope.row.items || []" border size="small">
              <el-table-column prop="product_code" label="编码" width="140" />
              <el-table-column prop="product_name" label="产品" min-width="180" />
              <el-table-column prop="display_specification" label="规格" width="140" />
              <el-table-column prop="packaging_name" label="包装" width="140" />
              <el-table-column prop="bag_count" label="袋数" width="100"><template #default="innerScope">{{ formatNumber(innerScope.row.bag_count) }}</template></el-table-column>
              <el-table-column prop="packaging_fee" label="包装费" width="110"><template #default="innerScope">{{ formatNumber(innerScope.row.packaging_fee) }}</template></el-table-column>
              <el-table-column prop="quantity" label="订单数量" width="120"><template #default="innerScope">{{ formatNumber(innerScope.row.quantity) }}</template></el-table-column>
              <el-table-column prop="shipped_quantity" label="已发货" width="120"><template #default="innerScope">{{ formatNumber(innerScope.row.shipped_quantity) }}</template></el-table-column>
              <el-table-column prop="unit_price" label="单价" width="120"><template #default="innerScope">{{ formatNumber(innerScope.row.unit_price) }}</template></el-table-column>
              <el-table-column prop="total_amount" label="金额" width="120"><template #default="innerScope">{{ formatNumber(innerScope.row.total_amount) }}</template></el-table-column>
            </el-table>
          </template>
        </el-table-column>
        <el-table-column prop="order_no" label="客户订单号" width="160" />
        <el-table-column prop="payment_type_label" label="付款方式" width="150" />
        <el-table-column prop="remaining_amount" label="待收金额" width="120"><template #default="scope">{{ formatNumber(scope.row.remaining_amount) }}</template></el-table-column>
        <el-table-column label="本次申请金额" width="160">
          <template #default="scope">
            <el-input-number v-model="scope.row.requested_amount" :min="0.01" :max="scope.row.remaining_amount" :precision="2" style="width: 100%" />
          </template>
        </el-table-column>
      </el-table>
      <el-form class="mt-16" :model="mergedReceiptForm" label-width="110px">
        <el-form-item label="说明">
          <el-input v-model="mergedReceiptForm.remark" type="textarea" :rows="3" />
        </el-form-item>
        <el-form-item label="指定财务">
          <el-select v-model="mergedReceiptForm.assigned_finance_user_id" clearable placeholder="不指定则系统自动分配" style="width: 100%">
            <el-option v-for="u in financeAssignees" :key="u.id" :label="u.is_super ? `${u.full_name}（超级财务）` : u.full_name" :value="u.id" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="mergedReceiptVisible = false">取消</el-button>
        <el-button type="primary" @click="submitMergedReceiptRequest">提交申请</el-button>
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

import { get, post } from '../../api/client'
import AppPagination from '../../components/AppPagination.vue'
import PageSection from '../../components/PageSection.vue'
import { useFinanceAssignees } from '../../composables/useFinanceAssignees'
import { useLocalPagination } from '../../composables/useLocalPagination'
import { useAuthStore } from '../../stores/auth'
import { formatDateTime, formatNumber, statusType } from '../../utils/format'

const auth = useAuthStore()
const router = useRouter()
const summary = reactive({})
const notifications = ref([])
const orders = ref([])
const receiptRequests = ref([])
const receivables = ref([])
const receiptVisible = ref(false)
const mergedReceiptVisible = ref(false)
const selectedOrderRows = ref([])

const { financeAssignees, loadFinanceAssignees } = useFinanceAssignees()

const receiptForm = reactive({
  sales_order_id: null,
  requested_amount: 0,
  payment_stage: 'deposit',
  installment_total: null,
  installment_index: null,
  remark: '',
  assigned_finance_user_id: null
})

const mergedReceiptForm = reactive({
  customer_id: null,
  payment_stage: 'deposit',
  installment_total: null,
  installment_index: null,
  remark: '',
  items: [],
  assigned_finance_user_id: null
})

const canSubmitReceipt = computed(() => ['sales', 'system_admin'].includes(auth.user?.role_code))
const selectedOrder = computed(() => orders.value.find((item) => item.id === receiptForm.sales_order_id) || null)
const receivablesForSelectedOrder = computed(() =>
  receivables.value.filter((item) => item.sales_order_id === receiptForm.sales_order_id)
)
const mergedReceiptCustomerName = computed(() => mergedReceiptForm.items[0]?.customer_name || '')
const mergedReceiptRequestedAmount = computed(() =>
  mergedReceiptForm.items.reduce((sum, item) => sum + Number(item.requested_amount || 0), 0)
)
const mergeableSelection = computed(() => selectedOrderRows.value.filter((item) => canSubmitForOrder(item)))
const canOpenReceiptFromSelection = computed(() => orders.value.some((item) => canSubmitForOrder(item)))

const { pagination: notificationPagination, total: notificationTotal, pageSizes, pagedItems: pagedNotifications } = useLocalPagination(notifications)
const { pagination: orderPagination, total: orderTotal, pagedItems: pagedOrders } = useLocalPagination(orders)
const { pagination: requestPagination, total: requestTotal, pagedItems: pagedRequests } = useLocalPagination(receiptRequests)
const { pagination: receivablePagination, total: receivableTotal, pagedItems: pagedReceivables } = useLocalPagination(receivables)

function defaultReceiptAmount(row, stage = receiptForm.payment_stage) {
  if (!row) return 0
  const remainingAmount = Number(row.remaining_amount || 0)
  const depositAmount = Number(row.deposit_amount || 0)
  const receivedAmount = Number(row.received_amount || 0)
  if (stage === 'deposit' && depositAmount > 0 && receivedAmount <= 0) {
    return Math.min(depositAmount, remainingAmount || depositAmount)
  }
  if (stage === 'balance' && depositAmount > 0) {
    return Math.max(remainingAmount, 0)
  }
  return Math.max(remainingAmount, 0)
}

function resetReceiptForm() {
  receiptForm.sales_order_id = null
  receiptForm.requested_amount = 0
  receiptForm.payment_stage = 'deposit'
  receiptForm.installment_total = null
  receiptForm.installment_index = null
  receiptForm.remark = ''
  receiptForm.assigned_finance_user_id = null
}

function resetMergedReceiptForm() {
  mergedReceiptForm.customer_id = null
  mergedReceiptForm.payment_stage = 'mixed'
  mergedReceiptForm.installment_total = null
  mergedReceiptForm.installment_index = null
  mergedReceiptForm.remark = ''
  mergedReceiptForm.items = []
  mergedReceiptForm.assigned_finance_user_id = null
}

async function load() {
  const data = await get('/api/sales/settlements')
  Object.assign(summary, data.summary || {})
  notifications.value = data.notifications || []
  orders.value = data.orders || []
  receiptRequests.value = data.receipt_requests || []
  receivables.value = data.receivables || []
  selectedOrderRows.value = []
}

function goOrder(orderId) {
  router.push({ path: '/sales/orders', query: { focusId: String(orderId) } })
}

function handleOrderSelectionChange(selection) {
  selectedOrderRows.value = selection || []
}

function openReceiptDialog(row = null) {
  resetReceiptForm()
  if (row && !canSubmitForOrder(row)) {
    ElMessage.warning('该订单已结清，不能重复提交收款确认')
    return
  }
  if (row) {
    receiptForm.sales_order_id = row.id
    receiptForm.requested_amount = defaultReceiptAmount(row, receiptForm.payment_stage)
  }
  receiptVisible.value = true
}

function openMergedReceiptDialog() {
  const selectedRows = mergeableSelection.value
  if (selectedRows.length < 2) {
    ElMessage.warning('请至少选择两张同一客户的客户订单')
    return
  }
  const customerKeys = new Set(
    selectedRows
      .map((item) => item.customer_id ?? item.customer_name ?? null)
      .filter((item) => item !== null && item !== undefined && item !== '')
  )
  if (customerKeys.size !== 1) {
    ElMessage.warning('仅支持同一客户的客户订单合并收款确认')
    return
  }
  resetMergedReceiptForm()
  mergedReceiptForm.customer_id = selectedRows[0].customer_id
  mergedReceiptForm.items = selectedRows.map((item) => ({
    sales_order_id: item.id,
    order_no: item.order_no,
    customer_name: item.customer_name,
    payment_type_label: item.payment_type_label,
    remaining_amount: Number(item.remaining_amount || 0),
    requested_amount: Number(item.remaining_amount || 0),
    items: item.items || []
  }))
  mergedReceiptVisible.value = true
}

function canSubmitForOrder(row) {
  return row && !row.settlement_completed && Number(row.remaining_amount || 0) > 0
}

async function submitReceiptRequest() {
  if (!receiptForm.sales_order_id || Number(receiptForm.requested_amount || 0) <= 0) {
    ElMessage.warning('请填写完整的收款确认信息')
    return
  }
  try {
    await post('/api/sales/settlements/receipt-requests', receiptForm)
    ElMessage.success('销售收款确认已提交')
    receiptVisible.value = false
    resetReceiptForm()
    await load()
  } catch (error) {
    ElMessage.error(error.message || '销售收款确认提交失败')
  }
}

async function submitMergedReceiptRequest() {
  if (!mergedReceiptForm.items.length) {
    ElMessage.warning('请先选择客户订单')
    return
  }
  try {
    await post('/api/sales/settlements/receipt-requests/merged', {
      customer_id: mergedReceiptForm.customer_id,
      payment_stage: mergedReceiptForm.payment_stage,
      installment_total: mergedReceiptForm.installment_total,
      installment_index: mergedReceiptForm.installment_index,
      remark: mergedReceiptForm.remark,
      items: mergedReceiptForm.items.map((item) => ({
        sales_order_id: item.sales_order_id,
        requested_amount: Number(item.requested_amount || 0)
      }))
    })
    ElMessage.success('合并收款确认已提交')
    mergedReceiptVisible.value = false
    resetMergedReceiptForm()
    await load()
  } catch (error) {
    ElMessage.error(error.message || '合并收款确认提交失败')
  }
}

async function completeOrder(row) {
  await post(`/api/sales/orders/${row.id}/complete`, {})
  ElMessage.success('客户订单已完成')
  await load()
}

watch(selectedOrder, (row) => {
  if (!row) return
  if (['deposit', 'balance', 'cod_full', 'pre_shipment_full'].includes(receiptForm.payment_stage)) {
    receiptForm.requested_amount = defaultReceiptAmount(row, receiptForm.payment_stage)
  } else if (!receiptForm.requested_amount) {
    receiptForm.requested_amount = defaultReceiptAmount(row, receiptForm.payment_stage)
  }
})

watch(() => receiptForm.payment_stage, (value) => {
  if (value !== 'installment') {
    receiptForm.installment_total = null
    receiptForm.installment_index = null
  }
  if (selectedOrder.value) {
    receiptForm.requested_amount = defaultReceiptAmount(selectedOrder.value, value)
  }
})

watch(() => mergedReceiptForm.payment_stage, (value) => {
  if (value !== 'installment') {
    mergedReceiptForm.installment_total = null
    mergedReceiptForm.installment_index = null
  }
})

onMounted(async () => {
  await Promise.all([load(), loadFinanceAssignees()])
})
</script>

<style scoped>
.page-stack { display: flex; flex-direction: column; gap: 16px; }
.summary-row { margin-bottom: 0; }
.summary-label { color: #909399; margin-bottom: 8px; }
.summary-value { font-size: 28px; font-weight: 700; color: #0f5c90; }
.order-summary-card { margin-bottom: 16px; }
.mt-16 { margin-top: 16px; }
.mb-16 { margin-bottom: 16px; }
</style>
