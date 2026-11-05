<template>
  <div class="cockpit-page">
    <section class="cockpit-hero">
      <div><p class="eyebrow">EXECUTIVE OPERATING COCKPIT</p><h1>企业经营驾驶舱</h1><p class="hero-note">销售、库存与供应链实时经营快照</p></div>
      <div class="hero-actions">
        <el-date-picker v-model="dateRange" type="daterange" value-format="YYYY-MM-DD" range-separator="至" start-placeholder="开始日期" end-placeholder="结束日期" :clearable="false" class="date-picker" />
        <el-button type="primary" :loading="loading" @click="load">查询</el-button><el-button @click="setThisMonth">本月</el-button>
      </div>
    </section>

    <section class="headline-grid">
      <article v-for="card in headlineCards" :key="card.key" class="headline-card">
        <span class="headline-icon">{{ card.icon }}</span><div><p>{{ card.label }}</p><strong>{{ card.money ? money(data.headline?.[card.key]) : number(data.headline?.[card.key]) }}</strong><small>{{ card.note }}</small></div>
      </article>
    </section>

    <nav class="slide-nav" aria-label="经营看板页面">
      <button v-for="(slide, index) in slides" :key="slide.key" :class="{ active: activeSlide === index }" @click="activeSlide = index">
        <span>{{ slide.icon }}</span><b>{{ slide.label }}</b><small>{{ slide.note }}</small>
      </button>
    </nav>

    <section class="slide-stage" :class="{ 'is-loading': loading }">
      <header class="slide-header">
        <div><span class="slide-index">0{{ activeSlide + 1 }}</span><h2>{{ slides[activeSlide].title }}</h2><p>{{ slides[activeSlide].description }}</p></div>
        <div class="slide-arrows"><button aria-label="上一页" @click="previousSlide">←</button><span>{{ activeSlide + 1 }} / {{ slides.length }}</span><button aria-label="下一页" @click="nextSlide">→</button></div>
      </header>

      <div v-if="activeSlide === 0" class="slide-content sales-slide">
        <article class="visual-card map-card">
          <div class="card-heading"><div><span class="card-kicker">地域热度</span><h3>国内订单分布</h3></div><div class="map-legend"><span>低</span><i /><span>高</span></div></div>
          <div class="map-layout">
            <div class="china-tile-map" aria-label="中国省份销售金额地图">
              <button v-for="province in provinceTiles" :key="province.name" class="province-tile" :class="{ hasData: province.amount > 0 }" :style="provinceStyle(province)" :title="`${province.name}：${money(province.amount)}，${province.orders} 单`">
                <b>{{ province.short }}</b><small v-if="province.amount > 0">{{ compactMoney(province.amount) }}</small>
              </button>
            </div>
            <div class="region-ranking"><p class="mini-title">订单区域 TOP 5</p>
              <div v-for="(region, index) in topRegions" :key="region.province" class="rank-row"><b>{{ index + 1 }}</b><span>{{ region.province }}</span><i><em :style="{ width: percent(region.amount, maxRegionAmount) }" /></i><strong>{{ compactMoney(region.amount) }}</strong></div>
              <div v-if="!topRegions.length" class="empty-hint">所选期间暂无已发货销售数据</div>
            </div>
          </div>
        </article>
        <article class="visual-card ranking-card">
          <div class="card-heading"><div><span class="card-kicker">产品结构</span><h3>热销产品</h3></div><span class="unit-note">按下单金额</span></div>
          <div class="product-ranking"><div v-for="(product, index) in topProducts" :key="product.product_id" class="product-row"><span class="product-no">{{ String(index + 1).padStart(2, '0') }}</span><div class="product-main"><div><b>{{ product.name }}</b><small>{{ product.specification }}</small></div><div class="progress-track"><i :style="{ width: percent(product.amount, maxProductAmount) }" /></div><p><span>{{ quantity(product.quantity) }} kg</span><strong>{{ money(product.amount) }}</strong></p></div></div><div v-if="!topProducts.length" class="empty-hint">暂无产品销售记录</div></div>
        </article>
        <article class="visual-card seller-card">
          <div class="card-heading"><div><span class="card-kicker">团队表现</span><h3>业务人员业绩</h3></div><span class="unit-note">销售额 / 客户数</span></div>
          <div class="seller-list"><div v-for="(seller, index) in topSellers" :key="seller.user_id" class="seller-row"><div class="avatar" :class="`rank-${index + 1}`">{{ seller.name?.slice(-2) }}</div><div><b>{{ seller.name }}</b><small>{{ seller.orders }} 单 · {{ seller.customers }} 位客户</small></div><strong>{{ compactMoney(seller.amount) }}</strong></div><div v-if="!topSellers.length" class="empty-hint">暂无业务人员业绩数据</div></div>
        </article>
      </div>

      <div v-else-if="activeSlide === 1" class="slide-content inventory-slide">
        <article class="inventory-alert" :class="{ warning: data.inventory?.warning_count }"><span>{{ data.inventory?.warning_count ? '!' : '✓' }}</span><div><b>{{ data.inventory?.warning_count || 0 }} 项库存低于安全线</b><small>{{ data.inventory?.unpriced_count || 0 }} 项有库存但成本尚未定价</small></div></article>
        <div class="warehouse-grid">
          <article v-for="warehouse in data.inventory?.warehouses || []" :key="warehouse.warehouse_id" class="warehouse-card"><div class="warehouse-roof"><span>{{ warehouse.name }}</span><small>{{ warehouse.location || '仓库位置未维护' }}</small></div><div class="warehouse-visual"><div class="warehouse-shell"><div class="stock-fill" :style="{ height: `${Math.max(6, warehouse.relative_level)}%` }"><span>{{ warehouse.relative_level }}%</span></div><i v-for="n in 4" :key="n" /></div></div><div class="warehouse-stats"><div><small>实有库存</small><b>{{ quantity(warehouse.quantity) }}</b></div><div><small>已占用</small><b>{{ quantity(warehouse.reserved) }}</b></div><div><small>库存价值</small><b>{{ compactMoney(warehouse.value) }}</b></div><div><small>SKU / 预警</small><b>{{ warehouse.sku_count }} / {{ warehouse.warning_count }}</b></div></div></article>
          <div v-if="!data.inventory?.warehouses?.length" class="empty-hint large">暂无库存快照</div>
        </div>
        <article class="visual-card inventory-structure"><div class="card-heading"><div><span class="card-kicker">结构快照</span><h3>库存构成</h3></div><span class="unit-note">当前实时库存，不受日期范围影响</span></div><div class="type-grid"><div v-for="(type, index) in data.inventory?.types || []" :key="type.name" class="type-card"><span :style="{ '--type-color': typeColors[index % typeColors.length] }">{{ type.name.slice(0, 1) }}</span><div><b>{{ type.name }}</b><small>{{ type.items }} 个库存项目</small></div><strong>{{ quantity(type.quantity) }}</strong><em>{{ money(type.value) }}</em></div></div></article>
      </div>

      <div v-else-if="activeSlide === 2" class="slide-content operation-slide">
        <article v-for="flow in operationFlows" :key="flow.key" class="flow-card"><div class="flow-title"><span>{{ flow.icon }}</span><div><small>{{ flow.english }}</small><h3>{{ flow.title }}</h3></div></div><div class="flow-metrics"><div v-for="metric in flow.metrics" :key="metric.label"><strong>{{ metric.value }}</strong><span>{{ metric.label }}</span></div></div><div class="flow-route"><template v-for="(step, index) in flow.steps" :key="step.label"><div class="flow-node" :class="step.state"><i>{{ step.icon }}</i><b>{{ step.value }}</b><span>{{ step.label }}</span></div><em v-if="index < flow.steps.length - 1">→</em></template></div><div v-if="flow.statuses?.length" class="status-cloud"><span v-for="status in flow.statuses.slice(0, 6)" :key="status.status">{{ statusText(status.status) }} {{ status.count }}</span></div></article>
      </div>

      <div v-else class="slide-content speed-slide">
        <article v-for="speed in speedCards" :key="speed.key" class="speed-card"><div class="speed-top"><span>{{ speed.icon }}</span><div><small>{{ speed.subtitle }}</small><h3>{{ speed.label }}</h3></div></div><div class="speed-gauge" :style="{ '--gauge': `${speed.rate * 3.6}deg`, '--gauge-color': speed.color }"><div><strong>{{ speed.avg }}</strong><span>平均小时</span></div></div><div class="speed-progress"><i :style="{ width: `${speed.rate}%`, background: speed.color }" /></div><p><span>已完成 {{ speed.completed }}</span><span>处理中 {{ speed.pending }}</span><b>{{ speed.rate }}%</b></p><small v-if="speed.extra">{{ speed.extra }}</small></article>
        <article class="bottleneck-card"><span>经营提示</span><h3>{{ bottleneck.title }}</h3><p>{{ bottleneck.description }}</p><div class="pulse-orbit"><i /><i /><i /></div></article>
      </div>
    </section>
  </div>
