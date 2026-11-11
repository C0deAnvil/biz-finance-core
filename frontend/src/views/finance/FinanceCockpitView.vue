<template>
  <div class="finance-cockpit" v-loading="loading">
    <header class="hero">
      <div class="hero-copy">
        <div class="eyebrow"><i /> FINANCE COMMAND CENTER</div>
        <h1>财务经营驾驶舱</h1>
        <p>收入、成本、费用、资金与审批的一体化财务运营视图</p>
      </div>
      <div class="hero-actions">
        <span class="sync"><i /> 数据已同步</span>
        <el-date-picker v-model="dateRange" type="daterange" value-format="YYYY-MM-DD" range-separator="至" :clearable="false" start-placeholder="开始日期" end-placeholder="结束日期" />
        <el-button @click="setThisMonth">本月</el-button>
        <el-button type="primary" :loading="loading" @click="load">更新看板</el-button>
      </div>
    </header>

    <section class="metric-grid">
      <article v-for="card in metrics" :key="card.label" class="metric-card" :style="{ '--accent': card.color }">
        <span class="metric-icon">{{ card.icon }}</span>
        <span class="metric-label">{{ card.label }}</span>
        <strong>{{ card.value }}</strong>
        <small>{{ card.note }}</small>
        <i class="metric-orb" />
      </article>
    </section>

    <main class="dashboard-grid">
      <section class="panel profit-panel">
        <div class="panel-title"><div><span>PROFIT BRIDGE</span><h2>利润构成</h2></div><b>{{ money(summary.net_profit) }}</b></div>
        <div class="profit-bridge">
          <div v-for="item in profitBridge" :key="item.label" class="bridge-item" :class="item.type">
            <span>{{ item.label }}</span><strong>{{ money(item.value) }}</strong><i :style="{ width: `${item.width}%` }" />
          </div>
        </div>
        <div class="profit-foot"><span>毛利润 {{ money(summary.gross_profit) }}</span><span>毛利率 <b>{{ percentage(summary.gross_margin) }}</b></span><span>已纳入已审核运费及已付款报销</span></div>
      </section>

      <section class="panel approval-panel">
        <div class="panel-title"><div><span>APPROVAL WORKBENCH</span><h2>财务审批处理</h2></div><router-link to="/approvals">进入审批中心 →</router-link></div>
        <div class="approval-summary">
          <div><b>{{ summary.finance_task_count || 0 }}</b><span>待处理</span></div><div class="warning"><b>{{ summary.rejected_count || 0 }}</b><span>本期驳回</span></div>
          <div><b>{{ money(pendingAmount) }}</b><span>待处理金额</span></div>
        </div>
        <div class="task-list">
          <div v-for="task in tasks" :key="`${task.no}-${task.id}`" class="task-row"><div><b>{{ task.name }}</b><span>{{ task.no }} · {{ task.party }}</span></div><strong>{{ money(task.amount) }}</strong></div>
          <div v-if="!tasks.length" class="empty">当前期间没有待处理的财务审批</div>
        </div>
      </section>

      <section class="panel trend-panel">
        <div class="panel-title"><div><span>FINANCIAL MOMENTUM</span><h2>收入与成本趋势</h2></div><div class="legend"><i class="income" />收入<i class="cost" />成本<i class="expense" />费用</div></div>
        <div class="bar-chart">
          <div v-for="row in trendBars" :key="row.date" class="bar-group">
            <div class="bars"><i class="income" :style="{ height: `${row.sales}%` }" /><i class="cost" :style="{ height: `${row.cost}%` }" /><i class="expense" :style="{ height: `${row.expenses}%` }" /></div>
            <span>{{ row.label }}</span>
          </div>
          <div v-if="!trendBars.length" class="empty">暂无期间趋势数据</div>
        </div>
      </section>

      <section class="panel position-panel">
        <div class="panel-title"><div><span>CAPITAL POSITION</span><h2>往来与现金情况</h2></div><b class="small-note">回款率 {{ collectionRate }}</b></div>
        <div class="position-cards">
          <div><span>应收余额</span><strong>{{ money(summary.receivable_balance) }}</strong><small>本期新增应收 {{ money(summary.receivable_amount) }}</small></div>
          <div><span>应付余额</span><strong>{{ money(summary.payable_balance) }}</strong><small>本期新增应付 {{ money(summary.payable_amount) }}</small></div>
          <div><span>经营净现金流</span><strong :class="netCash >= 0 ? 'positive' : 'negative'">{{ money(netCash) }}</strong><small>收款 {{ money(summary.collection_amount) }} / 付款 {{ money(summary.payment_amount) }}</small></div>
        </div>
        <div class="cash-track"><span>现金流入 {{ money(summary.cash_in) }}</span><i><b :style="{ width: `${cashInRatio}%` }" /></i><span>现金流出 {{ money(summary.cash_out) }}</span></div>
      </section>

      <section class="panel expense-panel">
        <div class="panel-title"><div><span>EXPENSE CONTROL</span><h2>费用与成本控制</h2></div><router-link to="/finance/reimbursements">费用明细 →</router-link></div>
        <div class="expense-list">
          <div v-for="item in expenses" :key="item.label"><div><span>{{ item.label }}</span><b>{{ money(item.value) }}</b></div><i><em :style="{ width: `${item.ratio}%` }" /></i><small>{{ item.note }}</small></div>
        </div>
      </section>

      <section class="panel ledger-panel">
        <div class="panel-title"><div><span>RISK RADAR</span><h2>应收应付重点单据</h2></div><router-link to="/finance/receivables">查看往来 →</router-link></div>
        <div class="ledger-columns">
          <div><h3>应收待回款</h3><p v-for="row in receivables" :key="row.no"><span>{{ row.party }}<small>{{ row.no }}</small></span><b>{{ money(row.balance) }}</b></p><p v-if="!receivables.length" class="empty">暂无应收单据</p></div>
          <div><h3>应付待付款</h3><p v-for="row in payables" :key="row.no"><span>{{ row.party }}<small>{{ row.no }}</small></span><b>{{ money(row.balance) }}</b></p><p v-if="!payables.length" class="empty">暂无应付单据</p></div>
        </div>
      </section>
    </main>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { get } from '../../api/client'

