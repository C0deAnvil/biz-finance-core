<template>
  <div class="page-stack">
    <el-row :gutter="12" class="summary-row">
      <el-col :span="6"><el-card shadow="hover"><div class="summary-label">物流记录</div><div class="summary-value">{{ filteredRows.length }}</div></el-card></el-col>
      <el-col :span="6"><el-card shadow="hover"><div class="summary-label">待到达</div><div class="summary-value text-warning">{{ pendingArrivalCount }}</div></el-card></el-col>
      <el-col :span="6"><el-card shadow="hover"><div class="summary-label">销售物流</div><div class="summary-value">{{ salesCount }}</div></el-card></el-col>
      <el-col :span="6"><el-card shadow="hover"><div class="summary-label">采购物流</div><div class="summary-value">{{ purchaseCount }}</div></el-card></el-col>
    </el-row>

    <PageSection :title="pageTitle">
      <template #actions>
        <el-date-picker v-model="filters.dateRange" type="daterange" value-format="YYYY-MM-DD" range-separator="至" start-placeholder="开始日期" end-placeholder="结束日期" class="w-280" />
        <el-select v-model="filters.businessType" clearable placeholder="业务类型" class="w-160" :disabled="businessTypeLocked">
          <el-option v-for="item in businessTypeOptions" :key="item.value" :label="item.label" :value="item.value" />
        </el-select>
        <el-input v-model="filters.keyword" clearable placeholder="物流单号 / 运单号 / 关联单号" class="w-240" />
        <el-button @click="load">刷新</el-button>
        <el-button v-if="canManage" type="primary" @click="openCreate">新增物流单</el-button>
      </template>

      <el-table :data="pagedRows" stripe>
        <el-table-column prop="logistics_no" label="物流单号" width="150" />
        <el-table-column prop="business_type_label" label="业务类型" width="120" />
        <el-table-column prop="related_no" label="关联单号" width="150" />
        <el-table-column label="业务摘要" min-width="220" show-overflow-tooltip>
          <template #default="scope">{{ businessSummary(scope.row) }}</template>
        </el-table-column>
        <el-table-column prop="tracking_no" label="运单号" min-width="150" />
        <el-table-column prop="bill_date" label="开单日期" width="120" />
        <el-table-column prop="origin_station" label="始发站" min-width="120" />
        <el-table-column prop="destination_station" label="目的站" min-width="120" />
        <el-table-column prop="carrier_name" label="承运方" min-width="150" />
        <el-table-column prop="delivery_fee" label="送货费" width="110">
          <template #default="scope">{{ formatNumber(scope.row.delivery_fee) }}</template>
        </el-table-column>
        <el-table-column prop="created_by_name" label="开单人" width="120" />
        <el-table-column label="操作" :width="canManage ? 170 : 90" fixed="right">
          <template #default="scope">
            <el-button size="small" text type="primary" @click="openDetail(scope.row)">详情</el-button>
            <el-button v-if="canManage" size="small" text type="primary" @click="openEdit(scope.row)">编辑</el-button>
          </template>
        </el-table-column>
      </el-table>
      <AppPagination v-model:page="pagination.page" v-model:page-size="pagination.pageSize" :total="total" :page-sizes="pageSizes" />
    </PageSection>

    <el-dialog v-model="dialogVisible" :title="editingId ? '编辑物流单' : '新增物流单'" width="980px">
      <el-form :model="form" label-width="100px">
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="业务类型">
              <el-select v-model="form.business_type" style="width: 100%" :disabled="businessTypeLocked">
                <el-option v-for="item in businessTypeOptions" :key="item.value" :label="item.label" :value="item.value" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="关联订单">
              <el-select v-model="form.business_id" filterable style="width: 100%">
                <el-option v-for="item in businessRows" :key="item.id" :label="businessOptionLabel(item)" :value="item.id" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12"><el-form-item label="仓库"><el-select v-model="form.warehouse_id" clearable style="width: 100%"><el-option v-for="item in options.warehouses" :key="item.id" :label="item.name" :value="item.id" /></el-select></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="运输方式"><el-select v-model="form.logistics_mode" style="width: 100%"><el-option v-for="item in options.logistics_modes" :key="item.value" :label="item.label" :value="item.value" /></el-select></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="运单号"><el-input v-model="form.tracking_no" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="开单日期"><el-date-picker v-model="form.bill_date" type="date" value-format="YYYY-MM-DD" style="width: 100%" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="承运方"><el-input v-model="form.carrier_name" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="司机"><el-input v-model="form.driver_name" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="司机电话"><el-input v-model="form.driver_phone" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="车牌号"><el-input v-model="form.vehicle_no" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="始发站"><el-input v-model="form.origin_station" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="目的站"><el-input v-model="form.destination_station" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="中转站"><el-input v-model="form.transfer_station" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="送货费"><el-input-number v-model="form.delivery_fee" :min="0" :precision="2" style="width: 100%" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="付款方式"><el-select v-model="form.freight_payment_type" clearable style="width: 100%"><el-option label="现付" value="现付" /><el-option label="到付" value="到付" /><el-option label="其他" value="其他" /></el-select></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="交接方式"><el-select v-model="form.handover_type" clearable style="width: 100%"><el-option label="自提" value="自提" /><el-option label="送货上门" value="送货上门" /><el-option label="其他" value="其他" /></el-select></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="托运人"><el-input v-model="form.consignor_name" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="托运人电话"><el-input v-model="form.consignor_phone" /></el-form-item></el-col>
          <el-col :span="24"><el-form-item label="托运人地址"><el-input v-model="form.consignor_address" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="收货人"><el-input v-model="form.consignee_name" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="收货人电话"><el-input v-model="form.consignee_phone" /></el-form-item></el-col>
          <el-col :span="24"><el-form-item label="收货人地址"><el-input v-model="form.consignee_address" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="发运时间"><el-date-picker v-model="form.dispatched_at" type="datetime" value-format="YYYY-MM-DDTHH:mm:ss" style="width: 100%" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="到达时间"><el-date-picker v-model="form.arrived_at" type="datetime" value-format="YYYY-MM-DDTHH:mm:ss" style="width: 100%" /></el-form-item></el-col>
        </el-row>
        <el-form-item label="物流单据">
          <input ref="logisticsFileInput" type="file" accept=".jpg,.jpeg,.png,.pdf,.xls,.xlsx" @change="handleDocumentFileChange" />
          <el-button v-if="form.document_attachment_id" class="ml-8" text type="primary" @click="openAttachment(form.document_attachment_id)">查看已上传单据</el-button>
        </el-form-item>
        <el-form-item label="备注"><el-input v-model="form.remark" type="textarea" :rows="3" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="submit">保存</el-button>
      </template>
    </el-dialog>

    <el-drawer v-model="detailVisible" title="物流单详情" size="60%">
      <el-descriptions v-if="detail" :column="2" border>
        <el-descriptions-item label="物流单号">{{ detail.logistics_no }}</el-descriptions-item>
        <el-descriptions-item label="所属模块">{{ detail.module_label }}</el-descriptions-item>
        <el-descriptions-item label="业务类型">{{ detail.business_type_label }}</el-descriptions-item>
        <el-descriptions-item label="关联单号">{{ detail.related_no || '-' }}</el-descriptions-item>
        <el-descriptions-item label="运单号">{{ detail.tracking_no || '-' }}</el-descriptions-item>
        <el-descriptions-item label="开单日期">{{ detail.bill_date || '-' }}</el-descriptions-item>
        <el-descriptions-item label="始发站">{{ detail.origin_station || '-' }}</el-descriptions-item>
        <el-descriptions-item label="目的站">{{ detail.destination_station || '-' }}</el-descriptions-item>
        <el-descriptions-item label="中转站">{{ detail.transfer_station || '-' }}</el-descriptions-item>
        <el-descriptions-item label="承运方">{{ detail.carrier_name }}</el-descriptions-item>
        <el-descriptions-item label="运输方式">{{ detail.logistics_mode_label }}</el-descriptions-item>
        <el-descriptions-item label="司机">{{ detail.driver_name || '-' }}</el-descriptions-item>
        <el-descriptions-item label="司机电话">{{ detail.driver_phone || '-' }}</el-descriptions-item>
        <el-descriptions-item label="车牌号">{{ detail.vehicle_no || '-' }}</el-descriptions-item>
        <el-descriptions-item label="送货费">{{ formatNumber(detail.delivery_fee) }}</el-descriptions-item>
        <el-descriptions-item label="付款方式">{{ detail.freight_payment_type || '-' }}</el-descriptions-item>
        <el-descriptions-item label="交接方式">{{ detail.handover_type || '-' }}</el-descriptions-item>
        <el-descriptions-item label="托运人">{{ detail.consignor_name || '-' }}</el-descriptions-item>
        <el-descriptions-item label="托运人电话">{{ detail.consignor_phone || '-' }}</el-descriptions-item>
        <el-descriptions-item label="托运人地址" :span="2">{{ detail.consignor_address || '-' }}</el-descriptions-item>
        <el-descriptions-item label="收货人">{{ detail.consignee_name || '-' }}</el-descriptions-item>
        <el-descriptions-item label="收货人电话">{{ detail.consignee_phone || '-' }}</el-descriptions-item>
        <el-descriptions-item label="收货人地址" :span="2">{{ detail.consignee_address || '-' }}</el-descriptions-item>
        <el-descriptions-item label="开单人">{{ detail.created_by_name || '-' }}</el-descriptions-item>
        <el-descriptions-item label="仓库">{{ detail.warehouse_name || '-' }}</el-descriptions-item>
        <el-descriptions-item label="发运时间">{{ formatDateTime(detail.dispatched_at) }}</el-descriptions-item>
        <el-descriptions-item label="到达时间">{{ formatDateTime(detail.arrived_at) }}</el-descriptions-item>
        <el-descriptions-item label="业务摘要" :span="2">{{ businessSummary(detail) }}</el-descriptions-item>
        <el-descriptions-item label="物流单据" :span="2">
          <el-button v-if="detail.document_attachment_id" text type="primary" @click="openAttachment(detail.document_attachment_id)">查看/下载单据</el-button>
          <span v-else>-</span>
        </el-descriptions-item>
        <el-descriptions-item label="备注" :span="2">{{ detail.remark || '-' }}</el-descriptions-item>
      </el-descriptions>
      <el-table v-if="detail?.goods_info?.length" :data="detail.goods_info" border class="mt-16">
        <el-table-column prop="item_code" label="编码" width="140" />
        <el-table-column prop="item_name" label="货物名称" min-width="180" />
        <el-table-column prop="specification" label="规格" min-width="140" />
        <el-table-column prop="quantity" label="数量" width="120">
          <template #default="scope">{{ formatNumber(scope.row.quantity) }}</template>
        </el-table-column>
      </el-table>
    </el-drawer>
  </div>
