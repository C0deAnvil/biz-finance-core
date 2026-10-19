<template>
  <div class="page-stack">
    <PageSection title="报表筛选" description="支持按周期、客户、供应商、产品和关键字筛选业务报表，并导出 Excel。">
      <template #actions>
        <el-button @click="load">刷新</el-button>
        <el-button @click="resetFilters">重置</el-button>
      </template>

      <el-row :gutter="12" class="filters-row">
        <el-col :span="8">
          <el-date-picker
            v-model="filters.dateRange"
            type="daterange"
            value-format="YYYY-MM-DD"
            range-separator="至"
            start-placeholder="开始日期"
            end-placeholder="结束日期"
            style="width: 100%"
          />
        </el-col>
        <el-col :span="4">
          <el-select v-model="filters.customerId" clearable placeholder="客户" style="width: 100%">
            <el-option v-for="item in customers" :key="item.id" :label="item.name" :value="item.id" />
          </el-select>
        </el-col>
        <el-col :span="4">
          <el-select v-model="filters.supplierId" clearable placeholder="供应商" style="width: 100%">
            <el-option v-for="item in suppliers" :key="item.id" :label="item.name" :value="item.id" />
          </el-select>
        </el-col>
        <el-col :span="4">
          <el-select v-model="filters.productId" clearable placeholder="产品" style="width: 100%">
            <el-option v-for="item in products" :key="item.id" :label="item.name" :value="item.id" />
          </el-select>
        </el-col>
        <el-col :span="4">
          <el-input v-model="filters.keyword" clearable placeholder="搜索单号 / 名称 / 备注" />
        </el-col>
      </el-row>
    </PageSection>

    <PageSection title="报表概览" description="以下统计基于当前筛选条件实时汇总。">
      <el-row :gutter="16">
        <el-col :span="4"><el-statistic title="销售单数" :value="report.summary.sales_order_count || 0" /></el-col>
        <el-col :span="4"><el-statistic title="销售额" :value="report.summary.sales_total || 0" /></el-col>
        <el-col :span="4"><el-statistic title="已定价销售利润" :value="report.summary.profit_total || 0" /></el-col>
        <el-col :span="4"><el-statistic title="采购付款" :value="report.summary.purchase_payment_total || 0" /></el-col>
        <el-col :span="4"><el-statistic title="资金流入" :value="report.summary.incoming_total || 0" /></el-col>
        <el-col :span="4"><el-statistic title="资金流出" :value="report.summary.outgoing_total || 0" /></el-col>
        <el-col :span="4" class="mt-16"><el-statistic title="库存资金占用" :value="report.summary.inventory_capital_total || 0" /></el-col>
        <el-col :span="4" class="mt-16"><el-statistic title="低库存条目" :value="report.summary.below_safety_stock_count || 0" /></el-col>
        <el-col :span="4" class="mt-16"><el-statistic title="净现金流" :value="netCashFlow" /></el-col>
      </el-row>
    </PageSection>

    <PageSection title="已发货销售盈利表" description="成本取实际出库流水；待定价订单不显示成本和利润，避免把未知成本当作零。">
      <template #actions>
        <el-button @click="exportSalesProfit">导出 Excel</el-button>
      </template>
      <el-table :data="pagedSalesProfitRows" stripe>
        <el-table-column prop="report_date" label="业务日期" min-width="160">
          <template #default="scope">{{ formatDateTime(scope.row.report_date) }}</template>
        </el-table-column>
        <el-table-column prop="order_no" label="销售单号" width="150" />
        <el-table-column prop="customer_name" label="客户" min-width="160" />
        <el-table-column prop="product_names" label="产品" min-width="180" show-overflow-tooltip />
        <el-table-column prop="total_quantity" label="数量" width="110">
          <template #default="scope">{{ formatNumber(scope.row.total_quantity) }}</template>
        </el-table-column>
        <el-table-column prop="sales_amount" label="销售额" width="120">
          <template #default="scope">{{ formatNumber(scope.row.sales_amount) }}</template>
        </el-table-column>
        <el-table-column prop="cost_amount" label="库存出库成本" width="130">
          <template #default="scope">{{ formatNumber(scope.row.cost_amount) }}</template>
        </el-table-column>
        <el-table-column prop="cost_status" label="成本状态" width="110">
          <template #default="scope">{{ scope.row.cost_status === 'actual' ? '实际成本' : scope.row.cost_status === 'estimated' ? '暂估成本' : '待定价' }}</template>
        </el-table-column>
        <el-table-column prop="transport_fee" label="运输费" width="110">
          <template #default="scope">{{ formatNumber(scope.row.transport_fee) }}</template>
        </el-table-column>
        <el-table-column prop="packaging_fee" label="包装费" width="110">
          <template #default="scope">{{ formatNumber(scope.row.packaging_fee) }}</template>
        </el-table-column>
        <el-table-column prop="profit_amount" label="利润" width="120">
          <template #default="scope">{{ formatNumber(scope.row.profit_amount) }}</template>
        </el-table-column>
        <el-table-column prop="status" label="状态" width="120" />
      </el-table>
      <AppPagination
        v-model:page="salesProfitPagination.page"
        v-model:page-size="salesProfitPagination.pageSize"
        :total="salesProfitTotal"
        :page-sizes="pageSizes"
      />
    </PageSection>

    <PageSection title="采购付款表" description="按付款记录查看采购订单、供应商、付款方式和金额。">
      <template #actions>
        <el-button @click="exportPurchasePayments">导出 Excel</el-button>
      </template>
      <el-table :data="pagedPurchasePaymentRows" stripe>
        <el-table-column prop="paid_at" label="付款时间" min-width="160">
          <template #default="scope">{{ formatDateTime(scope.row.paid_at) }}</template>
        </el-table-column>
        <el-table-column prop="purchase_order_no" label="采购单号" width="150" />
        <el-table-column prop="supplier_name" label="供应商" min-width="160" />
        <el-table-column prop="items_summary" label="采购内容" min-width="180" show-overflow-tooltip />
        <el-table-column prop="payment_type" label="付款方式" width="140" />
        <el-table-column prop="amount" label="金额" width="120">
          <template #default="scope">{{ formatNumber(scope.row.amount) }}</template>
        </el-table-column>
        <el-table-column prop="status" label="状态" width="120" />
        <el-table-column prop="remark" label="备注" min-width="180" show-overflow-tooltip />
      </el-table>
      <AppPagination
        v-model:page="purchasePaymentPagination.page"
        v-model:page-size="purchasePaymentPagination.pageSize"
        :total="purchasePaymentTotal"
        :page-sizes="pageSizes"
      />
    </PageSection>

    <PageSection title="资金流水表" description="集中查看各业务产生的资金流入和流出。">
      <template #actions>
        <el-button @click="exportCashFlows">导出 Excel</el-button>
      </template>
      <el-table :data="pagedCashFlowRows" stripe>
        <el-table-column prop="happened_at" label="发生时间" min-width="160">
          <template #default="scope">{{ formatDateTime(scope.row.happened_at) }}</template>
        </el-table-column>
        <el-table-column prop="record_no" label="流水号" width="150" />
        <el-table-column label="方向" width="100">
          <template #default="scope">
            <el-tag :type="scope.row.flow_type === 'incoming' ? 'success' : 'danger'">
              {{ scope.row.flow_type === 'incoming' ? '流入' : '流出' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="business_type" label="业务类型" width="140" />
        <el-table-column prop="related_no" label="关联单号" width="150" />
        <el-table-column prop="amount" label="金额" width="120">
          <template #default="scope">{{ formatNumber(scope.row.amount) }}</template>
        </el-table-column>
        <el-table-column prop="remark" label="备注" min-width="200" show-overflow-tooltip />
      </el-table>
      <AppPagination
        v-model:page="cashFlowPagination.page"
        v-model:page-size="cashFlowPagination.pageSize"
        :total="cashFlowTotal"
        :page-sizes="pageSizes"
      />
    </PageSection>

    <PageSection title="库存资金占用表" description="查看当前库存数量、移动均价、占用资金和安全库存状态。">
      <template #actions>
        <el-button @click="exportInventoryCapital">导出 Excel</el-button>
      </template>
      <el-table :data="pagedInventoryCapitalRows" stripe>
        <el-table-column prop="updated_at" label="更新时间" min-width="160">
          <template #default="scope">{{ formatDateTime(scope.row.updated_at) }}</template>
        </el-table-column>
        <el-table-column prop="warehouse_name" label="仓库" width="160" />
        <el-table-column label="类型" width="100">
          <template #default="scope">
            <el-tag :type="scope.row.item_type === 'product' ? 'success' : 'warning'">
              {{ scope.row.item_type === 'product' ? '产品' : '原料' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="item_code" label="编码" width="140" />
        <el-table-column prop="item_name" label="名称" min-width="160" />
        <el-table-column prop="quantity" label="库存数量" width="110">
          <template #default="scope">{{ formatNumber(scope.row.quantity) }}</template>
        </el-table-column>
        <el-table-column prop="available_quantity" label="可用数量" width="110">
          <template #default="scope">{{ formatNumber(scope.row.available_quantity) }}</template>
        </el-table-column>
        <el-table-column prop="avg_unit_cost" label="移动均价" width="110">
          <template #default="scope">{{ formatNumber(scope.row.avg_unit_cost) }}</template>
        </el-table-column>
        <el-table-column prop="inventory_amount" label="资金占用" width="120">
          <template #default="scope">{{ formatNumber(scope.row.inventory_amount) }}</template>
        </el-table-column>
        <el-table-column prop="safety_stock_qty" label="安全库存" width="110">
          <template #default="scope">{{ formatNumber(scope.row.safety_stock_qty) }}</template>
        </el-table-column>
        <el-table-column label="预警" width="100">
          <template #default="scope">
            <el-tag :type="scope.row.below_safety_stock ? 'danger' : 'success'">
              {{ scope.row.below_safety_stock ? '低库存' : '正常' }}
            </el-tag>
          </template>
        </el-table-column>
      </el-table>
      <AppPagination
        v-model:page="inventoryCapitalPagination.page"
        v-model:page-size="inventoryCapitalPagination.pageSize"
        :total="inventoryCapitalTotal"
        :page-sizes="pageSizes"
      />
    </PageSection>
  </div>
</template>

<script setup>
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/col/style/css'
import 'element-plus/es/components/date-picker/style/css'
import 'element-plus/es/components/input/style/css'
import 'element-plus/es/components/row/style/css'
import 'element-plus/es/components/select/style/css'
import 'element-plus/es/components/statistic/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/tag/style/css'

import ElButton from 'element-plus/es/components/button/index.mjs'
import ElCol from 'element-plus/es/components/col/index.mjs'
import ElDatePicker from 'element-plus/es/components/date-picker/index.mjs'
import ElInput from 'element-plus/es/components/input/index.mjs'
import ElRow from 'element-plus/es/components/row/index.mjs'
import ElSelect, { ElOption } from 'element-plus/es/components/select/index.mjs'
import ElStatistic from 'element-plus/es/components/statistic/index.mjs'
import ElTable, { ElTableColumn } from 'element-plus/es/components/table/index.mjs'
import ElTag from 'element-plus/es/components/tag/index.mjs'

import { computed, onMounted, reactive, ref } from 'vue'

import { get } from '../../api/client'
import AppPagination from '../../components/AppPagination.vue'
import PageSection from '../../components/PageSection.vue'
import { useLocalPagination } from '../../composables/useLocalPagination'
import { exportRowsToCsv } from '../../utils/export'
import { formatDateTime, formatNumber } from '../../utils/format'

function formatDate(date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
}

function defaultDateRange() {
  const today = new Date()
  return [formatDate(new Date(today.getFullYear(), today.getMonth(), 1)), formatDate(today)]
}

const filters = reactive({
  dateRange: defaultDateRange(),
  customerId: null,
  supplierId: null,
  productId: null,
  keyword: ''
})

const customers = ref([])
const suppliers = ref([])
const products = ref([])
const report = reactive({
  summary: {},
  sales_profit_rows: [],
  purchase_payment_rows: [],
  cash_flow_rows: [],
  inventory_capital_rows: []
})

const netCashFlow = computed(() => Number(report.summary.incoming_total || 0) - Number(report.summary.outgoing_total || 0))
const salesProfitRows = computed(() => report.sales_profit_rows || [])
const purchasePaymentRows = computed(() => report.purchase_payment_rows || [])
const cashFlowRows = computed(() => report.cash_flow_rows || [])
const inventoryCapitalRows = computed(() => report.inventory_capital_rows || [])
const {
  pagination: salesProfitPagination,
  total: salesProfitTotal,
  pageSizes,
  pagedItems: pagedSalesProfitRows
} = useLocalPagination(salesProfitRows)
const {
  pagination: purchasePaymentPagination,
  total: purchasePaymentTotal,
  pagedItems: pagedPurchasePaymentRows
} = useLocalPagination(purchasePaymentRows)
const {
  pagination: cashFlowPagination,
  total: cashFlowTotal,
  pagedItems: pagedCashFlowRows
} = useLocalPagination(cashFlowRows)
const {
  pagination: inventoryCapitalPagination,
  total: inventoryCapitalTotal,
  pagedItems: pagedInventoryCapitalRows
} = useLocalPagination(inventoryCapitalRows)

function buildQueryString() {
  const params = new URLSearchParams()
  if (filters.dateRange?.length === 2) {
    params.set('date_from', filters.dateRange[0])
    params.set('date_to', filters.dateRange[1])
  }
  if (filters.customerId) params.set('customer_id', filters.customerId)
  if (filters.supplierId) params.set('supplier_id', filters.supplierId)
  if (filters.productId) params.set('product_id', filters.productId)
  if (filters.keyword.trim()) params.set('keyword', filters.keyword.trim())
  const query = params.toString()
  return query ? `?${query}` : ''
}

async function loadOptions() {
  const [customerRows, supplierRows, productRows] = await Promise.all([
    get('/api/master-data/customers'),
    get('/api/master-data/suppliers'),
    get('/api/master-data/products')
  ])
  customers.value = customerRows
  suppliers.value = supplierRows
  products.value = productRows.filter((item) => item.is_active !== false)
}

async function load() {
  const payload = await get(`/api/finance/reports/detail${buildQueryString()}`)
  report.summary = payload.summary || {}
  report.sales_profit_rows = payload.sales_profit_rows || []
  report.purchase_payment_rows = payload.purchase_payment_rows || []
  report.cash_flow_rows = payload.cash_flow_rows || []
  report.inventory_capital_rows = payload.inventory_capital_rows || []
}

function resetFilters() {
  filters.dateRange = defaultDateRange()
  filters.customerId = null
  filters.supplierId = null
  filters.productId = null
  filters.keyword = ''
  load()
}

function exportSalesProfit() {
  exportRowsToCsv('sales_profit_report', report.sales_profit_rows)
}

function exportPurchasePayments() {
  exportRowsToCsv('purchase_payment_report', report.purchase_payment_rows)
}

function exportCashFlows() {
  exportRowsToCsv('cash_flow_report', report.cash_flow_rows)
}

function exportInventoryCapital() {
  exportRowsToCsv('inventory_capital_report', report.inventory_capital_rows)
}

onMounted(async () => {
  await loadOptions()
  await load()
})
</script>

<style scoped>
.page-stack { display: flex; flex-direction: column; gap: 16px; }
.filters-row { row-gap: 12px; }
.mt-16 { margin-top: 16px; }
</style>
