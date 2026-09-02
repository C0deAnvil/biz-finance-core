<template>
  <div class="flow-board-page">
    <!-- 顶部统计 -->
    <div class="board-header">
      <div class="board-title">
        <h2>📊 订单全链看板</h2>
        <span class="board-subtitle">从财务审批到发货的全流程状态一目了然</span>
      </div>
      <div class="board-stats">
        <div class="stat-card" v-for="s in stageStats" :key="s.key" :class="`is-${s.key}`" @click="setFilter(s.key)">
          <div class="stat-value">{{ s.count }}</div>
          <div class="stat-label">{{ s.label }}</div>
        </div>
      </div>
    </div>

    <!-- 筛选 -->
    <div class="board-filters">
      <el-radio-group v-model="tab" size="small" @change="handleTabChange">
        <el-radio-button label="all">全部订单</el-radio-button>
        <el-radio-button label="reviewing">待审批</el-radio-button>
        <el-radio-button label="shipping">履约中</el-radio-button>
        <el-radio-button label="completed">已完成</el-radio-button>
      </el-radio-group>
      <el-input v-model="keyword" placeholder="搜索订单号 / 客户" clearable class="board-search" @input="handleSearch" />
    </div>

    <!-- 订单卡片 -->
    <div v-loading="loading" class="order-cards">
      <div v-for="order in filteredOrders" :key="order.id" class="order-card" @click="goOrder(order)">
        <div class="card-head">
          <div class="order-no">
            <span class="order-no-text">{{ order.order_no }}</span>
            <el-tag size="small" :type="statusType(order.status)" effect="light">{{ order.status_label }}</el-tag>
          </div>
          <div class="order-meta">
            <span class="customer">{{ order.customer_name || '-' }}</span>
            <span class="sales">👤 {{ order.sales_user || '-' }}</span>
          </div>
        </div>

        <!-- 全链进度条 -->
        <div class="flow-track">
          <div
            v-for="(dim, idx) in order.dimensions"
            :key="dim.key"
            class="flow-node"
            :class="flowNodeClass(dim)"
          >
            <div class="node-dot">
              <span v-if="isNodeDone(dim)">✓</span>
              <span v-else-if="isNodeActive(dim)">●</span>
            </div>
            <div class="node-label">{{ dim.label }}</div>
            <div class="node-line" :class="{ active: idx < activeNodeIndex(order) }"></div>
          </div>
        </div>

        <div class="card-foot">
          <div class="amounts">
            <span class="total">¥{{ formatNumber(order.total_amount) }}</span>
            <span class="received" :class="{ full: order.settlement_status === 'settled' }">
              已收 ¥{{ formatNumber(order.received_amount) }}
            </span>
          </div>
          <div class="stage-badge" :class="stageBadgeClass(order)">
            {{ order.current_stage_label }}
          </div>
        </div>
      </div>

      <el-empty v-if="!filteredOrders.length && !loading" description="暂无订单" />
    </div>

    <AppPagination
      v-model:page="pagination.page"
      v-model:page-size="pagination.pageSize"
      :total="total"
      :page-sizes="[10, 20, 50]"
      @change="load"
    />
  </div>
</template>

<script setup>
import 'element-plus/es/components/radio/style/css'
import 'element-plus/es/components/tag/style/css'

import { ElRadioButton, ElRadioGroup } from 'element-plus/es/components/radio/index.mjs'
import ElTag from 'element-plus/es/components/tag/index.mjs'

import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'

import { get } from '../../api/client'
import AppPagination from '../../components/AppPagination.vue'
import PageSection from '../../components/PageSection.vue'
import { formatNumber, statusType } from '../../utils/format'

const router = useRouter()
const loading = ref(false)
const orders = ref([])
const total = ref(0)
const tab = ref('all')
const keyword = ref('')
const stageFilter = ref('')
const pagination = reactive({ page: 1, pageSize: 20 })

const DONE_STAGES = new Set(['completed', 'done', 'passed', 'paid', 'cleared', 'approved', 'shipped', 'not_required', 'settled', 'received', 'arrived'])
const ACTIVE_STAGES = new Set(['pending', 'in_progress', 'processing', 'waiting', 'partial', 'partial_paid', 'under_review'])

