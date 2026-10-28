<template>
  <div class="finance-overview">
    <PageSection title="财务经营概览" description="按业务发生日期汇总；点击数字查看原始单据。金额保留四位小数。">
      <template #actions>
        <el-button :loading="loading" @click="load">刷新</el-button>
      </template>
      <div class="filter-bar">
        <el-date-picker v-model="dateRange" type="daterange" value-format="YYYY-MM-DD" range-separator="至" start-placeholder="开始日期" end-placeholder="结束日期" :clearable="false" class="date-picker" />
        <el-button type="primary" :loading="loading" @click="load">查询</el-button>
        <el-button @click="setThisMonth">本月</el-button>
        <span class="period-note">{{ report.period?.date_from || '-' }} 至 {{ report.period?.date_to || '-' }}</span>
      </div>
      <div class="basis-note">
        销售按发货日；新增应收按应收计划创建日；采购应付按审批通过日（无需审批的按创建日）；报销按提交日；实际收付款按发生日。未收、未付余额为这些单据的当前余额。
      </div>
    </PageSection>

    <PageSection title="销售与回款" description="销售额包含销售单总额；产品销售明细按商品行金额统计。">
      <div class="metrics-grid">
        <button v-for="metric in salesMetrics" :key="metric.key" class="metric-card" :class="{ selected: selectedKey === metric.detail }" @click="selectDetail(metric)">
          <span class="metric-title">{{ metric.title }}</span>
          <strong>{{ metric.count ? (report.summary[metric.key] || 0) : money(report.summary[metric.key]) }}</strong>
          <span class="metric-caption">{{ metric.caption }} <span aria-hidden="true">›</span></span>
        </button>
      </div>
    </PageSection>

    <PageSection title="采购、报销与待办" description="新增应付反映采购义务；实际付款和资金流水另行统计。">
      <div class="metrics-grid">
        <button v-for="metric in expenseMetrics" :key="metric.key" class="metric-card" :class="{ selected: selectedKey === metric.detail }" @click="selectDetail(metric)">
          <span class="metric-title">{{ metric.title }}</span>
          <strong>{{ metric.count ? (report.summary[metric.key] || 0) : money(report.summary[metric.key]) }}</strong>
          <span class="metric-caption">{{ metric.caption }} <span aria-hidden="true">›</span></span>
        </button>
      </div>
    </PageSection>

    <PageSection title="每日业务趋势" description="日期轴显示所选期间内有销售、收款或采购付款记录的日期。">
      <div v-if="report.trend?.length" class="trend-wrap">
        <div class="trend-legend"><span class="sales-dot">销售额</span><span class="collection-dot">实际收款</span><span class="payment-dot">采购付款</span></div>
        <div class="trend-chart">
          <div v-for="day in report.trend" :key="day.date" class="trend-day" :title="`${day.date} 销售 ${money(day.sales)}；收款 ${money(day.collections)}；付款 ${money(day.payments)}`">
            <div class="trend-bars">
              <div class="trend-bar sales-bar" :style="barStyle(day.sales)" />
              <div class="trend-bar collection-bar" :style="barStyle(day.collections)" />
              <div class="trend-bar payment-bar" :style="barStyle(day.payments)" />
            </div>
            <span>{{ day.date.slice(5) }}</span>
          </div>
        </div>
      </div>
      <el-empty v-else description="所选期间暂无业务趋势数据" :image-size="72" />
    </PageSection>

    <div id="finance-overview-details">
    <PageSection :title="detailTitle" :description="detailDescription">
      <template #actions>
        <el-button :disabled="!activeRows.length" @click="exportDetails">导出当前明细</el-button>
      </template>
      <div class="detail-tabs">
        <el-button v-for="tab in detailTabs" :key="tab.key" size="small" :type="selectedKey === tab.key ? 'primary' : 'default'" @click="selectedKey = tab.key">{{ tab.label }} {{ (report.details?.[tab.key] || []).length }}</el-button>
      </div>
      <el-table :data="pagedRows" stripe class="detail-table">
        <el-table-column v-if="selectedKey !== 'products'" prop="date" label="业务时间" width="175"><template #default="scope">{{ formatDateTime(scope.row.date) }}</template></el-table-column>
        <el-table-column prop="no" :label="selectedKey === 'products' ? '产品编码' : '单据编号'" width="175" />
        <el-table-column :prop="selectedKey === 'products' ? 'name' : 'party'" :label="selectedKey === 'products' ? '产品' : '往来方 / 人员'" min-width="175" show-overflow-tooltip>
          <template #default="scope">{{ selectedKey === 'products' ? scope.row.name : scope.row.party }}</template>
        </el-table-column>
        <el-table-column v-if="selectedKey === 'sales'" prop="items" label="产品" min-width="200" show-overflow-tooltip />
        <el-table-column v-if="['finance_tasks', 'reimbursements', 'reimbursement_payments', 'sales_refunds', 'cash_in', 'cash_out'].includes(selectedKey)" prop="name" label="事项" min-width="155" show-overflow-tooltip />
        <el-table-column v-if="selectedKey === 'products'" label="销售数量" width="135"><template #default="scope">{{ quantity(scope.row.quantity) }}</template></el-table-column>
        <el-table-column v-if="selectedKey === 'products'" prop="order_count" label="销售单数" width="110" />
        <el-table-column label="金额" width="150" align="right"><template #default="scope">{{ money(scope.row.amount) }}</template></el-table-column>
        <el-table-column v-if="['receivables', 'payables'].includes(selectedKey)" :label="selectedKey === 'receivables' ? '已收' : '已付'" width="140" align="right"><template #default="scope">{{ money(selectedKey === 'receivables' ? scope.row.received : scope.row.paid) }}</template></el-table-column>
        <el-table-column v-if="['receivables', 'payables'].includes(selectedKey)" label="当前未结" width="145" align="right"><template #default="scope">{{ money(scope.row.balance) }}</template></el-table-column>
        <el-table-column v-if="showStatus" prop="status" label="状态" width="120" />
        <el-table-column label="操作" width="100" fixed="right"><template #default="scope"><el-button link type="primary" @click="openSource(scope.row)">查看</el-button></template></el-table-column>
      </el-table>
      <AppPagination v-model:page="pagination.page" v-model:page-size="pagination.pageSize" :total="total" :page-sizes="pageSizes" />
    </PageSection>
    </div>
  </div>
