<template>
  <div class="timeline-page">
    <div v-if="!loading && !timeline" class="empty-box">
      <el-empty description="未找到订单或暂无权限查看" />
      <div class="empty-actions">
        <el-button type="primary" @click="goBack">返回</el-button>
      </div>
    </div>

    <template v-else>
      <div v-loading="loading" class="timeline-body">
        <!-- 顶部:订单概要 -->
        <div class="page-head">
          <el-button size="small" @click="goBack">← 返回</el-button>
          <div class="title-wrap">
            <h2 class="order-no" :style="order.order_type_color ? { color: order.order_type_color } : {}">
              {{ order.order_no || '订单时间轴' }}
            </h2>
            <el-tag v-if="order.order_type_label" size="small" :color="order.order_type_color" effect="dark" class="ml-8">
              {{ order.order_type_label }}
            </el-tag>
            <el-tag size="small" :type="statusType(order.status)" class="ml-8">{{ order.status_label }}</el-tag>
            <span v-if="orderCompleted()" class="overall-stage is-closed">订单已完成 · 全链闭环</span>
            <span v-else-if="timeline.overall_stage_label" class="overall-stage">{{ timeline.overall_stage_label }}</span>
          </div>
          <div class="head-meta">
            <span>客户: <b>{{ order.customer_name || '-' }}</b></span>
            <span>销售: {{ order.sales_user || '-' }}</span>
            <span>付款: {{ order.payment_type_label || '-' }}</span>
            <span>金额: ¥{{ formatNumber(order.total_amount) }}</span>
            <span v-if="order.expected_delivery_date">期望发货: {{ order.expected_delivery_date }}</span>
            <span>创建: {{ formatDateTime(order.created_at) }}</span>
          </div>
        </div>

        <!-- 泳道图 -->
        <PageSection title="全链时间轴泳道图" :description="swimlaneDesc">
          <div class="swimlane">
            <!-- 时间轴表头 -->
            <div class="lane lane-head">
              <div class="lane-label">泳道 / 时间</div>
              <div class="lane-track">
                <div v-for="tick in ticks" :key="tick.label" class="tick-label" :style="{ left: tick.pct + '%' }">
                  {{ tick.label }}
                </div>
                <div v-if="nowPct > 0 && nowPct < 100" class="now-mark" :style="{ left: nowPct + '%' }">
                  <span class="now-label">现在</span>
                </div>
              </div>
            </div>

            <!-- 各泳道 -->
            <div
              v-for="(lane, i) in timeline.lanes"
              :key="lane.key"
              class="lane"
              :class="[`lane-${lane.key}`, { 'is-active': lane.key === activeLane }]"
            >
              <div class="lane-label">
                <span class="lane-idx">{{ i + 1 }}</span>
                <span class="lane-name">{{ lane.label }}</span>
                <span class="lane-status" :class="lanePillClass(lane)">{{ lanePillText(lane) }}</span>
                <span v-if="lane.blocked_reason" class="lane-blocked" :title="lane.blocked_reason">⚠</span>
              </div>
              <div class="lane-track">
                <template v-for="ev in lane.events" :key="ev.id">
                  <div class="ev" :style="{ left: xPct(ev.created_at) + '%' }" :title="evTitle(ev)">
                    <div class="ev-dot" :class="{ 'is-last': isLastEvent(lane, ev) }"></div>
                    <div v-if="isFirstEvent(lane, ev) || isLastEvent(lane, ev)" class="ev-name" :class="{ 'is-last': isLastEvent(lane, ev) }">
                      {{ ev.to_stage_label }}
                    </div>
                  </div>
                </template>
                <div v-if="!lane.events.length && lane.current_stage && !lane.not_required" class="ev ev-ghost" style="left: 94%">
                  <div class="ev-dot is-ghost"></div>
                  <div class="ev-name">{{ lane.current_stage_label }}</div>
                </div>
                <div v-if="!lane.events.length && !lane.current_stage" class="lane-empty">暂无记录</div>
              </div>
            </div>
          </div>
        </PageSection>

        <!-- 关联单据 -->
        <PageSection title="全链关联单据" :description="`上下游单据共 ${relations.length} 张(点击可跳转)`">
          <div v-if="relations.length" class="rel-grid">
            <div
              v-for="(doc, i) in relations"
              :key="doc.document_type + '_' + doc.document_id + '_' + i"
              class="rel-card"
              :class="'rel-' + doc.document_type"
              :title="doc.document_no"
              @click="openDoc(doc)"
            >
              <div class="rel-head">
                <span class="rel-type">{{ doc.document_type_label }}</span>
                <el-tag size="small" effect="plain">{{ doc.status_label }}</el-tag>
              </div>
              <div class="rel-no">{{ doc.document_no }}</div>
              <div class="rel-meta">
                <span v-if="doc.product_name">商品: {{ doc.product_name }}</span>
                <span v-if="doc.quantity != null">
                  {{ doc.document_type === 'purchase_order' ? '金额' : '数量' }}: {{ formatNumber(doc.quantity) }}
                </span>
                <span v-if="doc.supplier_name">供应商: {{ doc.supplier_name }}</span>
              </div>
            </div>
          </div>
          <el-empty v-else description="暂无关联单据" :image-size="60" />
        </PageSection>

        <!-- 状态事件明细 -->
        <PageSection title="状态事件明细" :description="`共 ${allEvents.length} 条状态流转记录`">
          <el-table v-if="allEvents.length" :data="allEvents" size="small" stripe max-height="440">
            <el-table-column label="时间" width="160">
              <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
            </el-table-column>
            <el-table-column label="泳道" width="110">
              <template #default="{ row }">
                <span :style="{ color: laneColorOf(row.dimension), fontWeight: 600 }">{{ row.lane_label }}</span>
              </template>
            </el-table-column>
            <el-table-column label="状态变更" min-width="200">
              <template #default="{ row }">{{ row.from_stage_label }} → {{ row.to_stage_label }}</template>
            </el-table-column>
            <el-table-column label="操作人" width="110" prop="operator_name" />
            <el-table-column label="关联单据" width="150">
              <template #default="{ row }">{{ row.related_document_no || '-' }}</template>
            </el-table-column>
            <el-table-column label="原因 / 说明" min-width="220" prop="reason_text" show-overflow-tooltip />
          </el-table>
          <el-empty v-else description="暂无状态流转记录" :image-size="60" />
        </PageSection>

        <!-- 发货照片：库管在「发货出库」时上传，销售在此直接查看 -->
        <PageSection title="发货照片" description="库管确认发货出库时拍摄的现场照片">
          <PhotoAttachments
            v-if="currentOrderId"
            :key="`timeline-ship-${currentOrderId}`"
            business-type="sales_order"
            :business-id="currentOrderId"
            attachment-type="发货照片"
            title="发货现场"
            readonly
          />
        </PageSection>
      </div>
    </template>
  </div>