const loading = ref(false)
const now = new Date()
const monthStart = new Date(now.getFullYear(), now.getMonth(), 1).toISOString().slice(0, 10)
const today = now.toISOString().slice(0, 10)
const dateRange = ref([monthStart, today])
const report = ref({ summary: {}, details: {}, trend: [] })
const summary = computed(() => report.value.summary || {})
const details = computed(() => report.value.details || {})
const value = (input) => Number(input || 0)
const money = (input) => `¥${value(input).toLocaleString('zh-CN', { minimumFractionDigits: 0, maximumFractionDigits: 2 })}`
const percentage = (input) => `${value(input).toFixed(1)}%`

const metrics = computed(() => [
  { label: '营业收入', value: money(summary.value.sales_amount), note: `${summary.value.sales_count || 0} 笔已发货/完成订单`, icon: '↗', color: '#24d5bb' },
  { label: '销售成本', value: money(summary.value.sales_cost), note: '产品、原料与包装成本', icon: '◈', color: '#4f8cff' },
  { label: '经营净利润', value: money(summary.value.net_profit), note: '已扣除成本、审核运费及已付报销', icon: '◉', color: '#ffbd5c' },
  { label: '毛利率', value: percentage(summary.value.gross_margin), note: `毛利润 ${money(summary.value.gross_profit)}`, icon: '%', color: '#a985ff' },
  { label: '应收余额', value: money(summary.value.receivable_balance), note: `${summary.value.receivable_count || 0} 笔本期应收`, icon: '↓', color: '#37b5ff' },
  { label: '应付余额', value: money(summary.value.payable_balance), note: `${summary.value.payable_count || 0} 笔本期应付`, icon: '↑', color: '#f47b95' }
])
const expensesTotal = computed(() => value(summary.value.reimbursement_paid_amount) + value(summary.value.freight_amount))
const profitBridge = computed(() => {
  const base = Math.max(value(summary.value.sales_amount), 1)
  return [
    { label: '营业收入', value: summary.value.sales_amount, width: 100, type: 'income' },
    { label: '销售成本', value: summary.value.sales_cost, width: value(summary.value.sales_cost) / base * 100, type: 'deduct' },
    { label: '费用（报销 + 运费）', value: expensesTotal.value, width: expensesTotal.value / base * 100, type: 'deduct' },
    { label: '净利润', value: summary.value.net_profit, width: Math.abs(value(summary.value.net_profit)) / base * 100, type: 'profit' }
  ]
})
const tasks = computed(() => (details.value.finance_tasks || []).slice(0, 4))
const pendingAmount = computed(() => tasks.value.reduce((sum, row) => sum + value(row.amount), 0))
const netCash = computed(() => value(summary.value.collection_amount) - value(summary.value.payment_amount) - value(summary.value.reimbursement_paid_amount) - value(summary.value.freight_amount))
const collectionRate = computed(() => value(summary.value.sales_amount) ? percentage(value(summary.value.collection_amount) / value(summary.value.sales_amount) * 100) : '0.0%')
const cashInRatio = computed(() => { const total = value(summary.value.cash_in) + value(summary.value.cash_out); return total ? value(summary.value.cash_in) / total * 100 : 50 })
const expenses = computed(() => {
  const rows = [
    { label: '销售成本', value: value(summary.value.sales_cost), note: '已发货订单成本' },
    { label: '报销支出', value: value(summary.value.reimbursement_paid_amount), note: '已付款报销' },
    { label: '审核运费', value: value(summary.value.freight_amount), note: `${summary.value.freight_count || 0} 笔已审核运费` },
    { label: '采购付款', value: value(summary.value.payment_amount), note: '已出账采购付款' }
  ]; const max = Math.max(...rows.map((row) => row.value), 1); return rows.map((row) => ({ ...row, ratio: row.value / max * 100 }))
})
const trendBars = computed(() => { const rows = report.value.trend || []; const max = Math.max(...rows.flatMap((row) => [value(row.sales), value(row.cost), value(row.expenses)]), 1); return rows.slice(-12).map((row) => ({ date: row.date, label: row.date.slice(5), sales: value(row.sales) / max * 100, cost: value(row.cost) / max * 100, expenses: value(row.expenses) / max * 100 })) })
const receivables = computed(() => [...(details.value.receivables || [])].filter((row) => value(row.balance) > 0).sort((a, b) => value(b.balance) - value(a.balance)).slice(0, 4))
const payables = computed(() => [...(details.value.payables || [])].filter((row) => value(row.balance) > 0).sort((a, b) => value(b.balance) - value(a.balance)).slice(0, 4))
async function load() { if (!dateRange.value?.[0] || !dateRange.value?.[1]) return; loading.value = true; try { const query = new URLSearchParams({ date_from: dateRange.value[0], date_to: dateRange.value[1] }); report.value = await get(`/api/finance/cockpit?${query}`, { force: true }) } catch (error) { ElMessage.error(error.message || '读取财务驾驶舱失败') } finally { loading.value = false } }
function setThisMonth() { dateRange.value = [monthStart, today]; load() }
onMounted(load)
</script>