</template>

<script setup>
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/date-picker/style/css'
import 'element-plus/es/components/empty/style/css'
import 'element-plus/es/components/message/style/css'
import 'element-plus/es/components/table/style/css'
import ElButton from 'element-plus/es/components/button/index.mjs'
import ElDatePicker from 'element-plus/es/components/date-picker/index.mjs'
import ElEmpty from 'element-plus/es/components/empty/index.mjs'
import { ElMessage } from 'element-plus/es/components/message/index.mjs'
import ElTable, { ElTableColumn } from 'element-plus/es/components/table/index.mjs'
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { get } from '../../api/client'
import AppPagination from '../../components/AppPagination.vue'
import PageSection from '../../components/PageSection.vue'
import { useLocalPagination } from '../../composables/useLocalPagination'
import { exportRowsToCsv } from '../../utils/export'
import { formatDateTime } from '../../utils/format'

const router = useRouter()
const pad = (n) => String(n).padStart(2, '0')
const dateText = (value) => `${value.getFullYear()}-${pad(value.getMonth() + 1)}-${pad(value.getDate())}`
const initialRange = () => { const now = new Date(); return [dateText(new Date(now.getFullYear(), now.getMonth(), 1)), dateText(now)] }
const dateRange = ref(initialRange())
const loading = ref(false)
const selectedKey = ref('products')
const report = ref({ period: {}, summary: {}, details: {}, trend: [] })

