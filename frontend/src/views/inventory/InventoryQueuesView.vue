<template>
  <div class="queue-page">
    <el-row :gutter="16">
      <el-col :span="12">
        <PageSection title="待置货列表" description="销售订单待确认时显示，便于库存提前置货。">
          <template #actions>
            <el-button @click="load">刷新</el-button>
          </template>
          <el-table :data="pagedPlacementRows" stripe>
            <el-table-column prop="order_no" label="订单号" width="140">
              <template #default="scope">
                <el-button text type="primary" @click="openOrderDetail(scope.row)">{{ scope.row.order_no }}</el-button>
              </template>
            </el-table-column>
            <el-table-column prop="customer_name" label="客户" min-width="120" show-overflow-tooltip />
            <el-table-column prop="status" label="状态" width="100">
              <template #default="scope"><el-tag :type="statusType(scope.row.status)">{{ statusLabel(scope.row.status) }}</el-tag></template>
            </el-table-column>
            <el-table-column label="财务修改" width="110">
              <template #default="scope"><el-tag v-if="scope.row.finance_edit_pending" type="warning">待确认</el-tag><span v-else>-</span></template>
            </el-table-column>
            <el-table-column label="快捷操作" width="140" fixed="right" align="center">
              <template #default="scope">
                <el-button v-if="canConfirmFinanceEdit(scope.row)" class="queue-main-action" size="small" type="warning" @click="openOrderDetail(scope.row)">改单详情</el-button>
                <el-button v-else class="queue-main-action" size="small" type="success" @click="markPlacement(scope.row)">已置货</el-button>
              </template>
            </el-table-column>
          </el-table>
          <AppPagination
            v-model:page="placementPagination.page"
            v-model:page-size="placementPagination.pageSize"
            :total="placementTotal"
            :page-sizes="pageSizes"
            @change="handlePlacementPaginationChange"
          />
        </PageSection>
      </el-col>

      <el-col :span="12">
        <PageSection title="待发货列表" description="订单进入待发货后显示；库管在此确认本次发货数量并直接完成出库。未全部发货的订单会继续保留在列表中。">
          <template #actions>
            <el-button @click="load">刷新</el-button>
          </template>
          <el-table :data="pagedShipmentRows" stripe>
            <el-table-column prop="order_no" label="订单号" width="140">
              <template #default="scope">
                <el-button text type="primary" @click="openOrderDetail(scope.row)">{{ scope.row.order_no }}</el-button>
              </template>
            </el-table-column>
            <el-table-column prop="customer_name" label="客户" min-width="120" show-overflow-tooltip />
            <el-table-column prop="status" label="状态" width="100">
              <template #default="scope"><el-tag :type="statusType(scope.row.status)">{{ statusLabel(scope.row.status) }}</el-tag></template>
            </el-table-column>
            <el-table-column label="财务修改" width="110">
              <template #default="scope"><el-tag v-if="scope.row.finance_edit_pending" type="warning">待确认</el-tag><span v-else>-</span></template>
            </el-table-column>
            <el-table-column label="快捷操作" width="160" fixed="right" align="center">
              <template #default="scope">
                <el-button v-if="canConfirmFinanceEdit(scope.row)" class="queue-main-action" size="small" type="warning" @click="openOrderDetail(scope.row)">改单详情</el-button>
                <el-button v-else class="queue-main-action" size="small" type="primary" @click="openShipmentDialog(scope.row)">发货出库</el-button>
              </template>
            </el-table-column>
          </el-table>
          <AppPagination
            v-model:page="shipmentPagination.page"
            v-model:page-size="shipmentPagination.pageSize"
            :total="shipmentTotal"
            :page-sizes="pageSizes"
            @change="handleShipmentPaginationChange"
          />
        </PageSection>
      </el-col>
    </el-row>

    <PageSection title="客户订单看板" description="仅展示去除单价和金额后的客户订单摘要，便于库存追踪已发货与已完结订单。">
      <template #actions>
        <el-button @click="load">刷新</el-button>
      </template>
      <el-table :data="pagedSalesOrderRows" stripe>
        <el-table-column prop="order_no" label="订单号" width="160">
          <template #default="scope">
            <el-button text type="primary" @click="openOrderDetail(scope.row)">{{ scope.row.order_no }}</el-button>
          </template>
        </el-table-column>
        <el-table-column prop="customer_name" label="客户" min-width="150" show-overflow-tooltip />
        <el-table-column prop="item_summary" label="商品摘要" min-width="220" show-overflow-tooltip />
        <el-table-column prop="item_count" label="明细数" width="90" />
        <el-table-column prop="status_label" label="状态" width="110">
          <template #default="scope"><el-tag :type="statusType(scope.row.status)">{{ scope.row.status_label }}</el-tag></template>
        </el-table-column>
        <el-table-column prop="finance_edit_pending" label="改单" width="100">
          <template #default="scope">
            <el-tag v-if="scope.row.finance_edit_pending" type="warning">待确认</el-tag>
            <span v-else>-</span>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="创建时间" min-width="170">
          <template #default="scope">{{ formatDateTime(scope.row.created_at) }}</template>
        </el-table-column>
      </el-table>
      <AppPagination
        v-model:page="salesOrderPagination.page"
        v-model:page-size="salesOrderPagination.pageSize"
        :total="salesOrderTotal"
        :page-sizes="pageSizes"
        @change="handleSalesOrderPaginationChange"
      />
    </PageSection>

    <el-dialog v-model="shipmentDialogVisible" title="客户订单发货出库" width="920px">
      <el-form v-if="activeShipment" :model="shipmentForm" label-width="100px">
        <el-row :gutter="16" class="shipment-head">
          <el-col :span="8"><span class="field-label">客户订单：</span>{{ activeShipment.order_no }}</el-col>
          <el-col :span="8"><span class="field-label">客户：</span>{{ activeShipment.customer_name || '-' }}</el-col>
          <el-col :span="8"><span class="field-label">状态：</span>{{ statusLabel(activeShipment.order_status || activeShipment.status) }}</el-col>
        </el-row>
        <el-table :data="shipmentForm.items" border>
          <el-table-column prop="product_name" label="商品" min-width="180" />
          <el-table-column prop="product_code" label="编码" width="130" />
          <el-table-column prop="display_specification" label="规格" width="130" />
          <el-table-column prop="packaging_name" label="包装" width="140" />
          <el-table-column prop="kg_per_bag" label="kg/袋" width="100">
            <template #default="scope">{{ formatNumber(scope.row.kg_per_bag) }}</template>
          </el-table-column>
          <el-table-column prop="bag_count" label="袋数" width="100">
            <template #default="scope">{{ formatNumber(scope.row.bag_count) }}</template>
          </el-table-column>
          <el-table-column prop="ordered_quantity" label="订单数量" width="110">
            <template #default="scope">{{ formatNumber(scope.row.ordered_quantity) }}</template>
          </el-table-column>
          <el-table-column prop="shipped_quantity" label="累计已发" width="110">
            <template #default="scope">{{ formatNumber(scope.row.shipped_quantity) }}</template>
          </el-table-column>
          <el-table-column prop="remaining_quantity" label="剩余待发" width="110">
            <template #default="scope">{{ formatNumber(scope.row.remaining_quantity) }}</template>
          </el-table-column>
          <el-table-column label="本次发货" width="160">
            <template #default="scope">
              <el-input-number
                v-model="scope.row.ship_quantity"
                :min="0"
                :max="Number(scope.row.remaining_quantity || 0)"
                :precision="2"
                style="width: 100%"
              />
            </template>
          </el-table-column>
        </el-table>
        <el-form-item class="mt-16" label="发货说明">
          <el-input v-model="shipmentForm.remark" type="textarea" :rows="3" placeholder="可填写分批发货、库存不足或其他特殊说明" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="shipmentDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="shipmentSubmitting" @click="submitShipment">确认出库并发货</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="detailVisible" title="客户订单详情" width="960px">
      <template v-if="orderDetail">
        <div v-if="orderDetail.finance_edit_snapshot" class="detail-toolbar">
          <span>原始订单详情</span>
        </div>
        <el-alert
          v-if="orderDetail.finance_edit_snapshot?.before_data?.legacy_snapshot_note"
          class="mb-12"
          type="warning"
          :closable="false"
          :title="orderDetail.finance_edit_snapshot.before_data.legacy_snapshot_note"
        />
        <el-descriptions v-if="orderDetail.finance_edit_snapshot" :column="2" border>
          <el-descriptions-item label="改单人">{{ orderDetail.finance_edit_snapshot.changed_by_name || '-' }}</el-descriptions-item>
          <el-descriptions-item label="改单时间">{{ formatDateTime(orderDetail.finance_edit_snapshot.changed_at) }}</el-descriptions-item>
          <el-descriptions-item label="改单说明" :span="2">{{ orderDetail.finance_edit_snapshot.content || '-' }}</el-descriptions-item>
          <el-descriptions-item label="客户">{{ orderDetail.finance_edit_snapshot.before_data?.customer_name || orderDetail.customer?.name || '-' }}</el-descriptions-item>
          <el-descriptions-item label="经办人">{{ orderDetail.finance_edit_snapshot.before_data?.operator_name || '-' }}</el-descriptions-item>
          <el-descriptions-item label="付款方式">{{ orderDetail.finance_edit_snapshot.before_data?.payment_type || '-' }}</el-descriptions-item>
          <el-descriptions-item label="收货人">{{ orderDetail.finance_edit_snapshot.before_data?.consignee || '-' }}</el-descriptions-item>
          <el-descriptions-item label="联系电话">{{ orderDetail.finance_edit_snapshot.before_data?.consignee_phone || '-' }}</el-descriptions-item>
          <el-descriptions-item label="送货地址" :span="2">{{ orderDetail.finance_edit_snapshot.before_data?.delivery_address || '-' }}</el-descriptions-item>
          <el-descriptions-item label="整单备注" :span="2">{{ orderDetail.finance_edit_snapshot.before_data?.order_remark || '-' }}</el-descriptions-item>
        </el-descriptions>
        <el-table v-if="orderDetail.finance_edit_snapshot" :data="orderDetail.finance_edit_snapshot.before_data?.items || []" border class="mt-16">
          <el-table-column prop="product_name" label="商品" min-width="180" />
          <el-table-column prop="product_code" label="编码" width="130" />
          <el-table-column prop="display_specification" label="规格" width="130" />
          <el-table-column prop="packaging_name" label="包装" width="140" />
          <el-table-column prop="kg_per_bag" label="kg/袋" width="100">
            <template #default="scope">{{ formatNumber(scope.row.kg_per_bag) }}</template>
          </el-table-column>
          <el-table-column prop="bag_count" label="袋数" width="100">
            <template #default="scope">{{ formatNumber(scope.row.bag_count) }}</template>
          </el-table-column>
          <el-table-column prop="quantity" label="订单数量" width="110">
            <template #default="scope">{{ formatNumber(scope.row.quantity) }}</template>
          </el-table-column>
          <el-table-column prop="remark" label="备注" min-width="180" show-overflow-tooltip />
        </el-table>

        <div class="detail-toolbar mt-16">
          <span>修改后订单详情</span>
        </div>
        <el-descriptions :column="2" border>
          <el-descriptions-item label="客户订单">{{ orderDetail.order_no }}</el-descriptions-item>
          <el-descriptions-item label="客户">{{ orderDetail.customer?.name || '-' }}</el-descriptions-item>
          <el-descriptions-item label="订单状态">{{ orderDetail.status_label || statusLabel(orderDetail.status) }}</el-descriptions-item>
          <el-descriptions-item label="付款方式">{{ orderDetail.payment_type_label || '-' }}</el-descriptions-item>
          <el-descriptions-item label="财务修改确认">{{ financeEditConfirmText(orderDetail) }}</el-descriptions-item>
          <el-descriptions-item label="收货人">{{ orderDetail.consignee || '-' }}</el-descriptions-item>
          <el-descriptions-item label="联系电话">{{ orderDetail.consignee_phone || '-' }}</el-descriptions-item>
          <el-descriptions-item label="送货地址" :span="2">{{ orderDetail.delivery_address || '-' }}</el-descriptions-item>
        </el-descriptions>
        <el-table :data="orderDetail.items || []" border class="mt-16">
          <el-table-column prop="product_name" label="商品" min-width="180" />
          <el-table-column prop="display_specification" label="规格" width="130" />
          <el-table-column prop="packaging_name" label="包装" width="140" />
          <el-table-column prop="kg_per_bag" label="kg/袋" width="100">
            <template #default="scope">{{ formatNumber(scope.row.kg_per_bag) }}</template>
          </el-table-column>
          <el-table-column prop="bag_count" label="袋数" width="100">
            <template #default="scope">{{ formatNumber(scope.row.bag_count) }}</template>
          </el-table-column>
          <el-table-column prop="quantity" label="订单数量" width="110">
            <template #default="scope">{{ formatNumber(scope.row.quantity) }}</template>
          </el-table-column>
          <el-table-column prop="shipped_quantity" label="累计已发" width="110">
            <template #default="scope">{{ formatNumber(scope.row.shipped_quantity) }}</template>
          </el-table-column>
          <el-table-column prop="remaining_shipment_quantity" label="剩余待发" width="110">
            <template #default="scope">{{ formatNumber(scope.row.remaining_shipment_quantity) }}</template>
          </el-table-column>
          <el-table-column prop="remark" label="备注" min-width="180" show-overflow-tooltip />
        </el-table>
      </template>
      <template #footer>
        <el-button @click="detailVisible = false">关闭</el-button>
        <el-button v-if="canConfirmFinanceEdit(orderDetail)" type="warning" @click="confirmFinanceEdit(orderDetail)">确认财务修改</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import 'element-plus/es/components/message/style/css'