<style scoped>
.finance-cockpit{min-height:100%;padding:26px;background:radial-gradient(circle at 88% -10%,#17466a 0,transparent 31%),radial-gradient(circle at 2% 20%,#123c45 0,transparent 25%),#07131f;color:#e7f3fa}.hero{display:flex;align-items:center;justify-content:space-between;gap:20px;padding:10px 4px 28px}.eyebrow,.panel-title>div>span{color:#5ed2d0;font-size:11px;letter-spacing:1.7px;font-weight:700}.eyebrow i,.sync i{display:inline-block;width:7px;height:7px;border-radius:50%;background:#27e0b8;box-shadow:0 0 12px #27e0b8;margin-right:7px}.hero h1{font-size:30px;letter-spacing:1px;margin:8px 0 5px;color:#fff}.hero p{margin:0;color:#7f9bad}.hero-actions{display:flex;align-items:center;gap:10px}.sync{font-size:12px;color:#9fc2cb}.metric-grid{display:grid;grid-template-columns:repeat(6,minmax(150px,1fr));gap:14px}.metric-card,.panel{background:linear-gradient(145deg,rgba(19,42,58,.98),rgba(11,27,41,.95));border:1px solid rgba(125,184,202,.14);box-shadow:0 15px 30px rgba(0,0,0,.16)}.metric-card{min-height:126px;padding:18px;position:relative;overflow:hidden;border-radius:10px}.metric-icon{color:var(--accent);font-size:18px;position:relative;z-index:1}.metric-label,.metric-card small{display:block;color:#88a3b5;font-size:12px;margin-top:12px}.metric-card strong{display:block;color:#f2f8fc;font-size:22px;margin:4px 0}.metric-card small{margin:0;font-size:11px}.metric-orb{position:absolute;width:86px;height:86px;border-radius:50%;right:-31px;bottom:-39px;background:var(--accent);opacity:.1;box-shadow:0 0 45px var(--accent)}.dashboard-grid{display:grid;grid-template-columns:1.15fr .85fr;gap:14px;margin-top:14px}.panel{border-radius:10px;padding:19px;min-width:0}.panel-title{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;margin-bottom:16px}.panel-title h2{font-size:17px;margin:4px 0 0;color:#fff}.panel-title>b{font-size:23px;color:#47e0c2}.panel-title a{color:#75c8e2;text-decoration:none;font-size:12px}.profit-bridge{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}.bridge-item{border-left:2px solid #3a9f9a;padding:2px 8px}.bridge-item.deduct{border-color:#f08a92}.bridge-item.profit{border-color:#fac568}.bridge-item span,.bridge-item strong{display:block}.bridge-item span{font-size:12px;color:#90adbc}.bridge-item strong{font-size:16px;margin:7px 0;color:#eaf4f8}.bridge-item i{display:block;height:4px;border-radius:4px;background:#36cdb8}.bridge-item.deduct i{background:#ee7e91}.bridge-item.profit i{background:#ffc665}.profit-foot{border-top:1px solid rgba(141,192,207,.13);margin-top:18px;padding-top:13px;display:flex;gap:18px;color:#91adbb;font-size:12px}.profit-foot b{color:#ffcc70}.approval-summary{display:grid;grid-template-columns:repeat(3,1fr);background:rgba(5,16,27,.4);padding:12px;border-radius:7px}.approval-summary div{padding-left:12px;border-left:1px solid rgba(117,185,198,.17)}.approval-summary b,.approval-summary span{display:block}.approval-summary b{font-size:20px;color:#47dac1}.approval-summary span{font-size:11px;color:#8fa9b7;margin-top:5px}.approval-summary .warning b{color:#ff8c95}.task-list{margin-top:10px}.task-row{display:flex;align-items:center;justify-content:space-between;padding:9px 2px;border-bottom:1px solid rgba(126,178,193,.1)}.task-row b,.task-row span{display:block}.task-row b{font-size:12px;color:#d8e8ee}.task-row span{font-size:11px;color:#7793a2;margin-top:3px}.task-row strong{font-size:12px;color:#f4c470}.legend{display:flex;gap:7px;align-items:center;font-size:11px;color:#7896a7}.legend i{width:7px;height:7px;border-radius:50%;margin-left:5px}.income{background:#38d2b7}.cost{background:#6ca8ff}.expense{background:#ffa968}.bar-chart{height:184px;display:flex;align-items:flex-end;gap:7px;padding:6px 4px 0;border-bottom:1px solid rgba(122,175,190,.16)}.bar-group{height:100%;flex:1;min-width:18px;display:flex;flex-direction:column;justify-content:flex-end;align-items:center;gap:7px}.bars{height:148px;display:flex;align-items:flex-end;gap:2px}.bars i{width:5px;min-height:2px;border-radius:3px 3px 0 0}.bar-group span{font-size:10px;color:#698896}.position-cards{display:grid;grid-template-columns:repeat(3,1fr);gap:9px}.position-cards>div{background:rgba(4,16,27,.45);padding:14px;border-radius:7px}.position-cards span,.position-cards small{display:block;font-size:11px;color:#86a2b2}.position-cards strong{display:block;font-size:17px;color:#eaf5fb;margin:8px 0}.positive{color:#46dabf!important}.negative{color:#ff8491!important}.cash-track{display:flex;gap:9px;align-items:center;margin-top:18px;font-size:11px;color:#8ba6b5}.cash-track i{flex:1;height:7px;background:#203545;border-radius:8px;overflow:hidden}.cash-track b{display:block;height:100%;background:linear-gradient(90deg,#38d1b6,#63a9f9);border-radius:8px}.small-note{font-size:12px!important;color:#f7c66d!important}.expense-list>div{margin-bottom:13px}.expense-list>div>div{display:flex;justify-content:space-between;font-size:12px}.expense-list span,.expense-list small{color:#8ca8b6}.expense-list b{color:#e8f2f6}.expense-list i{display:block;height:6px;border-radius:8px;background:#203847;margin:7px 0}.expense-list em{display:block;height:100%;border-radius:8px;background:linear-gradient(90deg,#579aff,#53dfc2)}.expense-list small{font-size:10px}.ledger-columns{display:grid;grid-template-columns:1fr 1fr;gap:18px}.ledger-columns>div+div{border-left:1px solid rgba(127,182,197,.12);padding-left:18px}.ledger-columns h3{font-size:12px;color:#8fc8d1;margin:0 0 8px}.ledger-columns p{display:flex;justify-content:space-between;align-items:center;margin:0;padding:8px 0;border-bottom:1px solid rgba(126,178,193,.1);font-size:12px}.ledger-columns span,.ledger-columns small{display:block}.ledger-columns small{font-size:10px;color:#7692a1;margin-top:3px}.ledger-columns b{color:#f4c270;font-size:12px}.empty{color:#718d9c!important;font-size:12px!important;justify-content:center!important;padding:22px!important}@media(max-width:1250px){.metric-grid{grid-template-columns:repeat(3,1fr)}}@media(max-width:900px){.hero{align-items:flex-start;flex-direction:column}.hero-actions{flex-wrap:wrap}.dashboard-grid{grid-template-columns:1fr}.metric-grid{grid-template-columns:repeat(2,1fr)}}@media(max-width:560px){.finance-cockpit{padding:15px}.metric-grid,.profit-bridge,.position-cards{grid-template-columns:1fr}.ledger-columns{grid-template-columns:1fr}.ledger-columns>div+div{border-left:0;border-top:1px solid rgba(127,182,197,.12);padding:15px 0 0}.profit-foot{flex-direction:column;gap:7px}.hero h1{font-size:24px}}
</style>
