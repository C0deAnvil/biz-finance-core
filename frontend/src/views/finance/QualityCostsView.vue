<template>
  <div class="page-stack">
    <div v-if="focusedRecordId" class="focus-banner">
      {{ texts.messages.focused }} #{{ focusedRecordId }}
    </div>

    <el-row :gutter="12" class="summary-row">
      <el-col :span="6">
        <el-card shadow="hover">
          <div class="summary-label">{{ texts.summary.total }}</div>
          <div class="summary-value">{{ filteredRows.length }}</div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover">
          <div class="summary-label">{{ texts.summary.pending }}</div>
          <div class="summary-value text-warning">{{ formatNumber(pendingAmount) }}</div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover">
          <div class="summary-label">{{ texts.summary.recognized }}</div>
          <div class="summary-value text-success">{{ formatNumber(recognizedAmount) }}</div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover">
          <div class="summary-label">{{ texts.summary.scrap }}</div>
          <div class="summary-value text-danger">{{ formatNumber(scrapAmount) }}</div>
        </el-card>
      </el-col>
    </el-row>

    <PageSection :title="texts.sectionTitle" :description="texts.sectionDescription">
      <template #actions>
        <el-select v-model="filters.dispositionType" clearable :placeholder="texts.filters.type" class="w-140">
          <el-option :label="texts.types.rework" value="rework" />
          <el-option :label="texts.types.scrap" value="scrap" />
        </el-select>
        <el-select v-model="filters.status" clearable :placeholder="texts.filters.status" class="w-140">
          <el-option :label="texts.status.pending" value="pending" />
          <el-option :label="texts.status.recognized" value="recognized" />
        </el-select>
        <el-input v-model="filters.keyword" clearable :placeholder="texts.filters.keyword" class="w-280" />
        <el-button @click="load">{{ texts.actions.refresh }}</el-button>
        <el-button @click="exportRows">{{ texts.actions.export }}</el-button>
      </template>

      <el-table
        ref="tableRef"
        :data="pagedRows"
        stripe
        highlight-current-row
        :row-class-name="rowClassName"
      >
        <el-table-column prop="quality_no" :label="texts.columns.qualityNo" width="150" />
        <el-table-column prop="source_production_task_no" :label="texts.columns.sourceTaskNo" width="150" />
        <el-table-column prop="product_name" :label="texts.columns.productName" min-width="160" />
        <el-table-column :label="texts.columns.type" width="100">
          <template #default="scope">
            <el-tag :type="scope.row.disposition_type === 'scrap' ? 'danger' : 'warning'">
              {{ dispositionLabel(scope.row.disposition_type) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="disposition_quantity" :label="texts.columns.quantity" width="110">
          <template #default="scope">{{ formatNumber(scope.row.disposition_quantity) }}</template>
        </el-table-column>
        <el-table-column prop="estimated_amount" :label="texts.columns.estimatedAmount" width="120">
          <template #default="scope">{{ formatNumber(scope.row.estimated_amount) }}</template>
        </el-table-column>
        <el-table-column prop="recognized_amount" :label="texts.columns.recognizedAmount" width="120">
          <template #default="scope">{{ formatNumber(scope.row.recognized_amount) }}</template>
        </el-table-column>
        <el-table-column prop="variance_amount" :label="texts.columns.variance" width="110">
          <template #default="scope">
            <span :class="scope.row.variance_amount > 0 ? 'text-danger' : scope.row.variance_amount < 0 ? 'text-success' : ''">
              {{ formatNumber(scope.row.variance_amount) }}
            </span>
          </template>
        </el-table-column>
        <el-table-column :label="texts.columns.status" width="110">
          <template #default="scope">
            <el-tag :type="statusType(scope.row.status)">{{ statusLabel(scope.row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="related_production_task_no" :label="texts.columns.relatedTaskNo" width="150" />
        <el-table-column prop="reason" :label="texts.columns.reason" min-width="220" show-overflow-tooltip />
        <el-table-column prop="recognized_at" :label="texts.columns.recognizedAt" min-width="170">
          <template #default="scope">{{ formatDateTime(scope.row.recognized_at) }}</template>
        </el-table-column>
        <el-table-column :label="texts.columns.actions" width="220" fixed="right">
          <template #default="scope">
            <el-button size="small" type="primary" @click="openDialog(scope.row)">
              {{ actionLabel(scope.row.status) }}
            </el-button>
            <el-button size="small" text @click="goToQualityTask(scope.row)">质检</el-button>
            <el-button v-if="scope.row.related_production_task_id" size="small" text type="warning" @click="goToProductionTask(scope.row.related_production_task_id)">返工</el-button>
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

    <el-dialog v-model="dialogVisible" :title="texts.dialog.title" width="560px">
      <el-form :model="form" label-width="100px">
        <el-form-item :label="texts.dialog.qualityNo">
          <span>{{ activeRow?.quality_no || '-' }}</span>
        </el-form-item>
        <el-form-item :label="texts.dialog.estimatedAmount">
          <span>{{ formatNumber(activeRow?.estimated_amount || 0) }}</span>
        </el-form-item>
        <el-form-item :label="texts.dialog.recognizedAmount">
          <el-input v-model.number="form.recognized_amount" type="number" min="0" />
        </el-form-item>
        <el-form-item :label="texts.dialog.remark">
          <el-input v-model="form.remark" type="textarea" :rows="3" :placeholder="texts.dialog.remarkPlaceholder" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">{{ texts.actions.cancel }}</el-button>
        <el-button type="primary" @click="submit">{{ texts.actions.save }}</el-button>
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
import ElInput from 'element-plus/es/components/input/index.mjs'
import ElRow from 'element-plus/es/components/row/index.mjs'
import ElSelect, { ElOption } from 'element-plus/es/components/select/index.mjs'
import ElTable, { ElTableColumn } from 'element-plus/es/components/table/index.mjs'
import ElTag from 'element-plus/es/components/tag/index.mjs'

import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useRouteFocus } from '../../composables/useRouteFocus'

import { get, post } from '../../api/client'
import AppPagination from '../../components/AppPagination.vue'
import PageSection from '../../components/PageSection.vue'
import { useLocalPagination } from '../../composables/useLocalPagination'
import { exportRowsToCsv } from '../../utils/export'
import { formatDateTime, formatNumber, statusType } from '../../utils/format'

const { route, focusedId: focusedRecordId, applyFocus: applyRouteFocus } = useRouteFocus()
const router = useRouter()
const tableRef = ref(null)

const texts = {
  summary: {
    total: '记录数',
    pending: '待确认金额',
    recognized: '已确认金额',
    scrap: '报废预估'
  },
  sectionTitle: '质量成本台账',
  sectionDescription: '集中登记质检报废损失与返工成本，财务确认后可用于管理分析。',
  filters: {
    type: '类型',
    status: '状态',
    keyword: '搜索质检号 / 任务号 / 产品'
  },
  types: {
    rework: '返工',
    scrap: '报废'
  },
  status: {
    pending: '待确认',
    recognized: '已确认'
  },
  columns: {
    qualityNo: '质检号',
    sourceTaskNo: '来源生产单',
    productName: '产品',
    type: '类型',
    quantity: '数量',
    estimatedAmount: '预估成本',
    recognizedAmount: '确认成本',
    variance: '差异',
    status: '状态',
    relatedTaskNo: '返工单',
    reason: '原因',
    recognizedAt: '确认时间',
    actions: '操作'
  },
  dialog: {
    title: '确认质量异常成本',
    qualityNo: '质检号',
    estimatedAmount: '预估成本',
    recognizedAmount: '确认金额',
    remark: '备注',
    remarkPlaceholder: '填写成本确认说明'
  },
  actions: {
    refresh: '刷新',
    export: '导出',
    cancel: '取消',
    save: '保存'
  },
  messages: {
    saved: '质量异常成本已确认',
    focused: '已定位关联记录'
  }
}

const rows = ref([])
const dialogVisible = ref(false)
const activeRow = ref(null)
const form = reactive({ recognized_amount: 0, remark: '' })
const filters = reactive({ dispositionType: '', status: '', keyword: '' })

const filteredRows = computed(() => {
  const keyword = filters.keyword.trim().toLowerCase()
  return rows.value.filter((item) => {
    if (filters.dispositionType && item.disposition_type !== filters.dispositionType) return false
    if (filters.status && item.status !== filters.status) return false
    if (!keyword) return true
    return [item.quality_no, item.source_production_task_no, item.related_production_task_no, item.product_name]
      .filter(Boolean)
      .some((value) => String(value).toLowerCase().includes(keyword))
  })
})
const { pagination, total, pageSizes, pagedItems: pagedRows, ensureVisible } = useLocalPagination(filteredRows)

const pendingAmount = computed(() => filteredRows.value
  .filter((item) => item.status === 'pending')
  .reduce((sum, item) => sum + Number(item.estimated_amount || 0), 0))
const recognizedAmount = computed(() => filteredRows.value
  .filter((item) => item.status === 'recognized')
  .reduce((sum, item) => sum + Number(item.recognized_amount || 0), 0))
const scrapAmount = computed(() => filteredRows.value
  .filter((item) => item.disposition_type === 'scrap')
  .reduce((sum, item) => sum + Number(item.estimated_amount || 0), 0))

function dispositionLabel(type) {
  return type === 'scrap' ? texts.types.scrap : texts.types.rework
}

function statusLabel(status) {
  return status === 'recognized' ? texts.status.recognized : texts.status.pending
}

function actionLabel(status) {
  return status === 'recognized' ? '重设' : '确认'
}

function rowClassName({ row }) {
  return focusedRecordId.value && row.id === focusedRecordId.value ? 'focused-row' : ''
}

async function applyFocusFromRoute({ openDialogIfPending = false } = {}) {
  await applyRouteFocus(
    (focusId) => {
      const matched = rows.value.find((item) => item.id === focusId)
      if (matched) {
        ensureVisible((item) => item.id === matched.id)
      }
      return matched
    },
    async (row) => {
      tableRef.value?.setCurrentRow?.(row)
      if (openDialogIfPending && row.status !== 'recognized') {
        openDialog(row)
      }
    }
  )
}

async function load() {
  rows.value = await get('/api/finance/quality-costs')
  await applyFocusFromRoute()
}

function openDialog(row) {
  activeRow.value = row
  form.recognized_amount = Number(row.recognized_amount || row.estimated_amount || 0)
  form.remark = row.remark || ''
  dialogVisible.value = true
}

function goToQualityTask(row) {
  if (!row.quality_task_id) return
  router.push({ path: '/quality/tasks', query: { focusId: String(row.quality_task_id) } })
}

function goToProductionTask(taskId) {
  if (!taskId) return
  router.push({ path: '/production/tasks', query: { focusId: String(taskId) } })
}

async function submit() {
  await post(`/api/finance/quality-costs/${activeRow.value.id}/recognize`, {
    recognized_amount: form.recognized_amount,
    remark: form.remark
  })
  ElMessage.success(texts.messages.saved)
  dialogVisible.value = false
  await load()
}

function exportRows() {
  exportRowsToCsv('quality_cost_records', filteredRows.value)
}

watch(
  () => route.query.focusId,
  async () => {
    await applyFocusFromRoute({ openDialogIfPending: false })
  }
)

onMounted(load)
</script>

<style scoped>
.page-stack {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.focus-banner {
  padding: 10px 14px;
  border-radius: 8px;
  background: #ecf5ff;
  color: #0f5c90;
  font-weight: 600;
}

.summary-row {
  margin-bottom: 0;
}

.summary-label {
  color: #909399;
  margin-bottom: 8px;
}

.summary-value {
  font-size: 28px;
  font-weight: 700;
  color: #0f5c90;
}

.text-warning {
  color: #e6a23c;
}

.text-success {
  color: #67c23a;
}

.text-danger {
  color: #f56c6c;
}

.w-140 {
  width: 140px;
}

.w-280 {
  width: 280px;
}
</style>

<style>
.focused-row > td {
  background: #f0f9eb !important;
}
</style>
