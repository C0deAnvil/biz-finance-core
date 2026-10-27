<template>
  <div class="receipts-page">
    <PageSection title="未来七天预计到货" description="按采购单预计到货日期统计；逾期未到货单独提示，未填写日期的订单不计入图表。">
      <div class="arrival-overview">
        <div class="overdue-summary">逾期未到货 <strong>{{ overdueCount }}</strong> 单</div>
        <div v-for="day in arrivalDays" :key="day.date" class="arrival-day">
          <span class="arrival-day-label">{{ day.label }}</span>
          <span class="arrival-bar-track"><span class="arrival-bar" :style="{ width: `${day.percent}%` }" /></span>
          <strong>{{ day.count }} 单</strong>
        </div>
      </div>
    </PageSection>

    <PageSection title="待到货采购订单" description="已批准、已下单且尚有未到货明细的采购单，按预计到货时间排列；到货后从对应订单直接登记。">
      <template #actions>
        <el-input v-model="arrivalKeyword" clearable placeholder="搜索采购单、供应商或物品" style="width: 235px" />
        <el-select v-model="arrivalStatus" aria-label="到货状态" style="width: 150px">
          <el-option label="全部状态" value="all" />
          <el-option label="待采购确认" value="pending_purchase_confirm" />
          <el-option label="采购中" value="purchasing" />
          <el-option label="部分到货" value="partial_received" />
        </el-select>
        <el-select v-model="arrivalWarehouseId" aria-label="目标仓库" clearable placeholder="全部仓库" style="width: 155px">
          <el-option v-for="item in warehouses" :key="item.id" :label="item.name" :value="item.id" />
        </el-select>
        <el-button @click="load">刷新</el-button>
      </template>
      <el-table :data="pagedArrivalRows" stripe>
        <el-table-column prop="order_no" label="采购单号" width="175" />
        <el-table-column prop="supplier_name" label="供应商" min-width="180" show-overflow-tooltip />
        <el-table-column prop="target_warehouse_name" label="目标仓库" width="140" />
        <el-table-column prop="item_summary" label="待到货物品" min-width="220" show-overflow-tooltip />
        <el-table-column prop="pending_item_count" label="待到货明细" width="110" />
        <el-table-column label="预计到货" width="140">
          <template #default="scope"><span :class="{ 'is-overdue': isOverdue(scope.row.expected_arrival_date) }">{{ scope.row.expected_arrival_date || '未填写' }}</span></template>
        </el-table-column>
        <el-table-column label="状态" width="120"><template #default="scope"><el-tag :type="statusType(scope.row.status)">{{ scope.row.status_label || statusLabel(scope.row.status) }}</el-tag></template></el-table-column>
        <el-table-column label="操作" width="130" fixed="right">
          <template #default="scope">
            <el-button v-if="canCreate && scope.row.can_receive" link type="primary" @click="openCreate(scope.row)">登记到货</el-button>
            <span v-else-if="!scope.row.can_receive" class="form-tip">待采购确认</span>
            <span v-else class="form-tip">无操作权限</span>
          </template>
        </el-table-column>
      </el-table>
      <AppPagination v-model:page="arrivalPagination.page" v-model:page-size="arrivalPagination.pageSize" :total="arrivalTotal" :page-sizes="arrivalPageSizes" />
    </PageSection>

    <PageSection title="到货与入库历史" description="保留每次到货登记；待质检、待入库和已入库状态分开显示，可查看凭证及执行已获准的入库作业。">
      <template #actions>
        <el-input v-model="historyKeyword" clearable placeholder="搜索到货单、采购单或供应商" style="width: 245px" />
        <el-select v-model="historyStatus" aria-label="入库状态" style="width: 130px">
          <el-option label="全部状态" value="all" />
          <el-option label="待质检" value="pending_quality" />
          <el-option label="待入库" value="pending_inbound" />
          <el-option label="已入库" value="completed" />
        </el-select>
        <el-button @click="load">刷新</el-button>
      </template>
      <el-table :data="pagedHistoryRows" stripe>
      <el-table-column prop="receipt_no" label="入库单号" width="150" />
      <el-table-column prop="purchase_order_no" label="采购单号" width="150" />
      <el-table-column prop="supplier_name" label="供应商" min-width="180" show-overflow-tooltip />
      <el-table-column prop="warehouse_name" label="仓库" width="140" />
      <el-table-column prop="expected_arrival_date" label="预计到货" width="135"><template #default="scope">{{ scope.row.expected_arrival_date || '-' }}</template></el-table-column>
      <el-table-column prop="item_count" label="明细数" width="100" />
      <el-table-column label="质检状态" width="120">
        <template #default="scope">
          <el-tag :type="scope.row.requires_quality ? statusType(scope.row.quality_status) : 'info'">
            {{ scope.row.requires_quality ? (scope.row.quality_status_label || statusLabel(scope.row.quality_status)) : '无需质检' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="120">
        <template #default="scope">
          <el-tag :type="statusType(scope.row.status)">{{ scope.row.status_label || statusLabel(scope.row.status) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="received_by_name" label="操作人" width="120" />
      <el-table-column prop="created_at" label="创建时间" min-width="170">
        <template #default="scope">{{ formatDateTime(scope.row.created_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="165" fixed="right">
        <template #default="scope">
          <el-button size="small" text type="primary" @click="openDetail(scope.row)">详情</el-button>
          <el-button v-if="canExecuteInventoryRequest(scope.row)" size="small" text type="success" @click="executeInventoryRequest(scope.row)">执行入库</el-button>
        </template>
      </el-table-column>
    </el-table>
    <AppPagination
      v-model:page="historyPagination.page"
      v-model:page-size="historyPagination.pageSize"
      :total="historyTotal"
      :page-sizes="historyPageSizes"
    />
    </PageSection>

    <el-dialog v-model="dialogVisible" title="采购到货登记" width="980px">
      <el-form label-width="90px">
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="采购单"><strong>{{ selectedOrder?.order_no || '-' }}</strong></el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="入库仓库">
              <el-select v-model="form.warehouse_id" :disabled="Boolean(selectedOrderTargetId)" style="width: 100%" @change="handleWarehouseChange">
                <el-option v-for="item in warehouses" :key="item.id" :label="item.name" :value="item.id" />
              </el-select>
              <div v-if="selectedOrderTargetId" class="form-tip">由采购单目标仓库确定；要变更请先修改采购单。</div>
            </el-form-item>
          </el-col>
        </el-row>
        <el-form-item label="到货时间">
          <el-date-picker v-model="form.recorded_at" type="datetime" value-format="YYYY-MM-DDTHH:mm:ss" style="width: 100%" />
        </el-form-item>
        <el-form-item label="初步验收">
          <el-input v-model="form.preliminary_acceptance_note" type="textarea" :rows="3" placeholder="记录外观、包装、数量初验、异常说明等，便于后续追溯" />
        </el-form-item>
        <el-form-item label="到货照片">
          <PhotoAttachments
            :key="`receipt-${selectedOrderId}`"
            business-type="purchase_order"
            :business-id="selectedOrderId"
            attachment-type="到货照片"
            title="到货照片"
            class="receipt-photos"
          />
          <span v-if="!selectedOrderId" class="photo-placeholder">请先选择上方采购单，再选择并上传到货照片</span>
        </el-form-item>
        <el-form-item label="备注"><el-input v-model="form.remark" /></el-form-item>

        <el-form-item label="是否质检">
          <el-switch v-model="form.requires_quality" :disabled="isInspectionWarehouse" />
        </el-form-item>
        <el-table :data="form.items" border>
          <el-table-column prop="name" label="物料/产品" min-width="180" />
          <el-table-column prop="pending_quantity" label="待收数量" width="120">
            <template #default="scope">{{ formatNumber(scope.row.pending_quantity) }}</template>
          </el-table-column>
          <el-table-column label="本次到货" width="140">
            <template #default="scope"><el-input-number v-model="scope.row.received_quantity" :min="0" style="width: 100%" /></template>
          </el-table-column>
          <el-table-column label="差异备注" min-width="220">
            <template #default="scope">
              <el-input v-model="scope.row.difference_remark" placeholder="应到/实到不一致时必填" />
            </template>
          </el-table-column>
          <el-table-column label="批次号" min-width="180"><template #default="scope"><el-input v-model="scope.row.batch_no" /></template></el-table-column>
          <el-table-column label="成本单价" width="140"><template #default="scope"><el-input-number v-model="scope.row.unit_cost" :min="0" style="width: 100%" /></template></el-table-column>
        </el-table>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="submit">确认到货登记</el-button>
      </template>
    </el-dialog>

      <el-drawer v-model="detailVisible" title="采购到货详情" size="52%">
      <el-descriptions v-if="detail" :column="2" border>
        <el-descriptions-item label="入库单号">{{ detail.receipt_no }}</el-descriptions-item>
        <el-descriptions-item label="采购单号">{{ detail.purchase_order_no || '-' }}</el-descriptions-item>
        <el-descriptions-item label="供应商">{{ detail.supplier_name || '-' }}</el-descriptions-item>
        <el-descriptions-item label="仓库">{{ detail.warehouse_name || '-' }}</el-descriptions-item>
        <el-descriptions-item label="状态">{{ detail.status_label || statusLabel(detail.status) }}</el-descriptions-item>
        <el-descriptions-item label="操作人">{{ detail.received_by_name || '-' }}</el-descriptions-item>
        <el-descriptions-item label="库存作业单">{{ detail.inventory_request_no || '-' }}</el-descriptions-item>
        <el-descriptions-item label="入库作业状态">{{ detail.inventory_request_status_label || '-' }}</el-descriptions-item>
        <el-descriptions-item label="到货时间">{{ formatDateTime(detail.recorded_at) }}</el-descriptions-item>
        <el-descriptions-item label="初步验收" :span="2">{{ detail.preliminary_acceptance_note || '-' }}</el-descriptions-item>
        <el-descriptions-item label="备注" :span="2">{{ detail.remark || '-' }}</el-descriptions-item>
      </el-descriptions>
      <template v-if="detail?.purchase_order_id">
        <el-divider content-position="left">到货照片</el-divider>
        <PhotoAttachments
          :key="`receipt-detail-${detail.purchase_order_id}`"
          business-type="purchase_order"
          :business-id="detail.purchase_order_id"
          attachment-type="到货照片"
          title="到货照片"
          readonly
        />
      </template>
      <div v-if="canExecuteInventoryRequest(detail)" class="detail-actions mt-16">
        <el-button type="success" @click="executeInventoryRequest(detail)">一键入库</el-button>
        <el-button type="primary" plain @click="goInventoryExecute(detail)">去库存作业执行</el-button>
      </div>
      <el-table :data="detail?.items || []" stripe class="mt-16">
        <el-table-column prop="item_code" label="编码" width="140" />
        <el-table-column prop="item_name" label="物料/产品" min-width="180" />
        <el-table-column prop="received_quantity" label="到货数量" width="120">
          <template #default="scope">{{ formatNumber(scope.row.received_quantity) }}</template>
        </el-table-column>
        <el-table-column prop="expected_quantity" label="应到数量" width="120">
          <template #default="scope">{{ formatNumber(scope.row.expected_quantity) }}</template>
        </el-table-column>
        <el-table-column prop="difference_remark" label="差异备注" min-width="180" show-overflow-tooltip />
        <el-table-column prop="batch_no" label="批次号" min-width="150" />
        <el-table-column prop="unit_cost" label="成本单价" width="120">
          <template #default="scope">{{ formatNumber(scope.row.unit_cost) }}</template>
        </el-table-column>
        <el-table-column prop="total_cost" label="总成本" width="120">
          <template #default="scope">{{ formatNumber(scope.row.total_cost) }}</template>
        </el-table-column>
      </el-table>
    </el-drawer>
  </div>
</template>

<script setup>
import 'element-plus/es/components/message/style/css'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/col/style/css'
import 'element-plus/es/components/date-picker/style/css'
import 'element-plus/es/components/descriptions/style/css'
import 'element-plus/es/components/dialog/style/css'
import 'element-plus/es/components/divider/style/css'
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
import ElCol from 'element-plus/es/components/col/index.mjs'
import ElDatePicker from 'element-plus/es/components/date-picker/index.mjs'
import ElDescriptions, { ElDescriptionsItem } from 'element-plus/es/components/descriptions/index.mjs'
import ElDialog from 'element-plus/es/components/dialog/index.mjs'
import ElDivider from 'element-plus/es/components/divider/index.mjs'
import ElDrawer from 'element-plus/es/components/drawer/index.mjs'
import ElForm, { ElFormItem } from 'element-plus/es/components/form/index.mjs'
import ElInputNumber from 'element-plus/es/components/input-number/index.mjs'
import ElInput from 'element-plus/es/components/input/index.mjs'
import ElRow from 'element-plus/es/components/row/index.mjs'
import ElSelect, { ElOption } from 'element-plus/es/components/select/index.mjs'
import ElSwitch from 'element-plus/es/components/switch/index.mjs'
import ElTable, { ElTableColumn } from 'element-plus/es/components/table/index.mjs'
import ElTag from 'element-plus/es/components/tag/index.mjs'

import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'

import { get, post } from '../../api/client'
import AppPagination from '../../components/AppPagination.vue'
import PageSection from '../../components/PageSection.vue'
import PhotoAttachments from '../../components/PhotoAttachments.vue'
import { useLocalPagination } from '../../composables/useLocalPagination'
import { usePagePermission } from '../../composables/usePagePermission'
import { useAuthStore } from '../../stores/auth'
import { formatDateTime, formatNumber, statusLabel, statusType } from '../../utils/format'

const auth = useAuthStore()
const pagePermission = usePagePermission()
const router = useRouter()
const rows = ref([])
const arrivalOrders = ref([])
const detail = ref(null)
const warehouses = ref([])
const dialogVisible = ref(false)
const detailVisible = ref(false)
const selectedOrderId = ref(null)
const selectedOrder = computed(() => arrivalOrders.value.find((order) => order.id === selectedOrderId.value) || null)
const submitting = ref(false)
const form = reactive({ warehouse_id: null, requires_quality: false, remark: '', preliminary_acceptance_note: '', recorded_at: '', items: [] })
const selectedOrderTargetId = ref(null)
const canCreate = computed(() => ['purchase', 'inventory'].includes(auth.user?.role_code) && pagePermission.can('create'))
const selectedWarehouse = computed(() => warehouses.value.find((item) => item.id === form.warehouse_id) || null)
const isInspectionWarehouse = computed(() => isInspectionWarehouseOption(selectedWarehouse.value))
const receiptAllowedStatuses = ['purchasing', 'partial_received']
const arrivalKeyword = ref('')
const arrivalStatus = ref('all')
const arrivalWarehouseId = ref(null)
const historyKeyword = ref('')
const historyStatus = ref('all')
const filteredArrivalRows = computed(() => arrivalOrders.value.filter((order) => {
  if (arrivalStatus.value !== 'all' && order.status !== arrivalStatus.value) return false
  if (arrivalWarehouseId.value && order.target_warehouse_id !== arrivalWarehouseId.value) return false
  const keyword = arrivalKeyword.value.trim().toLowerCase()
  return !keyword || [order.order_no, order.supplier_name, order.item_summary].some((text) => String(text || '').toLowerCase().includes(keyword))
}))
const filteredHistoryRows = computed(() => rows.value.filter((receipt) => {
  if (historyStatus.value !== 'all' && receipt.status !== historyStatus.value) return false
  const keyword = historyKeyword.value.trim().toLowerCase()
  return !keyword || [receipt.receipt_no, receipt.purchase_order_no, receipt.supplier_name, receipt.warehouse_name].some((text) => String(text || '').toLowerCase().includes(keyword))
}))
const { pagination: arrivalPagination, total: arrivalTotal, pageSizes: arrivalPageSizes, pagedItems: pagedArrivalRows } = useLocalPagination(filteredArrivalRows)
const { pagination: historyPagination, total: historyTotal, pageSizes: historyPageSizes, pagedItems: pagedHistoryRows } = useLocalPagination(filteredHistoryRows)

function dateKey(date) {
  const pad = (value) => String(value).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
}

const todayKey = ref(dateKey(new Date()))
const overdueCount = computed(() => arrivalOrders.value.filter((order) => order.expected_arrival_date && order.expected_arrival_date < todayKey.value).length)
const arrivalDays = computed(() => {
  const start = new Date(`${todayKey.value}T12:00:00`)
  const counts = Array.from({ length: 7 }, (_, offset) => {
    const date = new Date(start)
    date.setDate(start.getDate() + offset)
    const key = dateKey(date)
    return { date: key, label: offset === 0 ? '今天' : `${date.getMonth() + 1}/${date.getDate()}`, count: arrivalOrders.value.filter((order) => order.expected_arrival_date === key).length }
  })
  const maxCount = Math.max(1, ...counts.map((day) => day.count))
  return counts.map((day) => ({ ...day, percent: day.count ? Math.max(10, Math.round(day.count / maxCount * 100)) : 0 }))
})

function isOverdue(date) {
  return Boolean(date && date < todayKey.value)
}

function resetForm() {
  selectedOrderId.value = null
  selectedOrderTargetId.value = null
  form.warehouse_id = null
  form.requires_quality = false
  form.remark = ''
  form.preliminary_acceptance_note = ''
  form.recorded_at = currentDateTime()
  form.items = []
}

function isInspectionWarehouseOption(warehouse) {
  if (!warehouse) return false
  const code = String(warehouse.code || '').toLowerCase()
  const type = String(warehouse.warehouse_type || '').toLowerCase()
  const name = String(warehouse.name || '')
  return type.includes('inspection') || code.includes('inspection') || name.includes('待检')
}

function canReceiveOrder(order) {
  return Boolean(order?.can_receive && receiptAllowedStatuses.includes(order.status))
}

function handleWarehouseChange() {
  if (isInspectionWarehouse.value) {
    form.requires_quality = true
  }
}

function currentDateTime() {
  const date = new Date()
  const pad = (value) => String(value).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}:00`
}

async function openCreate(order) {
  if (!canCreate.value || !canReceiveOrder(order)) return
  resetForm()
  selectedOrderId.value = order.id
  try {
    await loadOrderDetail(order.id)
  } catch (error) {
    ElMessage.error(error.message || '读取采购单失败')
    return
  }
  if (!selectedOrderId.value) return
  dialogVisible.value = true
}

async function load() {
  const [receiptRows, expectedOrders] = await Promise.all([
    get('/api/purchases/receipts'),
    get('/api/purchases/receipts/arrival-board')
  ])
  rows.value = receiptRows || []
  arrivalOrders.value = expectedOrders || []
  todayKey.value = dateKey(new Date())
}

async function openDetail(row) {
  detail.value = await get(`/api/purchases/receipts/${row.id}`)
  detailVisible.value = true
}

function canExecuteInventoryRequest(row) {
  return auth.isRole('inventory')
    && row?.inventory_request_id
    && ['pending', 'processing'].includes(row?.inventory_request_status)
}

async function executeInventoryRequest(row) {
  if (!row?.inventory_request_id) return
  try {
    const requestDetail = await get(`/api/inventory/operation-requests/${row.inventory_request_id}`)
    const items = (requestDetail.original_items || []).map((item) => ({
      product_id: item.product_id || null,
      material_id: item.material_id || null,
      packaging_config_id: item.packaging_config_id || null,
      batch_no: item.batch_no || null,
      quantity: item.quantity,
      unit_cost: item.unit_cost || 0
    }))
    await post(`/api/inventory/operation-requests/${row.inventory_request_id}/complete`, {
      items,
      handled_remark: '采购到货一键入库',
      adjust_reason: null,
      quantity_difference_remark: null
    })
    ElMessage.success('采购到货已完成入库')
    detail.value = await get(`/api/purchases/receipts/${row.id}`)
    await load()
  } catch (error) {
    ElMessage.error(error.message || '执行入库失败')
  }
}

function goInventoryExecute(row) {
  if (!row?.inventory_request_id) return
  router.push({
    path: '/inventory/stocks',
    query: {
      focusId: String(row.inventory_request_id),
      focusType: 'inventory_operation_request',
      mode: 'execute'
    }
  })
}

async function loadOrderDetail(id) {
  if (!id) {
    selectedOrderTargetId.value = null
    form.items = []
    return
  }
  const candidate = arrivalOrders.value.find((item) => item.id === id)
  if (candidate && !canReceiveOrder(candidate)) {
    ElMessage.warning(`该采购单当前为${candidate.status_label || statusLabel(candidate.status)}，暂不能登记到货`)
    selectedOrderId.value = null
    form.items = []
    return
  }
  const detailData = await get(`/api/purchases/orders/${id}`)
  selectedOrderTargetId.value = detailData.target_warehouse_id || null
  if (selectedOrderTargetId.value) {
    form.warehouse_id = selectedOrderTargetId.value
    handleWarehouseChange()
  }
  if (detailData.arrival_status === 'arrived' || detailData.items.every((item) => Number(item.received_quantity || 0) >= Number(item.quantity || 0))) {
    ElMessage.warning('该采购单已全部到货，不能重复登记')
    selectedOrderId.value = null
    form.items = []
    return
  }
  form.items = detailData.items.map((item) => ({
    purchase_order_item_id: item.id,
    name: item.product_name || item.material_name || item.packaging_name,
    pending_quantity: Number(item.quantity) - Number(item.received_quantity),
    received_quantity: Math.max(0, Number(item.quantity) - Number(item.received_quantity)),
    difference_remark: '',
    batch_no: detailData.order_no,
    unit_cost: item.unit_price
  }))
}

async function submit() {
  if (submitting.value) return
  const items = form.items.filter((item) => Number(item.received_quantity) > 0)
  const overReceived = items.find((item) => Number(item.received_quantity || 0) > Number(item.pending_quantity || 0))
  if (overReceived) {
    ElMessage.warning(`${overReceived.name} 本次到货数量不能大于待收数量`)
    return
  }
  if (!selectedOrderId.value) {
    ElMessage.warning('请选择采购单')
    return
  }
  if (!form.warehouse_id) {
    ElMessage.warning('请选择入库仓库')
    return
  }
  if (!items.length) {
    ElMessage.warning('请至少填写一条入库明细')
    return
  }
  const missingDifferenceRemark = items.find((item) => Number(item.received_quantity || 0) !== Number(item.pending_quantity || 0) && !String(item.difference_remark || '').trim())
  if (missingDifferenceRemark) {
    ElMessage.warning(`${missingDifferenceRemark.name} 应到/实到不一致，请填写差异备注`)
    return
  }
  submitting.value = true
  try {
    await post('/api/purchases/receipts', {
      purchase_order_id: selectedOrderId.value,
      warehouse_id: form.warehouse_id,
      requires_quality: isInspectionWarehouse.value || form.requires_quality,
      remark: form.remark,
      preliminary_acceptance_note: form.preliminary_acceptance_note,
      recorded_at: form.recorded_at || null,
      items: items.map((item) => ({
        purchase_order_item_id: item.purchase_order_item_id,
        received_quantity: item.received_quantity,
        batch_no: item.batch_no,
        unit_cost: item.unit_cost,
        difference_remark: item.difference_remark || null
      }))
    })
    ElMessage.success(isInspectionWarehouse.value || form.requires_quality ? '采购到货已登记，待来料质检' : '采购到货已登记，待执行库存入库作业')
    dialogVisible.value = false
    resetForm()
    await load()
  } catch (error) {
    ElMessage.error(error.message || '提交入库失败')
  } finally {
    submitting.value = false
  }
}

onMounted(async () => {
  try {
    warehouses.value = await get('/api/master-data/warehouses')
    await load()
  } catch (error) {
    ElMessage.error(error.message || '读取采购到货数据失败')
  }
})
</script>

<style scoped>
.arrival-overview { display: flex; align-items: stretch; gap: 10px; overflow-x: auto; padding-bottom: 4px; }
.overdue-summary, .arrival-day { flex: 1 0 118px; min-width: 118px; padding: 10px 12px; border: 1px solid var(--el-border-color-lighter); border-radius: 8px; }
.overdue-summary { display: flex; flex-direction: column; justify-content: space-between; color: var(--el-color-warning-dark-2); }
.overdue-summary strong { font-size: 22px; }
.arrival-day { display: flex; flex-direction: column; gap: 8px; }
.arrival-day-label { color: var(--el-text-color-secondary); font-size: 12px; }
.arrival-day strong { align-self: flex-end; }
.arrival-bar-track { height: 8px; background: var(--el-fill-color-light); border-radius: 8px; overflow: hidden; }
.arrival-bar { display: block; height: 100%; background: var(--el-color-primary); border-radius: 8px; }
.is-overdue { color: var(--el-color-danger); font-weight: 600; }
.mt-16 { margin-top: 16px; }
.detail-actions { display: flex; gap: 12px; justify-content: flex-end; }
.receipt-photos { width: 100%; }
.photo-placeholder { color: #909399; font-size: 13px; }
.form-tip { color: #909399; font-size: 12px; margin-top: 4px; }
</style>