</template>

<script setup>
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/date-picker/style/css'
import 'element-plus/es/components/loading/style/css'
import 'element-plus/es/components/message/style/css'
import ElButton from 'element-plus/es/components/button/index.mjs'
import ElDatePicker from 'element-plus/es/components/date-picker/index.mjs'
import { ElMessage } from 'element-plus/es/components/message/index.mjs'
import { computed, onMounted, ref } from 'vue'
import { get } from '../../api/client'

const pad = (value) => String(value).padStart(2, '0')
const dateText = (value) => `${value.getFullYear()}-${pad(value.getMonth() + 1)}-${pad(value.getDate())}`
const thisMonth = () => { const now = new Date(); return [dateText(new Date(now.getFullYear(), now.getMonth(), 1)), dateText(now)] }
const dateRange = ref(thisMonth())
const activeSlide = ref(0)
const loading = ref(false)
const data = ref({ headline: {}, sales: {}, inventory: {}, operations: {}, speed: {} })
const slides = [
  { key: 'sales', icon: '◎', label: '市场销售', note: '地域 · 产品 · 人员', title: '市场销售全景', description: '用地域热度、产品结构和团队贡献观察增长来自哪里。' },
  { key: 'inventory', icon: '▦', label: '库存仓网', note: '仓库 · 结构 · 风险', title: '库存与仓库态势', description: '库存按仓库独立核算，装载高度表示各仓库间的相对库存量。' },
  { key: 'operations', icon: '⇄', label: '供应履约', note: '采购 · 生产 · 发货', title: '供应链履约态势', description: '从需求、执行到交付，快速判断各业务环节的积压和产出。' },
  { key: 'speed', icon: '◷', label: '处理效率', note: '时效 · 完成率 · 瓶颈', title: '需求响应效率', description: '用完成率和平均处理时长识别采购、生产、置货与发货瓶颈。' },
]
const headlineCards = [
  { key: 'sales_amount', label: '区间销售额', icon: '¥', note: '按实际发货时间', money: true },
  { key: 'sales_orders', label: '已发货订单', icon: '单', note: '所选区间', money: false },
  { key: 'sales_profit', label: '已定价利润', icon: '利', note: '不含未定价库存', money: true },
  { key: 'purchase_amount', label: '采购规模', icon: '采', note: '已提交采购订单', money: true },
  { key: 'inventory_value', label: '当前库存价值', icon: '库', note: '实时移动平均成本', money: true },
]
const typeColors = ['#1d7a68', '#d28a2f', '#376f9f']
const tileBase = [
  ['新疆','新',1,3],['西藏','藏',2,5],['青海','青',3,4],['甘肃','甘',4,3],['内蒙古','蒙',5,2],['黑龙江','黑',8,1],['吉林','吉',8,2],['辽宁','辽',8,3],['宁夏','宁',4,4],['陕西','陕',5,4],['山西','晋',6,3],['河北','冀',7,3],['北京','京',7,2],['天津','津',8,4],['山东','鲁',7,4],['四川','川',4,5],['重庆','渝',5,5],['河南','豫',6,4],['江苏','苏',8,5],['上海','沪',9,5],['安徽','皖',7,5],['云南','云',3,6],['贵州','贵',4,6],['湖北','鄂',6,5],['浙江','浙',8,6],['江西','赣',7,6],['福建','闽',8,7],['广西','桂',5,7],['湖南','湘',6,6],['广东','粤',6,7],['香港','港',7,8],['澳门','澳',6,8],['海南','琼',5,8],['台湾','台',9,7],
]
const provinceTiles = computed(() => { const map = new Map((data.value.sales?.regions || []).map((row) => [row.province.replace(/省|市|自治区|壮族|回族|维吾尔/g, ''), row])); return tileBase.map(([name, short, x, y]) => ({ name, short, x, y, amount: Number(map.get(name)?.amount || 0), orders: map.get(name)?.orders || 0 })) })
const maxRegionAmount = computed(() => Math.max(0, ...(data.value.sales?.regions || []).map((row) => Number(row.amount || 0))))
const maxProductAmount = computed(() => Math.max(0, ...(data.value.sales?.products || []).map((row) => Number(row.amount || 0))))
const topRegions = computed(() => (data.value.sales?.regions || []).slice(0, 5))
const topProducts = computed(() => (data.value.sales?.products || []).slice(0, 7))
const topSellers = computed(() => (data.value.sales?.sellers || []).slice(0, 8))
const operationFlows = computed(() => { const purchase = data.value.operations?.purchase || {}; const production = data.value.operations?.production || {}; const shipment = data.value.operations?.shipment || {}; return [
  { key: 'purchase', icon: '采', english: 'PROCUREMENT', title: '采购履约', metrics: [{ label: '采购金额', value: compactMoney(purchase.amount) }, { label: '采购订单', value: purchase.orders || 0 }], statuses: purchase.statuses || [], steps: [{ label: '采购需求', value: purchase.requests || 0, icon: '需', state: 'blue' }, { label: '采购下单', value: purchase.orders || 0, icon: '单', state: 'amber' }, { label: '到货登记', value: purchase.receipts || 0, icon: '到', state: 'green' }] },
  { key: 'production', icon: '产', english: 'PRODUCTION', title: '生产执行', metrics: [{ label: '计划数量', value: quantity(production.planned_quantity) }, { label: '完成产量', value: quantity(production.produced_quantity) }], statuses: production.statuses || [], steps: [{ label: '生产任务', value: production.tasks || 0, icon: '任', state: 'blue' }, { label: '计划产量', value: compactNumber(production.planned_quantity), icon: '计', state: 'amber' }, { label: '完成产量', value: compactNumber(production.produced_quantity), icon: '成', state: 'green' }] },
  { key: 'shipment', icon: '发', english: 'FULFILLMENT', title: '置货发货', metrics: [{ label: '履约需求', value: shipment.demands || 0 }, { label: '待发货', value: shipment.pending || 0 }], statuses: [], steps: [{ label: '履约需求', value: shipment.demands || 0, icon: '需', state: 'blue' }, { label: '完成置货', value: shipment.placed || 0, icon: '置', state: 'amber' }, { label: '完成发货', value: shipment.shipped || 0, icon: '发', state: 'green' }] },
] })
const speedCards = computed(() => { const speed = data.value.speed || {}; return [speedCard('purchase','采购补货','从需求到采购处理','采','#2d7ba3',speed.purchase,speed.purchase?.order_to_receipt_hours ? `下单至到货平均 ${speed.purchase.order_to_receipt_hours} 小时` : ''), speedCard('production','生产补货','从需求到生产处理','产','#7057a6',speed.production,speed.production?.task_hours ? `生产任务平均 ${speed.production.task_hours} 小时` : ''), speedCard('placement','置货响应','从生成到置货完成','置','#c07b2e',speed.placement), speedCard('shipment','发货响应','从置货到发货完成','发','#268066',speed.shipment)] })
const bottleneck = computed(() => { const cards = speedCards.value.filter((row) => row.total > 0); if (!cards.length) return { title: '等待业务数据', description: '所选期间暂无可计算的需求时效。' }; const worst = [...cards].sort((a,b) => (b.pending-a.pending)||(a.rate-b.rate))[0]; return { title: `${worst.label}需要优先关注`, description: `当前有 ${worst.pending} 项仍在处理中，完成率 ${worst.rate}%，平均已完成用时 ${worst.avg} 小时。` } })
function speedCard(key,label,subtitle,icon,color,source={},extra='') { const total=Number(source.total||0); const completed=Number(source.completed||0); return { key,label,subtitle,icon,color,total,completed,pending:Number(source.pending||0),avg:Number(source.avg_hours||0).toFixed(1),rate:total?Math.round(completed/total*100):0,extra } }
function provinceStyle(province) { const max=maxRegionAmount.value||1; const intensity=province.amount?.22+Math.sqrt(province.amount/max)*.78:0; return { gridColumn:province.x,gridRow:province.y,'--heat':intensity.toFixed(2) } }
function percent(value,max){return `${max?Math.max(3,Number(value||0)/Number(max)*100):0}%`}
function money(value){return `¥${Number(value||0).toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2})}`}
function number(value){return Number(value||0).toLocaleString('zh-CN')}
function quantity(value){return Number(value||0).toLocaleString('zh-CN',{maximumFractionDigits:2})}
function compactNumber(value){return Intl.NumberFormat('zh-CN',{notation:'compact',maximumFractionDigits:1}).format(Number(value||0))}
function compactMoney(value){return `¥${compactNumber(value)}`}
function statusText(status){return ({draft:'草稿',submitted:'已提交',approved:'已审批',pending:'待处理',completed:'已完成',received:'已到货',partial_received:'部分到货',pending_approval:'待审批',pending_payment:'待付款',cancelled:'已取消'})[status]||status}
function setThisMonth(){dateRange.value=thisMonth();load()}
function previousSlide(){activeSlide.value=(activeSlide.value-1+slides.length)%slides.length}
function nextSlide(){activeSlide.value=(activeSlide.value+1)%slides.length}
async function load(){if(!dateRange.value?.[0]||!dateRange.value?.[1])return;loading.value=true;try{const query=new URLSearchParams({date_from:dateRange.value[0],date_to:dateRange.value[1]});data.value=await get(`/api/finance/executive-dashboard?${query}`,{force:true})}catch(error){ElMessage.error(error.message||'读取经营驾驶舱失败')}finally{loading.value=false}}
onMounted(load)
</script>