</template>

<script setup>
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/empty/style/css'
import 'element-plus/es/components/tag/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/table-column/style/css'

import ElButton from 'element-plus/es/components/button/index.mjs'
import ElEmpty from 'element-plus/es/components/empty/index.mjs'
import ElTag from 'element-plus/es/components/tag/index.mjs'
import { ElTable, ElTableColumn } from 'element-plus/es/components/table/index.mjs'

import { computed, onMounted, ref } from 'vue'

// 当前时间轴对应的客户订单 id，发货照片按订单归档
const currentOrderId = ref(null)
import { useRoute, useRouter } from 'vue-router'

import { get } from '../../api/client'
import PageSection from '../../components/PageSection.vue'
import PhotoAttachments from '../../components/PhotoAttachments.vue'
import { formatDateTime, formatNumber, statusType } from '../../utils/format'

const route = useRoute()
const router = useRouter()

const loading = ref(false)
const timeline = ref(null)

const order = computed(() => timeline.value?.order || {})
const relations = computed(() => timeline.value?.relations || [])

// ===== 泳道 =====
const LANE_COLORS = {
  approval: '#E6A23C',
  finance: '#409EFF',
  placement: '#8a94a3',
  procurement: '#ff7c4d',
  production: '#7c4dff',
  quality: '#00b4a0',
  inbound: '#3db389',
  shipment: '#1c7ce0'
}