import 'element-plus/es/components/alert/style/css'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/col/style/css'
import 'element-plus/es/components/descriptions/style/css'
import 'element-plus/es/components/dialog/style/css'
import 'element-plus/es/components/form/style/css'
import 'element-plus/es/components/input/style/css'
import 'element-plus/es/components/input-number/style/css'
import 'element-plus/es/components/row/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/tag/style/css'

import { ElMessage } from 'element-plus/es/components/message/index.mjs'

import ElAlert from 'element-plus/es/components/alert/index.mjs'
import ElButton from 'element-plus/es/components/button/index.mjs'
import ElCol from 'element-plus/es/components/col/index.mjs'
import ElDescriptions, { ElDescriptionsItem } from 'element-plus/es/components/descriptions/index.mjs'
import ElDialog from 'element-plus/es/components/dialog/index.mjs'
import ElForm, { ElFormItem } from 'element-plus/es/components/form/index.mjs'
import ElInput from 'element-plus/es/components/input/index.mjs'
import ElInputNumber from 'element-plus/es/components/input-number/index.mjs'
import ElRow from 'element-plus/es/components/row/index.mjs'
import ElTable, { ElTableColumn } from 'element-plus/es/components/table/index.mjs'
import ElTag from 'element-plus/es/components/tag/index.mjs'

