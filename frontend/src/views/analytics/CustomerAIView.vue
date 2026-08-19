<template>
  <div class="page-stack">
    <el-row :gutter="12" class="summary-row">
      <el-col :span="6"><el-card shadow="hover"><div class="summary-label">分析客户</div><div class="summary-value">{{ summary.customer_count || 0 }}</div></el-card></el-col>
      <el-col :span="6"><el-card shadow="hover"><div class="summary-label">高价值客户</div><div class="summary-value success">{{ summary.high_value_count || 0 }}</div></el-card></el-col>
      <el-col :span="6"><el-card shadow="hover"><div class="summary-label">潜力客户</div><div class="summary-value primary">{{ summary.potential_count || 0 }}</div></el-card></el-col>
      <el-col :span="6"><el-card shadow="hover"><div class="summary-label">流失风险</div><div class="summary-value warning">{{ summary.risk_count || 0 }}</div></el-card></el-col>
    </el-row>

    <PageSection title="客户价值发现与推荐决策支持" description="基于客户订单、回款、产品组合和咨询记录输出客户分群、推荐商品和跟进优先级。">
      <template #actions>
        <el-input v-model="filters.keyword" clearable placeholder="搜索客户编码 / 名称 / 联系人 / 电话" class="w-280" @keyup.enter="load" />
        <el-select v-model="filters.segment" clearable placeholder="客户分群" class="w-180" @change="load">
          <el-option label="高价值客户" value="高价值客户" />
          <el-option label="潜力客户" value="潜力客户" />
          <el-option label="流失风险客户" value="流失风险客户" />
        </el-select>
        <el-button @click="load">查询</el-button>
        <el-button type="primary" :loading="refreshing" @click="refreshAnalysis">刷新模型结果</el-button>
        <el-button type="success" @click="openInquiryCreate">录入咨询</el-button>
      </template>

      <el-table :data="pagedRows" stripe>
        <el-table-column prop="customer_code" label="客户编码" width="130" />
        <el-table-column prop="customer_name" label="客户名称" min-width="170" show-overflow-tooltip />
        <el-table-column prop="segment_label" label="客户分群" width="120">
          <template #default="scope">
            <el-tag :type="scope.row.segment_type">{{ scope.row.segment_label }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="lead_label" label="线索意向" width="110">
          <template #default="scope">
            <el-tag :type="leadTagType(scope.row.lead_label)">{{ scope.row.lead_label }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="lead_score" label="意向得分" width="120">
          <template #default="scope">{{ percent(scope.row.lead_score) }}</template>
        </el-table-column>
        <el-table-column prop="frequency" label="购买频次" width="100" />
        <el-table-column prop="monetary" label="累计销售额" width="130">
          <template #default="scope">{{ formatNumber(scope.row.monetary) }}</template>
        </el-table-column>
        <el-table-column prop="recency" label="最近购买间隔" width="130">
          <template #default="scope">{{ scope.row.recency >= 900 ? '无订单' : `${scope.row.recency}天` }}</template>
        </el-table-column>
        <el-table-column prop="intent_rate" label="咨询意向率" width="120">
          <template #default="scope">{{ percent(scope.row.intent_rate) }}</template>
        </el-table-column>
        <el-table-column label="推荐商品" min-width="220" show-overflow-tooltip>
          <template #default="scope">{{ recommendationText(scope.row.recommendations) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="130" fixed="right">
          <template #default="scope">
            <el-button size="small" text type="primary" @click="openDetail(scope.row.customer_id)">画像详情</el-button>
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

    <el-drawer v-model="detailVisible" title="客户智能画像" size="58%">
      <template v-if="detail">
        <el-descriptions :column="2" border>
          <el-descriptions-item label="客户名称">{{ detail.customer.name }}</el-descriptions-item>
          <el-descriptions-item label="联系人">{{ detail.customer.contact_name }}</el-descriptions-item>
          <el-descriptions-item label="联系电话">{{ detail.customer.phone }}</el-descriptions-item>
          <el-descriptions-item label="客户星级">{{ detail.customer.star_level || '-' }}</el-descriptions-item>
          <el-descriptions-item label="客户分群">
            <el-tag :type="detail.analysis.segment_type">{{ detail.analysis.segment_label }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="线索意向">
            <el-tag :type="leadTagType(detail.analysis.lead_label)">{{ detail.analysis.lead_label }} {{ percent(detail.analysis.lead_score) }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="模型解释" :span="2">{{ detail.analysis.segment_reason }}</el-descriptions-item>
        </el-descriptions>

        <el-row :gutter="12" class="mt-16">
          <el-col :span="8"><el-card shadow="never"><div class="metric-label">最近购买间隔</div><div class="metric-value">{{ detail.analysis.rfm.recency >= 900 ? '无订单' : `${detail.analysis.rfm.recency}天` }}</div></el-card></el-col>
          <el-col :span="8"><el-card shadow="never"><div class="metric-label">购买频次</div><div class="metric-value">{{ detail.analysis.rfm.frequency || 0 }}</div></el-card></el-col>
          <el-col :span="8"><el-card shadow="never"><div class="metric-label">累计金额</div><div class="metric-value">{{ formatNumber(detail.analysis.rfm.monetary) }}</div></el-card></el-col>
        </el-row>

        <PageSection title="推荐商品" description="推荐结果来自历史商品配套规则，只作为销售跟进提示，不自动生成订单。">
          <el-table :data="detail.analysis.recommendations || []" stripe>
            <el-table-column prop="product_name" label="推荐商品" width="160" />
            <el-table-column prop="reason" label="推荐依据" min-width="260" show-overflow-tooltip />
            <el-table-column prop="confidence" label="置信度" width="100">
              <template #default="scope">{{ percent(scope.row.confidence) }}</template>
            </el-table-column>
            <el-table-column prop="lift" label="提升度" width="100" />
          </el-table>
        </PageSection>

        <PageSection title="近期订单" description="用于解释客户分群和购买偏好。">
          <el-table :data="detail.orders || []" stripe>
            <el-table-column prop="order_no" label="订单号" width="150" />
            <el-table-column prop="created_at" label="创建时间" width="180" />
            <el-table-column prop="total_amount" label="订单金额" width="130">
              <template #default="scope">{{ formatNumber(scope.row.total_amount) }}</template>
            </el-table-column>
            <el-table-column label="产品" min-width="220" show-overflow-tooltip>
              <template #default="scope">{{ (scope.row.products || []).join('、') || '-' }}</template>
            </el-table-column>
          </el-table>
        </PageSection>

        <PageSection title="咨询线索" description="录入咨询后系统会给出意向评分，并回写客户画像。">
          <el-table :data="detail.inquiries || []" stripe>
            <el-table-column prop="inquiry_no" label="咨询编号" width="150" />
            <el-table-column prop="intent_label" label="意向" width="100" />
            <el-table-column prop="intent_score" label="得分" width="100">
              <template #default="scope">{{ percent(scope.row.intent_score) }}</template>
            </el-table-column>
            <el-table-column prop="inquiry_text" label="咨询内容" min-width="260" show-overflow-tooltip />
            <el-table-column prop="created_at" label="时间" width="180" />
          </el-table>
        </PageSection>
      </template>
    </el-drawer>

    <el-dialog v-model="inquiryVisible" title="录入客户咨询并识别意向" width="720px">
      <el-form :model="inquiryForm" label-width="100px">
        <el-form-item label="客户">
          <el-select v-model="inquiryForm.customer_id" filterable style="width: 100%">
            <el-option v-for="item in rows" :key="item.customer_id" :label="item.customer_name" :value="item.customer_id" />
          </el-select>
        </el-form-item>
        <el-row :gutter="16">
          <el-col :span="12"><el-form-item label="来源渠道"><el-input v-model="inquiryForm.source_channel" placeholder="电话 / 微信 / 展会等" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="涉及产品"><el-input v-model="inquiryForm.product_hint" placeholder="可选" /></el-form-item></el-col>
        </el-row>
        <el-form-item label="咨询内容">
          <el-input v-model="inquiryForm.inquiry_text" type="textarea" :rows="5" placeholder="例如：客户询问工业氯化钠现货、报价、质检报告和发货时间" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="inquiryVisible = false">取消</el-button>
        <el-button type="primary" @click="submitInquiry">保存并评分</el-button>
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
import 'element-plus/es/components/message/style/css'
import 'element-plus/es/components/row/style/css'
import 'element-plus/es/components/select/style/css'
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
import ElRow from 'element-plus/es/components/row/index.mjs'
import ElSelect, { ElOption } from 'element-plus/es/components/select/index.mjs'
import ElTable, { ElTableColumn } from 'element-plus/es/components/table/index.mjs'
import ElTag from 'element-plus/es/components/tag/index.mjs'

import { computed, onMounted, reactive, ref } from 'vue'

import { get, post } from '../../api/client'
import AppPagination from '../../components/AppPagination.vue'
import PageSection from '../../components/PageSection.vue'
import { useLocalPagination } from '../../composables/useLocalPagination'
import { formatNumber } from '../../utils/format'

const rows = ref([])
const summary = ref({})
const detail = ref(null)
const detailVisible = ref(false)
const inquiryVisible = ref(false)
const refreshing = ref(false)
const filters = reactive({ keyword: '', segment: '' })
const inquiryForm = reactive({ customer_id: null, source_channel: '', product_hint: '', inquiry_text: '' })

const activeRows = computed(() => rows.value)
const { pagination, total, pageSizes, pagedItems: pagedRows } = useLocalPagination(activeRows)

function percent(value) {
  const number = Number(value || 0)
  return `${(number * 100).toFixed(1)}%`
}

function leadTagType(label) {
  return ({ 高意向: 'success', 中意向: 'primary', 待观察: 'info' })[label] || 'info'
}

function recommendationText(items) {
  if (!items?.length) return '-'
  return items.map((item) => item.product_name).join('、')
}

async function load() {
  const query = new URLSearchParams()
  if (filters.keyword.trim()) query.set('keyword', filters.keyword.trim())
  if (filters.segment) query.set('segment', filters.segment)
  const data = await get(`/api/customer-ai/customers${query.toString() ? `?${query}` : ''}`, { force: true })
  rows.value = data.items || []
  summary.value = data.summary || {}
}

async function refreshAnalysis() {
  refreshing.value = true
  try {
    const result = await post('/api/customer-ai/refresh', {})
    ElMessage.success(`已刷新 ${result.refreshed || 0} 个客户画像`)
    await load()
  } finally {
    refreshing.value = false
  }
}

async function openDetail(customerId) {
  detail.value = await get(`/api/customer-ai/customers/${customerId}`, { force: true })
  detailVisible.value = true
}

function openInquiryCreate() {
  Object.assign(inquiryForm, { customer_id: rows.value[0]?.customer_id || null, source_channel: '', product_hint: '', inquiry_text: '' })
  inquiryVisible.value = true
}

async function submitInquiry() {
  if (!inquiryForm.customer_id || !inquiryForm.inquiry_text.trim()) {
    ElMessage.warning('请选择客户并填写咨询内容')
    return
  }
  const result = await post('/api/customer-ai/inquiries', {
    customer_id: inquiryForm.customer_id,
    source_channel: inquiryForm.source_channel.trim(),
    product_hint: inquiryForm.product_hint.trim(),
    inquiry_text: inquiryForm.inquiry_text.trim()
  })
  ElMessage.success(`咨询已保存，识别结果：${result.intent_label} ${percent(result.intent_score)}`)
  inquiryVisible.value = false
  await load()
  if (detailVisible.value && detail.value?.customer?.id === inquiryForm.customer_id) {
    await openDetail(inquiryForm.customer_id)
  }
}

onMounted(load)
</script>

<style scoped>
.page-stack { display: flex; flex-direction: column; gap: 16px; }
.summary-row { margin-bottom: 0; }
.summary-label, .metric-label { color: #909399; margin-bottom: 8px; }
.summary-value { font-size: 28px; font-weight: 700; color: #0f5c90; }
.summary-value.success { color: #529b2e; }
.summary-value.primary { color: #337ecc; }
.summary-value.warning { color: #b88230; }
.metric-value { font-size: 20px; font-weight: 700; color: #0f5c90; }
.w-280 { width: 280px; }
.w-180 { width: 180px; }
.mt-16 { margin-top: 16px; }
</style>
