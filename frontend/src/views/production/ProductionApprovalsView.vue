<template>
  <div class="page-stack">
    <el-row :gutter="12" class="summary-row">
      <el-col :span="6"><el-card shadow="hover"><div class="summary-label">申请总数</div><div class="summary-value">{{ filteredRows.length }}</div></el-card></el-col>
      <el-col :span="6"><el-card shadow="hover"><div class="summary-label">待审批</div><div class="summary-value text-warning">{{ pendingCount }}</div></el-card></el-col>
      <el-col :span="6"><el-card shadow="hover"><div class="summary-label">已通过</div><div class="summary-value text-success">{{ approvedCount }}</div></el-card></el-col>
      <el-col :span="6"><el-card shadow="hover"><div class="summary-label">已驳回</div><div class="summary-value text-danger">{{ rejectedCount }}</div></el-card></el-col>
    </el-row>

    <PageSection title="领料审批" description="生产领料由原料库管审批；超额领料会自动升级为库管 + 经理联合审批。">
      <template #actions>
        <el-select v-model="filters.status" clearable placeholder="状态" class="w-160">
          <el-option label="待审批" value="pending_approval" />
          <el-option label="已通过" value="approved" />
          <el-option label="已驳回" value="rejected" />
        </el-select>
        <el-input v-model="filters.keyword" clearable placeholder="搜索任务号 / 产品 / 原料" class="w-260" />
        <el-button @click="load">刷新</el-button>
      </template>

      <el-table :data="pagedRows" stripe :row-class-name="rowClassName">
        <el-table-column prop="task_no" label="任务号" width="150" />
        <el-table-column prop="request_type_label" label="申请类型" width="110" />
        <el-table-column prop="product_name" label="产品" min-width="160" />
        <el-table-column prop="material_name" label="原料" min-width="160" />
        <el-table-column prop="planned_quantity" label="计划耗用" width="110"><template #default="scope">{{ formatNumber(scope.row.planned_quantity) }}</template></el-table-column>
        <el-table-column prop="allowed_quantity" label="浮动上限" width="110"><template #default="scope">{{ formatNumber(scope.row.allowed_quantity) }}</template></el-table-column>
        <el-table-column prop="requested_quantity" label="申请耗用" width="110"><template #default="scope">{{ formatNumber(scope.row.requested_quantity) }}</template></el-table-column>
        <el-table-column prop="issued_quantity" label="已出库" width="110"><template #default="scope">{{ formatNumber(scope.row.issued_quantity) }}</template></el-table-column>
        <el-table-column prop="over_limit_quantity" label="超额数量" width="110"><template #default="scope">{{ formatNumber(scope.row.over_limit_quantity) }}</template></el-table-column>
        <el-table-column label="状态" width="110">
          <template #default="scope">
            <el-tag :type="statusType(scope.row.status)">{{ statusLabel(scope.row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="reason" label="原因" min-width="220" show-overflow-tooltip />
        <el-table-column label="库存审批" min-width="180">
          <template #default="scope">{{ statusLabel(scope.row.inventory_approval_status) }} / {{ scope.row.inventory_remark || '-' }}</template>
        </el-table-column>
        <el-table-column label="经理审批" min-width="180">
          <template #default="scope">{{ statusLabel(scope.row.manager_approval_status) }} / {{ scope.row.manager_remark || '-' }}</template>
        </el-table-column>
        <el-table-column prop="updated_at" label="更新时间" min-width="170"><template #default="scope">{{ formatDateTime(scope.row.updated_at) }}</template></el-table-column>
        <el-table-column label="操作" width="180" fixed="right">
          <template #default="scope">
            <el-button v-if="canApprove(scope.row)" size="small" text type="success" @click="openApproval(scope.row, 'approved')">通过</el-button>
            <el-button v-if="canApprove(scope.row)" size="small" text type="danger" @click="openApproval(scope.row, 'rejected')">驳回</el-button>
            <span v-if="!canApprove(scope.row)">-</span>
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

    <el-dialog v-model="approvalVisible" :title="approvalForm.decision === 'approved' ? '审批通过' : '审批驳回'" width="560px">
      <el-form :model="approvalForm" label-width="100px">
        <el-form-item label="审批数量" v-if="showApprovedQuantity">
          <el-input-number v-model="approvalForm.approved_quantity" :min="0.01" :max="activeRow?.requested_quantity || undefined" :precision="2" style="width: 100%" />
        </el-form-item>
        <el-form-item label="审批意见">
          <el-input v-model="approvalForm.remark" type="textarea" :rows="4" placeholder="可填写审批说明" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="approvalVisible = false">取消</el-button>
        <el-button :type="approvalForm.decision === 'approved' ? 'success' : 'danger'" @click="submitApproval">确认</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import 'element-plus/es/components/message/style/css'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/card/style/css'
import 'element-plus/es/components/col/style/css'
import 'element-plus/es/components/dialog/style/css'
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
import ElDialog from 'element-plus/es/components/dialog/index.mjs'
import ElForm, { ElFormItem } from 'element-plus/es/components/form/index.mjs'
import ElInputNumber from 'element-plus/es/components/input-number/index.mjs'
import ElInput from 'element-plus/es/components/input/index.mjs'
import ElRow from 'element-plus/es/components/row/index.mjs'
import ElSelect, { ElOption } from 'element-plus/es/components/select/index.mjs'
import ElTable, { ElTableColumn } from 'element-plus/es/components/table/index.mjs'
import ElTag from 'element-plus/es/components/tag/index.mjs'

import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { useRouteFocus } from '../../composables/useRouteFocus'

import { get, post } from '../../api/client'
import AppPagination from '../../components/AppPagination.vue'
import { useLocalPagination } from '../../composables/useLocalPagination'
import PageSection from '../../components/PageSection.vue'
import { useAuthStore } from '../../stores/auth'
import { formatDateTime, formatNumber, statusLabel, statusType } from '../../utils/format'

const auth = useAuthStore()
const { route, focusedId: focusedTaskId, applyFocus: applyRouteFocus } = useRouteFocus()
const rows = ref([])
const activeRow = ref(null)
const approvalVisible = ref(false)
const filters = reactive({ status: '', keyword: '' })
const approvalForm = reactive({ decision: 'approved', approved_quantity: 0, remark: '' })

const filteredRows = computed(() => {
  const keyword = filters.keyword.trim().toLowerCase()
  return rows.value.filter((item) => {
    if (filters.status && item.status !== filters.status) return false
    if (!keyword) return true
    return [item.task_no, item.product_name, item.material_name].filter(Boolean).some((value) => String(value).toLowerCase().includes(keyword))
  })
})
const { pagination, total, pageSizes, pagedItems: pagedRows, ensureVisible } = useLocalPagination(filteredRows)
const pendingCount = computed(() => filteredRows.value.filter((item) => item.status === 'pending_approval').length)
const approvedCount = computed(() => filteredRows.value.filter((item) => item.status === 'approved').length)
const rejectedCount = computed(() => filteredRows.value.filter((item) => item.status === 'rejected').length)
const showApprovedQuantity = computed(
  () => approvalForm.decision === 'approved' && ['manager', 'system_admin'].includes(auth.user?.role_code) && activeRow.value?.request_type === 'special'
)

function canApprove(row) {
  const roleCode = auth.user?.role_code
  if (roleCode === 'inventory') {
    return row.inventory_approval_status === 'pending_approval'
  }
  if (roleCode === 'manager') {
    return row.request_type === 'special' && row.manager_approval_status === 'pending_approval'
  }
  if (roleCode === 'system_admin') {
    return row.inventory_approval_status === 'pending_approval' || row.manager_approval_status === 'pending_approval'
  }
  return false
}

async function applyFocus() {
  await applyRouteFocus(
    (focusId) => {
      const matched = rows.value.find((item) => item.production_task_id === focusId) || null
      if (matched) {
        ensureVisible((item) => item.id === matched.id)
      }
      return matched
    },
    async (matched) => {
      filters.keyword = matched.task_no || filters.keyword
    }
  )
}

function rowClassName({ row }) {
  return focusedTaskId.value && row.production_task_id === focusedTaskId.value ? 'is-focused-row' : ''
}

async function load() {
  rows.value = await get('/api/production/special-requests')
  await applyFocus()
}

function openApproval(row, decision) {
  activeRow.value = row
  approvalForm.decision = decision
  approvalForm.approved_quantity = Number(row.requested_quantity || 0)
  approvalForm.remark = ''
  approvalVisible.value = true
}

async function submitApproval() {
  await post(`/api/production/special-requests/${activeRow.value.id}/approve`, {
    decision: approvalForm.decision,
    approved_quantity: approvalForm.decision === 'approved' ? approvalForm.approved_quantity : undefined,
    remark: approvalForm.remark
  })
  ElMessage.success('审批已处理')
  approvalVisible.value = false
  await load()
}

onMounted(load)

watch(() => route.query.focusId, async () => {
  await applyFocus()
})
</script>

<style scoped>
.page-stack { display: flex; flex-direction: column; gap: 16px; }
.summary-row { margin-bottom: 0; }
.summary-label { color: #909399; margin-bottom: 8px; }
.summary-value { font-size: 28px; font-weight: 700; color: #0f5c90; }
.text-warning { color: #e6a23c; }
.text-success { color: #67c23a; }
.text-danger { color: #f56c6c; }
.w-160 { width: 160px; }
.w-260 { width: 260px; }
.page-stack :deep(.is-focused-row) > td { background: #fff8e1 !important; }
</style>
