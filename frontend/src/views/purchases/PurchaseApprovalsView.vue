<template>
  <div class="page-stack">
    <PageSection title="采购审批" description="展示待经理审批的采购单，支持在当前页面直接审批。">
      <template #actions>
        <el-button @click="load">刷新</el-button>
        <el-button v-if="canApproveOrders" type="success" :disabled="!selection.length" @click="batchApprove">批量通过</el-button>
      </template>
      <el-table :data="pagedPendingRows" stripe empty-text="当前没有待审批采购单" @selection-change="selection = $event">
        <el-table-column v-if="canApproveOrders" type="selection" width="55" />
        <el-table-column prop="order_no" label="采购单号" width="150" />
        <el-table-column prop="supplier_name" label="供应商" min-width="180" />
        <el-table-column prop="payment_type_label" label="付款方式" min-width="140" />
        <el-table-column prop="total_amount" label="金额" width="120">
          <template #default="scope">{{ formatNumber(scope.row.total_amount) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="220" fixed="right">
          <template #default="scope">
            <el-button size="small" @click="showDetail(scope.row)">详情</el-button>
            <el-button v-if="canApproveOrders" size="small" type="success" @click="decide(scope.row.id, 'approved')">通过</el-button>
            <el-button v-if="canApproveOrders" size="small" type="danger" @click="openReject(scope.row.id)">驳回</el-button>
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

    <PageSection title="审批历史" description="展示当前账号已处理的采购审批记录。">
      <el-table :data="pagedHistoryRows" stripe empty-text="当前没有采购审批历史">
        <el-table-column prop="order_no" label="采购单号" width="150" />
        <el-table-column prop="supplier_name" label="供应商" min-width="180" />
        <el-table-column prop="payment_type_label" label="付款方式" min-width="140" />
        <el-table-column prop="total_amount" label="金额" width="120">
          <template #default="scope">{{ formatNumber(scope.row.total_amount) }}</template>
        </el-table-column>
        <el-table-column prop="decision_label" label="审批结果" width="110" />
        <el-table-column prop="order_status_label" label="当前状态" width="120" />
        <el-table-column prop="approved_at" label="审批时间" min-width="170">
          <template #default="scope">{{ formatDateTime(scope.row.approved_at) }}</template>
        </el-table-column>
        <el-table-column prop="note" label="审批意见" min-width="220" show-overflow-tooltip />
        <el-table-column label="操作" width="100" fixed="right">
          <template #default="scope">
            <el-button size="small" @click="showDetail(scope.row)">详情</el-button>
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

    <el-drawer v-model="detailVisible" title="采购单详情" size="52%">
      <el-descriptions v-if="detail" :column="2" border>
        <el-descriptions-item label="采购单号">{{ detail.order_no }}</el-descriptions-item>
        <el-descriptions-item label="供应商">{{ detail.supplier_name || '-' }}</el-descriptions-item>
        <el-descriptions-item label="付款方式">{{ detail.payment_type_label || '-' }}</el-descriptions-item>
        <el-descriptions-item label="当前状态">{{ detail.status_label || '-' }}</el-descriptions-item>
        <el-descriptions-item label="审批结果">{{ activeRow?.decision_label || (detail.status === 'pending_approval' ? '待审批' : '-') }}</el-descriptions-item>
        <el-descriptions-item label="审批时间">{{ formatDateTime(activeRow?.approved_at) }}</el-descriptions-item>
        <el-descriptions-item label="审批意见" :span="2">{{ activeRow?.note || '-' }}</el-descriptions-item>
      </el-descriptions>
      <el-table :data="detail?.items || []" stripe class="mt-16" empty-text="暂无采购明细">
        <el-table-column prop="product_name" label="产品" min-width="150" />
        <el-table-column prop="material_name" label="原料" min-width="150" />
        <el-table-column prop="quantity" label="数量" width="120">
          <template #default="scope">{{ formatNumber(scope.row.quantity) }}</template>
        </el-table-column>
        <el-table-column prop="unit_price" label="单价" width="120">
          <template #default="scope">{{ formatNumber(scope.row.unit_price) }}</template>
        </el-table-column>
        <el-table-column prop="received_quantity" label="已到货" width="120">
          <template #default="scope">{{ formatNumber(scope.row.received_quantity) }}</template>
        </el-table-column>
      </el-table>
      <template #footer>
        <el-button @click="detailVisible = false">关闭</el-button>
        <template v-if="canApproveOrders && detail?.status === 'pending_approval'">
          <el-button type="success" :loading="approvalSubmitting" :disabled="approvalSubmitting" @click="decide(detail.id, 'approved')">通过</el-button>
          <el-button type="danger" :disabled="approvalSubmitting" @click="openReject(detail.id)">驳回</el-button>
        </template>
      </template>
    </el-drawer>

    <el-dialog v-model="rejectVisible" title="驳回原因" width="500px">
      <el-input v-model="rejectNote" type="textarea" :rows="4" placeholder="请输入驳回原因" />
      <template #footer>
        <el-button @click="rejectVisible = false">取消</el-button>
        <el-button type="danger" @click="submitReject">确认驳回</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import 'element-plus/es/components/message/style/css'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/descriptions/style/css'
