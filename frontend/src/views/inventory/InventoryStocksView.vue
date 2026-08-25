<template>
  <div class="page-stack">
    <el-row :gutter="12" class="summary-row">
      <el-col :span="6"><el-card shadow="hover"><div class="summary-label">库存条目</div><div class="summary-value">{{ filteredRows.length }}</div></el-card></el-col>
      <el-col :span="6"><el-card shadow="hover"><div class="summary-label">库存金额</div><div class="summary-value">{{ formatNumber(summary.inventoryAmount) }}</div></el-card></el-col>
      <el-col :span="6"><el-card shadow="hover"><div class="summary-label">低于安全库存</div><div class="summary-value text-danger">{{ summary.belowSafety }}</div></el-card></el-col>
      <el-col :span="6"><el-card shadow="hover"><div class="summary-label">可用库存总量</div><div class="summary-value">{{ formatNumber(summary.availableQuantity) }}</div></el-card></el-col>
    </el-row>

    <PageSection title="库存状态">
      <template #actions>
        <el-select v-model="filters.warehouseId" clearable placeholder="仓库" class="w-180">
          <el-option v-for="item in warehouses" :key="item.id" :label="item.name" :value="item.id" />
        </el-select>
        <el-select v-model="filters.itemType" clearable placeholder="类型" class="w-140">
          <el-option label="产品" value="product" />
          <el-option label="原料" value="material" />
        </el-select>
        <el-input v-model="filters.keyword" clearable placeholder="搜索编码 / 名称" class="w-240" />
        <el-switch v-model="filters.alertOnly" inline-prompt active-text="仅预警" inactive-text="全部" />
        <el-button @click="load">刷新</el-button>
        <el-button v-if="canManualOperate" type="primary" plain @click="openManualDialog('inbound')">手工入库</el-button>
        <el-button v-if="canManualOperate" type="warning" plain @click="openManualDialog('outbound')">手工出库</el-button>
        <el-button @click="exportTable">导出</el-button>
      </template>

      <div class="inventory-table-scroll inventory-status-table-scroll">
      <el-table class="inventory-status-table" :data="pagedRows" stripe :fit="false" :row-class-name="stockRowClassName">
        <el-table-column prop="warehouse_name" label="仓库" width="180" />
        <el-table-column label="类型" width="100">
          <template #default="scope">
            <el-tag :type="scope.row.item_type === 'product' ? 'success' : 'warning'">{{ scope.row.item_type === 'product' ? '产品' : '原料' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="item_code" label="编码" width="150" />
        <el-table-column prop="item_name" label="名称" min-width="180" />
        <el-table-column prop="quantity" label="实际库存" width="120"><template #default="scope">{{ formatNumber(scope.row.quantity) }}</template></el-table-column>
        <el-table-column prop="reserved_quantity" label="预计占用库存" width="130"><template #default="scope">{{ formatNumber(scope.row.reserved_quantity) }}</template></el-table-column>
        <el-table-column prop="expected_available_quantity" label="预计可用库存" width="140"><template #default="scope">{{ formatNumber(scope.row.expected_available_quantity ?? scope.row.available_quantity) }}</template></el-table-column>
        <el-table-column prop="safety_stock_qty" label="安全库存" width="120"><template #default="scope">{{ formatNumber(scope.row.safety_stock_qty) }}</template></el-table-column>
        <el-table-column prop="avg_unit_cost" label="移动均价" width="120"><template #default="scope">{{ formatNumber(scope.row.avg_unit_cost) }}</template></el-table-column>
        <el-table-column prop="inventory_amount" label="库存金额" width="120"><template #default="scope">{{ formatNumber(scope.row.inventory_amount) }}</template></el-table-column>
        <el-table-column label="预警" width="100">
          <template #default="scope">
            <el-tag v-if="scope.row.below_safety_stock" type="danger">低库存</el-tag>
            <el-tag v-else type="success">正常</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="updated_at" label="更新时间" min-width="170"><template #default="scope">{{ formatDateTime(scope.row.updated_at) }}</template></el-table-column>
        <el-table-column label="操作" width="120" fixed="right">
          <template #default="scope">
            <el-button size="small" text type="primary" @click="openTransactions(scope.row)">查看流水</el-button>
          </template>
        </el-table-column>
      </el-table>
      </div>
      <AppPagination
        v-model:page="pagination.page"
        v-model:page-size="pagination.pageSize"
        :total="total"
        :page-sizes="pageSizes"
        @change="handleStockPaginationChange"
      />
    </PageSection>

    <PageSection title="库存作业请求" description="所有生产领料、采购到货、质检放行、销售发货、退货回库等库存动作，统一由库管在此手工完成。">
      <div class="inventory-table-scroll inventory-operation-table-scroll">
      <el-table class="inventory-operation-table" :data="operationRequests" stripe :fit="false">
        <el-table-column prop="request_no" label="作业号" width="150" />
        <el-table-column prop="operation_type" label="类型" width="100">
          <template #default="scope">{{ scope.row.operation_type === 'inbound' ? '入库' : '出库' }}</template>
        </el-table-column>
        <el-table-column prop="request_category_label" label="来源类别" width="190" />
        <el-table-column prop="source_no" label="来源单号" width="160" />
        <el-table-column prop="status_label" label="状态" width="120">
          <template #default="scope"><el-tag :type="scope.row.status === 'completed' ? 'success' : 'warning'">{{ scope.row.status_label }}</el-tag></template>
        </el-table-column>
        <el-table-column prop="remark" label="备注" min-width="220" show-overflow-tooltip />
        <el-table-column prop="created_at" label="创建时间" min-width="170"><template #default="scope">{{ formatDateTime(scope.row.created_at) }}</template></el-table-column>
        <el-table-column label="操作" width="220" fixed="right">
          <template #default="scope">
            <el-button size="small" text type="primary" @click="openOperationRequest(scope.row)">详情</el-button>
            <el-button v-if="canApproveInventoryOperation(scope.row)" size="small" text type="warning" @click="openOperationRequest(scope.row, true)">{{ approvalActionLabel }}</el-button>
            <el-button v-if="canExecuteInventoryOperation(scope.row)" size="small" text type="success" @click="openOperationRequest(scope.row, true)">执行</el-button>
            <el-button v-if="canRejectOperation(scope.row)" size="small" text type="danger" @click="openRejectDialog(scope.row)">拒绝</el-button>
          </template>
        </el-table-column>
      </el-table>
      </div>
    </PageSection>

    <el-drawer v-model="drawerVisible" size="56%" title="库存流水">
      <div v-if="activeStock" class="drawer-head">
        <div>
          <div class="drawer-title">{{ activeStock.item_name }}</div>
          <div class="drawer-subtitle">{{ activeStock.warehouse_name }} · {{ activeStock.item_code }}</div>
        </div>
        <el-tag :type="activeStock.item_type === 'product' ? 'success' : 'warning'">{{ activeStock.item_type === 'product' ? '产品' : '原料' }}</el-tag>
      </div>
      <el-table :data="transactionRows" stripe>
        <el-table-column prop="transaction_no" label="流水号" width="150" />
        <el-table-column prop="transaction_type" label="事务类型" width="190">
          <template #default="scope">{{ scope.row.transaction_type_label || transactionTypeLabel(scope.row.transaction_type) }}</template>
        </el-table-column>
        <el-table-column prop="batch_no" label="批次号" min-width="150" />
        <el-table-column prop="quantity" label="数量" width="110"><template #default="scope">{{ formatNumber(scope.row.quantity) }}</template></el-table-column>
        <el-table-column prop="unit_cost" label="单价" width="110"><template #default="scope">{{ formatNumber(scope.row.unit_cost) }}</template></el-table-column>
        <el-table-column prop="total_cost" label="总额" width="120"><template #default="scope">{{ formatNumber(scope.row.total_cost) }}</template></el-table-column>
        <el-table-column prop="related_type" label="关联业务" width="150" />
        <el-table-column prop="created_at" label="时间" min-width="170"><template #default="scope">{{ formatDateTime(scope.row.created_at) }}</template></el-table-column>
      </el-table>
    </el-drawer>

    <el-dialog v-model="operationDialogVisible" :title="operationDialogTitle" width="860px">
      <el-form v-if="activeOperation" :model="operationForm" label-width="100px">
        <el-form-item label="作业号"><el-input :model-value="activeOperation.request_no" disabled /></el-form-item>
        <el-form-item label="作业说明"><el-input :model-value="activeOperation.remark || activeOperation.source_no || '-'" disabled /></el-form-item>
        <el-descriptions v-if="sourceDetailEntries.length" :column="2" border class="mb-16">
          <el-descriptions-item v-for="item in sourceDetailEntries" :key="item.key" :label="item.label" :span="item.span || 1">
            {{ item.value }}
          </el-descriptions-item>
        </el-descriptions>
        <el-table v-if="sourceDetailItems.length" :data="sourceDetailItems" border class="mb-16">
          <el-table-column prop="item_name" label="来源明细" min-width="180" />
          <el-table-column prop="display_specification" label="规格" width="130" />
          <el-table-column prop="packaging_name" label="包装" width="140" />
          <el-table-column prop="kg_per_bag" label="kg/袋" width="100">
            <template #default="scope">{{ scope.row.kg_per_bag == null ? '-' : formatNumber(scope.row.kg_per_bag) }}</template>
          </el-table-column>
          <el-table-column prop="bag_count" label="袋数" width="100">
            <template #default="scope">{{ scope.row.bag_count == null ? '-' : formatNumber(scope.row.bag_count) }}</template>
          </el-table-column>
          <el-table-column prop="quantity" label="订单/计划数量" width="130">
            <template #default="scope">{{ formatNumber(sourceItemQuantity(scope.row)) }}</template>
          </el-table-column>
          <el-table-column prop="received_quantity" label="到货数量" width="110">
            <template #default="scope">{{ scope.row.received_quantity == null ? '-' : formatNumber(scope.row.received_quantity) }}</template>
          </el-table-column>
          <el-table-column prop="requested_quantity" label="申请数量" width="110">
            <template #default="scope">{{ scope.row.requested_quantity == null ? '-' : formatNumber(scope.row.requested_quantity) }}</template>
          </el-table-column>
          <el-table-column v-if="!hidePriceInOperation" prop="unit_cost" label="单价" width="100">
            <template #default="scope">{{ scope.row.unit_cost == null ? '-' : formatNumber(scope.row.unit_cost) }}</template>
          </el-table-column>
        </el-table>
        <el-table :data="operationForm.items" border>
          <el-table-column label="产品ID" width="100"><template #default="scope">{{ scope.row.product_id || '-' }}</template></el-table-column>
          <el-table-column label="原料ID" width="100"><template #default="scope">{{ scope.row.material_id || '-' }}</template></el-table-column>
          <el-table-column label="物料名称" min-width="160"><template #default="scope">{{ operationItemName(scope.row) }}</template></el-table-column>
          <el-table-column prop="batch_no" label="批次号" min-width="150" />
          <el-table-column :label="expectedQuantityLabel" width="120">
            <template #default="scope">{{ formatNumber(scope.row.expected_quantity ?? scope.row.original_quantity ?? scope.row.quantity) }}</template>
          </el-table-column>
          <el-table-column :label="actualQuantityLabel" width="140">
            <template #default="scope">
              <el-input-number v-model="scope.row.quantity" :disabled="operationItemsReadOnly" :min="0.01" :precision="2" style="width: 100%" />
            </template>
          </el-table-column>
          <el-table-column v-if="!hidePriceInOperation" label="单价" width="140">
            <template #default="scope">
              <el-input-number v-model="scope.row.unit_cost" :disabled="operationItemsReadOnly" :min="0" :precision="2" style="width: 100%" />
            </template>
          </el-table-column>
        </el-table>
        <el-form-item class="mt-16" label="处理备注"><el-input v-model="operationForm.handled_remark" :disabled="operationReadOnly" type="textarea" :rows="3" /></el-form-item>
        <el-form-item label="调整原因"><el-input v-model="operationForm.adjust_reason" :disabled="operationReadOnly || operationApprovalMode" type="textarea" :rows="3" /></el-form-item>
        <el-form-item label="差异备注"><el-input v-model="operationForm.quantity_difference_remark" :disabled="operationReadOnly || operationApprovalMode" type="textarea" :rows="3" placeholder="应到/实到或应出/实出不一致时必填" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="operationDialogVisible = false">关闭</el-button>
        <DelayedActionButton v-if="!operationReadOnly && canRejectOperation(activeOperation)" type="danger" @click="rejectActiveOperation">{{ activeOperation?.request_category === 'production_material_issue' ? '拒绝领料申请' : '拒绝申请' }}</DelayedActionButton>
        <DelayedActionButton v-if="!operationReadOnly" type="primary" @click="submitOperationRequest">{{ operationSubmitLabel }}</DelayedActionButton>
      </template>
    </el-dialog>

    <el-dialog v-model="rejectDialogVisible" title="拒绝库存作业" width="560px">
      <el-form :model="rejectForm" label-width="100px">
        <el-form-item label="作业号">
          <el-input :model-value="rejectOperation?.request_no || '-'" disabled />
        </el-form-item>
        <el-form-item label="拒绝原因">
          <el-input v-model="rejectForm.reject_reason" type="textarea" :rows="4" placeholder="请说明拒绝原因，系统将同步给发起部门" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="rejectDialogVisible = false">取消</el-button>
        <DelayedActionButton type="danger" @click="submitRejectOperation">确认拒绝</DelayedActionButton>
      </template>
    </el-dialog>

    <el-dialog v-model="manualDialogVisible" :title="manualOperationType === 'inbound' ? '手工入库' : '手工出库'" width="920px">
      <el-form :model="manualForm" label-width="100px">
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="目标仓库">
              <el-select v-model="manualForm.warehouse_id" filterable style="width: 100%">
                <el-option v-for="item in warehouses" :key="item.id" :label="item.name" :value="item.id" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="关联单号">
              <el-input v-model="manualForm.source_no" placeholder="可选，用于记录来源单据号" />
            </el-form-item>
          </el-col>
        </el-row>
        <el-form-item label="处理说明">
          <el-input v-model="manualForm.remark" type="textarea" :rows="3" placeholder="请输入本次手工出入库说明" />
        </el-form-item>
        <div class="detail-toolbar">
          <span>明细</span>
          <el-button size="small" @click="addManualItem">新增明细</el-button>
        </div>
        <el-table :data="manualForm.items" border>
          <el-table-column label="类型" width="120">
            <template #default="scope">
              <el-select v-model="scope.row.item_type" style="width: 100%" @change="handleManualItemTypeChange(scope.row)">
                <el-option label="产品" value="product" />
                <el-option label="原料" value="material" />
              </el-select>
            </template>
          </el-table-column>
          <el-table-column label="物料/产品" min-width="220">
            <template #default="scope">
              <el-select v-model="scope.row.item_id" filterable style="width: 100%">
                <el-option
                  v-for="item in scope.row.item_type === 'product' ? products : materials"
                  :key="item.id"
                  :label="item.name"
                  :value="item.id"
                />
              </el-select>
            </template>
          </el-table-column>
          <el-table-column label="批次号" min-width="160">
            <template #default="scope">
              <el-input v-model="scope.row.batch_no" />
            </template>
          </el-table-column>
          <el-table-column label="数量" width="140">
            <template #default="scope">
              <el-input-number v-model="scope.row.quantity" :min="0.01" :precision="2" style="width: 100%" />
            </template>
          </el-table-column>
          <el-table-column label="单价" width="140">
            <template #default="scope">
              <el-input-number v-model="scope.row.unit_cost" :min="0" :precision="2" style="width: 100%" />
            </template>
          </el-table-column>
          <el-table-column label="操作" width="90">
            <template #default="scope">
              <el-button size="small" text type="danger" @click="removeManualItem(scope.$index)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-form>
      <template #footer>
        <el-button @click="manualDialogVisible = false">取消</el-button>
        <el-button type="primary" @click="submitManualOperation">确认{{ manualOperationType === 'inbound' ? '入库' : '出库' }}</el-button>
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
import 'element-plus/es/components/drawer/style/css'
import 'element-plus/es/components/form/style/css'
import 'element-plus/es/components/input/style/css'
import 'element-plus/es/components/input-number/style/css'
import 'element-plus/es/components/message/style/css'
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
import ElInput from 'element-plus/es/components/input/index.mjs'
import ElInputNumber from 'element-plus/es/components/input-number/index.mjs'
import ElRow from 'element-plus/es/components/row/index.mjs'
import ElSelect, { ElOption } from 'element-plus/es/components/select/index.mjs'
import ElSwitch from 'element-plus/es/components/switch/index.mjs'
import ElTable, { ElTableColumn } from 'element-plus/es/components/table/index.mjs'
import ElTag from 'element-plus/es/components/tag/index.mjs'

import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import { get, post } from '../../api/client'
import AppPagination from '../../components/AppPagination.vue'
import DelayedActionButton from '../../components/DelayedActionButton.vue'
import PageSection from '../../components/PageSection.vue'
import { useAuthStore } from '../../stores/auth'
import { exportRowsToCsv } from '../../utils/export'
import { formatDateTime, formatNumber, transactionTypeLabel } from '../../utils/format'

const route = useRoute()
const auth = useAuthStore()

const rows = ref([])
const warehouses = ref([])
const products = ref([])
const materials = ref([])
const transactions = ref([])
const operationRequests = ref([])
const drawerVisible = ref(false)
const operationDialogVisible = ref(false)
const manualDialogVisible = ref(false)
const rejectDialogVisible = ref(false)
const manualOperationType = ref('inbound')
const activeStock = ref(null)
const activeOperation = ref(null)
const rejectOperation = ref(null)
const operationReadOnly = ref(true)
const filters = reactive({ warehouseId: null, itemType: '', keyword: '', alertOnly: false })
const operationForm = reactive({ items: [], handled_remark: '', adjust_reason: '', quantity_difference_remark: '' })
const manualForm = reactive({ warehouse_id: null, source_no: '', remark: '', items: [] })
const rejectForm = reactive({ reject_reason: '' })
const canManualOperate = computed(() => auth.user?.role_code === 'inventory')
const canApproveInventoryOperation = (row) => Boolean(
  row && ['manager', 'system_admin'].includes(auth.user?.role_code) && row.status === 'pending_manager_approval'
)
const canExecuteInventoryOperation = (row) => Boolean(
  row && canManualOperate.value && ['pending', 'processing'].includes(row.status)
)
const approvalActionLabel = '\u5ba1\u6279'

const pagination = reactive({ page: 1, pageSize: 20 })
const total = ref(0)
const pageSizes = [10, 20, 50, 100]
const filteredRows = computed(() => rows.value)
const pagedRows = rows

const summary = computed(() => ({
  inventoryAmount: filteredRows.value.reduce((sum, item) => sum + Number(item.inventory_amount || 0), 0),
  belowSafety: filteredRows.value.filter((item) => item.below_safety_stock).length,
  availableQuantity: filteredRows.value.reduce((sum, item) => sum + Number(item.available_quantity || 0), 0)
}))

const transactionRows = computed(() => {
  if (!activeStock.value) return []
  return transactions.value.filter((item) => item.warehouse_id === activeStock.value.warehouse_id && item.item_type === activeStock.value.item_type && item.item_id === activeStock.value.item_id)
})

const sourceDetailEntries = computed(() => {
  const detail = activeOperation.value?.source_detail || {}
  const entries = []
  const labels = {
    receipt_no: '到货单号',
    purchase_order_no: '采购单号',
    supplier_name: '供应商',
    received_by_name: '到货登记人',
    recorded_at: '到货登记时间',
    preliminary_acceptance_note: '初步验收',
    receipt_remark: '到货备注',
    task_no: '生产任务',
    product_name: '生产产品',
    completed_at: '完工时间',
    completion_recorded_by_name: '完工记录人',
    material_name: '领用原料',
    material_code: '原料编码',
    requested_quantity: '申请数量',
    approved_quantity: '批准数量',
    issued_quantity: '已出库数量',
    planned_quantity: '计划数量',
    warehouse_name: '仓库',
    status_label: '来源状态',
    task_status_label: '任务状态',
    remark: '来源备注'
  }
  Object.entries(labels).forEach(([key, label]) => {
    const value = detail[key]
    if (value !== null && value !== undefined && value !== '') {
      entries.push({ key, label, value: typeof value === 'number' ? formatNumber(value) : value, span: key === 'remark' ? 2 : 1 })
    }
  })
  return entries
})

const sourceDetailItems = computed(() => activeOperation.value?.source_detail?.items || [])
const hidePriceInOperation = computed(() => ['purchase_receipt_inbound', 'purchase_quality_inbound', 'sales_shipment'].includes(activeOperation.value?.request_category))
const expectedQuantityLabel = computed(() => activeOperation.value?.operation_type === 'inbound' ? '应到' : '应出')
const actualQuantityLabel = computed(() => activeOperation.value?.operation_type === 'inbound' ? '实到' : '实出')
const operationApprovalMode = computed(() => activeOperation.value?.status === 'pending_manager_approval' && canApproveInventoryOperation(activeOperation.value))
const operationItemsReadOnly = computed(() => operationReadOnly.value || operationApprovalMode.value)
const operationDialogTitle = computed(() => {
  if (operationReadOnly.value) return '库存作业详情'
  return operationApprovalMode.value ? '\u5ba1\u6279\u5e93\u5b58\u5165\u5e93' : '\u6267\u884c\u5e93\u5b58\u4f5c\u4e1a'
})
const operationSubmitLabel = computed(() => {
  if (operationApprovalMode.value) return '\u5ba1\u6279\u901a\u8fc7'
  if (activeOperation.value?.request_category === 'production_material_issue') return '同意领料并出库'
  if (activeOperation.value?.operation_type === 'inbound') return '确认入库'
  if (activeOperation.value?.operation_type === 'outbound') return '确认出库'
  return '确认完成'
})

function sourceItemQuantity(row) {
  return row.quantity ?? row.order_quantity ?? row.planned_quantity ?? row.requested_quantity ?? 0
}

async function load({ skipRouteFocus = false } = {}) {
  const stockParams = new URLSearchParams({ page: String(pagination.page), page_size: String(pagination.pageSize) })
  if (filters.warehouseId) stockParams.set('warehouse_id', String(filters.warehouseId))
  if (filters.itemType) stockParams.set('item_type', filters.itemType)
  if (filters.keyword) stockParams.set('keyword', filters.keyword)
  if (filters.alertOnly) stockParams.set('alert_only', 'true')
  const [stocks, warehouseRows] = await Promise.all([
    get(`/api/inventory/stocks?${stockParams.toString()}`),
    get('/api/master-data/warehouses')
  ])
  rows.value = stocks.items || stocks
  total.value = Number(stocks.pagination?.total || rows.value.length)
  warehouses.value = warehouseRows
  operationRequests.value = await get('/api/inventory/operation-requests')
  if (route.query.focusType === 'low_stock_alert') {
    filters.alertOnly = true
  }
  if (!skipRouteFocus) {
    await applyRouteFocus()
  }
}

function resetManualForm() {
  manualForm.warehouse_id = null
  manualForm.source_no = ''
  manualForm.remark = ''
  manualForm.items = [{ item_type: 'product', item_id: null, batch_no: '', quantity: 1, unit_cost: 0 }]
}

function openManualDialog(operationType) {
  manualOperationType.value = operationType
  resetManualForm()
  manualDialogVisible.value = true
}

function addManualItem() {
  manualForm.items.push({ item_type: 'product', item_id: null, batch_no: '', quantity: 1, unit_cost: 0 })
}

function handleManualItemTypeChange(row) {
  row.item_id = null
}

function removeManualItem(index) {
  manualForm.items.splice(index, 1)
}

function stockRowClassName({ row }) {
  if (route.query.focusType !== 'low_stock_alert') return ''
  const focusId = Number(route.query.focusId || 0)
  return focusId && row.id === focusId ? 'row-focus' : ''
}

async function applyRouteFocus() {
  const focusId = Number(route.query.focusId || 0)
  const focusType = String(route.query.focusType || '')
  if (!focusId || focusType !== 'inventory_operation_request') return
  const target = operationRequests.value.find((item) => item.id === focusId)
  await nextTick()
  await openOperationRequest(target || { id: focusId }, route.query.mode === 'execute')
}

async function openTransactions(row) {
  activeStock.value = row
  const params = new URLSearchParams({ page: '1', page_size: '100' })
  params.set('warehouse_id', String(row.warehouse_id))
  params.set(row.item_type === 'product' ? 'product_id' : 'material_id', String(row.item_id))
  const data = await get(`/api/inventory/transactions?${params.toString()}`)
  transactions.value = data.items || data
  drawerVisible.value = true
}

async function handleStockPaginationChange({ page, pageSize }) {
  pagination.page = page
  pagination.pageSize = pageSize
  await load({ skipRouteFocus: true })
}

function buildOperationForm(row) {
  operationForm.items = (row.original_items || row.handled_items || []).map((item) => ({
    product_id: item.product_id || null,
    material_id: item.material_id || null,
    batch_no: item.batch_no || '',
    expected_quantity: Number(item.quantity || 0),
    original_quantity: Number(item.quantity || 0),
    quantity: Number(item.quantity || 0),
    unit_cost: Number(item.unit_cost || 0)
  }))
  operationForm.handled_remark = row.handled_remark || ''
  operationForm.adjust_reason = row.adjust_reason || ''
  operationForm.quantity_difference_remark = row.quantity_difference_remark || ''
}

function operationItemName(row) {
  if (row.product_id) {
    return products.value.find((item) => item.id === row.product_id)?.name || `产品#${row.product_id}`
  }
  if (row.material_id) {
    return materials.value.find((item) => item.id === row.material_id)?.name || `原料#${row.material_id}`
  }
  return '-'
}

function canRejectOperation(row) {
  if (!row) return false
  if (row.status === 'pending_manager_approval') return canApproveInventoryOperation(row)
  return canExecuteInventoryOperation(row)
}

async function openOperationRequest(row, editable = false) {
  const detail = await get(`/api/inventory/operation-requests/${row.id}`)
  activeOperation.value = detail
  const canEdit = editable && (
    canApproveInventoryOperation(detail) || canExecuteInventoryOperation(detail)
  )
  operationReadOnly.value = !canEdit || detail.status === 'completed'
  buildOperationForm(detail)
  operationDialogVisible.value = true
}

async function submitOperationRequest() {
  if (!activeOperation.value) return
  try {
    if (operationApprovalMode.value) {
      await post(`/api/inventory/operation-requests/${activeOperation.value.id}/approve`, {
        decision: 'approved',
        note: operationForm.handled_remark
      })
      ElMessage.success('\u5e93\u5b58\u5165\u5e93\u7533\u8bf7\u5df2\u5ba1\u6279\u901a\u8fc7')
    } else {
      await post(`/api/inventory/operation-requests/${activeOperation.value.id}/complete`, {
        items: operationForm.items,
        handled_remark: operationForm.handled_remark,
        adjust_reason: operationForm.adjust_reason,
        quantity_difference_remark: operationForm.quantity_difference_remark
      })
      ElMessage.success('库存作业已完成')
    }
    operationDialogVisible.value = false
    await load({ skipRouteFocus: true })
  } catch (error) {
    ElMessage.error(error.message || '库存作业执行失败')
  }
}

async function rejectActiveOperation() {
  if (!activeOperation.value) return
  openRejectDialog(activeOperation.value)
  operationDialogVisible.value = false
}

function openRejectDialog(row) {
  rejectOperation.value = row
  rejectForm.reject_reason = ''
  rejectDialogVisible.value = true
}

async function submitRejectOperation() {
  if (!rejectOperation.value || rejectForm.reject_reason.trim().length < 2) {
    ElMessage.warning('请填写有效的拒绝原因')
    return
  }
  try {
    if (rejectOperation.value.status === 'pending_manager_approval') {
      await post(`/api/inventory/operation-requests/${rejectOperation.value.id}/approve`, {
        decision: 'rejected',
        note: rejectForm.reject_reason.trim()
      })
    } else {
      await post(`/api/inventory/operation-requests/${rejectOperation.value.id}/reject`, {
        reject_reason: rejectForm.reject_reason.trim()
      })
    }
    ElMessage.success('库存作业已拒绝并同步发起部门')
    rejectDialogVisible.value = false
    await load({ skipRouteFocus: true })
  } catch (error) {
    ElMessage.error(error.message || '库存作业拒绝失败')
  }
}

async function submitManualOperation() {
  const items = manualForm.items
    .filter((item) => item.item_id && Number(item.quantity || 0) > 0)
    .map((item) => ({
      product_id: item.item_type === 'product' ? item.item_id : null,
      material_id: item.item_type === 'material' ? item.item_id : null,
      batch_no: item.batch_no || null,
      quantity: item.quantity,
      unit_cost: item.unit_cost || 0
    }))

  if (!manualForm.warehouse_id) {
    ElMessage.warning('请选择目标仓库')
    return
  }
  if (!items.length) {
    ElMessage.warning('请至少填写一条有效明细')
    return
  }

  try {
    await post('/api/inventory/manual-operations', {
      warehouse_id: manualForm.warehouse_id,
      operation_type: manualOperationType.value,
      source_no: manualForm.source_no,
      remark: manualForm.remark,
      items
    })
    ElMessage.success(manualOperationType.value === 'inbound' ? '手工入库已完成' : '手工出库已完成')
    manualDialogVisible.value = false
    resetManualForm()
    await load({ skipRouteFocus: true })
  } catch (error) {
    ElMessage.error(error.message || (manualOperationType.value === 'inbound' ? '手工入库失败' : '手工出库失败'))
  }
}

function exportTable() {
  exportRowsToCsv('inventory_stocks', filteredRows.value)
}

onMounted(async () => {
  const [productRows, materialRows] = await Promise.all([
    get('/api/master-data/products'),
    get('/api/master-data/materials')
  ])
  products.value = productRows
  materials.value = materialRows
  await load()
})

watch(
  () => [route.query.focusId, route.query.focusType, route.query.mode],
  async () => {
    await applyRouteFocus()
  }
)
</script>

<style scoped>
.page-stack { display: flex; flex-direction: column; gap: 16px; }
.summary-row { margin-bottom: 0; }
.summary-label { color: #909399; margin-bottom: 8px; }
.summary-value { font-size: 28px; font-weight: 700; color: #0f5c90; }
.text-danger { color: #f56c6c; }
.w-140 { width: 140px; }
.w-180 { width: 180px; }
.w-240 { width: 240px; }
.inventory-table-scroll {
  width: 100%;
  min-width: 0;
  overflow-x: auto;
  overflow-y: hidden;
  overscroll-behavior-x: contain;
  -webkit-overflow-scrolling: touch;
  scrollbar-width: thin;
  scrollbar-color: #9db5c7 transparent;
}
.inventory-status-table { min-width: 1760px; }
.inventory-operation-table { min-width: 1340px; }
.drawer-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px; }
.drawer-title { font-size: 18px; font-weight: 700; color: #303133; }
.drawer-subtitle { font-size: 13px; color: #909399; margin-top: 4px; }
.detail-toolbar { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
.mb-16 { margin-bottom: 16px; }
:deep(.row-focus td) { background: #fff3d8 !important; }

@media (max-width: 768px) {
  .inventory-table-scroll {
    padding-bottom: 8px;
  }

  .inventory-status-table,
  .inventory-operation-table {
    width: max-content !important;
    max-width: none !important;
  }

  .inventory-status-table {
    min-width: 1760px !important;
  }

  .inventory-operation-table {
    min-width: 1340px !important;
  }
}
</style>
