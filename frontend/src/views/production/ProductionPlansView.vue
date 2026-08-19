<template>
  <div class="page-stack">
    <el-row :gutter="12" class="summary-row">
      <el-col :span="6"><el-card shadow="hover"><div class="summary-label">生产计划</div><div class="summary-value">{{ rows.length }}</div></el-card></el-col>
      <el-col :span="6"><el-card shadow="hover"><div class="summary-label">待转任务</div><div class="summary-value">{{ pendingConvertCount }}</div></el-card></el-col>
      <el-col :span="6"><el-card shadow="hover"><div class="summary-label">已转任务</div><div class="summary-value">{{ convertedCount }}</div></el-card></el-col>
      <el-col :span="6"><el-card shadow="hover"><div class="summary-label">高优先级</div><div class="summary-value">{{ urgentCount }}</div></el-card></el-col>
    </el-row>

    <PageSection title="生产计划" description="独立维护年度、季度、月度、周度和自定义生产计划，可指派责任人并一键转为生产任务。">
      <template #actions>
        <el-button @click="load">刷新</el-button>
        <el-button v-if="canManage" type="primary" @click="openCreate">新增计划</el-button>
      </template>

      <el-table :data="pagedRows" stripe>
        <el-table-column prop="plan_no" label="计划号" width="150" />
        <el-table-column prop="plan_name" label="计划名称" min-width="180" />
        <el-table-column prop="period_type" label="周期类型" width="120">
          <template #default="scope">{{ periodTypeLabel(scope.row.period_type) }}</template>
        </el-table-column>
        <el-table-column prop="period_start" label="周期开始" min-width="170">
          <template #default="scope">{{ formatDateTime(scope.row.period_start) }}</template>
        </el-table-column>
        <el-table-column prop="period_end" label="周期结束" min-width="170">
          <template #default="scope">{{ formatDateTime(scope.row.period_end) }}</template>
        </el-table-column>
        <el-table-column prop="product_name" label="产品" min-width="160" />
        <el-table-column prop="planned_quantity" label="计划数量" width="110">
          <template #default="scope">{{ formatNumber(scope.row.planned_quantity) }}</template>
        </el-table-column>
        <el-table-column prop="assigned_user_name" label="责任人" width="120" />
        <el-table-column prop="priority" label="优先级" width="100">
          <template #default="scope">{{ priorityLabel(scope.row.priority) }}</template>
        </el-table-column>
        <el-table-column prop="status_label" label="状态" width="120">
          <template #default="scope"><el-tag :type="statusType(scope.row.status)">{{ scope.row.status_label }}</el-tag></template>
        </el-table-column>
        <el-table-column label="操作" width="240" fixed="right">
          <template #default="scope">
            <el-button v-if="!scope.row.converted_task_id" size="small" text type="primary" @click="openEdit(scope.row)">编辑</el-button>
            <el-button size="small" text type="info" @click="exportPlan(scope.row)">导出</el-button>
            <el-button v-if="canManage && !scope.row.converted_task_id" size="small" text type="success" @click="convertToTask(scope.row)">转生产任务</el-button>
            <el-button v-else-if="scope.row.converted_task_id" size="small" text type="primary" @click="goTask(scope.row.converted_task_id)">查看任务</el-button>
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

    <el-dialog v-model="dialogVisible" :title="dialogMode === 'create' ? '新增生产计划' : '编辑生产计划'" width="980px">
      <el-form :model="form" label-width="100px">
        <el-row :gutter="16">
          <el-col :span="12"><el-form-item label="计划名称"><el-input v-model="form.plan_name" placeholder="例如：2026年二季度盐酸钙排产计划" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="周期类型"><el-select v-model="form.period_type" style="width: 100%"><el-option label="年度计划" value="annual" /><el-option label="季度计划" value="quarterly" /><el-option label="月度计划" value="monthly" /><el-option label="周度计划" value="weekly" /><el-option label="自定义计划" value="custom" /></el-select></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="周期开始"><el-date-picker v-model="form.period_start" type="datetime" format="YYYY-MM-DD HH:00" value-format="YYYY-MM-DDTHH:00:00" style="width: 100%" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="周期结束"><el-date-picker v-model="form.period_end" type="datetime" format="YYYY-MM-DD HH:00" value-format="YYYY-MM-DDTHH:00:00" style="width: 100%" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="产品"><el-select v-model="form.product_id" filterable style="width: 100%"><el-option v-for="item in products" :key="item.id" :label="item.name" :value="item.id" /></el-select></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="关联订单"><el-select v-model="form.sales_order_id" clearable filterable style="width: 100%"><el-option v-for="item in salesOrders" :key="item.id" :label="item.order_no" :value="item.id" /></el-select></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="计划数量"><el-input-number v-model="form.planned_quantity" :min="0.01" style="width: 100%" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="责任人"><el-select v-model="form.assigned_user_id" clearable filterable style="width: 100%"><el-option v-for="item in productionUsers" :key="item.id" :label="item.full_name" :value="item.id" /></el-select></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="计划开工"><el-date-picker v-model="form.scheduled_start_at" type="datetime" format="YYYY-MM-DD HH:00" value-format="YYYY-MM-DDTHH:00:00" style="width: 100%" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="计划完工"><el-date-picker v-model="form.scheduled_end_at" type="datetime" format="YYYY-MM-DD HH:00" value-format="YYYY-MM-DDTHH:00:00" style="width: 100%" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="优先级"><el-select v-model="form.priority" style="width: 100%"><el-option label="低" value="low" /><el-option label="普通" value="normal" /><el-option label="高" value="high" /><el-option label="紧急" value="urgent" /></el-select></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="产线/班组"><el-input v-model="form.line_name" placeholder="例如：1#产线 / 白班" /></el-form-item></el-col>
          <el-col :span="24"><el-form-item label="排产说明"><el-input v-model="form.scheduling_note" type="textarea" :rows="3" /></el-form-item></el-col>
        </el-row>
      </el-form>

      <div class="detail-toolbar"><span>计划细节</span></div>
      <el-row :gutter="16">
        <el-col :span="12"><el-form-item label="排产节奏"><el-input v-model="form.detail_json.arrangement" placeholder="例如：按周滚动排产 / 分两批投产" /></el-form-item></el-col>
        <el-col :span="12"><el-form-item label="班次安排"><el-input v-model="form.detail_json.shift" placeholder="例如：白班+夜班" /></el-form-item></el-col>
        <el-col :span="24"><el-form-item label="计划细节说明"><el-input v-model="form.detail_json.target_note" type="textarea" :rows="4" placeholder="填写该周期内产品、数量、关键节点、风险点和协同要求" /></el-form-item></el-col>
      </el-row>

      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="submit">{{ dialogMode === 'create' ? '创建' : '保存' }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/card/style/css'
import 'element-plus/es/components/col/style/css'
import 'element-plus/es/components/date-picker/style/css'
import 'element-plus/es/components/dialog/style/css'
import 'element-plus/es/components/form/style/css'
import 'element-plus/es/components/input/style/css'
import 'element-plus/es/components/input-number/style/css'
import 'element-plus/es/components/message/style/css'
import 'element-plus/es/components/row/style/css'
import 'element-plus/es/components/select/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/tag/style/css'

import { ElMessage } from 'element-plus/es/components/message/index.mjs'
import ElButton from 'element-plus/es/components/button/index.mjs'
import ElCard from 'element-plus/es/components/card/index.mjs'
import ElCol from 'element-plus/es/components/col/index.mjs'
import ElDatePicker from 'element-plus/es/components/date-picker/index.mjs'
import ElDialog from 'element-plus/es/components/dialog/index.mjs'
import ElForm, { ElFormItem } from 'element-plus/es/components/form/index.mjs'
import ElInput from 'element-plus/es/components/input/index.mjs'
import ElInputNumber from 'element-plus/es/components/input-number/index.mjs'
import ElRow from 'element-plus/es/components/row/index.mjs'
import ElSelect, { ElOption } from 'element-plus/es/components/select/index.mjs'
import ElTable, { ElTableColumn } from 'element-plus/es/components/table/index.mjs'
import ElTag from 'element-plus/es/components/tag/index.mjs'

import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'

import { get, post, put } from '../../api/client'
import AppPagination from '../../components/AppPagination.vue'
import PageSection from '../../components/PageSection.vue'
import { useLocalPagination } from '../../composables/useLocalPagination'
import { useAuthStore } from '../../stores/auth'
import { formatDateTime, formatNumber, statusType } from '../../utils/format'

const router = useRouter()
const auth = useAuthStore()
const canManage = computed(() => ['production', 'system_admin'].includes(auth.user?.role_code))
const rows = ref([])
const products = ref([])
const salesOrders = ref([])
const productionUsers = ref([])
const dialogVisible = ref(false)
const dialogMode = ref('create')
const editingId = ref(null)

const { pagination, total, pageSizes, pagedItems: pagedRows } = useLocalPagination(rows)

const pendingConvertCount = computed(() => rows.value.filter((item) => !item.converted_task_id).length)
const convertedCount = computed(() => rows.value.filter((item) => !!item.converted_task_id).length)
const urgentCount = computed(() => rows.value.filter((item) => item.priority === 'urgent').length)

const form = reactive({
  plan_name: '',
  period_type: 'custom',
  period_start: '',
  period_end: '',
  product_id: null,
  sales_order_id: null,
  assigned_user_id: null,
  planned_quantity: 1,
  scheduled_start_at: '',
  scheduled_end_at: '',
  priority: 'normal',
  line_name: '',
  scheduling_note: '',
  detail_json: { arrangement: '', shift: '', target_note: '' },
  status: 'planned'
})

function periodTypeLabel(value) {
  return ({
    annual: '年度计划',
    quarterly: '季度计划',
    monthly: '月度计划',
    weekly: '周度计划',
    custom: '自定义计划'
  })[value] || value || '-'
}

function priorityLabel(value) {
  return ({ low: '低', normal: '普通', high: '高', urgent: '紧急' })[value] || value || '-'
}

function resetForm() {
  editingId.value = null
  Object.assign(form, {
    plan_name: '',
    period_type: 'custom',
    period_start: '',
    period_end: '',
    product_id: null,
    sales_order_id: null,
    assigned_user_id: null,
    planned_quantity: 1,
    scheduled_start_at: '',
    scheduled_end_at: '',
    priority: 'normal',
    line_name: '',
    scheduling_note: '',
    detail_json: { arrangement: '', shift: '', target_note: '' },
    status: 'planned'
  })
}

function validatePlanForm() {
  if (form.period_start && form.period_end && new Date(form.period_end).getTime() <= new Date(form.period_start).getTime()) {
    ElMessage.warning('计划结束时间必须晚于计划开始时间')
    return false
  }
  if (form.scheduled_start_at && form.scheduled_end_at && new Date(form.scheduled_end_at).getTime() <= new Date(form.scheduled_start_at).getTime()) {
    ElMessage.warning('计划完工时间必须晚于计划开工时间')
    return false
  }
  return true
}

function openCreate() {
  dialogMode.value = 'create'
  resetForm()
  dialogVisible.value = true
}

function openEdit(row) {
  dialogMode.value = 'edit'
  editingId.value = row.id
  Object.assign(form, {
    plan_name: row.plan_name || '',
    period_type: row.period_type || 'custom',
    period_start: row.period_start || '',
    period_end: row.period_end || '',
    product_id: row.product_id,
    sales_order_id: row.sales_order_id,
    assigned_user_id: row.assigned_user_id,
    planned_quantity: row.planned_quantity || 1,
    scheduled_start_at: row.scheduled_start_at || '',
    scheduled_end_at: row.scheduled_end_at || '',
    priority: row.priority || 'normal',
    line_name: row.line_name || '',
    scheduling_note: row.scheduling_note || '',
    detail_json: {
      arrangement: row.detail_json?.arrangement || '',
      shift: row.detail_json?.shift || '',
      target_note: row.detail_json?.target_note || ''
    },
    status: row.status || 'planned'
  })
  dialogVisible.value = true
}

async function submit() {
  if (!validatePlanForm()) return
  const payload = {
    ...form,
    detail_json: { ...form.detail_json }
  }
  if (dialogMode.value === 'create') {
    await post('/api/production/plans', payload)
    ElMessage.success('生产计划已创建')
  } else {
    await put(`/api/production/plans/${editingId.value}`, payload)
    ElMessage.success('生产计划已更新')
  }
  dialogVisible.value = false
  await load()
}

function exportPlan(row) {
  window.open(`/api/production/plans/${row.id}/export`, '_blank')
}

async function convertToTask(row) {
  await post(`/api/production/plans/${row.id}/convert-task`, {})
  ElMessage.success('生产计划已转为生产任务')
  await load()
}

function goTask(taskId) {
  router.push({ path: '/production/tasks', query: { focusId: String(taskId) } })
}

async function load() {
  rows.value = await get('/api/production/plans')
}

onMounted(async () => {
  const [planRows, productRows, orderRows, userRows] = await Promise.all([
    get('/api/production/plans'),
    get('/api/master-data/products'),
    get('/api/sales/orders'),
    get('/api/production/plan-users')
  ])
  rows.value = planRows
  products.value = productRows
  salesOrders.value = orderRows
  productionUsers.value = userRows
})
</script>

<style scoped>
.page-stack { display: flex; flex-direction: column; gap: 16px; }
.summary-row { margin-bottom: 0; }
.summary-label { color: #909399; margin-bottom: 8px; }
.summary-value { font-size: 28px; font-weight: 700; color: #0f5c90; }
.detail-toolbar { margin-bottom: 12px; font-weight: 600; }
</style>
