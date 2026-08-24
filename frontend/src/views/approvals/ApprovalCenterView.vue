<template>
  <div class="page-stack">
    <el-row :gutter="12" class="summary-row">
      <el-col :span="8"><el-card shadow="hover"><div class="summary-label">待审批</div><div class="summary-value text-warning">{{ pendingRows.length }}</div></el-card></el-col>
      <el-col :span="8"><el-card shadow="hover"><div class="summary-label">审批历史</div><div class="summary-value">{{ historyRows.length }}</div></el-card></el-col>
      <el-col :span="8"><el-card shadow="hover"><div class="summary-label">当前角色</div><div class="summary-value text-primary">{{ auth.user?.role_name || '-' }}</div></el-card></el-col>
    </el-row>

    <PageSection title="审批中心" description="统一处理待审批单据，详情均为只读态。">
      <template #actions>
        <el-button @click="load">刷新</el-button>
      </template>
      <el-table :data="pagedPendingRows" stripe :row-class-name="pendingRowClassName">
        <el-table-column prop="approval_type_label" label="审批类型" width="160" />
        <el-table-column prop="title" label="标题" min-width="220" />
        <el-table-column prop="module" label="模块" width="120" />
        <el-table-column prop="status_label" label="状态" width="120">
          <template #default="scope">
            <el-tag :type="statusType(scope.row.status)">{{ scope.row.status_label }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="提交时间" min-width="180">
          <template #default="scope">{{ formatDateTime(scope.row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="240" fixed="right">
          <template #default="scope">
            <el-button size="small" @click="openDetail(scope.row)">详情</el-button>
            <el-button v-if="scope.row.action_route" size="small" type="primary" @click="goAction(scope.row)">前往处理</el-button>
            <el-button v-if="scope.row.can_approve && !isActionOnly(scope.row)" size="small" type="success" @click="openDetail(scope.row)">通过</el-button>
            <el-button v-if="scope.row.can_reject && !isActionOnly(scope.row)" size="small" type="danger" @click="openDetail(scope.row)">驳回</el-button>
          </template>
        </el-table-column>
      </el-table>
      <AppPagination
        v-model:page="pendingPagination.page"
        v-model:page-size="pendingPagination.pageSize"
        :total="pendingTotal"
        :page-sizes="pageSizes"
      />
    </PageSection>

    <PageSection title="审批历史" description="查看已处理审批记录，支持再次查看只读详情。">
      <el-table :data="pagedHistoryRows" stripe :row-class-name="historyRowClassName">
        <el-table-column prop="approval_type_label" label="审批类型" width="160" />
        <el-table-column prop="title" label="标题" min-width="220" />
        <el-table-column prop="module" label="模块" width="120" />
        <el-table-column prop="status_label" label="结果" width="120">
          <template #default="scope">
            <el-tag :type="statusType(scope.row.status)">{{ scope.row.status_label }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="处理时间" min-width="180">
          <template #default="scope">{{ formatDateTime(scope.row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="120" fixed="right">
          <template #default="scope">
            <el-button size="small" @click="openDetail(scope.row)">详情</el-button>
          </template>
        </el-table-column>
      </el-table>
      <AppPagination
        v-model:page="historyPagination.page"
        v-model:page-size="historyPagination.pageSize"
        :total="historyTotal"
        :page-sizes="pageSizes"
      />
    </PageSection>

    <el-drawer v-model="detailVisible" title="审批详情" size="62%">
      <template v-if="activeApproval">
        <el-descriptions :column="2" border>
          <el-descriptions-item label="审批类型">{{ activeApproval.approval_type_label }}</el-descriptions-item>
          <el-descriptions-item label="当前状态">{{ activeApproval.status_label }}</el-descriptions-item>
          <el-descriptions-item label="标题" :span="2">{{ activeApproval.title }}</el-descriptions-item>
        </el-descriptions>

        <el-descriptions v-if="detailEntries.length" :column="2" border class="mt-16">
          <el-descriptions-item v-for="item in detailEntries" :key="item.key" :label="item.label" :span="item.span || 1">
            {{ item.value }}
          </el-descriptions-item>
        </el-descriptions>

        <el-table v-if="detailItems.length" :data="detailItems" stripe class="mt-16">
          <el-table-column v-for="column in detailItemColumns" :key="column" :prop="column" :label="itemColumnLabel(column)" min-width="140">
            <template #default="scope">
              {{ renderDetailCell(scope.row[column], column) }}
            </template>
          </el-table-column>
        </el-table>

        <el-table v-if="attachmentItems.length" :data="attachmentItems" stripe class="mt-16">
          <el-table-column prop="attachment_type" label="附件类型" width="140" />
          <el-table-column prop="original_name" label="文件名" min-width="220" />
          <el-table-column label="操作" width="100">
            <template #default="scope">
              <el-button size="small" text type="primary" @click="openAttachment(scope.row.id)">查看</el-button>
            </template>
          </el-table-column>
        </el-table>

        <div v-if="canDecideActiveApproval" class="drawer-actions">
          <DelayedActionButton type="success" @click="handleDrawerDecision('approved')">通过</DelayedActionButton>
          <DelayedActionButton type="danger" @click="handleDrawerDecision('rejected')">驳回</DelayedActionButton>
        </div>
      </template>
    </el-drawer>

  </div>
</template>

<script setup>
import 'element-plus/es/components/message/style/css'
import 'element-plus/es/components/message-box/style/css'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/card/style/css'
import 'element-plus/es/components/col/style/css'
import 'element-plus/es/components/descriptions/style/css'
import 'element-plus/es/components/drawer/style/css'
import 'element-plus/es/components/row/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/tag/style/css'

import { ElMessage } from 'element-plus/es/components/message/index.mjs'
import { ElMessageBox } from 'element-plus/es/components/message-box/index.mjs'

import ElButton from 'element-plus/es/components/button/index.mjs'
import ElCard from 'element-plus/es/components/card/index.mjs'
import ElCol from 'element-plus/es/components/col/index.mjs'
import ElDescriptions, { ElDescriptionsItem } from 'element-plus/es/components/descriptions/index.mjs'
import ElDrawer from 'element-plus/es/components/drawer/index.mjs'
import ElRow from 'element-plus/es/components/row/index.mjs'
import ElTable, { ElTableColumn } from 'element-plus/es/components/table/index.mjs'
import ElTag from 'element-plus/es/components/tag/index.mjs'

import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { get, post } from '../../api/client'
import AppPagination from '../../components/AppPagination.vue'
import DelayedActionButton from '../../components/DelayedActionButton.vue'
import PageSection from '../../components/PageSection.vue'
import { useLocalPagination } from '../../composables/useLocalPagination'
import { useAuthStore } from '../../stores/auth'
import { formatDateTime, formatNumber, inventoryRequestCategoryLabel, paymentStageLabel, paymentTypeLabel, returnTypeLabel, statusLabel, statusType } from '../../utils/format'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const pendingRows = ref([])
const historyRows = ref([])
const activeApproval = ref(null)
const detailPayload = ref(null)
const detailVisible = ref(false)
const decisionSubmitting = ref(false)

const focusApprovalId = computed(() => String(route.query.focusId || ''))
const {
  pagination: pendingPagination,
  total: pendingTotal,
  pageSizes,
  pagedItems: pagedPendingRows,
} = useLocalPagination(pendingRows)
const {
  pagination: historyPagination,
  total: historyTotal,
  pagedItems: pagedHistoryRows,
} = useLocalPagination(historyRows)
const detailItems = computed(() => detailPayload.value?.items || [])
const attachmentItems = computed(() => detailPayload.value?.attachment_items || [])
const detailItemColumns = computed(() => {
  const firstRow = detailItems.value[0]
  return firstRow ? Object.keys(firstRow) : []
})

const detailEntries = computed(() => {
  const detail = detailPayload.value || {}
  return Object.entries(detail)
    .filter(([key, value]) => key !== 'items' && value != null && value !== '')
    .filter(([key]) => key !== 'attachment_items')
    .map(([key, value]) => ({
      key,
      label: itemColumnLabel(key),
      value: renderDetailCell(value, key),
      span: typeof value === 'string' && value.length > 24 ? 2 : 1
    }))
})

const canDecideActiveApproval = computed(() =>
  activeApproval.value && !decisionSubmitting.value && (activeApproval.value.can_approve || activeApproval.value.can_reject) && !isActionOnly(activeApproval.value)
)

function isActionOnly(row) {
  return ['purchase_payment_request', 'sales_receipt_request'].includes(row?.approval_type) && !!row?.action_route
}

function itemColumnLabel(key) {
  const map = {
    order_no: '单号',
    customer_name: '客户',
    supplier_name: '供应商',
    payment_type: '付款方式',
    payment_type_label: '付款方式',
    status: '状态',
    status_label: '状态',
    consignee: '收货人',
    consignee_phone: '联系电话',
    delivery_address: '收货地址',
    total_amount: '总金额',
    requested_amount: '申请金额',
    actual_amount: '实际金额',
    order_total_amount: '订单总金额',
    order_status: '订单状态',
    order_status_label: '订单状态',
    order_amount: '订单金额',
    other_fee_amount: '其他费用',
    received_amount: '已收金额',
    remaining_amount: '剩余金额',
    deposit_amount: '定金',
    transport_fee: '运费',
    packaging_fee: '包装费',
    request_no: '申请单号',
    purchase_order_no: '采购单号',
    sales_order_no: '客户订单号',
    arrival_status: '到货状态',
    arrival_status_label: '到货状态',
    payment_detail_remark: '申请说明',
    completion_remark: '完成说明',
    contract_attachments: '合同附件',
    receipt_history: '历史回款',
    payment_stage: '收款阶段',
    installment_total: '总期数',
    installment_index: '当前期数',
    approval_remark: '审批意见',
    quality_no: '质检单号',
    batch_no: '批次号',
    warehouse_name: '仓库',
    return_no: '退换货单号',
    return_type: '退换货类型',
    return_type_label: '退换货类型',
    reason: '原因',
    refund_amount: '退款金额',
    review_note: '审批备注',
    task_no: '任务号',
    product_name: '产品',
    material_name: '原料',
    planned_quantity: '计划耗用',
    requested_quantity: '申请耗用',
    approved_quantity: '审批耗用',
    inventory_approval_status: '库存审批状态',
    inventory_approval_status_label: '库存审批状态',
    inventory_remark: '库存意见',
    manager_approval_status: '经理审批状态',
    manager_approval_status_label: '经理审批状态',
    manager_remark: '经理意见',
    matter_no: '事务单号',
    reimbursement_no: '报销单号',
    business_matter_id: '关联事务',
    applicant_name: '申请人',
    expense_category: '费用大类',
    expense_subcategory: '费用小类',
    amount: '报销金额',
    occurred_at: '发生时间',
    detail: '事务详情',
    chairman_name: '董事长',
    chairman_remark: '董事长意见',
    finance_name: '财务',
    finance_remark: '财务备注',
    payment_method: '出账方式',
    paid_at: '出账时间',
    estimated_amount: '预估金额',
    approved_at: '审批时间',
    item_name: '物料',
    packaging_config_id: '包装配置',
    packaging_name: '包装名称',
    packaging_unit_price: '包装单价',
    packaging_fee: '包装费',
    quantity: '数量',
    unit_price: '单价',
    received_quantity: '已到货',
    returned_quantity: '已退货',
    display_specification: '规格',
    kg_per_bag: 'kg/袋',
    bag_count: '袋数',
    remark: '备注'
  }
  return map[key] || key
}

function renderDetailCell(value, key) {
  if (value == null || value === '') return '-'
  if (key === 'expense_category') {
    return { management: '管理费用', sales: '销售费用', finance: '财务费用', other: '其他' }[value] || value
  }
  if (key.endsWith('_at') || key === 'created_at') return formatDateTime(value)
  if (key === 'status' || key.endsWith('_status')) return statusLabel(value)
  if (key.endsWith('_status_label')) return value
  if (key === 'payment_type') return paymentTypeLabel(value)
  if (key === 'payment_stage') return paymentStageLabel(value)
  if (key === 'request_category') return inventoryRequestCategoryLabel(value)
  if (key === 'return_type') return returnTypeLabel(value)
  if (typeof value === 'number') return formatNumber(value)
  return value
}

function pendingRowClassName({ row }) {
  return focusApprovalId.value && row.id === focusApprovalId.value ? 'is-focused-row' : ''
}

function historyRowClassName({ row }) {
  return focusApprovalId.value && row.id === focusApprovalId.value ? 'is-focused-row' : ''
}

async function load() {
  const [pendingData, historyData] = await Promise.all([
    get('/api/approvals/pending'),
    get('/api/approvals/history')
  ])
  pendingRows.value = filterApprovalRows(pendingData)
  historyRows.value = filterApprovalRows(historyData)
  await applyFocus()
}

async function applyFocus() {
  if (!focusApprovalId.value) return
  const row = pendingRows.value.find((item) => item.id === focusApprovalId.value) || historyRows.value.find((item) => item.id === focusApprovalId.value)
  if (row) {
    await openDetail(row)
  }
}

async function openDetail(row) {
  if (!canUseApprovalRow(row)) return
  activeApproval.value = row
  const payload = await get(`/api/approvals/${row.id}`)
  detailPayload.value = payload.detail || {}
  detailVisible.value = true
}

function canUseApprovalRow(row) {
  return !(auth.user?.role_code === 'chairman' && row?.approval_type === 'purchase_order')
}

function filterApprovalRows(rows) {
  return (rows || []).filter(canUseApprovalRow)
}

function goAction(row) {
  if (!row.action_route) return
  router.push({ path: row.action_route, query: row.action_query || {} })
}

function openAttachment(id) {
  if (!id) return
  window.open(`/api/files/${id}`, '_blank')
}

function buildDecisionPayload(decision) {
  const summary = activeApproval.value?.summary || {}
  const detail = detailPayload.value || {}
  const approvalType = activeApproval.value?.approval_type || ''
  if (approvalType === 'sales_order') {
    const amount = Number(summary.actual_amount || summary.voucher_amount || detail.actual_amount || detail.requested_amount || detail.total_amount || detail.deposit_amount || 0)
    return { actual_amount: amount, voucher_amount: amount }
  }
  if (approvalType === 'purchase_payment_request') {
    const amount = Number(summary.requested_amount || summary.actual_amount || detail.requested_amount || detail.actual_amount || 0)
    return { actual_amount: amount }
  }
  if (approvalType === 'sales_receipt_request') {
    const amount = Number(summary.requested_amount || summary.actual_amount || detail.requested_amount || detail.actual_amount || 0)
    return { actual_amount: amount }
  }
  if (approvalType === 'production_material_request' || approvalType === 'production_special_request') {
    const quantity = Number(summary.requested_quantity || summary.approved_quantity || detail.requested_quantity || detail.approved_quantity || 0)
    return { approved_quantity: quantity }
  }
  return {}
}

async function postDecision(decision, payload = {}, options = {}) {
  if (!activeApproval.value || decisionSubmitting.value) return
  const activeApprovalId = activeApproval.value.id
  decisionSubmitting.value = true
  const endpoint = decision === 'rejected' ? 'reject' : 'approve'
  try {
    await post(`/api/approvals/${activeApprovalId}/${endpoint}`, {
      remark: payload.remark || '',
      note: payload.note || payload.remark || '',
      voucher_amount: payload.voucher_amount || 0,
      actual_amount: payload.actual_amount || 0,
      approved_quantity: payload.approved_quantity,
      create_replenishment: Boolean(payload.create_replenishment)
    })
    markActiveApprovalHandled(decision)
    ElMessage.success('审批已处理')
    await load()
    const row = historyRows.value.find((item) => item.id === activeApprovalId)
    if (row) {
      await openDetail(row)
      markActiveApprovalHandled(decision)
    }
  } catch (error) {
    const shortage = decision === 'approved'
      && options.allowShortageRetry !== false
      && error?.data?.code === 'inventory_shortage_confirmation_required'
    if (shortage) {
      const shortageData = error.data
      try {
        await ElMessageBox.confirm(
          `${shortageData.material_name}申请${shortageData.requested_quantity}，当前可用库存${shortageData.available_quantity}，缺口${shortageData.shortage_quantity}。是否生成采购补货需求并继续审批？`,
          '库存不足',
          {
            confirmButtonText: '是，生成采购需求',
            cancelButtonText: '否，保持待审批',
            type: 'warning'
          }
        )
        decisionSubmitting.value = false
        await postDecision(decision, { ...payload, create_replenishment: true }, { allowShortageRetry: false })
      } catch (confirmError) {
        if (confirmError === 'cancel' || confirmError === 'close') {
          ElMessage.info('已保留待审批状态')
        } else {
          ElMessage.error(confirmError?.message || '补货确认处理失败')
        }
      }
      return
    }
    ElMessage.error(error?.message || '审批处理失败')
  } finally {
    decisionSubmitting.value = false
  }
}

function markActiveApprovalHandled(decision) {
  if (!activeApproval.value) return
  const approved = decision === 'approved'
  activeApproval.value = {
    ...activeApproval.value,
    can_approve: false,
    can_reject: false,
    status: approved ? 'approved' : 'rejected',
    status_label: approved ? '已通过' : '已驳回'
  }
}

async function handleDrawerDecision(decision) {
  if (!activeApproval.value) return
  await postDecision(decision, buildDecisionPayload(decision))
}

onMounted(load)

watch(() => route.query.focusId, async () => {
  if (pendingRows.value.length || historyRows.value.length) {
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
.text-primary { color: #409eff; }
.mt-16 { margin-top: 16px; }
.drawer-actions { display: flex; justify-content: flex-end; gap: 12px; margin-top: 20px; padding-top: 16px; border-top: 1px solid #ebeef5; }
.page-stack :deep(.is-focused-row) > td { background: #fff8e1 !important; }
</style>