import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import { get, post } from '../../api/client'
import AppPagination from '../../components/AppPagination.vue'
import PageSection from '../../components/PageSection.vue'
import { useLocalPagination } from '../../composables/useLocalPagination'
import { formatDateTime, formatNumber, statusLabel, statusType } from '../../utils/format'

const route = useRoute()
const queues = reactive({ placement: [], shipment: [], salesOrders: [] })
const shipmentDialogVisible = ref(false)
const activeShipment = ref(null)
const detailVisible = ref(false)
const orderDetail = ref(null)
const shipmentForm = reactive({ items: [], remark: '' })
const shipmentSubmitting = ref(false)
const placementPagination = reactive({ page: 1, pageSize: 10 })
const shipmentPagination = reactive({ page: 1, pageSize: 10 })
const salesOrderPagination = reactive({ page: 1, pageSize: 10 })
const placementTotal = ref(0)
const shipmentTotal = ref(0)
const salesOrderTotal = ref(0)
const pageSizes = [10, 20, 50, 100]
const pagedPlacementRows = computed(() => queues.placement)
const pagedShipmentRows = computed(() => queues.shipment)
const pagedSalesOrderRows = computed(() => queues.salesOrders)

async function load() {
  const query = new URLSearchParams({
    placement_page: String(placementPagination.page),
    placement_page_size: String(placementPagination.pageSize),
    shipment_page: String(shipmentPagination.page),
    shipment_page_size: String(shipmentPagination.pageSize),
    sales_order_page: String(salesOrderPagination.page),
    sales_order_page_size: String(salesOrderPagination.pageSize)
  })
  const data = await get(`/api/inventory/queues?${query.toString()}`)
  queues.placement = data.placement || []
  queues.shipment = data.shipment || []
  queues.salesOrders = data.sales_orders || []
  placementTotal.value = Number(data.pagination?.placement?.total || 0)
  shipmentTotal.value = Number(data.pagination?.shipment?.total || 0)
  salesOrderTotal.value = Number(data.pagination?.sales_orders?.total || 0)
  applyFocus()
}