</template>

<script setup>
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/card/style/css'
import 'element-plus/es/components/col/style/css'
import 'element-plus/es/components/date-picker/style/css'
import 'element-plus/es/components/descriptions/style/css'
import 'element-plus/es/components/dialog/style/css'
import 'element-plus/es/components/drawer/style/css'
import 'element-plus/es/components/form/style/css'
import 'element-plus/es/components/input/style/css'
import 'element-plus/es/components/input-number/style/css'
import 'element-plus/es/components/message/style/css'
import 'element-plus/es/components/row/style/css'
import 'element-plus/es/components/select/style/css'
import 'element-plus/es/components/table/style/css'

import { ElMessage } from 'element-plus/es/components/message/index.mjs'
import ElButton from 'element-plus/es/components/button/index.mjs'
import ElCard from 'element-plus/es/components/card/index.mjs'
import ElCol from 'element-plus/es/components/col/index.mjs'
import ElDatePicker from 'element-plus/es/components/date-picker/index.mjs'
import ElDescriptions, { ElDescriptionsItem } from 'element-plus/es/components/descriptions/index.mjs'
import ElDialog from 'element-plus/es/components/dialog/index.mjs'
import ElDrawer from 'element-plus/es/components/drawer/index.mjs'
import ElForm, { ElFormItem } from 'element-plus/es/components/form/index.mjs'
import ElInput from 'element-plus/es/components/input/index.mjs'
import ElInputNumber from 'element-plus/es/components/input-number/index.mjs'
import ElRow from 'element-plus/es/components/row/index.mjs'
import ElSelect, { ElOption } from 'element-plus/es/components/select/index.mjs'
import ElTable, { ElTableColumn } from 'element-plus/es/components/table/index.mjs'