function isNodeDone(dim) {
  return DONE_STAGES.has(dim.stage)
}
function isNodeActive(dim) {
  return !isNodeDone(dim) && dim.stage && dim.stage !== ''
}
function activeNodeIndex(order) {
  const idx = order.dimensions.findIndex((d) => isNodeActive(d))
  return idx === -1 ? order.dimensions.length : idx
}
function flowNodeClass(dim) {
  if (isNodeDone(dim)) return 'is-done'
  if (isNodeActive(dim)) return 'is-active'
  return 'is-pending'
}
function stageBadgeClass(order) {
  const stage = order.current_stage
  if (order.status === 'completed') return 'is-completed'
  if (['finance', 'approval'].includes(stage)) return 'is-finance'
  if (['placement'].includes(stage)) return 'is-placement'
  if (['production'].includes(stage)) return 'is-production'
  if (['procurement'].includes(stage)) return 'is-procurement'
  if (['quality'].includes(stage)) return 'is-quality'
  if (['inbound'].includes(stage)) return 'is-inbound'
  if (['shipment'].includes(stage)) return 'is-shipment'
  return 'is-default'
}

const stageStats = computed(() => {
  const stats = [
    { key: 'reviewing', label: '待审批', count: 0 },
    { key: 'finance', label: '财务', count: 0 },
    { key: 'placement', label: '待置货', count: 0 },
    { key: 'production', label: '生产中', count: 0 },
    { key: 'procurement', label: '采购中', count: 0 },
    { key: 'quality', label: '质检中', count: 0 },
    { key: 'inbound', label: '待入库', count: 0 },
    { key: 'shipment', label: '待发货', count: 0 },
    { key: 'completed', label: '已完成', count: 0 },
  ]
  for (const o of orders.value) {
    const stage = o.current_stage
    const s = stats.find((x) => x.key === stage)
    if (s) s.count++
    if (o.status === 'completed') stats.find((x) => x.key === 'completed').count++
  }
  // 待审批看 status
  const reviewingCount = orders.value.filter((o) => ['pending_confirmation', 'pending_price_approval', 'rejected'].includes(o.status)).length
  stats.find((x) => x.key === 'reviewing').count = reviewingCount
  return stats
})

const filteredOrders = computed(() => {
  if (!keyword.value) return orders.value
  const kw = keyword.value.trim().toLowerCase()
  return orders.value.filter((o) =>
    (o.order_no || '').toLowerCase().includes(kw) || (o.customer_name || '').toLowerCase().includes(kw)
  )
})

function setFilter(key) {
  stageFilter.value = key
  if (key === 'reviewing') tab.value = 'reviewing'
  else if (key === 'completed') tab.value = 'completed'
  else tab.value = 'all'
  orders.value = []
  pagination.page = 1
  load()
}
function handleTabChange() {
  stageFilter.value = ''
  orders.value = []
  pagination.page = 1
  load()
}
function handleSearch() {
  // 前端过滤即可
}
function goOrder(order) {
  router.push({ path: '/sales/orders', query: { focusId: order.id, focusType: 'sales_order' } })
}