// 各泳道"已完成"的终极状态
const LANE_DONE = {
  approval: new Set(['approved', 'not_required', 'completed']),
  finance: new Set(['paid', 'settled', 'cleared', 'recognized', 'not_required', 'completed']),
  placement: new Set(['placed', 'completed', 'not_required']),
  procurement: new Set(['fully_received', 'completed', 'not_required']),
  production: new Set(['completed', 'reworked', 'not_required']),
  quality: new Set(['passed', 'completed', 'not_required']),
  inbound: new Set(['inbound_completed', 'completed', 'not_required']),
  shipment: new Set(['shipped', 'completed', 'not_required'])
}

// 订单整体已完成时,所有泳道按已完成展示(兼容历史快照残留的中间状态)
function orderCompleted() {
  return order.value.status === 'completed'
}

const LANE_LABELS = {
  approval: '销售审批',
  finance: '财务收款',
  placement: '置货备货',
  procurement: '采购补货',
  production: '生产制造',
  quality: '质检',
  inbound: '入库',
  shipment: '发货出库'
}

function laneColorOf(key) {
  return LANE_COLORS[key] || '#909399'
}

// ===== 时间计算 =====
const startMs = computed(() => {
  const raw = timeline.value?.time_start
  const t = raw ? new Date(raw).getTime() : Date.now()
  return Number.isFinite(t) ? t : Date.now()
})
const endMs = computed(() => {
  const raw = timeline.value?.time_end || timeline.value?.order?.updated_at
  const t = raw ? new Date(raw).getTime() : Date.now()
  return Number.isFinite(t) ? t : Date.now()
})
const nowMs = computed(() => Date.now())

function xPct(timeStr) {
  if (!timeStr) return 2
  const t = new Date(timeStr).getTime()
  if (!Number.isFinite(t)) return 2
  const span = Math.max(endMs.value - startMs.value, 1)
  const pct = ((t - startMs.value) / span) * 100
  return Math.min(98, Math.max(2, pct))
}

const nowPct = computed(() => Math.min(98, Math.max(2, ((nowMs.value - startMs.value) / Math.max(endMs.value - startMs.value, 1)) * 100)))

const ticks = computed(() => {
  const span = Math.max(endMs.value - startMs.value, 1)
  return [0, 0.2, 0.4, 0.6, 0.8, 1].map((f) => ({
    pct: Math.round(f * 100),
    label: formatDateTime(new Date(startMs.value + span * f))
  }))
})

const swimlaneDesc = computed(() => {
  if (!timeline.value?.time_start) return ''
  const spanMs = endMs.value - startMs.value
  if (spanMs <= 0) return ''
  const days = spanMs / 86400000
  const spanText = days >= 1 ? `${days.toFixed(1)} 天` : `${Math.max(1, Math.round(spanMs / 60000))} 分钟`
  return `自 ${formatDateTime(timeline.value.time_start)} 起,当前历时约 ${spanText},按业务泳道展示每一次状态流转`
})

// ===== 泳道状态 =====
const activeLane = computed(() => {
  if (orderCompleted()) return ''
  const lane = (timeline.value?.lanes || []).find((l) => {
    if (!l.current_stage || l.current_stage === 'not_required') return false
    return !(LANE_DONE[l.key] || new Set()).has(l.current_stage)
  })
  return lane ? lane.key : ''
})

function lanePillText(lane) {
  if (lane.not_required) return '无需'
  if (!lane.current_stage) return '—'
  if (orderCompleted()) return '✓ 完成'
  if ((LANE_DONE[lane.key] || new Set()).has(lane.current_stage)) return '✓ 完成'
  return lane.current_stage_label || '进行中'
}
function lanePillClass(lane) {
  if (lane.not_required) return 'is-na'
  if (!lane.current_stage) return 'is-none'
  if (orderCompleted()) return 'is-done'
  if ((LANE_DONE[lane.key] || new Set()).has(lane.current_stage)) return 'is-done'
  return 'is-active'
}