const salesMetrics = [
  { key: 'sales_amount', title: '发货销售额', detail: 'sales', caption: '按销售单总额', count: false },
  { key: 'sales_count', title: '已发货销售单', detail: 'sales', caption: '查看销售单', count: true },
  { key: 'product_count', title: '已售产品种类', detail: 'products', caption: '查看产品销售', count: true },
  { key: 'receivable_amount', title: '新增应收额', detail: 'receivables', caption: '区间内生成', count: false },
  { key: 'receivable_count', title: '应收客户订单', detail: 'receivables', caption: '按订单去重', count: true },
  { key: 'receivable_balance', title: '上述应收当前未收', detail: 'receivables', caption: '当前余额', count: false },
  { key: 'collection_amount', title: '实际收款', detail: 'collections', caption: '按回款日期', count: false },
  { key: 'sales_refund_amount', title: '销售退款', detail: 'sales_refunds', caption: '按退款日期', count: false },
  { key: 'cash_in', title: '资金流入', detail: 'cash_in', caption: '按资金流水日期', count: false },
]
const expenseMetrics = [
  { key: 'payable_amount', title: '新增采购应付', detail: 'payables', caption: '已生效采购单', count: false },
  { key: 'payable_count', title: '采购应付单数', detail: 'payables', caption: '查看采购单', count: true },
  { key: 'payable_balance', title: '上述采购当前未付', detail: 'payables', caption: '当前余额', count: false },
  { key: 'payment_amount', title: '实际采购付款', detail: 'payments', caption: '按付款日期', count: false },
  { key: 'reimbursement_amount', title: '报销申请额', detail: 'reimbursements', caption: '不含已驳回', count: false },
  { key: 'reimbursement_count', title: '报销申请单数', detail: 'reimbursements', caption: '查看报销单', count: true },
  { key: 'reimbursement_paid_amount', title: '实际报销出账', detail: 'reimbursement_payments', caption: '按出账日期', count: false },
  { key: 'cash_out', title: '资金流出', detail: 'cash_out', caption: '按资金流水日期', count: false },
  { key: 'finance_task_count', title: '当前待财务处理', detail: 'finance_tasks', caption: '区间内提交', count: true },
]
const detailTabs = [
  { key: 'products', label: '产品销售' }, { key: 'sales', label: '销售订单' },
  { key: 'receivables', label: '新增应收' }, { key: 'collections', label: '实际收款' },
  { key: 'sales_refunds', label: '销售退款' },
  { key: 'payables', label: '采购应付' }, { key: 'payments', label: '采购付款' },
  { key: 'reimbursements', label: '报销申请' }, { key: 'reimbursement_payments', label: '报销出账' }, { key: 'cash_in', label: '资金流入' },
  { key: 'cash_out', label: '资金流出' }, { key: 'finance_tasks', label: '财务待办' },
]
const activeRows = computed(() => report.value.details?.[selectedKey.value] || [])
const { pagination, total, pageSizes, pagedItems: pagedRows } = useLocalPagination(activeRows)
watch(selectedKey, () => { pagination.page = 1 })
const detailTitle = computed(() => `${detailTabs.find((tab) => tab.key === selectedKey.value)?.label || '业务'}明细`)
const detailDescription = computed(() => selectedKey.value === 'finance_tasks' ? '只显示所选期间提交、目前仍待当前财务处理的事项；指定财务的单据仅对指定人员显示。' : '金额与上方对应卡片同口径；可查看来源页面。')
const showStatus = computed(() => ['sales', 'payables', 'reimbursements', 'reimbursement_payments', 'finance_tasks'].includes(selectedKey.value))
const maxTrend = computed(() => Math.max(1, ...(report.value.trend || []).flatMap((day) => [Number(day.sales || 0), Number(day.collections || 0), Number(day.payments || 0)])))
function barStyle(value) { return { height: `${Math.max(Number(value || 0) > 0 ? 4 : 0, Number(value || 0) / maxTrend.value * 100)}%` } }
function money(value) {
  const [rawInt, rawFraction = ''] = String(value ?? '0').split('.')
  const negative = rawInt.startsWith('-') ? '-' : ''
  const integer = rawInt.replace('-', '').replace(/\B(?=(\d{3})+(?!\d))/g, ',')
  return `${negative}${integer}.${rawFraction.padEnd(4, '0').slice(0, 4)}`
}
function quantity(value) { return money(value) }
async function selectDetail(metric) {
  selectedKey.value = metric.detail
  pagination.page = 1
  await nextTick()
  document.getElementById('finance-overview-details')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}
