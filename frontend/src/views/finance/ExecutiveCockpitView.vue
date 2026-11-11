<template>
  <div class="executive-cockpit">
    <header class="cockpit-header">
      <div class="brand-block">
        <span class="brand-mark"><i /><i /><i /></span>
        <div>
          <p>BUSINESS INTELLIGENCE CENTER</p>
          <h1>企业经营驾驶舱</h1>
          <small>销售、库存、采购、生产与交付的一体化经营视图</small>
        </div>
      </div>
      <div class="header-actions">
        <div class="live-status"><i /> 数据实时汇总</div>
        <el-date-picker v-model="dateRange" type="daterange" value-format="YYYY-MM-DD" range-separator="至" start-placeholder="开始日期" end-placeholder="结束日期" :clearable="false" class="date-picker" />
        <el-button type="primary" :loading="loading" @click="load">更新看板</el-button>
        <el-button @click="setThisMonth">本月</el-button>
      </div>
    </header>

    <section class="metric-strip">
      <article v-for="metric in headlineMetrics" :key="metric.key" class="metric-card">
        <div class="metric-icon" :style="{ '--accent': metric.color }">{{ metric.icon }}</div>
        <div class="metric-copy"><span>{{ metric.label }}</span><strong>{{ metric.display || (metric.money ? money(metric.value) : number(metric.value)) }}</strong><small>{{ metric.note }}</small></div>
        <i class="metric-glow" :style="{ background: metric.color }" />
      </article>
    </section>

    <nav class="cockpit-tabs">
      <button v-for="(tab, index) in tabs" :key="tab.key" :class="{ active: activeTab === index }" @click="activeTab = index">
        <span>{{ tab.icon }}</span><div><b>{{ tab.label }}</b><small>{{ tab.description }}</small></div>
      </button>
    </nav>

    <main class="visual-stage" :class="{ loading }">
      <div class="stage-title">
        <div><span>0{{ activeTab + 1 }}</span><div><h2>{{ tabs[activeTab].title }}</h2><p>{{ tabs[activeTab].subtitle }}</p></div></div>
        <div class="stage-pager"><button @click="previousTab">‹</button><b>{{ activeTab + 1 }}</b><span>/ {{ tabs.length }}</span><button @click="nextTab">›</button></div>
      </div>

      <section v-if="activeTab === 0" class="sales-dashboard dashboard-grid">
        <article class="chart-panel map-panel">
          <PanelTitle eyebrow="GEOGRAPHICAL DISTRIBUTION" title="全国订单金额热力" note="按订单提交时间统计" />
          <VChart class="chart map-chart" :option="mapOption" autoresize />
          <div class="map-summary">
            <div><span>覆盖省份</span><strong>{{ coveredProvinceCount }}</strong></div>
            <div><span>订单区域金额</span><strong>{{ compactMoney(regionTotal) }}</strong></div>
            <div><span>订单总数</span><strong>{{ regionOrderCount }}</strong></div>
          </div>
        </article>
        <article class="chart-panel product-panel">
          <PanelTitle eyebrow="PRODUCT PORTFOLIO" title="产品销售贡献" note="金额与销量联动" />
          <VChart class="chart ranking-chart" :option="productOption" autoresize />
        </article>
        <article class="chart-panel seller-panel">
          <PanelTitle eyebrow="SALES PERFORMANCE" title="业务团队业绩" note="订单金额 / 客户覆盖" />
          <VChart class="chart seller-chart" :option="sellerOption" autoresize />
        </article>
      </section>

      <section v-else-if="activeTab === 1" class="inventory-dashboard dashboard-grid">
        <article class="chart-panel warehouse-panel">
          <PanelTitle eyebrow="WAREHOUSE NETWORK" title="仓库库存态势" note="实有、占用和可用库存" />
          <VChart class="chart warehouse-chart" :option="warehouseOption" autoresize />
        </article>
        <article class="chart-panel inventory-ring-panel">
          <PanelTitle eyebrow="INVENTORY STRUCTURE" title="库存价值构成" note="按产品、原料、包装分类" />
          <VChart class="chart inventory-ring-chart" :option="inventoryRingOption" autoresize />
          <div class="risk-badges">
            <div :class="{ danger: Number(data.inventory?.warning_count || 0) > 0 }"><i>!</i><span>安全库存预警</span><strong>{{ data.inventory?.warning_count || 0 }}</strong></div>
            <div :class="{ danger: Number(data.inventory?.unpriced_count || 0) > 0 }"><i>¥</i><span>成本未定价</span><strong>{{ data.inventory?.unpriced_count || 0 }}</strong></div>
          </div>
        </article>
        <article class="warehouse-cards-panel">
          <div v-for="warehouse in data.inventory?.warehouses || []" :key="warehouse.warehouse_id" class="warehouse-summary-card">
            <div class="warehouse-name"><span>▦</span><div><b>{{ warehouse.name }}</b><small>{{ warehouse.location || '位置未维护' }}</small></div></div>
            <div class="warehouse-level"><i :style="{ width: `${warehouse.relative_level}%` }" /></div>
            <div class="warehouse-numbers"><div><span>库存价值</span><b>{{ compactMoney(warehouse.value) }}</b></div><div><span>库存项目</span><b>{{ warehouse.sku_count }}</b></div><div><span>预警</span><b>{{ warehouse.warning_count }}</b></div></div>
          </div>
        </article>
      </section>

      <section v-else-if="activeTab === 2" class="operation-dashboard dashboard-grid">
        <article class="chart-panel sankey-panel">
          <PanelTitle eyebrow="END-TO-END FULFILLMENT" title="供应链履约流向" note="从需求到交付的业务转化" />
          <VChart class="chart sankey-chart" :option="sankeyOption" autoresize />
        </article>
        <article class="chart-panel operation-ring-panel">
          <PanelTitle eyebrow="OPERATION COMPLETION" title="关键环节完成度" note="采购、生产、置货、发货" />
          <VChart class="chart operation-ring-chart" :option="operationRingOption" autoresize />
        </article>
        <article class="operation-metrics">
          <div v-for="item in operationMetrics" :key="item.label" class="operation-metric" :style="{ '--accent': item.color }">
            <span>{{ item.icon }}</span><div><small>{{ item.label }}</small><strong>{{ item.value }}</strong><em>{{ item.note }}</em></div>
          </div>
        </article>
      </section>

      <section v-else class="efficiency-dashboard dashboard-grid">
        <article class="chart-panel gauge-panel">
          <PanelTitle eyebrow="PROCESS VELOCITY" title="需求处理效率" note="完成率与平均处理时长" />
          <div class="gauge-grid"><div v-for="gauge in efficiencyGauges" :key="gauge.key" class="gauge-item"><VChart class="chart gauge-chart" :option="gauge.option" autoresize /><div><b>{{ gauge.label }}</b><small>平均 {{ gauge.hours }} 小时 · 待处理 {{ gauge.pending }}</small></div></div></div>
        </article>
        <article class="chart-panel radar-panel">
          <PanelTitle eyebrow="EFFICIENCY PROFILE" title="流程健康雷达" note="越接近外圈代表运行越健康" />
          <VChart class="chart radar-chart" :option="radarOption" autoresize />
        </article>
        <article class="insight-panel">
          <span>AI-LIKE BUSINESS INSIGHT</span><h3>{{ bottleneck.title }}</h3><p>{{ bottleneck.description }}</p>
          <div class="insight-pulse"><i /><i /><i /></div>
        </article>
      </section>
    </main>
  </div>
