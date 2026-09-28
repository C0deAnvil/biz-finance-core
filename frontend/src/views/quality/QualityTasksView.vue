<template>
  <div class="page-stack">
    <el-row :gutter="12" class="summary-row">
      <el-col :span="6"><el-card shadow="hover"><div class="summary-label">当前任务</div><div class="summary-value">{{ activeRows.length }}</div></el-card></el-col>
      <el-col :span="6"><el-card shadow="hover"><div class="summary-label">待录入</div><div class="summary-value">{{ waitingCount }}</div></el-card></el-col>
      <el-col :span="6"><el-card shadow="hover"><div class="summary-label">待异常处理</div><div class="summary-value text-danger">{{ pendingDispositionCount }}</div></el-card></el-col>
      <el-col :span="6"><el-card shadow="hover"><div class="summary-label">历史任务</div><div class="summary-value">{{ historyRows.length }}</div></el-card></el-col>
    </el-row>

    <PageSection title="质检任务">
      <template #actions>
        <el-select v-model="filters.status" clearable placeholder="状态" class="w-180">
          <el-option label="待质检" value="pending" />
          <el-option label="质检中" value="inspecting" />
          <el-option label="待异常处理" value="pending_disposition" />
          <el-option label="待准许入库" value="ready_for_inbound" />
        </el-select>
        <el-input v-model="filters.keyword" clearable placeholder="搜索质检单号 / 批次 / 产品" class="w-260" />
        <el-button @click="load">刷新</el-button>
        <el-button v-if="canOperateQuality" @click="purchaseRequestsPanel?.openCreate()">提交采购需求</el-button>
        <el-button v-if="canOperateQuality" type="primary" @click="openCreate">新增质检任务</el-button>
      </template>
      <el-table :data="pagedActiveRows" stripe :row-class-name="rowClassName">
        <el-table-column prop="quality_no" label="质检任务号" width="160" />
        <el-table-column prop="source_label" label="来源" width="110" />
        <el-table-column prop="purchase_order_no" label="采购订单" width="150" />
        <el-table-column prop="batch_no" label="批次号" min-width="170" />
        <el-table-column prop="product_name" label="产品" min-width="160" />
        <el-table-column label="进度" width="110">
          <template #default="scope">{{ scope.row.completed_tests }}/{{ scope.row.required_tests }}</template>
        </el-table-column>
        <el-table-column label="最新等级" width="110">
          <template #default="scope">
            <el-tag v-if="scope.row.latest_grade" :type="scope.row.latest_grade === '不合格' ? 'danger' : 'success'">{{ scope.row.latest_grade }}</el-tag>
            <span v-else>-</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="140">
          <template #default="scope"><el-tag :type="statusType(scope.row.status)">{{ scope.row.status_label || statusLabel(scope.row.status) }}</el-tag></template>
        </el-table-column>
        <el-table-column label="指定质检员" width="140">
          <template #default="scope">
            <el-tag v-if="isAssignedToMe(scope.row)" type="primary" effect="dark" size="small">{{ scope.row.assigned_inspector_name }}（我）</el-tag>
            <span v-else-if="scope.row.assigned_inspector_name">{{ scope.row.assigned_inspector_name }}</span>
            <span v-else class="text-muted">未指定</span>
          </template>
        </el-table-column>
        <el-table-column label="异常处理" min-width="180">
          <template #default="scope">
            <span v-if="scope.row.disposition_type === 'rework'">返工：{{ scope.row.related_production_task_no || '-' }}</span>
            <span v-else-if="scope.row.disposition_type === 'scrap'">报废：{{ formatNumber(scope.row.disposition_quantity) }}</span>
            <span v-else>-</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="420" fixed="right">
          <template #default="scope">
            <el-button size="small" text type="primary" @click="openDetail(scope.row)">详情</el-button>
            <el-button v-if="canOperateQuality" size="small" @click="openInspection(scope.row)" :disabled="!scope.row.can_record_inspection">录入质检</el-button>
            <el-button v-if="canOperateQuality" size="small" type="warning" @click="openRework(scope.row)" :disabled="!scope.row.can_rework">发起返工</el-button>
            <el-button v-if="canOperateQuality" size="small" type="danger" @click="openScrap(scope.row)" :disabled="!scope.row.can_scrap">登记报废</el-button>
            <el-button v-if="canOperateQuality" size="small" type="success" @click="allowInbound(scope.row)" :disabled="!scope.row.can_allow_inbound">准许入库</el-button>
          </template>
        </el-table-column>
      </el-table>
      <AppPagination
        v-model:page="activePagination.page"
        v-model:page-size="activePagination.pageSize"
        :total="activeTotal"
        :page-sizes="pageSizes"
      />
    </PageSection>

    <PageSection title="历史任务" description="已完成、已报废、已发起返工的任务自动进入历史。">
      <el-table :data="pagedHistoryRows" stripe>
        <el-table-column prop="quality_no" label="质检任务号" width="160" />
        <el-table-column prop="source_label" label="来源" width="110" />
        <el-table-column prop="purchase_order_no" label="采购订单" width="150" />
        <el-table-column prop="batch_no" label="批次号" min-width="170" />
        <el-table-column prop="product_name" label="产品" min-width="160" />
        <el-table-column label="状态" width="140">
          <template #default="scope"><el-tag :type="statusType(scope.row.status)">{{ scope.row.status_label || statusLabel(scope.row.status) }}</el-tag></template>
        </el-table-column>
        <el-table-column label="操作" width="100" fixed="right">
          <template #default="scope">
            <el-button size="small" text type="primary" @click="openDetail(scope.row)">详情</el-button>
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

    <el-dialog v-model="createVisible" title="新增采购来料质检任务" width="860px">
      <el-form :model="createForm" label-width="100px">
        <el-form-item label="采购订单">
          <el-select v-model="createForm.purchase_order_id" filterable style="width: 100%" @change="handlePurchaseOrderChange">
            <el-option v-for="item in purchaseOrders" :key="item.id" :label="`${item.order_no} / ${item.supplier_name || '-'}`" :value="item.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="供应商"><el-input :model-value="selectedOrderDetail?.supplier_name || '-'" disabled /></el-form-item>
        <el-form-item label="批次规则"><el-input :model-value="batchRuleText" disabled /></el-form-item>
        <el-form-item label="质检次数"><el-input-number v-model="createForm.required_tests" :min="1" :max="20" style="width: 100%" /></el-form-item>
        <el-form-item label="指定质检员">
          <el-select v-model="createForm.assigned_inspector_id" clearable placeholder="不指定则由该仓库质检员自行领取" style="width: 100%">
            <el-option v-for="u in qualityInspectors" :key="u.id" :label="u.full_name" :value="u.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="备注"><el-input v-model="createForm.remark" type="textarea" :rows="3" /></el-form-item>
        <el-table :data="previewRows" border>
          <el-table-column prop="item_type_label" label="类型" width="100" />
          <el-table-column prop="item_name" label="物料/产品" min-width="180" />
          <el-table-column prop="item_code" label="编码" width="140" />
          <el-table-column prop="display_specification" label="规格" min-width="120" />
          <el-table-column prop="quantity" label="数量" width="120">
            <template #default="scope">{{ formatNumber(scope.row.quantity) }}</template>
          </el-table-column>
          <el-table-column prop="batch_no" label="批次号" min-width="160" />
        </el-table>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" @click="submitCreate">提交</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="inspectionVisible" title="录入质检结果" width="620px">
      <el-form :model="inspectionForm" label-width="90px">
        <el-form-item label="序号"><el-input-number v-model="inspectionForm.sequence_no" :min="1" style="width: 100%" /></el-form-item>
        <el-form-item label="结果描述"><el-input v-model="inspectionForm.result_text" type="textarea" :rows="4" /></el-form-item>
        <el-form-item label="产品等级">
          <el-select v-model="inspectionForm.grade" style="width: 100%">
            <el-option label="A" value="A" />
            <el-option label="B" value="B" />
            <el-option label="C" value="C" />
            <el-option label="不合格" value="不合格" />
          </el-select>
        </el-form-item>
        <el-form-item label="是否通过"><el-switch v-model="inspectionForm.is_passed" /></el-form-item>
        <el-form-item label="检验数量"><el-input-number v-model="inspectionForm.inspected_quantity" :min="0.01" style="width: 100%" /></el-form-item>
        <el-form-item label="取样耗用"><el-input-number v-model="inspectionForm.sample_consumption_qty" :min="0" :precision="2" style="width: 100%" /></el-form-item>
        <el-form-item label="报告摘要"><el-input v-model="inspectionForm.report_summary" type="textarea" :rows="3" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="inspectionVisible = false">取消</el-button>
        <el-button type="primary" @click="submitInspection">提交</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="reworkVisible" title="发起返工" width="520px">
      <el-form :model="reworkForm" label-width="90px">
        <el-form-item label="返工数量"><el-input-number v-model="reworkForm.quantity" :min="0.01" style="width: 100%" /></el-form-item>
        <el-form-item label="返工原因"><el-input v-model="reworkForm.reason" type="textarea" :rows="4" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="reworkVisible = false">取消</el-button>
        <el-button type="warning" @click="submitRework">确认发起</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="scrapVisible" title="登记报废" width="520px">
      <el-form :model="scrapForm" label-width="90px">
        <el-form-item label="报废数量"><el-input-number v-model="scrapForm.quantity" :min="0.01" style="width: 100%" /></el-form-item>
        <el-form-item label="报废原因"><el-input v-model="scrapForm.reason" type="textarea" :rows="4" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="scrapVisible = false">取消</el-button>
        <el-button type="danger" @click="submitScrap">确认登记</el-button>
      </template>
    </el-dialog>

    <el-drawer v-model="detailVisible" size="60%" title="质检任务详情">
      <el-descriptions v-if="detail" :column="2" border>
        <el-descriptions-item label="质检任务号">{{ detail.quality_no }}</el-descriptions-item>
        <el-descriptions-item label="来源">{{ detail.source_label || '-' }}</el-descriptions-item>
        <el-descriptions-item label="采购订单">{{ detail.purchase_order_no || '-' }}</el-descriptions-item>
        <el-descriptions-item label="到货单">{{ detail.purchase_receipt_no || '-' }}</el-descriptions-item>
        <el-descriptions-item label="供应商">{{ detail.supplier_name || '-' }}</el-descriptions-item>
        <el-descriptions-item label="仓库">{{ detail.warehouse_name || '-' }}</el-descriptions-item>
        <el-descriptions-item label="产品">{{ detail.product_name || '-' }}</el-descriptions-item>
        <el-descriptions-item label="批次号">{{ detail.batch_no }}</el-descriptions-item>
        <el-descriptions-item label="质检进度">{{ detail.completed_tests }}/{{ detail.required_tests }}</el-descriptions-item>
        <el-descriptions-item label="状态">{{ detail.status_label || statusLabel(detail.status) }}</el-descriptions-item>
        <el-descriptions-item label="累计取样耗用">{{ formatNumber(detail.sample_consumption_total) }}</el-descriptions-item>
        <el-descriptions-item label="最新报告摘要">{{ detail.latest_report_summary || '-' }}</el-descriptions-item>
        <el-descriptions-item label="来源任务">{{ detail.production_task_no || '-' }}</el-descriptions-item>
        <el-descriptions-item label="流程提示">{{ detail.workflow_hint || '-' }}</el-descriptions-item>
      </el-descriptions>
      <div class="detail-toolbar mt-16"><span>质检记录</span></div>
      <el-table :data="detail?.inspections || []" stripe>
        <el-table-column prop="sequence_no" label="序号" width="80" />
        <el-table-column prop="grade" label="等级" width="110">
          <template #default="scope"><el-tag :type="scope.row.grade === '不合格' ? 'danger' : 'success'">{{ scope.row.grade }}</el-tag></template>
        </el-table-column>
        <el-table-column prop="is_passed" label="是否通过" width="100">
          <template #default="scope">{{ scope.row.is_passed ? '通过' : '不通过' }}</template>
        </el-table-column>
        <el-table-column prop="inspected_quantity" label="检验数量" width="110"><template #default="scope">{{ formatNumber(scope.row.inspected_quantity) }}</template></el-table-column>
        <el-table-column prop="sample_consumption_qty" label="取样耗用" width="110"><template #default="scope">{{ formatNumber(scope.row.sample_consumption_qty) }}</template></el-table-column>
        <el-table-column prop="result_text" label="结果描述" min-width="220" show-overflow-tooltip />
        <el-table-column prop="report_summary" label="报告摘要" min-width="220" show-overflow-tooltip />
        <el-table-column prop="inspected_at" label="检验时间" min-width="170"><template #default="scope">{{ formatDateTime(scope.row.inspected_at) }}</template></el-table-column>
        <el-table-column label="附件" width="100">
          <template #default="scope">
            <el-button size="small" text type="primary" @click="openAttachment(scope.row)">照片</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div class="detail-toolbar mt-16"><span>异常处理</span></div>
      <el-descriptions :column="1" border>
        <el-descriptions-item label="处理结果">{{ dispositionLabel(detail?.disposition?.type) }}</el-descriptions-item>
        <el-descriptions-item label="数量">{{ detail?.disposition ? formatNumber(detail.disposition.quantity) : '-' }}</el-descriptions-item>
        <el-descriptions-item label="关联返工任务">{{ detail?.disposition?.related_production_task_no || '-' }}</el-descriptions-item>
        <el-descriptions-item label="原因">{{ detail?.disposition?.reason || '-' }}</el-descriptions-item>
      </el-descriptions>
    </el-drawer>

    <el-dialog v-model="attachmentVisible" title="质检照片上传" width="760px">
      <AttachmentUploader v-if="attachmentTargetId" business-type="quality_inspection" :business-id="attachmentTargetId" :readonly="!canUploadAttachments" />
    </el-dialog>
    <DepartmentPurchaseRequests v-if="auth.isRole('quality')" ref="purchaseRequestsPanel" />
  </div>