async function handlePlacementPaginationChange({ page, pageSize }) {
  placementPagination.page = page
  placementPagination.pageSize = pageSize
  await load()
}

async function handleShipmentPaginationChange({ page, pageSize }) {
  shipmentPagination.page = page
  shipmentPagination.pageSize = pageSize
  await load()
}

async function handleSalesOrderPaginationChange({ page, pageSize }) {
  salesOrderPagination.page = page
  salesOrderPagination.pageSize = pageSize
  await load()
}

function resolveOrderId(row) {
  return row?.order_id ?? row?.id ?? null
}

async function openOrderDetail(row) {
  const orderId = resolveOrderId(row)
  if (!orderId) return
  try {
    orderDetail.value = await get(`/api/inventory/sales-orders/${orderId}`)
    detailVisible.value = true
  } catch (error) {
    ElMessage.error(error.message || '获取订单详情失败')
  }
}

async function markPlacement(row) {
  await post(`/api/inventory/queues/${row.queue_id}/placement`, { action: 'placed' })
  ElMessage.success('已标记为已置货')
  await load()
}


function canConfirmFinanceEdit(row) {
  return Boolean(row?.finance_edit_confirm_state?.pending)
}

async function confirmFinanceEdit(row) {
  const orderId = resolveOrderId(row)
  if (!orderId || !canConfirmFinanceEdit(row)) return
  try {
    const result = await post(`/api/sales/orders/${orderId}/finance-edit/confirm`, { note: '?????????' })
    ElMessage.success('???????')
    const nextState = { ...(row.finance_edit_confirm_state || {}), pending: false }
    Object.assign(row, {
      finance_edit_confirm_state: nextState,
      finance_edit_pending: Boolean(result?.finance_edit_pending)
    })
    if (orderDetail.value?.id === orderId) {
      orderDetail.value = {
        ...orderDetail.value,
        finance_edit_pending: Boolean(result?.finance_edit_pending),
        finance_edit_confirm_state: nextState
      }
    }
    const patchRows = (items) => items.map((item) => resolveOrderId(item) === orderId ? { ...item, finance_edit_confirm_state: nextState, finance_edit_pending: Boolean(result?.finance_edit_pending) } : item)
    queues.placement = patchRows(queues.placement)
    queues.shipment = patchRows(queues.shipment)
    queues.salesOrders = patchRows(queues.salesOrders)
  } catch (error) {
    ElMessage.error(error.message || '????????')
  }
}

