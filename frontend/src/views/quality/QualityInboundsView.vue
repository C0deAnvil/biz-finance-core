<template>
  <PageSection title="质检入库申请" description="质检人员提交入库申请后仅查看历史；审批角色可查看完整明细并审批。">
    <template #actions>
      <el-button @click="load">刷新</el-button>
    </template>

    <el-table :data="pagedRows" stripe :row-class-name="rowClassName">
      <el-table-column prop="quality_no" label="质检任务号" width="160" />
      <el-table-column prop="batch_no" label="批次号" min-width="160" />
      <el-table-column prop="warehouse_name" label="目标仓库" min-width="160" />
      <el-table-column label="状态" width="120">
        <template #default="scope">
          <el-tag :type="statusType(scope.row.status)">{{ scope.row.status_label || statusLabel(scope.row.status) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="created_at" label="创建时间" min-width="180">
        <template #default="scope">{{ formatDateTime(scope.row.created_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="320">
        <template #default="scope">
          <el-button size="small" @click="openDetail(scope.row)">详情</el-button>
          <el-button size="small" text @click="goToQualityTask(scope.row)">质检任务</el-button>
          <template v-if="canApprove(scope.row)">
            <el-button size="small" type="success" @click="decide(scope.row.id, 'approved')">通过</el-button>
            <el-button size="small" type="danger" @click="decide(scope.row.id, 'rejected')">驳回</el-button>
          </template>
        </template>
      </el-table-column>
    </el-table>
    <AppPagination
      v-model:page="pagination.page"
      v-model:page-size="pagination.pageSize"
      :total="total"
      :page-sizes="pageSizes"
    />

    <el-drawer v-model="detailVisible" title="入库申请详情" size="56%">
      <el-descriptions v-if="detail" :column="2" border>
        <el-descriptions-item label="质检任务号">{{ detail.quality_no }}</el-descriptions-item>
        <el-descriptions-item label="生产任务号">{{ detail.production_task_no || '-' }}</el-descriptions-item>
        <el-descriptions-item label="产品">{{ detail.product_name || '-' }}</el-descriptions-item>
        <el-descriptions-item label="批次号">{{ detail.batch_no }}</el-descriptions-item>
        <el-descriptions-item label="目标仓库">{{ detail.warehouse_name }}</el-descriptions-item>
        <el-descriptions-item label="状态">{{ detail.status_label || statusLabel(detail.status) }}</el-descriptions-item>
        <el-descriptions-item label="申请人">{{ detail.requested_by_name || '-' }}</el-descriptions-item>
        <el-descriptions-item label="审批人">{{ detail.approved_by_name || '-' }}</el-descriptions-item>
        <el-descriptions-item label="质检进度">{{ detail.completed_tests }}/{{ detail.required_tests }}</el-descriptions-item>
        <el-descriptions-item label="通过次数">{{ detail.passed_tests }}</el-descriptions-item>
        <el-descriptions-item label="最近等级">{{ detail.latest_grade || '-' }}</el-descriptions-item>
        <el-descriptions-item label="审批时间">{{ formatDateTime(detail.approved_at) }}</el-descriptions-item>
        <el-descriptions-item label="备注" :span="2">{{ detail.remark || '-' }}</el-descriptions-item>
      </el-descriptions>

      <div class="section-title mt-16">质检记录</div>
      <el-table :data="detail?.inspections || []" stripe class="mt-16">
        <el-table-column prop="sequence_no" label="序号" width="80" />
        <el-table-column prop="grade" label="等级" width="120" />
        <el-table-column prop="inspected_quantity" label="检验数量" width="120">
          <template #default="scope">{{ formatNumber(scope.row.inspected_quantity) }}</template>
        </el-table-column>
        <el-table-column prop="result_text" label="检验结果" min-width="220" show-overflow-tooltip />
        <el-table-column prop="inspected_at" label="检验时间" min-width="170">
          <template #default="scope">{{ formatDateTime(scope.row.inspected_at) }}</template>
        </el-table-column>
      </el-table>

      <div class="section-title mt-16">入库流水</div>
      <el-table :data="detail?.inventory_transactions || []" stripe class="mt-16">
        <el-table-column prop="transaction_no" label="流水号" min-width="150" />
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
        <el-table-column prop="created_at" label="时间" min-width="170">
          <template #default="scope">{{ formatDateTime(scope.row.created_at) }}</template>
        </el-table-column>
      </el-table>
    </el-drawer>
  </PageSection>
</template>

<script setup>
import 'element-plus/es/components/message/style/css'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/descriptions/style/css'
import 'element-plus/es/components/drawer/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/tag/style/css'

import { ElMessage } from 'element-plus/es/components/message/index.mjs'

import ElButton from 'element-plus/es/components/button/index.mjs'
import ElDescriptions, { ElDescriptionsItem } from 'element-plus/es/components/descriptions/index.mjs'
import ElDrawer from 'element-plus/es/components/drawer/index.mjs'
import ElTable, { ElTableColumn } from 'element-plus/es/components/table/index.mjs'
import ElTag from 'element-plus/es/components/tag/index.mjs'

import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import { get, post } from '../../api/client'
import AppPagination from '../../components/AppPagination.vue'
import PageSection from '../../components/PageSection.vue'
import { useLocalPagination } from '../../composables/useLocalPagination'
import { useRouteFocus } from '../../composables/useRouteFocus'
import { useAuthStore } from '../../stores/auth'
import { formatDateTime, formatNumber, statusLabel, statusType } from '../../utils/format'

const auth = useAuthStore()
const router = useRouter()
const { route, focusedId, applyFocus } = useRouteFocus()
const rows = ref([])
const detail = ref(null)
const detailVisible = ref(false)
const { pagination, total, pageSizes, pagedItems: pagedRows, ensureVisible } = useLocalPagination(rows)

const approvalRoles = computed(() => new Set(['inventory', 'manager', 'chairman', 'system_admin']))

function canApprove(row) {
  return approvalRoles.value.has(auth.user?.role_code) && row.status === 'pending'
}

async function load() {
  rows.value = await get('/api/quality/inbounds')
  await applyFocusFromRoute()
}

async function openDetail(row) {
  detail.value = await get(`/api/quality/inbounds/${row.id}`)
  detailVisible.value = true
}

function goToQualityTask(row) {
  router.push({ path: '/quality/tasks', query: { focusId: String(row.quality_task_id) } })
}

function rowClassName({ row }) {
  return focusedId.value === row.id ? 'focused-row' : ''
}

async function applyFocusFromRoute() {
  await applyFocus(
    (focusValue) => {
      const matched = rows.value.find((item) => item.id === focusValue)
      if (matched) {
        ensureVisible((item) => item.id === matched.id)
      }
      return matched
    },
    async (row) => {
      await openDetail(row)
    }
  )
}

async function decide(id, decision) {
  await post(`/api/quality/inbounds/${id}/approve`, {
    decision,
    remark: decision === 'approved' ? '审批通过' : '审批驳回'
  })
  ElMessage.success('入库申请已处理')
  if (detailVisible.value && detail.value?.id === id) {
    detail.value = await get(`/api/quality/inbounds/${id}`)
  }
  await load()
}

onMounted(load)

watch(
  () => route.query.focusId,
  async () => {
    if (rows.value.length) {
      await applyFocusFromRoute()
    }
  }
)
</script>

<style scoped>
.mt-16 { margin-top: 16px; }
.section-title { font-size: 14px; font-weight: 600; color: #303133; }
.focused-row > td { background: #f0f9eb !important; }
</style>