import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import { get, post, put, upload } from '../../api/client'
import AppPagination from '../../components/AppPagination.vue'
import PageSection from '../../components/PageSection.vue'
import { useLocalPagination } from '../../composables/useLocalPagination'
import { useAuthStore } from '../../stores/auth'
import { formatDateTime, formatNumber } from '../../utils/format'

const auth = useAuthStore()
const route = useRoute()

const modulePathMap = { '/sales/logistics': 'sales', '/purchases/logistics': 'purchase', '/inventory/logistics': 'inventory' }
const moduleTitleMap = { sales: '销售物流管理', purchase: '采购物流管理', inventory: '库存物流管理' }
const businessTypeLockMap = { sales: 'sales_order', purchase: 'purchase_order', inventory: '' }

const routeModule = computed(() => modulePathMap[route.path] || 'sales')
const pageTitle = computed(() => moduleTitleMap[routeModule.value] || '物流管理')
const lockedBusinessType = computed(() => businessTypeLockMap[routeModule.value] || '')
const businessTypeLocked = computed(() => Boolean(lockedBusinessType.value))
const canManage = computed(() => ['sales', 'purchase', 'inventory', 'system_admin', 'master_data_admin'].includes(auth.user?.role_code))

const rows = ref([])
const detail = ref(null)
const dialogVisible = ref(false)
const detailVisible = ref(false)
const editingId = ref(null)
const logisticsFileInput = ref(null)
const selectedDocumentFile = ref(null)