</template>

<script setup>
import 'element-plus/es/components/message/style/css'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/card/style/css'
import 'element-plus/es/components/col/style/css'
import 'element-plus/es/components/descriptions/style/css'
import 'element-plus/es/components/dialog/style/css'
import 'element-plus/es/components/drawer/style/css'
import 'element-plus/es/components/form/style/css'
import 'element-plus/es/components/input-number/style/css'
import 'element-plus/es/components/input/style/css'
import 'element-plus/es/components/row/style/css'
import 'element-plus/es/components/select/style/css'
import 'element-plus/es/components/switch/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/tag/style/css'

import { ElMessage } from 'element-plus/es/components/message/index.mjs'

import ElButton from 'element-plus/es/components/button/index.mjs'
import ElCard from 'element-plus/es/components/card/index.mjs'
import ElCol from 'element-plus/es/components/col/index.mjs'
import ElDescriptions, { ElDescriptionsItem } from 'element-plus/es/components/descriptions/index.mjs'
import ElDialog from 'element-plus/es/components/dialog/index.mjs'
import ElDrawer from 'element-plus/es/components/drawer/index.mjs'
import ElForm, { ElFormItem } from 'element-plus/es/components/form/index.mjs'
import ElInputNumber from 'element-plus/es/components/input-number/index.mjs'
import ElInput from 'element-plus/es/components/input/index.mjs'
import ElRow from 'element-plus/es/components/row/index.mjs'
import ElSelect, { ElOption } from 'element-plus/es/components/select/index.mjs'
import ElSwitch from 'element-plus/es/components/switch/index.mjs'
import ElTable, { ElTableColumn } from 'element-plus/es/components/table/index.mjs'
import ElTag from 'element-plus/es/components/tag/index.mjs'