function applyFocus() {
  const focusId = Number(route.query.focusId || 0)
  const focusType = String(route.query.focusType || '')
  if (!focusId || focusType !== 'sales_order_finance_edit_inventory_confirm') return
  const target = [...queues.placement, ...queues.shipment, ...queues.salesOrders].find((item) => resolveOrderId(item) === focusId)
  if (target) {
    openOrderDetail(target)
  }
}

function buildShipmentItems(row) {
  return (row.items || [])
    .filter((item) => Number(item.remaining_quantity || 0) > 0)
    .map((item) => ({
      ...item,
      ship_quantity: Number(item.ship_quantity ?? item.remaining_quantity ?? 0)
    }))
}

async function openShipmentDialog(row) {
  const orderId = resolveOrderId(row)
  if (!orderId) {
    ElMessage.error('未找到客户订单，无法发货')
    return
  }
  try {
    const detail = await get(`/api/inventory/sales-orders/${orderId}`)
    activeShipment.value = row
    shipmentForm.items = buildShipmentItems({
      items: (detail.items || []).map((item) => ({
        ...item,
        ordered_quantity: item.quantity,
        remaining_quantity: item.remaining_shipment_quantity,
        ship_quantity: item.remaining_shipment_quantity
      }))
    })
    shipmentForm.remark = ''
    if (!shipmentForm.items.length) {
      ElMessage.warning('当前客户订单没有可发货明细，请刷新后重试')
    }
    shipmentDialogVisible.value = true
  } catch (error) {
    ElMessage.error(error.message || '获取待发货订单失败')
  }
}