const options = reactive({ business_types: [], logistics_modes: [], sales_orders: [], purchase_orders: [], warehouses: [] })
const filters = reactive({ businessType: '', keyword: '', dateRange: [] })

const emptyForm = () => ({
  module: routeModule.value,
  business_type: lockedBusinessType.value || 'sales_order',
  business_id: null,
  warehouse_id: null,
  logistics_mode: 'road',
  carrier_name: '',
  driver_name: '',
  driver_phone: '',
  vehicle_no: '',
  tracking_no: '',
  bill_date: '',
  origin_station: '',
  destination_station: '',
  transfer_station: '',
  consignor_name: '',
  consignor_phone: '',
  consignor_address: '',
  consignee_name: '',
  consignee_phone: '',
  consignee_address: '',
  delivery_fee: 0,
  freight_payment_type: '',
  handover_type: '',
  document_attachment_id: null,
  dispatched_at: '',
  arrived_at: '',
  remark: ''
})
const form = reactive(emptyForm())

const businessTypeOptions = computed(() => businessTypeLocked.value ? options.business_types.filter((item) => item.value === lockedBusinessType.value) : options.business_types)
const businessRows = computed(() => form.business_type === 'purchase_order' ? options.purchase_orders : options.sales_orders)
const filteredRows = computed(() => {
  const keyword = filters.keyword.trim().toLowerCase()
  return rows.value.filter((item) => {
    if (filters.businessType && item.business_type !== filters.businessType) return false
    if (!keyword) return true
    return [item.logistics_no, item.related_no, item.carrier_name, item.tracking_no, item.origin_station, item.destination_station].filter(Boolean).some((value) => String(value).toLowerCase().includes(keyword))
  })
})
const { pagination, total, pageSizes, pagedItems: pagedRows } = useLocalPagination(filteredRows)
const pendingArrivalCount = computed(() => filteredRows.value.filter((item) => !item.arrived_at).length)
const salesCount = computed(() => filteredRows.value.filter((item) => item.business_type === 'sales_order').length)
const purchaseCount = computed(() => filteredRows.value.filter((item) => item.business_type === 'purchase_order').length)

function resetForm() {
  Object.assign(form, emptyForm())
  editingId.value = null
  selectedDocumentFile.value = null
  if (logisticsFileInput.value) logisticsFileInput.value.value = ''
}

function businessOptionLabel(item) {
  if (!item) return '-'
  if ('customer_name' in item) return `${item.order_no} / ${item.customer_name || '-'}`
  return `${item.order_no} / ${item.supplier_name || '-'}`
}

function businessSummary(row) {
  const summary = row.business_summary || {}
  if (summary.customer_name) return `${summary.business_no || '-'} / ${summary.customer_name} / ${summary.owner_name || '-'}`
  if (summary.supplier_name) return `${summary.business_no || '-'} / ${summary.supplier_name} / ${summary.owner_name || '-'}`
  return row.related_no || '-'
}

function buildQuery() {
  const params = new URLSearchParams()
  params.set('module', routeModule.value)
  if (filters.businessType) params.set('business_type', filters.businessType)
  if (filters.dateRange?.[0]) params.set('date_from', filters.dateRange[0])
  if (filters.dateRange?.[1]) params.set('date_to', filters.dateRange[1])
  const query = params.toString()
  return query ? `?${query}` : ''
}

function normalizePayload() {
  return {
    module: routeModule.value,
    business_type: form.business_type,
    business_id: form.business_id,
    warehouse_id: form.warehouse_id || null,
    logistics_mode: form.logistics_mode,
    carrier_name: form.carrier_name,
    driver_name: form.driver_name || null,
    driver_phone: form.driver_phone || null,
    vehicle_no: form.vehicle_no || null,
    tracking_no: form.tracking_no || null,
    bill_date: form.bill_date || null,
    origin_station: form.origin_station || null,
    destination_station: form.destination_station || null,
    transfer_station: form.transfer_station || null,
    consignor_name: form.consignor_name || null,
    consignor_phone: form.consignor_phone || null,
    consignor_address: form.consignor_address || null,
    consignee_name: form.consignee_name || null,
    consignee_phone: form.consignee_phone || null,
    consignee_address: form.consignee_address || null,
    delivery_fee: Number(form.delivery_fee || 0),
    freight_payment_type: form.freight_payment_type || null,
    handover_type: form.handover_type || null,
    document_attachment_id: form.document_attachment_id || null,
    dispatched_at: form.dispatched_at || null,
    arrived_at: form.arrived_at || null,
    remark: form.remark || null
  }
}