import { computed, onMounted, reactive, ref, watch } from 'vue'

import { get, post } from '../../api/client'
import { useRouteFocus } from '../../composables/useRouteFocus'
import AttachmentUploader from '../../components/AttachmentUploader.vue'
import AppPagination from '../../components/AppPagination.vue'
import PageSection from '../../components/PageSection.vue'
import DepartmentPurchaseRequests from '../../components/DepartmentPurchaseRequests.vue'
import { useLocalPagination } from '../../composables/useLocalPagination'
import { usePagePermission } from '../../composables/usePagePermission'
import { useQualityInspectors } from '../../composables/useQualityInspectors'
import { useAuthStore } from '../../stores/auth'
import { formatDateTime, formatNumber, statusLabel, statusType } from '../../utils/format'

const auth = useAuthStore()
const pagePermission = usePagePermission()
const { qualityInspectors, loadQualityInspectors } = useQualityInspectors()
const { route, focusedId: focusedTaskId, applyFocus: applyRouteFocus } = useRouteFocus()

const rows = ref([])
const purchaseRequestsPanel = ref(null)
const purchaseOrders = ref([])
const selectedOrderDetail = ref(null)
const activeTask = ref(null)
const detail = ref(null)
const createVisible = ref(false)
const inspectionVisible = ref(false)
const detailVisible = ref(false)
const attachmentVisible = ref(false)
const reworkVisible = ref(false)
const scrapVisible = ref(false)
const attachmentTargetId = ref(null)