import 'element-plus/es/components/dialog/style/css'
import 'element-plus/es/components/drawer/style/css'
import 'element-plus/es/components/input/style/css'
import 'element-plus/es/components/table/style/css'

import { ElMessage } from 'element-plus/es/components/message/index.mjs'

import ElButton from 'element-plus/es/components/button/index.mjs'
import ElDescriptions, { ElDescriptionsItem } from 'element-plus/es/components/descriptions/index.mjs'
import ElDialog from 'element-plus/es/components/dialog/index.mjs'
import ElDrawer from 'element-plus/es/components/drawer/index.mjs'
import ElInput from 'element-plus/es/components/input/index.mjs'
import ElTable, { ElTableColumn } from 'element-plus/es/components/table/index.mjs'

import { computed, onMounted, ref } from 'vue'

import { get, post } from '../../api/client'
import AppPagination from '../../components/AppPagination.vue'
import PageSection from '../../components/PageSection.vue'
import { useLocalPagination } from '../../composables/useLocalPagination'
import { usePagePermission } from '../../composables/usePagePermission'
import { useAuthStore } from '../../stores/auth'
import { formatDateTime, formatNumber } from '../../utils/format'

const auth = useAuthStore()
const pagePermission = usePagePermission()
const pendingRows = ref([])
const historyRows = ref([])
const selection = ref([])
const detail = ref(null)
const activeRow = ref(null)
const detailVisible = ref(false)
const rejectVisible = ref(false)
const activeId = ref(null)
const rejectNote = ref('')
const approvalSubmitting = ref(false)
const canApproveOrders = computed(() => ['manager', 'chairman', 'system_admin'].includes(auth.user?.role_code) && pagePermission.can('approve'))
const { pagination: pendingPagination, total: pendingTotal, pageSizes, pagedItems: pagedPendingRows } = useLocalPagination(pendingRows)
const { pagination: historyPagination, total: historyTotal, pagedItems: pagedHistoryRows } = useLocalPagination(historyRows)

async function load() {
  const [pending, history] = await Promise.all([
    get('/api/purchases/approvals/pending'),
    get('/api/purchases/approvals/history')
  ])
  pendingRows.value = pending
  historyRows.value = history
}

async function showDetail(row) {
  activeRow.value = row
  detail.value = await get(`/api/purchases/orders/${row.purchase_order_id || row.id}`)
  detailVisible.value = true
}

async function decide(id, decision, note = '审批通过') {
  if (approvalSubmitting.value) return
  approvalSubmitting.value = true
  try {
    await post(`/api/purchases/orders/${id}/approve`, { decision, note })
    markCurrentApprovalHandled(id, decision, note)
    ElMessage.success('审批已完成')
    await load()
    if (detailVisible.value && Number(detail.value?.id) === Number(id)) {
      const latestDetail = await get(`/api/purchases/orders/${id}`)
      detail.value = { ...detail.value, ...latestDetail }
      activeRow.value = historyRows.value.find((row) => Number(row.purchase_order_id) === Number(id)) || activeRow.value
    }
  } finally {
    approvalSubmitting.value = false
  }
}

function markCurrentApprovalHandled(id, decision, note) {
  if (!detail.value || Number(detail.value.id) !== Number(id)) return
  const approved = decision === 'approved'
  detail.value = {
    ...detail.value,
    status: approved ? 'pending_purchase_confirm' : 'rejected',
    status_label: approved ? '待采购确认' : '已驳回'
  }
  activeRow.value = {
    ...(activeRow.value || {}),
    decision,
    decision_label: approved ? '通过' : '驳回',
    order_status: detail.value.status,
    order_status_label: detail.value.status_label,
    note
  }
}

function openReject(id) {
  activeId.value = id
  rejectNote.value = ''
  rejectVisible.value = true
}

async function submitReject() {
  await decide(activeId.value, 'rejected', rejectNote.value || '审批驳回')
  rejectVisible.value = false
}

async function batchApprove() {
  for (const row of selection.value) {
    await post(`/api/purchases/orders/${row.id}/approve`, { decision: 'approved', note: '批量审批通过' })
  }
  ElMessage.success('批量审批完成')
  await load()
}

onMounted(load)
</script>

<style scoped>
.page-stack {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.mt-16 {
  margin-top: 16px;
}
</style>