async function load() {
  loading.value = true
  try {
    const params = new URLSearchParams({ tab: tab.value, page: String(pagination.page), page_size: String(pagination.pageSize) })
    const data = await get(`/api/sales/flow-board?${params.toString()}`)
    orders.value = data.items || []
    total.value = Number(data.pagination?.total || 0)
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.flow-board-page { padding: 4px 2px; }
.board-header { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; flex-wrap: wrap; margin-bottom: 16px; }
.board-title h2 { margin: 0 0 4px; font-size: 22px; color: #1f2f3d; }
.board-subtitle { color: #909399; font-size: 13px; }
.board-stats { display: flex; gap: 8px; flex-wrap: wrap; }
.stat-card { padding: 8px 14px; border-radius: 10px; cursor: pointer; text-align: center; min-width: 64px; background: #fff; border: 1px solid #e4e7ed; transition: all .2s; }
.stat-card:hover { transform: translateY(-2px); box-shadow: 0 4px 12px rgba(0,0,0,.08); }
.stat-card .stat-value { font-size: 20px; font-weight: 700; color: #1f2f3d; }
.stat-card .stat-label { font-size: 12px; color: #909399; margin-top: 2px; }
.stat-card.is-reviewing .stat-value { color: #E6A23C; }
.stat-card.is-finance .stat-value { color: #409EFF; }
.stat-card.is-placement .stat-value { color: #909399; }
.stat-card.is-production .stat-value { color: #7c4dff; }
.stat-card.is-procurement .stat-value { color: #ff7c4d; }
.stat-card.is-quality .stat-value { color: #00b4a0; }
.stat-card.is-inbound .stat-value { color: #3db389; }
.stat-card.is-shipment .stat-value { color: #1c7ce0; }
.stat-card.is-completed .stat-value { color: #67C23A; }

.board-filters { display: flex; align-items: center; gap: 12px; margin-bottom: 16px; }
.board-search { width: 260px; }

.order-cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(420px, 1fr)); gap: 16px; margin-bottom: 16px; }
.order-card { background: #fff; border: 1px solid #e8ecf1; border-radius: 14px; padding: 16px 18px; cursor: pointer; transition: all .22s; }
.order-card:hover { transform: translateY(-3px); box-shadow: 0 8px 24px rgba(24,95,147,.12); border-color: #185f93; }
.card-head { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 14px; }
.order-no { display: flex; align-items: center; gap: 8px; }
.order-no-text { font-weight: 700; font-size: 15px; color: #1f2f3d; }
.order-meta { text-align: right; display: flex; flex-direction: column; gap: 2px; }
.customer { font-size: 13px; color: #303133; font-weight: 500; }
.sales { font-size: 12px; color: #909399; }

.flow-track { display: flex; align-items: flex-start; margin: 8px 0 14px; padding: 10px 6px 0; position: relative; }
.flow-node { flex: 1; text-align: center; position: relative; }
.node-dot { width: 22px; height: 22px; border-radius: 50%; margin: 0 auto 4px; display: flex; align-items: center; justify-content: center; font-size: 12px; position: relative; z-index: 2; background: #f0f2f5; color: #c0c4cc; border: 2px solid #dcdfe6; transition: all .25s; }
.flow-node.is-done .node-dot { background: #67C23A; border-color: #67C23A; color: #fff; }
.flow-node.is-active .node-dot { background: #fff; border-color: #185f93; color: #185f93; box-shadow: 0 0 0 4px rgba(24,95,147,.15); animation: pulse 1.6s infinite; }
@keyframes pulse { 0%,100% { box-shadow: 0 0 0 3px rgba(24,95,147,.18);} 50% { box-shadow: 0 0 0 7px rgba(24,95,147,.08);} }
.node-label { font-size: 11px; color: #909399; }
.flow-node.is-done .node-label { color: #67C23A; font-weight: 600; }
.flow-node.is-active .node-label { color: #185f93; font-weight: 700; }
.node-line { position: absolute; top: 10px; left: 50%; width: 100%; height: 3px; background: #e4e7ed; z-index: 1; }
.node-line.active { background: #67C23A; }

.card-foot { display: flex; justify-content: space-between; align-items: center; border-top: 1px dashed #eef0f4; padding-top: 10px; }
.amounts { display: flex; gap: 12px; align-items: baseline; }
.total { font-size: 16px; font-weight: 700; color: #1f2f3d; }
.received { font-size: 12px; color: #909399; }
.received.full { color: #67C23A; }
.stage-badge { padding: 4px 12px; border-radius: 20px; font-size: 12px; font-weight: 600; }
.stage-badge.is-finance { background: #ecf5ff; color: #409EFF; }
.stage-badge.is-placement { background: #f4f4f5; color: #909399; }
.stage-badge.is-production { background: #f3efff; color: #7c4dff; }
.stage-badge.is-procurement { background: #fff3ec; color: #ff7c4d; }
.stage-badge.is-quality { background: #e6faf8; color: #00b4a0; }
.stage-badge.is-inbound { background: #e8f7ef; color: #3db389; }
.stage-badge.is-shipment { background: #e8f2fd; color: #1c7ce0; }
.stage-badge.is-completed { background: #f0f9eb; color: #67C23A; }
.stage-badge.is-default { background: #f4f4f5; color: #909399; }
</style>