</template>

<script setup>
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/date-picker/style/css'
import 'element-plus/es/components/message/style/css'
import ElButton from 'element-plus/es/components/button/index.mjs'
import ElDatePicker from 'element-plus/es/components/date-picker/index.mjs'
import { ElMessage } from 'element-plus/es/components/message/index.mjs'
import { BarChart, GaugeChart, MapChart, PieChart, RadarChart, SankeyChart } from 'echarts/charts'
import { DataZoomComponent, GeoComponent, GridComponent, LegendComponent, RadarComponent, TitleComponent, TooltipComponent, VisualMapComponent } from 'echarts/components'
import { registerMap, use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { computed, defineComponent, h, onMounted, ref } from 'vue'
import VChart from 'vue-echarts'
import chinaGeoJSON from '../../assets/maps/china.json'
import { get } from '../../api/client'

use([CanvasRenderer, MapChart, BarChart, PieChart, GaugeChart, RadarChart, SankeyChart, GeoComponent, GridComponent, TooltipComponent, VisualMapComponent, LegendComponent, TitleComponent, RadarComponent, DataZoomComponent])
registerMap('china-executive', chinaGeoJSON)

const PanelTitle = defineComponent({
  props: { eyebrow: String, title: String, note: String },
  setup(props) { return () => h('div', { class: 'panel-title' }, [h('div', [h('span', props.eyebrow), h('h3', props.title)]), h('small', props.note)]) },
})

const palette = ['#37c8ad', '#4f8df7', '#f0a654', '#856ee7', '#e75f75', '#4eb9d8']
const textColor = '#dbe8f2'
const mutedColor = '#7992a7'
const gridColor = 'rgba(132, 166, 190, .13)'
const pad = (value) => String(value).padStart(2, '0')
const dateText = (value) => `${value.getFullYear()}-${pad(value.getMonth() + 1)}-${pad(value.getDate())}`
const currentMonth = () => { const now = new Date(); return [dateText(new Date(now.getFullYear(), now.getMonth(), 1)), dateText(now)] }
const dateRange = ref(currentMonth())
const activeTab = ref(0)
const loading = ref(false)
const data = ref({ headline: {}, sales: {}, inventory: {}, operations: {}, speed: {} })

const tabs = [
  { key: 'sales', icon: '◉', label: '市场销售', description: '地域 · 产品 · 人员', title: '市场与销售全景', subtitle: '通过真实中国地图和销售排名识别增长来源' },
  { key: 'inventory', icon: '▦', label: '库存仓网', description: '仓库 · 结构 · 风险', title: '库存与仓库态势', subtitle: '洞察仓库库存、价值构成、占用与安全库存风险' },
  { key: 'operation', icon: '⇄', label: '供应履约', description: '采购 · 生产 · 交付', title: '供应链履约态势', subtitle: '从需求到采购、生产、置货与发货的端到端流向' },
  { key: 'efficiency', icon: '◷', label: '处理效率', description: '时效 · 完成率 · 瓶颈', title: '业务响应效率', subtitle: '用完成率、平均时长和流程健康度发现经营瓶颈' },
]

const headlineMetrics = computed(() => [
  { key: 'sales_amount', label: '已发货销售额', icon: '¥', note: '按实际发货日期', color: '#37c8ad', value: data.value.headline?.sales_amount, money: true },
  { key: 'sales_orders', label: '已发货订单', icon: '单', note: '所选统计区间', color: '#4f8df7', value: data.value.headline?.sales_orders },
  { key: 'sales_profit', label: '净利润', icon: '利', note: '已扣审核通过的实际运费', color: '#f0a654', value: data.value.headline?.sales_profit, money: true },
  { key: 'gross_margin', label: '毛利率', icon: '%', note: '收入减销售成本 / 收入', color: '#e75f75', display: `${Number(data.value.headline?.gross_margin || 0).toFixed(2)}%` },
  { key: 'purchase_amount', label: '采购规模', icon: '采', note: '已提交采购单', color: '#856ee7', value: data.value.headline?.purchase_amount, money: true },
  { key: 'inventory_value', label: '当前库存价值', icon: '库', note: '移动平均成本', color: '#4eb9d8', value: data.value.headline?.inventory_value, money: true },
])

const provinceNameMap = { 北京:'北京市', 天津:'天津市', 河北:'河北省', 山西:'山西省', 内蒙古:'内蒙古自治区', 辽宁:'辽宁省', 吉林:'吉林省', 黑龙江:'黑龙江省', 上海:'上海市', 江苏:'江苏省', 浙江:'浙江省', 安徽:'安徽省', 福建:'福建省', 江西:'江西省', 山东:'山东省', 河南:'河南省', 湖北:'湖北省', 湖南:'湖南省', 广东:'广东省', 广西:'广西壮族自治区', 海南:'海南省', 重庆:'重庆市', 四川:'四川省', 贵州:'贵州省', 云南:'云南省', 西藏:'西藏自治区', 陕西:'陕西省', 甘肃:'甘肃省', 青海:'青海省', 宁夏:'宁夏回族自治区', 新疆:'新疆维吾尔自治区', 香港:'香港特别行政区', 澳门:'澳门特别行政区', 台湾:'台湾省' }
const mapRows = computed(() => (data.value.sales?.regions || []).map(row => ({ ...row, name: provinceNameMap[normalizeProvince(row.province)] || row.province, value: Number(row.amount || 0) })))
const regionMax = computed(() => Math.max(1, ...mapRows.value.map(row => row.value)))
const coveredProvinceCount = computed(() => mapRows.value.filter(row => row.value > 0 && row.province !== '其他').length)
const regionTotal = computed(() => mapRows.value.reduce((sum, row) => sum + row.value, 0))
const regionOrderCount = computed(() => mapRows.value.reduce((sum, row) => sum + Number(row.orders || 0), 0))

const tooltipBase = { backgroundColor: 'rgba(8, 25, 39, .96)', borderColor: '#31546b', textStyle: { color: textColor, fontSize: 12 }, extraCssText: 'box-shadow:0 12px 32px rgba(0,0,0,.35);border-radius:8px;' }
const axisStyle = { axisLine: { lineStyle: { color: gridColor } }, axisTick: { show: false }, axisLabel: { color: mutedColor, fontSize: 10 }, splitLine: { lineStyle: { color: gridColor } } }

const mapOption = computed(() => ({
  tooltip: { ...tooltipBase, trigger: 'item', formatter: ({ data: row, name }) => `<b>${name}</b><br/>订单金额：${money(row?.value || 0)}<br/>订单数量：${number(row?.orders || 0)} 单<br/>产品数量：${quantity(row?.quantity || 0)} kg` },
  visualMap: { min: 0, max: regionMax.value, calculable: true, orient: 'horizontal', left: 'center', bottom: 4, itemWidth: 14, itemHeight: 110, text: ['高', '低'], textStyle: { color: mutedColor, fontSize: 10 }, inRange: { color: ['#172b3c', '#155970', '#168d8a', '#45d8b8'] } },
  series: [{ type: 'map', map: 'china-executive', roam: true, scaleLimit: { min: .85, max: 5 }, zoom: 1.08, top: 12, bottom: 38, label: { show: true, color: '#b9cad7', fontSize: 9, formatter: ({ name }) => name.replace(/省|市|壮族自治区|回族自治区|维吾尔自治区|自治区|特别行政区/g, '') }, itemStyle: { areaColor: '#172b3c', borderColor: '#51758d', borderWidth: .7 }, emphasis: { label: { color: '#fff', fontWeight: 'bold' }, itemStyle: { areaColor: '#f0a654', shadowBlur: 18, shadowColor: 'rgba(240,166,84,.45)' } }, select: { disabled: true }, data: mapRows.value }],
}))

const topProducts = computed(() => (data.value.sales?.products || []).slice(0, 10).reverse())
const productOption = computed(() => ({
  color: ['#37c8ad'], tooltip: { ...tooltipBase, trigger: 'axis', axisPointer: { type: 'shadow' }, formatter: items => { const row=topProducts.value[items[0].dataIndex]; return `<b>${row.name}</b><br/>金额：${money(row.amount)}<br/>销量：${quantity(row.quantity)} kg<br/>订单：${row.orders} 单` } },
  grid: { left: 8, right: 25, top: 10, bottom: 10, containLabel: true }, xAxis: { type: 'value', ...axisStyle, axisLabel: { ...axisStyle.axisLabel, formatter: compactNumber } }, yAxis: { type: 'category', data: topProducts.value.map(row => truncate(row.name, 8)), ...axisStyle, axisLabel: { color: '#b9cad7', fontSize: 10 } },
  series: [{ type: 'bar', data: topProducts.value.map(row => Number(row.amount || 0)), barWidth: 10, showBackground: true, backgroundStyle: { color: 'rgba(255,255,255,.035)', borderRadius: 8 }, itemStyle: { borderRadius: [0,8,8,0], color: { type:'linear',x:0,y:0,x2:1,y2:0,colorStops:[{offset:0,color:'#176b76'},{offset:1,color:'#43d5b6'}] } }, label: { show: true, position: 'right', color: '#9eb4c4', fontSize: 9, formatter: ({ value }) => compactMoney(value) } }],
}))

const topSellers = computed(() => (data.value.sales?.sellers || []).slice(0, 8))
const sellerOption = computed(() => ({
  tooltip: { ...tooltipBase, trigger: 'axis', axisPointer: { type: 'shadow' }, formatter: items => { const row=topSellers.value[items[0].dataIndex]; return `<b>${row.name}</b><br/>业绩：${money(row.amount)}<br/>订单：${row.orders} 单<br/>客户：${row.customers} 位` } },
  grid: { left: 10, right: 12, top: 24, bottom: 22, containLabel: true }, xAxis: { type: 'category', data: topSellers.value.map(row => row.name), ...axisStyle, axisLabel: { color: mutedColor, fontSize: 9, interval: 0, rotate: topSellers.value.length > 5 ? 25 : 0 } }, yAxis: { type: 'value', ...axisStyle, axisLabel: { ...axisStyle.axisLabel, formatter: compactNumber } },
  series: [{ type: 'bar', data: topSellers.value.map((row,index) => ({ value:Number(row.amount||0), itemStyle:{ color: index===0?'#f0a654':'#4f8df7' } })), barMaxWidth: 26, itemStyle: { borderRadius:[7,7,0,0] }, label: { show:true,position:'top',color:'#a9bdcb',fontSize:9,formatter:({value})=>compactNumber(value) } }],
}))

const warehouses = computed(() => data.value.inventory?.warehouses || [])
const warehouseOption = computed(() => ({
  color: ['#37c8ad','#f0a654','#4f8df7'], tooltip: { ...tooltipBase, trigger:'axis',axisPointer:{type:'shadow'},valueFormatter: value=>`${quantity(value)} kg` }, legend: { top:0,right:5,textStyle:{color:mutedColor,fontSize:10},data:['可用库存','已占用'] },
  grid:{left:18,right:20,top:38,bottom:20,containLabel:true}, xAxis:{type:'value',...axisStyle,axisLabel:{...axisStyle.axisLabel,formatter:compactNumber}}, yAxis:{type:'category',data:warehouses.value.map(row=>row.name),...axisStyle,axisLabel:{color:'#b9cad7',fontSize:10}},
  series:[{name:'可用库存',type:'bar',stack:'stock',data:warehouses.value.map(row=>Number(row.available||0)),barWidth:18,itemStyle:{borderRadius:[5,0,0,5]}},{name:'已占用',type:'bar',stack:'stock',data:warehouses.value.map(row=>Number(row.reserved||0)),barWidth:18,itemStyle:{borderRadius:[0,5,5,0]}}],
}))
const inventoryTypes = computed(() => data.value.inventory?.types || [])
const inventoryRingOption = computed(() => ({
  color:palette,tooltip:{...tooltipBase,trigger:'item',formatter:'{b}<br/>库存价值：¥{c}<br/>占比：{d}%'},legend:{orient:'vertical',right:5,top:'center',textStyle:{color:mutedColor,fontSize:10},formatter:name=>{const row=inventoryTypes.value.find(x=>x.name===name);return `${name}  ${compactMoney(row?.value)}`}},
  graphic:[{type:'text',left:'29%',top:'43%',style:{text:'库存价值',fill:mutedColor,font:'11px sans-serif',textAlign:'center'}},{type:'text',left:'29%',top:'50%',style:{text:compactMoney(data.value.headline?.inventory_value),fill:textColor,font:'bold 18px sans-serif',textAlign:'center'}}],
  series:[{type:'pie',radius:['56%','76%'],center:['30%','50%'],avoidLabelOverlap:true,padAngle:3,itemStyle:{borderRadius:6},label:{show:false},data:inventoryTypes.value.map(row=>({name:row.name,value:Number(row.value||0)}))}],
}))

const purchase = computed(()=>data.value.operations?.purchase||{})
const production = computed(()=>data.value.operations?.production||{})
const shipment = computed(()=>data.value.operations?.shipment||{})
const sankeyOption = computed(()=>{
  const nodes=[['采购需求',purchase.value.requests],['采购订单',purchase.value.orders],['采购到货',purchase.value.receipts],['生产任务',production.value.tasks],['完成生产',completionCount(production.value.statuses)],['履约需求',shipment.value.demands],['完成置货',shipment.value.placed],['完成发货',shipment.value.shipped]].map(([name,count],i)=>({name, value:Number(count||0),itemStyle:{color:palette[i%palette.length]}}))
  const link=(source,target,value)=>({source,target,value:Math.max(Number(value||0),.1)})
  const links=[link('采购需求','采购订单',purchase.value.orders),link('采购订单','采购到货',purchase.value.receipts),link('生产任务','完成生产',completionCount(production.value.statuses)),link('履约需求','完成置货',shipment.value.placed),link('完成置货','完成发货',shipment.value.shipped)]
  return {tooltip:{...tooltipBase,trigger:'item'},series:[{type:'sankey',left:18,right:18,top:20,bottom:15,nodeWidth:16,nodeGap:20,layoutIterations:32,draggable:false,emphasis:{focus:'adjacency'},label:{color:textColor,fontSize:10},lineStyle:{color:'gradient',curveness:.55,opacity:.35},data:nodes,links}]}
})
const operationRates = computed(()=>[{name:'采购到货',value:rate(purchase.value.receipts,purchase.value.orders)},{name:'生产完成',value:rate(completionCount(production.value.statuses),production.value.tasks)},{name:'置货完成',value:rate(shipment.value.placed,shipment.value.demands)},{name:'发货完成',value:rate(shipment.value.shipped,shipment.value.demands)}])
const operationRingOption = computed(()=>({color:palette,tooltip:{...tooltipBase,trigger:'item',formatter:'{b}：{c}%'},legend:{bottom:0,textStyle:{color:mutedColor,fontSize:9}},series:[{type:'pie',radius:['48%','68%'],center:['50%','44%'],padAngle:4,itemStyle:{borderRadius:5},label:{color:'#bfd0dc',fontSize:10,formatter:'{b}\n{c}%'},data:operationRates.value}]}))
const operationMetrics = computed(()=>[
  {label:'采购订单金额',value:compactMoney(purchase.value.amount),note:`${purchase.value.orders||0} 张采购单`,icon:'采',color:'#4f8df7'},
  {label:'计划生产数量',value:compactNumber(production.value.planned_quantity),note:`完成 ${compactNumber(production.value.produced_quantity)}`,icon:'产',color:'#856ee7'},
  {label:'履约需求总数',value:number(shipment.value.demands),note:`待发货 ${shipment.value.pending||0}`,icon:'履',color:'#37c8ad'},
  {label:'已完成发货',value:number(shipment.value.shipped),note:`完成率 ${rate(shipment.value.shipped,shipment.value.demands)}%`,icon:'发',color:'#f0a654'},
])

const speedRows = computed(()=>[
  speedRow('purchase','采购补货','采','#4f8df7',data.value.speed?.purchase),speedRow('production','生产补货','产','#856ee7',data.value.speed?.production),speedRow('placement','置货响应','置','#f0a654',data.value.speed?.placement),speedRow('shipment','发货响应','发','#37c8ad',data.value.speed?.shipment),
])
const efficiencyGauges = computed(()=>speedRows.value.map(row=>({ ...row, option:{series:[{type:'gauge',startAngle:210,endAngle:-30,min:0,max:100,radius:'92%',center:['50%','55%'],progress:{show:true,width:10,roundCap:true,itemStyle:{color:row.color}},axisLine:{lineStyle:{width:10,color:[[1,'rgba(121,146,167,.18)']]}},axisTick:{show:false},splitLine:{show:false},axisLabel:{show:false},pointer:{show:false},anchor:{show:false},title:{show:false},detail:{valueAnimation:true,offsetCenter:[0,'8%'],formatter:'{value}%',color:textColor,fontSize:19,fontWeight:'bold'},data:[{value:row.rate}]}] } })))
const radarOption = computed(()=>({
  color:['#37c8ad'],tooltip:{...tooltipBase},radar:{radius:'65%',splitNumber:4,indicator:speedRows.value.map(row=>({name:row.label,max:100})),axisName:{color:'#9db1c0',fontSize:10},splitLine:{lineStyle:{color:gridColor}},splitArea:{areaStyle:{color:['rgba(24,51,70,.18)','rgba(13,37,54,.1)']}},axisLine:{lineStyle:{color:gridColor}}},
  series:[{type:'radar',symbol:'circle',symbolSize:6,lineStyle:{width:2,color:'#37c8ad'},itemStyle:{color:'#fff',borderColor:'#37c8ad',borderWidth:2},areaStyle:{color:'rgba(55,200,173,.25)'},data:[{name:'流程健康度',value:speedRows.value.map(row=>healthScore(row))}]}],
}))
const bottleneck = computed(()=>{const rows=speedRows.value.filter(row=>row.total>0);if(!rows.length)return{title:'等待业务数据',description:'所选区间内暂无可分析的需求处理记录。'};const worst=[...rows].sort((a,b)=>(b.pending-a.pending)||(a.rate-b.rate))[0];return{title:`${worst.label}是当前优先改善环节`,description:`共有 ${worst.pending} 项仍在处理中，完成率 ${worst.rate}%，已完成任务的平均处理时长为 ${worst.hours} 小时。建议优先检查责任分配、库存或审批等待节点。`}})

function speedRow(key,label,icon,color,source={}){const total=Number(source?.total||0),completed=Number(source?.completed||0);return{key,label,icon,color,total,completed,pending:Number(source?.pending||0),hours:Number(source?.avg_hours||0).toFixed(1),rate:rate(completed,total)}}
function healthScore(row){const completion=row.rate;const timeScore=Math.max(0,100-Math.min(100,Number(row.hours)*2));return Math.round(completion*.7+timeScore*.3)}
function completionCount(statuses=[]){return statuses.filter(row=>['completed','finished','closed'].includes(row.status)).reduce((sum,row)=>sum+Number(row.count||0),0)}
function normalizeProvince(value=''){return String(value).replace(/省|市|壮族自治区|回族自治区|维吾尔自治区|自治区|特别行政区/g,'')}
function rate(value,total){return Number(total||0)>0?Math.min(100,Math.round(Number(value||0)/Number(total)*100)):0}
function truncate(value,length){const text=String(value||'');return text.length>length?`${text.slice(0,length)}…`:text}
function money(value){return `¥${Number(value||0).toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2})}`}
function number(value){return Number(value||0).toLocaleString('zh-CN')}
function quantity(value){return Number(value||0).toLocaleString('zh-CN',{maximumFractionDigits:2})}
function compactNumber(value){return Intl.NumberFormat('zh-CN',{notation:'compact',maximumFractionDigits:1}).format(Number(value||0))}
function compactMoney(value){return `¥${compactNumber(value)}`}
function previousTab(){activeTab.value=(activeTab.value-1+tabs.length)%tabs.length}
function nextTab(){activeTab.value=(activeTab.value+1)%tabs.length}
function setThisMonth(){dateRange.value=currentMonth();load()}
async function load(){if(!dateRange.value?.[0]||!dateRange.value?.[1])return;loading.value=true;try{const query=new URLSearchParams({date_from:dateRange.value[0],date_to:dateRange.value[1]});data.value=await get(`/api/finance/executive-dashboard?${query}`,{force:true})}catch(error){ElMessage.error(error.message||'读取经营驾驶舱失败')}finally{loading.value=false}}
onMounted(load)
</script>

<style scoped>
.executive-cockpit{--bg:#071722;--panel:#0c2231;--panel-2:#0e2939;--line:rgba(111,155,183,.18);--text:#dbe8f2;--muted:#7992a7;min-height:calc(100vh - 112px);margin:-16px;padding:18px;background:radial-gradient(circle at 76% 0,rgba(28,113,121,.2),transparent 29%),linear-gradient(145deg,#06151f,#0a1d2b 52%,#081722);color:var(--text);font-variant-numeric:tabular-nums}.cockpit-header{display:flex;justify-content:space-between;align-items:center;gap:22px;padding:19px 22px;border:1px solid var(--line);border-radius:18px;background:linear-gradient(110deg,rgba(14,48,64,.94),rgba(10,32,46,.88));box-shadow:0 18px 55px rgba(0,0,0,.2)}.brand-block{display:flex;align-items:center;gap:15px}.brand-mark{width:49px;height:49px;border-radius:14px;background:linear-gradient(145deg,#2bcbb1,#226f89);display:flex;align-items:flex-end;justify-content:center;gap:3px;padding:11px;box-shadow:0 0 25px rgba(55,200,173,.24)}.brand-mark i{width:6px;border-radius:4px;background:#fff}.brand-mark i:nth-child(1){height:11px}.brand-mark i:nth-child(2){height:21px}.brand-mark i:nth-child(3){height:15px}.brand-block p{margin:0 0 2px;font-size:8px;letter-spacing:3px;color:#60c7bb}.brand-block h1{margin:0;font-size:24px;letter-spacing:2px}.brand-block small{color:#86a0b2;font-size:10px}.header-actions{display:flex;align-items:center;gap:8px}.live-status{padding:7px 11px;border-radius:20px;background:rgba(55,200,173,.08);color:#68d7c4;font-size:9px}.live-status i{display:inline-block;width:6px;height:6px;margin-right:5px;border-radius:50%;background:#49dbbc;box-shadow:0 0 0 4px rgba(73,219,188,.12)}.date-picker{width:285px}
:deep(.el-input__wrapper),:deep(.el-range-editor.el-input__wrapper){background:#0a1d2a;box-shadow:0 0 0 1px rgba(112,155,180,.25) inset}:deep(.el-range-input){color:#cfe0eb;background:transparent}:deep(.el-range-separator),:deep(.el-input__icon){color:#7892a5}
.metric-strip{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:11px;margin:13px 0}.metric-card{position:relative;overflow:hidden;display:flex;align-items:center;gap:12px;min-height:93px;padding:14px;border:1px solid var(--line);border-radius:15px;background:linear-gradient(145deg,rgba(15,42,57,.95),rgba(9,29,42,.95));box-shadow:0 10px 28px rgba(0,0,0,.13)}.metric-icon{width:39px;height:39px;flex:0 0 39px;display:grid;place-items:center;border-radius:12px;color:var(--accent);background:color-mix(in srgb,var(--accent) 12%,transparent);border:1px solid color-mix(in srgb,var(--accent) 25%,transparent);font-weight:800}.metric-copy span,.metric-copy small{display:block;color:var(--muted)}.metric-copy span{font-size:10px}.metric-copy strong{display:block;margin:4px 0 2px;color:#edf7fb;font-size:18px}.metric-copy small{font-size:8px}.metric-glow{position:absolute;right:-20px;bottom:-28px;width:70px;height:70px;border-radius:50%;filter:blur(25px);opacity:.12}
.cockpit-tabs{display:grid;grid-template-columns:repeat(4,1fr);gap:7px;padding:6px;border:1px solid var(--line);border-radius:14px;background:rgba(6,21,31,.7)}.cockpit-tabs button{display:flex;align-items:center;justify-content:center;gap:10px;padding:10px;border:0;border-radius:10px;color:#7891a4;background:transparent;cursor:pointer;transition:.2s}.cockpit-tabs button>span{font-size:20px}.cockpit-tabs b,.cockpit-tabs small{display:block;text-align:left}.cockpit-tabs b{font-size:11px}.cockpit-tabs small{margin-top:2px;font-size:8px}.cockpit-tabs button.active{color:#dff8f3;background:linear-gradient(120deg,rgba(42,173,157,.24),rgba(32,93,118,.25));box-shadow:inset 0 0 0 1px rgba(64,209,185,.22)}
.visual-stage{margin-top:12px;padding:17px;border:1px solid var(--line);border-radius:18px;background:rgba(7,24,35,.75);transition:opacity .2s}.visual-stage.loading{opacity:.55;pointer-events:none}.stage-title{display:flex;align-items:center;justify-content:space-between;margin-bottom:13px}.stage-title>div:first-child{display:flex;gap:12px;align-items:center}.stage-title>div:first-child>span{font-size:24px;font-weight:800;color:#37c8ad}.stage-title h2{margin:0;font-size:17px}.stage-title p{margin:3px 0 0;font-size:9px;color:var(--muted)}.stage-pager{display:flex;align-items:center;gap:7px;color:var(--muted);font-size:9px}.stage-pager button{width:28px;height:28px;border:1px solid var(--line);border-radius:50%;color:#b5cad7;background:#0d2838;cursor:pointer;font-size:17px}.stage-pager b{color:#51d3bd;font-size:12px}
.dashboard-grid{display:grid;gap:12px;animation:fade-slide .24s ease}@keyframes fade-slide{from{opacity:0;transform:translateY(5px)}}.chart-panel{position:relative;overflow:hidden;padding:14px;border:1px solid var(--line);border-radius:15px;background:linear-gradient(145deg,rgba(13,39,54,.96),rgba(8,28,41,.96));box-shadow:0 12px 30px rgba(0,0,0,.12)}.panel-title{display:flex;justify-content:space-between;align-items:flex-start;min-height:38px}.panel-title span{display:block;color:#4fcbb8;font-size:7px;letter-spacing:1.8px}.panel-title h3{margin:3px 0 0;font-size:13px}.panel-title>small{color:#70899b;font-size:8px}.chart{width:100%}.sales-dashboard{grid-template-columns:minmax(520px,1.55fr) minmax(330px,.85fr)}.map-panel{grid-row:1/3;min-height:570px}.map-chart{height:475px}.map-summary{display:grid;grid-template-columns:repeat(3,1fr);gap:7px}.map-summary div{padding:9px 10px;border-radius:9px;background:rgba(255,255,255,.025);border:1px solid rgba(111,155,183,.1)}.map-summary span,.map-summary strong{display:block}.map-summary span{color:var(--muted);font-size:8px}.map-summary strong{margin-top:3px;font-size:12px}.product-panel,.seller-panel{min-height:278px}.ranking-chart,.seller-chart{height:218px}
.inventory-dashboard{grid-template-columns:minmax(520px,1.4fr) minmax(330px,.8fr)}.warehouse-panel,.inventory-ring-panel{min-height:390px}.warehouse-chart,.inventory-ring-chart{height:315px}.risk-badges{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:-8px}.risk-badges>div{display:grid;grid-template-columns:27px 1fr auto;gap:7px;align-items:center;padding:8px;border-radius:9px;background:rgba(55,200,173,.06);color:#7ed9c8}.risk-badges>div.danger{background:rgba(231,95,117,.08);color:#eb8192}.risk-badges i{width:25px;height:25px;display:grid;place-items:center;border:1px solid currentColor;border-radius:50%;font-style:normal;font-size:9px}.risk-badges span{font-size:8px}.risk-badges strong{font-size:14px}.warehouse-cards-panel{grid-column:1/-1;display:grid;grid-template-columns:repeat(auto-fit,minmax(245px,1fr));gap:10px}.warehouse-summary-card{padding:12px;border:1px solid var(--line);border-radius:12px;background:linear-gradient(135deg,rgba(15,45,61,.9),rgba(8,29,42,.9))}.warehouse-name{display:flex;gap:9px;align-items:center}.warehouse-name>span{width:31px;height:31px;display:grid;place-items:center;border-radius:9px;background:rgba(79,141,247,.12);color:#6aa2fa}.warehouse-name b,.warehouse-name small{display:block}.warehouse-name b{font-size:10px}.warehouse-name small{color:var(--muted);font-size:7px}.warehouse-level{height:4px;margin:11px 0;background:rgba(255,255,255,.055);border-radius:5px;overflow:hidden}.warehouse-level i{display:block;height:100%;background:linear-gradient(90deg,#298193,#45d3b5);border-radius:5px}.warehouse-numbers{display:grid;grid-template-columns:repeat(3,1fr);gap:5px}.warehouse-numbers span,.warehouse-numbers b{display:block}.warehouse-numbers span{color:var(--muted);font-size:7px}.warehouse-numbers b{margin-top:2px;font-size:10px}
.operation-dashboard{grid-template-columns:minmax(540px,1.4fr) minmax(330px,.8fr)}.sankey-panel,.operation-ring-panel{min-height:430px}.sankey-chart,.operation-ring-chart{height:365px}.operation-metrics{grid-column:1/-1;display:grid;grid-template-columns:repeat(4,1fr);gap:9px}.operation-metric{display:flex;gap:10px;align-items:center;padding:12px;border:1px solid var(--line);border-radius:12px;background:rgba(12,39,54,.85)}.operation-metric>span{width:35px;height:35px;display:grid;place-items:center;border-radius:10px;color:var(--accent);background:color-mix(in srgb,var(--accent) 12%,transparent);font-weight:800}.operation-metric small,.operation-metric strong,.operation-metric em{display:block}.operation-metric small{color:var(--muted);font-size:8px}.operation-metric strong{margin:2px 0;font-size:14px}.operation-metric em{color:#6d8799;font-size:7px;font-style:normal}
.efficiency-dashboard{grid-template-columns:minmax(600px,1.45fr) minmax(330px,.75fr)}.gauge-panel{min-height:410px}.gauge-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:3px}.gauge-item{text-align:center}.gauge-chart{height:270px}.gauge-item b,.gauge-item small{display:block}.gauge-item b{font-size:11px}.gauge-item small{margin-top:3px;color:var(--muted);font-size:7px}.radar-panel{min-height:410px}.radar-chart{height:340px}.insight-panel{grid-column:1/-1;position:relative;overflow:hidden;min-height:120px;padding:20px;border:1px solid rgba(55,200,173,.25);border-radius:15px;background:radial-gradient(circle at 88% 55%,rgba(55,200,173,.2),transparent 24%),linear-gradient(110deg,#0d3545,#0b2738)}.insight-panel>span{color:#55d6bf;font-size:7px;letter-spacing:2px}.insight-panel h3{margin:9px 0 5px;font-size:15px}.insight-panel p{max-width:75%;margin:0;color:#8da6b7;font-size:9px;line-height:1.8}.insight-pulse{position:absolute;right:40px;top:50%;width:70px;height:70px;transform:translateY(-50%);border:1px solid rgba(91,221,198,.24);border-radius:50%;animation:pulse 2.4s infinite}.insight-pulse i{position:absolute;inset:12px;border:1px solid rgba(91,221,198,.24);border-radius:50%}.insight-pulse i:nth-child(2){inset:25px}.insight-pulse i:nth-child(3){inset:32px;background:#57d7c0;border:0;box-shadow:0 0 20px #57d7c0}@keyframes pulse{50%{transform:translateY(-50%) scale(1.12);opacity:.65}}
@media(max-width:1300px){.metric-strip{grid-template-columns:repeat(3,1fr)}.sales-dashboard,.inventory-dashboard,.operation-dashboard,.efficiency-dashboard{grid-template-columns:1fr}.map-panel{grid-row:auto}.warehouse-cards-panel,.operation-metrics,.insight-panel{grid-column:auto}.operation-metrics{grid-template-columns:repeat(2,1fr)}}
@media(max-width:850px){.executive-cockpit{margin:-10px;padding:10px}.cockpit-header{align-items:flex-start;flex-direction:column}.header-actions{flex-wrap:wrap}.date-picker{width:100%}.metric-strip{grid-template-columns:1fr 1fr}.cockpit-tabs button small{display:none}.cockpit-tabs button{padding:8px 2px}.sales-dashboard{grid-template-columns:1fr}.map-panel{min-height:470px}.map-chart{height:380px}.gauge-grid{grid-template-columns:1fr 1fr}.operation-metrics{grid-template-columns:1fr}.stage-title p{display:none}.insight-panel p{max-width:100%;padding-right:70px}}
</style>
