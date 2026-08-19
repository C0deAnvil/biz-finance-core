<template>
  <div class="page-stack">
    <el-row :gutter="12" class="summary-row">
      <el-col :span="6">
        <el-card shadow="hover">
          <div class="summary-label">分析供应商</div>
          <div class="summary-value">{{ summary.supplier_count || 0 }}</div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover">
          <div class="summary-label">A级供应商</div>
          <div class="summary-value success">{{ summary.grade_a_count || 0 }}</div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover">
          <div class="summary-label">C级供应商</div>
          <div class="summary-value danger">{{ summary.grade_c_count || 0 }}</div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover">
          <div class="summary-label">平均风险指数</div>
          <div class="summary-value warning">{{ percent(summary.avg_risk_score) }}</div>
        </el-card>
      </el-col>
    </el-row>

    <PageSection title="风险预警概览" description="按当前履约风险、BiLSTM时序风险和风险趋势自动筛出需要优先复核的供应商。">
      <el-row :gutter="12">
        <el-col :span="8">
          <el-card shadow="never" class="warning-card">
            <div class="warning-label">高风险供应商</div>
            <div class="warning-value danger">{{ summary.high_risk_count || 0 }}</div>
            <div class="warning-text">建议暂停大额新增采购，先核对近期到货和质检记录。</div>
          </el-card>
        </el-col>
        <el-col :span="8">
          <el-card shadow="never" class="warning-card">
            <div class="warning-label">BiLSTM时序预警</div>
            <div class="warning-value warning">{{ sequenceWarningCount }}</div>
            <div class="warning-text">根据近期履约序列识别未来交付风险偏高的供应商。</div>
          </el-card>
        </el-col>
        <el-col :span="8">
          <el-card shadow="never" class="warning-card">
            <div class="warning-label">风险趋势上升</div>
            <div class="warning-value primary">{{ increasingRiskCount }}</div>
            <div class="warning-text">适合优先安排采购跟进、备选供应商和到货节点复核。</div>
          </el-card>
        </el-col>
      </el-row>

      <div class="focus-grid mt-16" v-if="topRiskRows.length">
        <el-card v-for="row in topRiskRows" :key="row.supplier_id" shadow="hover" class="focus-card">
          <div class="focus-head">
            <div>
              <div class="focus-name">{{ row.supplier_name }}</div>
              <div class="focus-code">{{ row.supplier_code }}</div>
            </div>
            <el-tag :type="row.grade_type">{{ row.predicted_grade }}级</el-tag>
          </div>
          <div class="focus-risk">
            <span>当前风险 {{ percent(row.risk_score) }}</span>
            <span>时序风险 {{ percent(row.sequence_risk_score) }}</span>
          </div>
          <div class="focus-reason">{{ (row.risk_reasons || [])[0] || '当前履约表现整体稳定' }}</div>
          <el-button class="focus-button" size="small" type="primary" plain @click="openDetail(row.supplier_id)">查看处理建议</el-button>
        </el-card>
      </div>
    </PageSection>

    <PageSection title="供应商分级与风险预测" description="基于系统供应商数据与迁移学习适配结果，输出供应商等级、当前风险、未来风险趋势和业务处理建议。">
      <template #actions>
        <el-input v-model="filters.keyword" clearable placeholder="搜索供应商编码 / 名称" class="w-240" @keyup.enter="load" />
        <el-select v-model="filters.grade" clearable placeholder="供应商等级" class="w-140" @change="load">
          <el-option label="A级" value="A" />
          <el-option label="C级" value="C" />
        </el-select>
        <el-select v-model="filters.risk" clearable placeholder="风险等级" class="w-150" @change="load">
          <el-option label="高风险" value="高风险" />
          <el-option label="中风险" value="中风险" />
          <el-option label="低风险" value="低风险" />
        </el-select>
        <el-button @click="load">查询</el-button>
        <el-button type="primary" :loading="refreshing" @click="refreshAnalysis">刷新模型结果</el-button>
        <el-button type="info" plain @click="openModelInfo">模型说明</el-button>
      </template>

      <el-alert
        v-if="adapterNotes.length"
        class="mb-12"
        type="info"
        :closable="false"
        show-icon
        :title="adapterNotes.join('；')"
      />

      <el-table :data="pagedRows" stripe>
        <el-table-column prop="supplier_code" label="供应商编码" width="130" />
        <el-table-column prop="supplier_name" label="供应商名称" min-width="180" show-overflow-tooltip />
        <el-table-column prop="predicted_grade" label="模型分级" width="120">
          <template #default="scope">
            <el-tag :type="scope.row.grade_type">{{ scope.row.predicted_grade }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="confidence" label="置信度" width="130">
          <template #default="scope">{{ percent(scope.row.confidence) }}</template>
        </el-table-column>
        <el-table-column prop="risk_score" label="风险指数" width="180">
          <template #default="scope">
            <div class="risk-cell">
              <el-progress :percentage="progressPercent(scope.row.risk_score)" :stroke-width="10" :show-text="false" />
              <el-tag size="small" :type="scope.row.risk_type">{{ scope.row.risk_label }}</el-tag>
            </div>
          </template>
        </el-table-column>
        <el-table-column prop="sequence_risk_score" label="BiLSTM时序风险" width="190">
          <template #default="scope">
            <div class="risk-cell">
              <el-progress :percentage="progressPercent(scope.row.sequence_risk_score)" :stroke-width="10" :show-text="false" />
              <el-tag size="small" :type="scope.row.sequence_risk_type">{{ scope.row.sequence_risk_label }}</el-tag>
            </div>
          </template>
        </el-table-column>
        <el-table-column prop="risk_trend" label="风险趋势" width="110">
          <template #default="scope">
            <el-tag :type="scope.row.risk_trend === '上升' ? 'danger' : scope.row.risk_trend === '下降' ? 'success' : 'warning'">
              {{ scope.row.risk_trend || '平稳' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="采购订单数" width="120">
          <template #default="scope">{{ formatNumber(scope.row.features?.business?.order_count) }}</template>
        </el-table-column>
        <el-table-column label="采购金额" width="130">
          <template #default="scope">{{ formatNumber(scope.row.features?.business?.total_purchase_amount) }}</template>
        </el-table-column>
        <el-table-column label="延期率" width="110">
          <template #default="scope">{{ percent(scope.row.features?.business?.late_delivery_rate) }}</template>
        </el-table-column>
        <el-table-column label="不合格率" width="110">
          <template #default="scope">{{ percent(scope.row.features?.business?.quality_fail_rate) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="130" fixed="right">
          <template #default="scope">
            <el-button size="small" text type="primary" @click="openDetail(scope.row.supplier_id)">画像详情</el-button>
          </template>
        </el-table-column>
      </el-table>
      <AppPagination
        v-model:page="pagination.page"
        v-model:page-size="pagination.pageSize"
        :total="total"
        :page-sizes="pageSizes"
      />
    </PageSection>

    <el-drawer v-model="detailVisible" title="供应商智能画像" size="62%">
      <template v-if="detail">
        <el-descriptions :column="2" border>
          <el-descriptions-item label="供应商名称">{{ detail.supplier.name }}</el-descriptions-item>
          <el-descriptions-item label="供应商编码">{{ detail.supplier.code }}</el-descriptions-item>
          <el-descriptions-item label="联系人">{{ detail.supplier.contact_name }}</el-descriptions-item>
          <el-descriptions-item label="联系电话">{{ detail.supplier.phone }}</el-descriptions-item>
          <el-descriptions-item label="地址" :span="2">{{ detail.supplier.address }}</el-descriptions-item>
          <el-descriptions-item label="模型分级">
            <el-tag :type="detail.analysis.grade_type">{{ detail.analysis.predicted_grade }}</el-tag>
            <span class="ml-8">置信度 {{ percent(detail.analysis.confidence) }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="风险判断">
            <el-tag :type="detail.analysis.risk_type">{{ detail.analysis.risk_label }}</el-tag>
            <span class="ml-8">风险指数 {{ percent(detail.analysis.risk_score) }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="BiLSTM时序风险">
            <el-tag :type="detail.analysis.sequence_risk_type">{{ detail.analysis.sequence_risk_label }}</el-tag>
            <span class="ml-8">时序风险 {{ percent(detail.analysis.sequence_risk_score) }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="未来趋势">
            <el-tag :type="detail.analysis.risk_trend === '上升' ? 'danger' : detail.analysis.risk_trend === '下降' ? 'success' : 'warning'">
              {{ detail.analysis.risk_trend || '平稳' }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="判断依据" :span="2">{{ detail.analysis.features.sequence?.basis || '-' }}</el-descriptions-item>
        </el-descriptions>

        <el-row :gutter="12" class="mt-16">
          <el-col :span="6"><el-card shadow="never"><div class="metric-label">采购金额</div><div class="metric-value">{{ formatNumber(detail.analysis.features.business.total_purchase_amount) }}</div></el-card></el-col>
          <el-col :span="6"><el-card shadow="never"><div class="metric-label">订单数</div><div class="metric-value">{{ formatNumber(detail.analysis.features.business.order_count) }}</div></el-card></el-col>
          <el-col :span="6"><el-card shadow="never"><div class="metric-label">按时到货率</div><div class="metric-value">{{ percent(detail.analysis.features.business.on_time_delivery_rate) }}</div></el-card></el-col>
          <el-col :span="6"><el-card shadow="never"><div class="metric-label">质检通过率</div><div class="metric-value">{{ percent(detail.analysis.features.business.quality_pass_rate) }}</div></el-card></el-col>
        </el-row>

        <PageSection title="风险原因与处理建议" description="面向采购和管理人员展示可执行结论，不展示底层模型特征。">
          <el-row :gutter="12">
            <el-col :span="12">
              <el-card shadow="never">
                <div class="section-mini-title">主要风险原因</div>
                <ul class="advice-list">
                  <li v-for="reason in detail.analysis.risk_reasons || []" :key="reason">{{ reason }}</li>
                </ul>
              </el-card>
            </el-col>
            <el-col :span="12">
              <el-card shadow="never">
                <div class="section-mini-title">建议处理动作</div>
                <ul class="advice-list">
                  <li v-for="suggestion in detail.analysis.action_suggestions || []" :key="suggestion">{{ suggestion }}</li>
                </ul>
              </el-card>
            </el-col>
          </el-row>
        </PageSection>

        <PageSection title="近期采购依据" description="展示模型画像对应的近期采购订单，便于核对风险判断来源。">
          <el-table :data="detail.recent_orders || []" stripe>
            <el-table-column prop="order_no" label="采购单号" width="150" />
            <el-table-column prop="created_at" label="创建时间" width="170">
              <template #default="scope">{{ formatDateTime(scope.row.created_at) }}</template>
            </el-table-column>
            <el-table-column prop="status" label="订单状态" width="110" />
            <el-table-column prop="total_amount" label="订单金额" width="130">
              <template #default="scope">{{ formatNumber(scope.row.total_amount) }}</template>
            </el-table-column>
            <el-table-column prop="paid_amount" label="已付金额" width="130">
              <template #default="scope">{{ formatNumber(scope.row.paid_amount) }}</template>
            </el-table-column>
            <el-table-column prop="item_quantity" label="采购数量" width="120">
              <template #default="scope">{{ formatNumber(scope.row.item_quantity) }}</template>
            </el-table-column>
            <el-table-column prop="received_quantity" label="已到货数量" width="130">
              <template #default="scope">{{ formatNumber(scope.row.received_quantity) }}</template>
            </el-table-column>
          </el-table>
        </PageSection>
      </template>
    </el-drawer>

    <el-dialog v-model="modelVisible" title="供应商智能模型说明" width="920px">
      <template v-if="modelInfo">
        <el-descriptions :column="2" border>
          <el-descriptions-item label="模型名称">{{ modelInfo.model_name }}</el-descriptions-item>
          <el-descriptions-item label="模型版本">{{ modelInfo.model_version }}</el-descriptions-item>
          <el-descriptions-item label="适配方式">{{ modelInfo.transfer_learning_mode || '迁移学习适配' }}</el-descriptions-item>
          <el-descriptions-item label="供应商等级">{{ (modelInfo.grade_labels || []).join(' / ') || '-' }}</el-descriptions-item>
          <el-descriptions-item label="时序风险模型">{{ modelInfo.risk_sequence_model || 'BiLSTM' }}</el-descriptions-item>
          <el-descriptions-item label="输出结果">{{ (modelInfo.output_items || []).join('、') || '-' }}</el-descriptions-item>
        </el-descriptions>

        <el-alert
          class="mt-16"
          type="info"
          :closable="false"
          show-icon
          :title="modelInfo.business_description || '模型结果已面向系统供应商数据进行迁移适配，页面仅展示业务可解释结论。'"
        />
        <PageSection title="能力说明" description="该说明面向业务用户，不展开底层训练特征和训练过程。">
          <el-row :gutter="12">
            <el-col v-for="item in modelInfo.capability_items || []" :key="item.title" :span="8">
              <el-card shadow="never" class="capability-card">
                <div class="section-mini-title">{{ item.title }}</div>
                <div class="capability-text">{{ item.description }}</div>
              </el-card>
            </el-col>
          </el-row>
        </PageSection>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import 'element-plus/es/components/alert/style/css'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/card/style/css'
import 'element-plus/es/components/col/style/css'
import 'element-plus/es/components/descriptions/style/css'
import 'element-plus/es/components/dialog/style/css'
import 'element-plus/es/components/drawer/style/css'
import 'element-plus/es/components/input/style/css'
import 'element-plus/es/components/message/style/css'
import 'element-plus/es/components/progress/style/css'
import 'element-plus/es/components/row/style/css'
import 'element-plus/es/components/select/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/tag/style/css'

import { ElMessage } from 'element-plus/es/components/message/index.mjs'
import ElAlert from 'element-plus/es/components/alert/index.mjs'
import ElButton from 'element-plus/es/components/button/index.mjs'
import ElCard from 'element-plus/es/components/card/index.mjs'
import ElCol from 'element-plus/es/components/col/index.mjs'
import ElDescriptions, { ElDescriptionsItem } from 'element-plus/es/components/descriptions/index.mjs'
import ElDialog from 'element-plus/es/components/dialog/index.mjs'
import ElDrawer from 'element-plus/es/components/drawer/index.mjs'
import ElInput from 'element-plus/es/components/input/index.mjs'
import ElProgress from 'element-plus/es/components/progress/index.mjs'
import ElRow from 'element-plus/es/components/row/index.mjs'
import ElSelect, { ElOption } from 'element-plus/es/components/select/index.mjs'
import ElTable, { ElTableColumn } from 'element-plus/es/components/table/index.mjs'
import ElTag from 'element-plus/es/components/tag/index.mjs'

import { computed, onMounted, reactive, ref } from 'vue'

import { get, post } from '../../api/client'
import AppPagination from '../../components/AppPagination.vue'
import PageSection from '../../components/PageSection.vue'
import { useLocalPagination } from '../../composables/useLocalPagination'
import { formatDateTime, formatNumber } from '../../utils/format'

const rows = ref([])
const summary = ref({})
const adapterReport = ref({})
const detail = ref(null)
const modelInfo = ref(null)
const detailVisible = ref(false)
const modelVisible = ref(false)
const refreshing = ref(false)
const filters = reactive({ keyword: '', grade: '', risk: '' })

const activeRows = computed(() => rows.value)
const adapterNotes = computed(() => adapterReport.value?.notes || [])
const sequenceWarningCount = computed(() => rows.value.filter((row) => Number(row.sequence_risk_score || 0) >= 0.55).length)
const increasingRiskCount = computed(() => rows.value.filter((row) => row.risk_trend === '上升').length)
const topRiskRows = computed(() => rows.value
  .filter((row) => row.predicted_grade === 'C' || row.risk_label === '高风险' || Number(row.sequence_risk_score || 0) >= 0.55)
  .slice(0, 3))
const { pagination, total, pageSizes, pagedItems: pagedRows } = useLocalPagination(activeRows)

function percent(value) {
  const number = Number(value || 0)
  return `${(number * 100).toFixed(1)}%`
}

function progressPercent(value) {
  return Math.max(0, Math.min(100, Number(value || 0) * 100))
}

async function load() {
  const query = new URLSearchParams()
  if (filters.keyword.trim()) query.set('keyword', filters.keyword.trim())
  if (filters.grade) query.set('grade', filters.grade)
  if (filters.risk) query.set('risk', filters.risk)
  const data = await get(`/api/supplier-ai/suppliers${query.toString() ? `?${query}` : ''}`, { force: true })
  rows.value = data.items || []
  summary.value = data.summary || {}
  adapterReport.value = data.adapter_report || {}
}

async function refreshAnalysis() {
  refreshing.value = true
  try {
    const result = await post('/api/supplier-ai/refresh', {})
    ElMessage.success(`已刷新 ${result.refreshed || 0} 个供应商画像`)
    await load()
  } finally {
    refreshing.value = false
  }
}

async function openDetail(supplierId) {
  detail.value = await get(`/api/supplier-ai/suppliers/${supplierId}`, { force: true })
  detailVisible.value = true
}

async function openModelInfo() {
  modelInfo.value = await get('/api/supplier-ai/model-info', { force: true })
  modelVisible.value = true
}

onMounted(load)
</script>

<style scoped>
.summary-row { margin-bottom: 16px; }
.summary-label { color: #6b7c8f; font-size: 13px; }
.summary-value { margin-top: 8px; font-size: 26px; font-weight: 700; color: #243447; }
.summary-value.success { color: #23965f; }
.summary-value.warning { color: #c77900; }
.summary-value.danger { color: #c94040; }
.warning-card { min-height: 132px; }
.warning-label { color: #6b7c8f; font-size: 13px; }
.warning-value { margin-top: 8px; font-size: 28px; font-weight: 700; color: #243447; }
.warning-value.primary { color: #2563eb; }
.warning-value.warning { color: #c77900; }
.warning-value.danger { color: #c94040; }
.warning-text { margin-top: 8px; color: #526579; line-height: 1.6; font-size: 13px; }
.focus-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
.focus-card { min-height: 172px; }
.focus-head { display: flex; justify-content: space-between; gap: 12px; align-items: flex-start; }
.focus-name { font-weight: 700; color: #243447; line-height: 1.5; }
.focus-code { margin-top: 2px; color: #7a8a99; font-size: 12px; }
.focus-risk { display: grid; gap: 6px; margin-top: 12px; color: #435466; font-size: 13px; }
.focus-reason { margin-top: 10px; min-height: 22px; color: #c94040; font-size: 13px; }
.focus-button { margin-top: 10px; }
.risk-cell { display: grid; grid-template-columns: 1fr auto; align-items: center; gap: 8px; }
.metric-label { color: #7a8a99; font-size: 13px; }
.metric-value { margin-top: 8px; font-size: 20px; font-weight: 700; color: #1f2f3d; }
.section-mini-title { font-weight: 700; color: #243447; margin-bottom: 8px; }
.advice-list { margin: 0; padding-left: 18px; color: #435466; line-height: 1.9; }
.capability-card { min-height: 118px; }
.capability-text { color: #526579; line-height: 1.7; }
.w-240 { width: 240px; }
.w-150 { width: 150px; }
.w-140 { width: 140px; }
.mb-12 { margin-bottom: 12px; }
.mt-16 { margin-top: 16px; }
.ml-8 { margin-left: 8px; }
</style>