async function loadOptions() {
  const data = await get('/api/logistics/options')
  options.business_types = data.business_types || []
  options.logistics_modes = data.logistics_modes || []
  options.sales_orders = data.sales_orders || []
  options.purchase_orders = data.purchase_orders || []
  options.warehouses = data.warehouses || []
}
async function load() { rows.value = await get(`/api/logistics${buildQuery()}`) }
function openCreate() { resetForm(); dialogVisible.value = true }

async function openEdit(row) {
  const data = await get(`/api/logistics/${row.id}`)
  resetForm()
  editingId.value = row.id
  Object.assign(form, {
    module: data.module,
    business_type: data.business_type,
    business_id: data.business_id,
    warehouse_id: data.warehouse_id,
    logistics_mode: data.logistics_mode,
    carrier_name: data.carrier_name,
    driver_name: data.driver_name || '',
    driver_phone: data.driver_phone || '',
    vehicle_no: data.vehicle_no || '',
    tracking_no: data.tracking_no || '',
    bill_date: data.bill_date || '',
    origin_station: data.origin_station || '',
    destination_station: data.destination_station || '',
    transfer_station: data.transfer_station || '',
    consignor_name: data.consignor_name || '',
    consignor_phone: data.consignor_phone || '',
    consignor_address: data.consignor_address || '',
    consignee_name: data.consignee_name || '',
    consignee_phone: data.consignee_phone || '',
    consignee_address: data.consignee_address || '',
    delivery_fee: Number(data.delivery_fee || 0),
    freight_payment_type: data.freight_payment_type || '',
    handover_type: data.handover_type || '',
    document_attachment_id: data.document_attachment_id || null,
    dispatched_at: data.dispatched_at || '',
    arrived_at: data.arrived_at || '',
    remark: data.remark || ''
  })
  dialogVisible.value = true
}
async function openDetail(row) { detail.value = await get(`/api/logistics/${row.id}`); detailVisible.value = true }
function handleDocumentFileChange(event) { selectedDocumentFile.value = event.target.files?.[0] || null }
function openAttachment(id) { if (id) window.open(`/api/files/${id}`, '_blank') }

async function uploadDocumentIfNeeded() {
  if (!selectedDocumentFile.value) return form.document_attachment_id || null
  if (!form.business_id) throw new Error('请先选择关联订单')
  const formData = new FormData()
  formData.append('business_type', 'logistics_record')
  formData.append('business_id', String(editingId.value || form.business_id))
  formData.append('attachment_type', '物流单据')
  formData.append('file', selectedDocumentFile.value)
  const result = await upload('/api/files/upload', formData)
  return result.id
}

async function submit() {
  try {
    form.document_attachment_id = await uploadDocumentIfNeeded()
    const payload = normalizePayload()
    if (editingId.value) {
      await put(`/api/logistics/${editingId.value}`, payload)
      ElMessage.success('物流单已更新')
    } else {
      await post('/api/logistics', payload)
      ElMessage.success('物流单已创建')
    }
    dialogVisible.value = false
    await load()
  } catch (error) {
    ElMessage.error(error.message || '物流单保存失败')
  }
}

watch(routeModule, async () => { filters.businessType = lockedBusinessType.value || ''; resetForm(); await load() })
watch(() => form.business_type, (value) => {
  if (!value) { form.business_id = null; return }
  const exists = businessRows.value.some((item) => item.id === form.business_id)
  if (!exists) form.business_id = null
})
onMounted(async () => { filters.businessType = lockedBusinessType.value || ''; await loadOptions(); resetForm(); await load() })
</script>

<style scoped>
.page-stack { display: flex; flex-direction: column; gap: 16px; }
.summary-row { margin-bottom: 0; }
.summary-label { color: #909399; margin-bottom: 8px; }
.summary-value { font-size: 28px; font-weight: 700; color: #0f5c90; }
.text-warning { color: #e6a23c; }
.w-280 { width: 280px; }
.w-240 { width: 240px; }
.w-160 { width: 160px; }
.ml-8 { margin-left: 8px; }
.mt-16 { margin-top: 16px; }
</style>