async function submitShipment() {
  if (!activeShipment.value || shipmentSubmitting.value) return
  const items = shipmentForm.items
    .filter((item) => Number(item.ship_quantity || 0) > 0)
    .map((item) => ({
      sales_order_item_id: item.sales_order_item_id,
      shipped_quantity: Number(item.ship_quantity || 0)
    }))
  if (!items.length) {
    ElMessage.warning('请至少填写一项本次发货数量')
    return
  }
  const orderId = resolveOrderId(activeShipment.value)
  if (!orderId) {
    ElMessage.error('未找到客户订单，无法发货')
    return
  }
  const targetUrl = activeShipment.value.queue_id
    ? `/api/inventory/queues/${activeShipment.value.queue_id}/shipment`
    : `/api/inventory/sales-orders/${orderId}/shipment`
  shipmentSubmitting.value = true
  try {
    await post(targetUrl, {
      items,
      remark: shipmentForm.remark
    })
    ElMessage.success('客户订单发货出库已完成')
    shipmentDialogVisible.value = false
    await load()
  } catch (error) {
    ElMessage.error(error.message || '客户订单发货出库失败')
  } finally {
    shipmentSubmitting.value = false
  }
}

function financeEditConfirmText(row) {
  if (!row?.finance_edit_pending) return '无待确认修改'
  return row.finance_edit_confirm_state?.pending ? '待当前账号确认' : '当前账号已确认，等待其他相关人员确认'
}

onMounted(load)

watch(() => [route.query.focusId, route.query.focusType], () => {
  applyFocus()
})
</script>

<style scoped>
.queue-page :deep(.el-table .cell) { white-space: nowrap; }
.queue-main-action { min-width: 84px; font-weight: 600; }
.detail-toolbar { display: flex; justify-content: space-between; align-items: center; margin: 8px 0 12px; font-weight: 600; }
.shipment-head { margin-bottom: 16px; color: #303133; }
.field-label { color: #909399; }
.mb-12 { margin-bottom: 12px; }
.mt-16 { margin-top: 16px; }
</style>