<style scoped>
.cockpit-page{--ink:#163047;--muted:#6d8092;--line:#dfe8ee;display:flex;flex-direction:column;gap:16px;color:var(--ink)}
.cockpit-hero{padding:22px 26px;border-radius:18px;color:#fff;background:radial-gradient(circle at 75% 10%,rgba(83,181,164,.35),transparent 30%),linear-gradient(118deg,#123c55,#17616b 65%,#1f766d);display:flex;justify-content:space-between;align-items:center;gap:18px;box-shadow:0 16px 35px rgba(17,61,78,.16)}
.eyebrow{margin:0 0 5px;font-size:10px;letter-spacing:3px;color:#9ad8d0}.cockpit-hero h1{margin:0;font-size:27px;letter-spacing:2px}.hero-note{margin:6px 0 0;color:#c9e4e1;font-size:13px}.hero-actions{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.date-picker{width:290px}
.headline-grid{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:12px}.headline-card{min-height:104px;padding:16px;background:#fff;border:1px solid var(--line);border-radius:15px;display:flex;gap:13px;align-items:center;box-shadow:0 7px 22px rgba(28,69,90,.06)}.headline-icon{width:42px;height:42px;flex:0 0 42px;border-radius:13px;display:grid;place-items:center;background:#e9f4f2;color:#197266;font-weight:800;font-size:16px}.headline-card p,.headline-card small{margin:0;color:var(--muted)}.headline-card p{font-size:12px}.headline-card strong{display:block;margin:5px 0 3px;font-size:20px;font-variant-numeric:tabular-nums}.headline-card small{font-size:10px}
.slide-nav{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;padding:7px;background:#e9eff3;border-radius:14px}.slide-nav button{border:0;border-radius:10px;padding:10px 14px;background:transparent;color:#62778a;display:grid;grid-template-columns:30px 1fr;grid-template-rows:auto auto;text-align:left;cursor:pointer}.slide-nav button>span{grid-row:1/3;align-self:center;font-size:21px}.slide-nav b{font-size:13px}.slide-nav small{font-size:10px;margin-top:2px}.slide-nav button.active{background:#fff;color:#17675f;box-shadow:0 4px 14px rgba(23,69,84,.09)}
.slide-stage{min-height:570px;padding:21px;border:1px solid var(--line);border-radius:18px;background:#f8fbfc}.slide-header{display:flex;justify-content:space-between;align-items:flex-start;gap:15px;margin-bottom:18px}.slide-header>div:first-child{display:grid;grid-template-columns:40px 1fr}.slide-index{grid-row:1/3;color:#2e8377;font-weight:800;font-size:24px}.slide-header h2{margin:0;font-size:20px}.slide-header p{margin:4px 0 0;color:var(--muted);font-size:12px}.slide-arrows{display:flex;align-items:center;gap:10px;color:var(--muted);font-size:11px}.slide-arrows button{width:32px;height:32px;border:1px solid #d6e1e7;border-radius:50%;background:#fff;cursor:pointer;color:#276b69;font-size:16px}.slide-content{animation:slide-in .25s ease}@keyframes slide-in{from{opacity:0;transform:translateX(8px)}}
.slide-stage.is-loading{opacity:.62;pointer-events:none;transition:opacity .16s}
.sales-slide{display:grid;grid-template-columns:minmax(520px,1.55fr) minmax(300px,.8fr);gap:14px}.visual-card{padding:18px;background:#fff;border:1px solid var(--line);border-radius:15px}.map-card{grid-row:1/3}.card-heading{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:15px}.card-heading h3{margin:2px 0 0;font-size:16px}.card-kicker,.unit-note{color:#7890a1;font-size:10px;letter-spacing:1px}.map-legend{display:flex;align-items:center;gap:5px;font-size:9px;color:#7c8f9e}.map-legend i{width:65px;height:8px;border-radius:9px;background:linear-gradient(90deg,#edf3f3,#117064)}
.map-layout{display:grid;grid-template-columns:minmax(400px,1fr) 230px;gap:22px;align-items:center}.china-tile-map{display:grid;grid-template-columns:repeat(9,46px);grid-template-rows:repeat(8,41px);gap:4px;justify-content:center;min-height:356px;padding-top:12px}.province-tile{border:1px solid #d5e1e4;border-radius:7px;background:#edf3f3;color:#71858f;cursor:default;display:flex;flex-direction:column;justify-content:center;align-items:center;padding:1px}.province-tile b{font-size:11px}.province-tile small{font-size:7px;margin-top:1px}.province-tile.hasData{color:#fff;border-color:rgba(14,88,79,.4);background:rgba(18,112,99,var(--heat));box-shadow:0 3px 8px rgba(13,83,75,.12)}
.mini-title{color:#738999;font-size:11px;margin:0 0 12px}.rank-row{display:grid;grid-template-columns:20px 43px 1fr 55px;gap:6px;align-items:center;margin-bottom:13px;font-size:11px}.rank-row>b{color:#1e756c}.rank-row i,.progress-track{height:6px;background:#edf2f4;border-radius:5px;overflow:hidden}.rank-row em,.progress-track i{display:block;height:100%;border-radius:5px;background:linear-gradient(90deg,#51a89d,#176c66)}.rank-row strong{text-align:right;font-size:10px}
.product-row{display:flex;gap:10px;margin-bottom:12px}.product-no{color:#8ba0ae;font-weight:800;font-size:11px;padding-top:2px}.product-main{flex:1;min-width:0}.product-main>div:first-child{display:flex;justify-content:space-between;gap:8px}.product-main b{font-size:11px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.product-main small{color:#91a1ad;font-size:9px}.product-main p{margin:4px 0 0;display:flex;justify-content:space-between;color:#708695;font-size:9px}.product-main p strong{color:#24455b}.progress-track{margin-top:5px;height:5px}.progress-track i{background:linear-gradient(90deg,#d6a04f,#bd7028)}
.seller-list{display:flex;flex-direction:column;gap:9px}.seller-row{display:grid;grid-template-columns:34px 1fr auto;gap:10px;align-items:center}.avatar{width:34px;height:34px;border-radius:50%;display:grid;place-items:center;background:#dce8ed;color:#365b70;font-size:10px;font-weight:700}.avatar.rank-1{background:#f5d493;color:#7b4a0d}.avatar.rank-2{background:#dbe1e6}.avatar.rank-3{background:#e6c8aa}.seller-row b{display:block;font-size:11px}.seller-row small{color:#899aa7;font-size:9px}.seller-row strong{font-size:11px;color:#176c63}.empty-hint{padding:30px 5px;text-align:center;color:#98a8b3;font-size:11px}.empty-hint.large{grid-column:1/-1}
.inventory-alert{padding:12px 16px;margin-bottom:14px;border-radius:12px;display:flex;align-items:center;gap:12px;background:#e9f5f1;color:#216b5e}.inventory-alert.warning{background:#fff3df;color:#8b5a19}.inventory-alert>span{width:29px;height:29px;border:2px solid currentColor;border-radius:50%;display:grid;place-items:center;font-weight:800}.inventory-alert b,.inventory-alert small{display:block}.inventory-alert b{font-size:12px}.inventory-alert small{margin-top:2px;font-size:9px;opacity:.75}
.warehouse-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(270px,1fr));gap:14px}.warehouse-card{background:#fff;border:1px solid var(--line);border-radius:15px;overflow:hidden}.warehouse-roof{padding:13px 16px;color:#fff;background:linear-gradient(110deg,#274f66,#29776f);display:flex;justify-content:space-between;align-items:center}.warehouse-roof span{font-weight:700;font-size:13px}.warehouse-roof small{font-size:9px;opacity:.75}.warehouse-visual{padding:17px 30px 5px}.warehouse-shell{height:116px;position:relative;overflow:hidden;border:5px solid #5f7683;border-top-width:10px;background:repeating-linear-gradient(90deg,#eef3f5 0,#eef3f5 31%,#dce5e9 32%,#eef3f5 33%)}.warehouse-shell>i{position:relative;z-index:2;display:block;height:25%;border-top:2px solid rgba(62,84,94,.42)}.stock-fill{position:absolute;z-index:1;bottom:0;left:0;right:0;min-height:6%;transition:height .4s;background:repeating-linear-gradient(135deg,#4aa092,#4aa092 10px,#3c8b80 10px,#3c8b80 20px)}.stock-fill span{position:absolute;top:4px;right:5px;color:#fff;font-size:9px;font-weight:700}.warehouse-stats{display:grid;grid-template-columns:repeat(4,1fr);gap:6px;padding:12px 14px 15px}.warehouse-stats div{text-align:center}.warehouse-stats small,.warehouse-stats b{display:block}.warehouse-stats small{font-size:8px;color:#8193a0}.warehouse-stats b{font-size:10px;margin-top:3px}
.inventory-structure{margin-top:14px}.type-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}.type-card{display:grid;grid-template-columns:42px 1fr auto;grid-template-rows:auto auto;gap:2px 10px;align-items:center;padding:12px;border-radius:12px;background:#f6f9fa}.type-card>span{grid-row:1/3;width:42px;height:42px;border-radius:12px;display:grid;place-items:center;background:#e7f0ee;color:var(--type-color);font-weight:800}.type-card b,.type-card small{display:block}.type-card b{font-size:12px}.type-card small{color:#8799a6;font-size:9px}.type-card strong{font-size:12px}.type-card em{font-style:normal;font-size:9px;color:#6f8594;text-align:right}
.operation-slide{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}.flow-card{min-height:405px;padding:20px;border-radius:16px;background:#fff;border:1px solid var(--line)}.flow-title{display:flex;gap:11px;align-items:center}.flow-title>span{width:42px;height:42px;border-radius:13px;display:grid;place-items:center;background:#e8f2f1;color:#1b7268;font-weight:800}.flow-title small{color:#93a2ad;font-size:8px;letter-spacing:1.5px}.flow-title h3{margin:2px 0 0;font-size:16px}.flow-metrics{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin:22px 0}.flow-metrics div{padding:12px;border-radius:10px;background:#f5f8f9}.flow-metrics strong,.flow-metrics span{display:block}.flow-metrics strong{font-size:17px}.flow-metrics span{color:#7d909e;font-size:9px;margin-top:3px}.flow-route{display:flex;align-items:center;justify-content:center;gap:6px;padding:15px 0}.flow-route>em{font-style:normal;color:#9babb5}.flow-node{flex:1;text-align:center}.flow-node i{width:43px;height:43px;margin:auto;border-radius:50%;display:grid;place-items:center;font-style:normal;color:#fff;background:#377a9f;box-shadow:0 0 0 6px #eaf2f6}.flow-node.amber i{background:#c9822f;box-shadow:0 0 0 6px #fbf1e3}.flow-node.green i{background:#318475;box-shadow:0 0 0 6px #e6f3ef}.flow-node b,.flow-node span{display:block}.flow-node b{margin-top:11px;font-size:16px}.flow-node span{color:#7f929f;font-size:9px}.status-cloud{display:flex;flex-wrap:wrap;gap:6px;margin-top:18px}.status-cloud span{padding:5px 8px;border-radius:12px;background:#edf3f5;color:#607785;font-size:9px}
.speed-slide{display:grid;grid-template-columns:repeat(5,1fr);gap:14px}.speed-card,.bottleneck-card{padding:18px;border-radius:16px;background:#fff;border:1px solid var(--line)}.speed-top{display:flex;gap:9px;align-items:center}.speed-top>span{width:34px;height:34px;border-radius:10px;display:grid;place-items:center;background:#eef4f5;color:#286e6d;font-weight:800}.speed-top small{color:#8a9ca8;font-size:8px}.speed-top h3{margin:1px 0 0;font-size:13px}.speed-gauge{width:116px;height:116px;margin:25px auto 18px;border-radius:50%;display:grid;place-items:center;background:conic-gradient(var(--gauge-color) var(--gauge),#e9eef1 0);position:relative}.speed-gauge::after{content:'';position:absolute;inset:10px;border-radius:50%;background:#fff}.speed-gauge>div{z-index:1;text-align:center}.speed-gauge strong,.speed-gauge span{display:block}.speed-gauge strong{font-size:23px}.speed-gauge span{color:#8798a4;font-size:8px}.speed-progress{height:5px;background:#edf1f3;border-radius:5px;overflow:hidden}.speed-progress i{display:block;height:100%;border-radius:5px}.speed-card p{display:flex;justify-content:space-between;color:#778b98;font-size:8px}.speed-card p b{color:#294b5e}.speed-card>small{display:block;padding-top:8px;border-top:1px dashed #dfe6ea;color:#6d8290;font-size:8px;line-height:1.5}.bottleneck-card{color:#fff;background:radial-gradient(circle at 70% 75%,rgba(82,205,179,.28),transparent 28%),linear-gradient(145deg,#184457,#1b6f67);border:0;position:relative;overflow:hidden}.bottleneck-card>span{color:#9dd9d1;font-size:9px;letter-spacing:2px}.bottleneck-card h3{margin:20px 0 10px;font-size:18px}.bottleneck-card p{color:#c9e1df;font-size:10px;line-height:1.8}.pulse-orbit{position:absolute;width:120px;height:120px;right:-35px;bottom:-35px;border:1px solid rgba(255,255,255,.2);border-radius:50%}.pulse-orbit i{position:absolute;inset:15px;border:1px solid rgba(255,255,255,.15);border-radius:50%}.pulse-orbit i:nth-child(2){inset:32px}.pulse-orbit i:nth-child(3){inset:49px;background:#6fd0bd;border:0}
@media(max-width:1250px){.headline-grid{grid-template-columns:repeat(3,1fr)}.sales-slide{grid-template-columns:1fr}.map-card{grid-row:auto}.operation-slide{grid-template-columns:1fr}.flow-card{min-height:auto}.speed-slide{grid-template-columns:repeat(2,1fr)}.bottleneck-card{min-height:220px}}
@media(max-width:800px){.cockpit-hero{align-items:flex-start;flex-direction:column}.headline-grid{grid-template-columns:1fr 1fr}.slide-nav button small{display:none}.slide-nav button{display:flex;gap:5px;align-items:center;justify-content:center;padding:10px 5px}.slide-nav button>span{font-size:16px}.map-layout{grid-template-columns:1fr}.china-tile-map{transform:scale(.82);transform-origin:center top;margin-bottom:-55px}.type-grid,.speed-slide{grid-template-columns:1fr}.warehouse-stats{grid-template-columns:1fr 1fr}.slide-stage{padding:13px}.slide-header p{display:none}}
</style>