// ===== 事件 =====
function isFirstEvent(lane, ev) {
  return lane.events.length > 0 && lane.events[0].id === ev.id
}
function isLastEvent(lane, ev) {
  return lane.events.length > 0 && lane.events[lane.events.length - 1].id === ev.id
}
function evTitle(ev) {
  const lines = [formatDateTime(ev.created_at), `${ev.from_stage_label} → ${ev.to_stage_label}`]
  if (ev.operator_name) lines.push(`操作人: ${ev.operator_name}`)
  if (ev.related_document_no) lines.push(`关联单据: ${ev.related_document_no}`)
  if (ev.reason_text) lines.push(`说明: ${ev.reason_text}`)
  return lines.join('\n')
}

const allEvents = computed(() => {
  const rows = []
  for (const lane of timeline.value?.lanes || []) {
    for (const ev of lane.events) {
      rows.push({
        ...ev,
        dimension: lane.key,
        lane_label: lane.label || LANE_LABELS[lane.key] || lane.key
      })
    }
  }
  return rows.sort((a, b) => String(b.created_at).localeCompare(String(a.created_at) || ''))
})

// ===== 关联单据跳转 =====
const DOC_ROUTES = {
  fulfillment_demand: (doc) => (doc.fulfillment_mode === 'production' ? '/production/tasks' : '/purchases/orders'),
  production_task: () => '/production/tasks',
  purchase_order: () => '/purchases/orders',
  quality_task: () => '/quality/tasks'
}
function openDoc(doc) {
  const routeFor = DOC_ROUTES[doc.document_type]
  if (!routeFor) return
  const path = typeof routeFor === 'function' ? routeFor(doc) : routeFor
  router.push({ path, query: { focusId: doc.document_id, focusType: doc.document_type } })
}

// ===== 加载 =====
async function load() {
  const orderId = Number(route.query.id || route.params.id || 0)
  currentOrderId.value = orderId || null
  if (!orderId) {
    timeline.value = null
    return
  }
  loading.value = true
  try {
    timeline.value = await get(`/api/sales/orders/${orderId}/timeline`)
  } catch (error) {
    timeline.value = null
  } finally {
    loading.value = false
  }
}

function goBack() {
  if (window.history.length > 1) {
    router.back()
  } else {
    router.replace('/flow-board')
  }
}

onMounted(load)
</script>

<style scoped>
.timeline-page { padding: 4px 2px; }
.empty-box { padding: 60px 0; display: flex; flex-direction: column; align-items: center; gap: 12px; }
.empty-actions { display: flex; gap: 8px; }