const canOperateQuality = computed(() => auth.isRole('quality') && pagePermission.can('inspect'))
const canUploadAttachments = computed(() => auth.isRole('quality') && pagePermission.can('upload'))

const filters = reactive({ status: '', keyword: '' })
const createForm = reactive({ purchase_order_id: null, required_tests: 1, remark: '', assigned_inspector_id: null })
const inspectionForm = reactive({ sequence_no: 1, result_text: '', grade: 'A', is_passed: true, inspected_quantity: 1, sample_consumption_qty: 0, report_summary: '' })
const reworkForm = reactive({ quantity: 1, reason: '' })
const scrapForm = reactive({ quantity: 1, reason: '' })

const filteredRows = computed(() => {
  const keyword = filters.keyword.trim().toLowerCase()
  return rows.value.filter((item) => {
    if (filters.status && item.status !== filters.status) return false
    if (!keyword) return true
    return [item.quality_no, item.batch_no, item.product_name, item.purchase_order_no, item.item_name].filter(Boolean).some((value) => String(value).toLowerCase().includes(keyword))
  })
})

const activeRows = computed(() => filteredRows.value.filter((item) => !['completed', 'rework_created', 'scrapped'].includes(item.status)))
const historyRows = computed(() => filteredRows.value.filter((item) => ['completed', 'rework_created', 'scrapped'].includes(item.status)))
const {
  pagination: activePagination,
  total: activeTotal,
  pageSizes,
  pagedItems: pagedActiveRows,
} = useLocalPagination(activeRows)
const {
  pagination: historyPagination,
  total: historyTotal,
  pagedItems: pagedHistoryRows,
} = useLocalPagination(historyRows)
const waitingCount = computed(() => activeRows.value.filter((item) => ['pending', 'inspecting'].includes(item.status)).length)
const pendingDispositionCount = computed(() => activeRows.value.filter((item) => item.status === 'pending_disposition').length)
const batchRuleText = computed(() => {
  if (!selectedOrderDetail.value?.order_no) return '-'
  const count = selectedOrderDetail.value?.items?.length || 0
  return count <= 1 ? `${selectedOrderDetail.value.order_no}-ZJ` : `${selectedOrderDetail.value.order_no}-ZJ-序号`
})
const previewRows = computed(() => {
  const order = selectedOrderDetail.value
  if (!order?.items?.length) return []
  const total = order.items.length
  return order.items.map((item, index) => ({
    item_type_label: item.product_id ? '产品' : '原料',
    item_name: item.product_name || item.material_name || '-',
    item_code: item.product_code || item.material_code || '-',
    display_specification: item.display_specification || '-',
    quantity: Number(item.received_quantity || item.quantity || 0),
    batch_no: total <= 1 ? `${order.order_no}-ZJ` : `${order.order_no}-ZJ-${String(index + 1).padStart(2, '0')}`
  })).filter((item) => Number(item.quantity || 0) > 0)
})