function setThisMonth() { dateRange.value = initialRange(); load() }
async function load() {
  if (!dateRange.value || dateRange.value.length !== 2) { ElMessage.warning('请选择开始日期和结束日期'); return }
  loading.value = true
  try {
    const params = new URLSearchParams({ date_from: dateRange.value[0], date_to: dateRange.value[1] })
    report.value = await get(`/api/finance/overview?${params.toString()}`, { force: true })
  } catch (error) { ElMessage.error(error.message || '读取财务概览失败') }
  finally { loading.value = false }
}
function openSource(row) {
  const id = row.focus_id || row.id
  router.push({ path: row.route, query: { focusId: String(id), focusType: row.focus_type } })
}
function exportDetails() { exportRowsToCsv(`finance_${selectedKey.value}_${report.value.period.date_from}_${report.value.period.date_to}`, activeRows.value) }
onMounted(load)
</script>

<style scoped>
.finance-overview { display: flex; flex-direction: column; gap: 16px; }
.filter-bar { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; }
.date-picker { width: 310px; max-width: 100%; }
.period-note { color: #63758b; font-size: 13px; margin-left: auto; }
.basis-note { margin-top: 12px; padding: 10px 12px; color: #53677d; background: #f4f8fc; border-radius: 8px; font-size: 13px; line-height: 1.6; }
.metrics-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(185px, 1fr)); gap: 12px; }
.metric-card { border: 1px solid #e1eaf3; border-radius: 12px; background: #fff; text-align: left; min-height: 113px; padding: 14px; cursor: pointer; display: flex; flex-direction: column; gap: 8px; transition: border-color .16s, transform .16s; }
.metric-card:hover { border-color: #68a8dd; transform: translateY(-2px); }
.metric-card.selected { border-color: #348bd0; background: #f5faff; }
.metric-title { color: #53677d; font-size: 13px; }
.metric-card strong { color: #142d42; font-size: 22px; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
.metric-caption { color: #6d88a0; font-size: 12px; display: flex; justify-content: space-between; }
.detail-tabs { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 14px; }
.detail-tabs :deep(.el-button) { margin: 0; }
.trend-wrap { overflow-x: auto; }
.trend-legend { display: flex; gap: 18px; font-size: 12px; color: #5c7187; margin-bottom: 12px; }
.trend-legend span::before { content: ''; display: inline-block; width: 8px; height: 8px; margin-right: 6px; border-radius: 2px; }
.sales-dot::before, .sales-bar { background: #337db4; }
.collection-dot::before, .collection-bar { background: #3aa978; }
.payment-dot::before, .payment-bar { background: #d99442; }
.trend-chart { display: flex; align-items: flex-end; gap: 5px; min-height: 155px; border-bottom: 1px solid #d9e3eb; }
.trend-day { flex: 1 0 31px; min-width: 31px; color: #687e91; font-size: 10px; text-align: center; }
.trend-bars { height: 125px; display: flex; gap: 2px; align-items: flex-end; justify-content: center; }
.trend-bar { width: 7px; min-height: 0; border-radius: 2px 2px 0 0; }
.detail-table { width: 100%; }
@media (max-width: 700px) { .period-note { margin-left: 0; } .metrics-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } .metric-card strong { font-size: 18px; } }
</style>