.page-head { display: flex; align-items: center; gap: 16px; flex-wrap: wrap; background: #fff; border: 1px solid #e4edf6; border-radius: 14px; padding: 14px 18px; margin-bottom: 16px; box-shadow: 0 10px 26px rgba(31,47,61,.05); }
.title-wrap { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; flex: 1; min-width: 260px; }
.order-no { margin: 0; font-size: 20px; font-weight: 800; color: #1f2f3d; }
.overall-stage { margin-left: 6px; padding: 3px 10px; border-radius: 12px; background: #f0f9eb; color: #67C23A; font-size: 12px; font-weight: 600; }
.overall-stage.is-closed { background: #e8f7ef; color: #2f8f62; }
.head-meta { display: flex; gap: 14px; flex-wrap: wrap; color: #7a8a99; font-size: 13px; }
.head-meta b { color: #303133; }

/* ===== 泳道图 ===== */
.swimlane { border: 1px solid #e4edf6; border-radius: 12px; overflow: hidden; background: #fff; }
.lane { display: grid; grid-template-columns: 150px minmax(0, 1fr); align-items: stretch; border-bottom: 1px dashed #eff2f6; min-height: 54px; }
.lane:last-child { border-bottom: none; }
.lane-head { min-height: 40px; }
.lane-label { padding: 8px 12px; border-right: 1px solid #eff2f6; background: #fafbfc; display: flex; flex-direction: column; align-items: flex-start; justify-content: center; gap: 3px; position: relative; }
.lane-name { font-size: 13px; font-weight: 700; color: #303133; }
.lane-idx { position: absolute; top: 4px; right: 8px; font-size: 10px; color: #c0c4cc; font-weight: 700; }
.lane-status { font-size: 11px; padding: 1px 8px; border-radius: 10px; font-weight: 600; }
.lane-status.is-done { background: #f0f9eb; color: #67C23A; }
.lane-status.is-active { background: #ecf5ff; color: #185f93; }
.lane-status.is-na { background: #f4f4f5; color: #909399; }
.lane-status.is-none { background: transparent; color: #c0c4cc; }
.lane-blocked { position: absolute; bottom: 3px; right: 8px; font-size: 11px; cursor: help; }

.lane.is-active .lane-label { background: color-mix(in srgb, var(--lane-color, #185f93) 8%, #fff); }
.lane.is-active .lane-name { color: var(--lane-color, #185f93); }

.lane-track { position: relative; min-height: 54px; background: repeating-linear-gradient(90deg, #eef1f5 0, #eef1f5 1px, transparent 1px, transparent 20%); }
.lane-head .lane-track { min-height: 40px; }
.tick-label { position: absolute; top: 4px; transform: translateX(-50%); font-size: 10px; color: #a8b2bd; white-space: nowrap; }
.now-mark { position: absolute; top: 0; bottom: 0; width: 0; border-left: 2px dashed #d3dce6; z-index: 1; }
.now-label { position: absolute; top: 2px; left: 4px; font-size: 10px; color: #1c7ce0; background: #fff; padding: 0 3px; border-radius: 4px; }
.lane-empty { position: absolute; left: 50%; top: 50%; transform: translate(-50%, -50%); font-size: 11px; color: #c0c4cc; }

.ev { position: absolute; top: 50%; transform: translate(-50%, -50%); text-align: center; z-index: 2; cursor: default; }
.ev-dot { width: 13px; height: 13px; border-radius: 50%; background: #fff; border: 3px solid var(--lane-color, #909399); margin: 0 auto; }
.ev-dot.is-last { background: var(--lane-color, #909399); box-shadow: 0 0 0 4px color-mix(in srgb, var(--lane-color, #909399) 25%, transparent); }
.ev-dot.is-ghost { border-style: dashed; opacity: .55; }
.ev-name { position: absolute; top: 16px; left: 50%; transform: translateX(-50%); font-size: 10px; color: #7a8a99; white-space: nowrap; max-width: 100px; overflow: hidden; text-overflow: ellipsis; }
.ev-name.is-last { color: var(--lane-color, #909399); font-weight: 700; }
.ev-ghost .ev-name { top: 16px; }

.lane-approval { --lane-color: #E6A23C; }
.lane-finance { --lane-color: #409EFF; }
.lane-placement { --lane-color: #8a94a3; }
.lane-procurement { --lane-color: #ff7c4d; }
.lane-production { --lane-color: #7c4dff; }
.lane-quality { --lane-color: #00b4a0; }
.lane-inbound { --lane-color: #3db389; }
.lane-shipment { --lane-color: #1c7ce0; }

/* ===== 关联单据 ===== */
.rel-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 12px; }
.rel-card { border: 1px solid #e4edf6; border-radius: 12px; padding: 12px 14px; cursor: pointer; transition: all .2s; background: #fff; }
.rel-card:hover { transform: translateY(-2px); box-shadow: 0 6px 18px rgba(31,47,61,.1); border-color: var(--lane-color, #185f93); }
.rel-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
.rel-type { font-size: 12px; font-weight: 700; color: var(--lane-color, #185f93); }
.rel-no { font-size: 14px; font-weight: 700; color: #1f2f3d; margin-bottom: 6px; }
.rel-meta { display: flex; flex-direction: column; gap: 2px; font-size: 12px; color: #7a8a99; }
.rel-fulfillment_demand { --lane-color: #ff7c4d; }
.rel-production_task { --lane-color: #7c4dff; }
.rel-purchase_order { --lane-color: #E6A23C; }
.rel-quality_task { --lane-color: #00b4a0; }

.ml-8 { margin-left: 8px; }
</style>