function dispositionLabel(value) {
  if (value === 'rework') return '返工'
  if (value === 'scrap') return '报废'
  return value || '-'
}

function rowClassName({ row }) {
  return focusedTaskId.value && row.id === focusedTaskId.value ? 'focused-row' : ''
}

async function applyFocusFromRoute() {
  await applyRouteFocus(
    (focusId) => rows.value.find((item) => item.id === focusId),
    async (row) => {
      await openDetail(row)
    }
  )
}

async function load() {
  rows.value = await get('/api/quality/tasks')
  await applyFocusFromRoute()
}

// 派给当前账号的任务高亮,质检员一眼能看出哪些该自己做
function isAssignedToMe(row) {
  return Boolean(row?.assigned_inspector_id) && Number(row.assigned_inspector_id) === Number(auth.user?.id)
}

function openCreate() {
  createForm.purchase_order_id = null
  createForm.required_tests = 1
  createForm.remark = ''
  createForm.assigned_inspector_id = null
  selectedOrderDetail.value = null
  createVisible.value = true
}

async function handlePurchaseOrderChange(orderId) {
  if (!orderId) {
    selectedOrderDetail.value = null
    return
  }
  selectedOrderDetail.value = await get(`/api/purchases/orders/${orderId}`)
}

async function submitCreate() {
  if (!createForm.purchase_order_id) {
    ElMessage.warning('请选择采购订单')
    return
  }
  if (!previewRows.value.length) {
    ElMessage.warning('采购订单没有可生成的质检明细')
    return
  }
  const result = await post('/api/quality/tasks/manual', createForm)
  ElMessage.success(`已创建 ${result.count || 1} 条质检任务`)
  createVisible.value = false
  await load()
}

async function openDetail(row) {
  detail.value = await get(`/api/quality/tasks/${row.id}`)
  detailVisible.value = true
}

function openInspection(row) {
  activeTask.value = row
  inspectionForm.sequence_no = Number(row.completed_tests || 0) + 1
  inspectionForm.result_text = ''
  inspectionForm.grade = 'A'
  inspectionForm.is_passed = true
  inspectionForm.inspected_quantity = 1
  inspectionForm.sample_consumption_qty = 0
  inspectionForm.report_summary = ''
  inspectionVisible.value = true
}

async function submitInspection() {
  const result = await post('/api/quality/inspections', { ...inspectionForm, quality_task_id: activeTask.value.id })
  ElMessage.success('质检记录已提交')
  inspectionVisible.value = false
  await load()
  attachmentTargetId.value = result.id
  attachmentVisible.value = true
}

function openRework(row) {
  activeTask.value = row
  reworkForm.quantity = 1
  reworkForm.reason = ''
  reworkVisible.value = true
}

async function submitRework() {
  const result = await post(`/api/quality/tasks/${activeTask.value.id}/rework`, reworkForm)
  ElMessage.success(`已生成返工任务：${result.rework_task_no}`)
  reworkVisible.value = false
  await load()
}

function openScrap(row) {
  activeTask.value = row
  scrapForm.quantity = 1
  scrapForm.reason = ''
  scrapVisible.value = true
}

async function submitScrap() {
  await post(`/api/quality/tasks/${activeTask.value.id}/scrap`, scrapForm)
  ElMessage.success('报废记录已登记')
  scrapVisible.value = false
  await load()
}

async function allowInbound(row) {
  await post(`/api/quality/tasks/${row.id}/allow-inbound`, {})
  ElMessage.success('已准许入库，并已通知生产继续处理')
  await load()
}

function openAttachment(row) {
  attachmentTargetId.value = row.id
  attachmentVisible.value = true
}

watch(
  () => route.query.focusId,
  async () => {
    await applyFocusFromRoute()
  }
)

onMounted(async () => {
  const orderData = await get('/api/purchases/orders?tab=all&page=1&page_size=100')
  purchaseOrders.value = Array.isArray(orderData) ? orderData : (orderData.items || [])
  await Promise.all([loadQualityInspectors(), load()])
})
</script>

<style scoped>
.page-stack { display: flex; flex-direction: column; gap: 16px; }
.summary-row { margin-bottom: 0; }
.summary-label { color: #909399; margin-bottom: 8px; }
.summary-value { font-size: 28px; font-weight: 700; color: #0f5c90; }
.text-danger { color: #f56c6c; }
.w-180 { width: 180px; }
.w-260 { width: 260px; }
.mb-12 { margin-bottom: 12px; }
.mt-16 { margin-top: 16px; }
.detail-toolbar { display: flex; justify-content: space-between; align-items: center; font-weight: 600; margin-bottom: 10px; }
.focused-row > td { background: #f0f9eb !important; }

@media (max-width: 768px) {
  .detail-toolbar {
    align-items: flex-start;
    flex-wrap: wrap;
    gap: 8px;
  }

  .quality-page :deep(.el-table),
  .page-stack :deep(.el-table) {
    min-width: 720px;
  }

  .quality-page :deep(.el-table__inner-wrapper),
  .page-stack :deep(.el-table__inner-wrapper) {
    overflow-x: auto;
  }
}
</style>
