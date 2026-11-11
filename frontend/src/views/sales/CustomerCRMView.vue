<template>
  <div class="crm-page">
    <el-row :gutter="14">
      <el-col v-for="card in summaryCards" :key="card.key" :xs="24" :sm="8">
        <el-card shadow="hover">
          <div class="summary-label">{{ card.label }}</div>
          <div class="summary-value">{{ summary[card.key] || 0 }}</div>
        </el-card>
      </el-col>
    </el-row>

    <PageSection title="客户CRM" description="客户建档、跟进、转交、流失与恢复记录分表展示；销售仅查看本人客户，财务和管理层查看全部客户。">
      <template #actions>
        <el-button @click="resetFilters">重置</el-button>
        <el-button @click="search">查询</el-button>
        <el-button type="success" plain @click="exportCurrent">导出当前列表</el-button>
      </template>

      <div class="filter-row">
        <el-input v-model="filters.keyword" clearable placeholder="关键词：编码、内容、原因" @keyup.enter="search" />
        <el-input v-model="filters.customerName" clearable placeholder="客户名称" @keyup.enter="search" />
        <el-select v-if="activeTab === 'overview'" v-model="filters.status" clearable placeholder="全部CRM状态">
          <el-option v-for="item in statuses" :key="item.value" :label="item.label" :value="item.value" />
        </el-select>
        <el-date-picker
          v-model="filters.dateRange"
          type="daterange"
          value-format="YYYY-MM-DD"
          range-separator="至"
          start-placeholder="开始日期"
          end-placeholder="结束日期"
          unlink-panels
        />
      </div>

      <el-tabs v-model="activeTab" @tab-change="changeTab">
        <el-tab-pane label="客户概览" name="overview" />
        <el-tab-pane label="跟进记录" name="follow_up" />
        <el-tab-pane label="转交记录" name="transfer" />
        <el-tab-pane label="客户新增记录" name="created" />
        <el-tab-pane label="流失与恢复" name="lost" />
      </el-tabs>

      <el-table v-if="activeTab === 'overview'" v-loading="loading" :data="rows" stripe border>
        <el-table-column prop="code" label="客户编码" width="150" />
        <el-table-column prop="name" label="客户名称" min-width="210" show-overflow-tooltip />
        <el-table-column prop="owner_user_name" label="销售负责人" width="130" />
        <el-table-column label="CRM状态" width="110">
          <template #default="{ row }"><el-tag :type="statusTag(row.crm_status)">{{ row.crm_status_label }}</el-tag></template>
        </el-table-column>
        <el-table-column label="新增时间" width="170"><template #default="{ row }">{{ formatDateTime(row.first_qualified_order_at) }}</template></el-table-column>
        <el-table-column label="客户建档时间" width="170"><template #default="{ row }">{{ formatDateTime(row.created_at) }}</template></el-table-column>
        <el-table-column label="最后下单时间" width="170"><template #default="{ row }">{{ formatDateTime(row.last_order_at) }}</template></el-table-column>
        <el-table-column label="最近跟进" width="170"><template #default="{ row }">{{ formatDateTime(row.last_follow_at) }}</template></el-table-column>
        <el-table-column prop="last_follow_content" label="最近跟进内容" min-width="230" show-overflow-tooltip />
        <el-table-column label="下次跟进" width="170"><template #default="{ row }">{{ formatDateTime(row.next_follow_at) }}</template></el-table-column>
        <el-table-column label="流失时间" width="170"><template #default="{ row }">{{ formatDateTime(row.lost_at) }}</template></el-table-column>
        <el-table-column prop="lost_reason" label="流失原因" min-width="220" show-overflow-tooltip />
        <el-table-column label="操作" width="245" fixed="right">
          <template #default="{ row }">
            <el-button v-if="canManage" text type="primary" @click="openFollow(row)">记录跟进</el-button>
            <el-button text @click="openHistory(row)">历史</el-button>
            <el-button v-if="canManage && row.crm_status === 'lost'" text type="success" @click="restore(row)">恢复正常</el-button>
          </template>
        </el-table-column>
      </el-table>

      <el-table v-else v-loading="loading" :data="rows" stripe border>
        <el-table-column label="记录时间" width="175"><template #default="{ row }">{{ formatDateTime(row.occurred_at) }}</template></el-table-column>
        <el-table-column prop="customer_code" label="客户编码" width="145" />
        <el-table-column prop="customer_name" label="客户名称" min-width="210" show-overflow-tooltip />
        <el-table-column prop="owner_name" label="销售负责人" width="130" />
        <el-table-column prop="operator_name" label="记录/操作人" width="130" />
        <el-table-column prop="summary" :label="recordSummaryLabel" min-width="260" show-overflow-tooltip />
        <el-table-column prop="detail" label="说明" min-width="220" show-overflow-tooltip />
        <el-table-column v-if="activeTab === 'follow_up'" label="下次跟进" width="175"><template #default="{ row }">{{ formatDateTime(row.next_follow_at) }}</template></el-table-column>
        <el-table-column v-if="activeTab === 'lost'" label="当前状态" width="110">
          <template #default="{ row }"><el-tag :type="statusTag(row.status)">{{ crmStatusLabel(row.status) }}</el-tag></template>
        </el-table-column>
        <el-table-column label="操作" width="210" fixed="right">
          <template #default="{ row }">
            <el-button text @click="openHistory({ id: row.customer_id, name: row.customer_name })">历史</el-button>
            <el-button v-if="canManage && ['follow_up', 'lost'].includes(activeTab)" text type="primary" @click="openFollow({ id: row.customer_id, name: row.customer_name })">继续跟进</el-button>
            <el-button v-if="canManage && activeTab === 'lost' && row.status === 'lost'" text type="success" @click="restore({ id: row.customer_id, name: row.customer_name })">恢复正常</el-button>
          </template>
        </el-table-column>
      </el-table>

      <AppPagination
        v-model:page="pagination.page"
        v-model:page-size="pagination.pageSize"
        :total="total"
        :page-sizes="[10, 20, 50, 100]"
        @change="changePage"
      />
    </PageSection>

    <el-dialog v-model="followVisible" title="新增客户跟进记录" width="640px">
      <el-form label-width="100px">
        <el-form-item label="客户"><el-input :model-value="active?.name" disabled /></el-form-item>
        <el-form-item label="跟进方式" required>
          <el-select v-model="follow.follow_type" style="width:100%">
            <el-option v-for="item in followTypes" :key="item.value" :label="item.label" :value="item.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="跟进内容" required><el-input v-model="follow.content" type="textarea" :rows="6" maxlength="3000" show-word-limit /></el-form-item>
        <el-form-item label="下次跟进"><el-date-picker v-model="follow.next_follow_at" type="datetime" value-format="YYYY-MM-DDTHH:mm:ss" placeholder="选填" style="width:100%" /></el-form-item>
      </el-form>
      <template #footer><el-button @click="followVisible=false">取消</el-button><el-button type="primary" @click="saveFollow">保存跟进</el-button></template>
    </el-dialog>

    <el-drawer v-model="historyVisible" :title="`${active?.name || ''} · CRM历史`" size="55%">
      <el-divider content-position="left">跟进记录</el-divider>
      <el-timeline>
        <el-timeline-item v-for="item in history.follow_ups" :key="`f${item.id}`" :timestamp="formatDateTime(item.created_at)">
          <b>{{ followTypeLabel(item.follow_type) }}</b> · {{ item.followed_by_name || '-' }}
          <div>{{ item.content }}</div>
          <div v-if="item.next_follow_at" class="muted">下次跟进：{{ formatDateTime(item.next_follow_at) }}</div>
        </el-timeline-item>
      </el-timeline>
      <el-empty v-if="!history.follow_ups.length" description="暂无跟进记录" />
      <el-divider content-position="left">转交记录</el-divider>
      <el-timeline>
        <el-timeline-item v-for="item in history.transfers" :key="`t${item.id}`" :timestamp="formatDateTime(item.transferred_at)">
          {{ item.from_user_name || '未分配' }} → {{ item.to_user_name }}，操作人：{{ item.transferred_by_name || '-' }}
          <div class="muted">{{ item.reason || '未填写原因' }}</div>
        </el-timeline-item>
      </el-timeline>
      <el-empty v-if="!history.transfers.length" description="暂无转交记录" />
      <el-divider content-position="left">流失与恢复记录</el-divider>
      <el-timeline>
        <el-timeline-item v-for="item in history.events" :key="`e${item.id}`" :timestamp="formatDateTime(item.event_at)">
          <b>{{ item.event_type === 'lost' ? '标记流失' : '恢复正常' }}</b> · {{ item.operator_name || '系统' }}
          <div class="muted">{{ item.reason || '-' }}</div>
        </el-timeline-item>
      </el-timeline>
      <el-empty v-if="!history.events.length" description="暂无状态变化记录" />
    </el-drawer>
  </div>
</template>

<script setup>
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/card/style/css'
import 'element-plus/es/components/col/style/css'
import 'element-plus/es/components/date-picker/style/css'
import 'element-plus/es/components/dialog/style/css'
import 'element-plus/es/components/divider/style/css'
import 'element-plus/es/components/drawer/style/css'
import 'element-plus/es/components/empty/style/css'
import 'element-plus/es/components/form/style/css'
import 'element-plus/es/components/form-item/style/css'
import 'element-plus/es/components/input/style/css'
import 'element-plus/es/components/loading/style/css'
import 'element-plus/es/components/message/style/css'
import 'element-plus/es/components/message-box/style/css'
import 'element-plus/es/components/option/style/css'
import 'element-plus/es/components/row/style/css'
import 'element-plus/es/components/select/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/tabs/style/css'
import 'element-plus/es/components/tag/style/css'
import 'element-plus/es/components/timeline/style/css'
import 'element-plus/es/components/timeline-item/style/css'
import ElButton from 'element-plus/es/components/button/index.mjs'
import ElCard from 'element-plus/es/components/card/index.mjs'
import ElCol from 'element-plus/es/components/col/index.mjs'
import ElDatePicker from 'element-plus/es/components/date-picker/index.mjs'
import ElDialog from 'element-plus/es/components/dialog/index.mjs'
import ElDivider from 'element-plus/es/components/divider/index.mjs'
import ElDrawer from 'element-plus/es/components/drawer/index.mjs'
import ElEmpty from 'element-plus/es/components/empty/index.mjs'
import ElForm, { ElFormItem } from 'element-plus/es/components/form/index.mjs'
import ElInput from 'element-plus/es/components/input/index.mjs'
import { ElMessage } from 'element-plus/es/components/message/index.mjs'
import { ElMessageBox } from 'element-plus/es/components/message-box/index.mjs'
import ElRow from 'element-plus/es/components/row/index.mjs'
import ElSelect, { ElOption } from 'element-plus/es/components/select/index.mjs'
import ElTable, { ElTableColumn } from 'element-plus/es/components/table/index.mjs'
import ElTabs, { ElTabPane } from 'element-plus/es/components/tabs/index.mjs'
import ElTag from 'element-plus/es/components/tag/index.mjs'
import ElTimeline, { ElTimelineItem } from 'element-plus/es/components/timeline/index.mjs'
import { computed, onMounted, reactive, ref } from 'vue'

import { get, post } from '../../api/client'
import AppPagination from '../../components/AppPagination.vue'
import PageSection from '../../components/PageSection.vue'
import { useAuthStore } from '../../stores/auth'
import { formatDateTime } from '../../utils/format'

const auth = useAuthStore()
const activeTab = ref('overview')
const rows = ref([])
const total = ref(0)
const loading = ref(false)
const active = ref(null)
const followVisible = ref(false)
const historyVisible = ref(false)
const pagination = reactive({ page: 1, pageSize: 20 })
const summary = reactive({ lead: 0, active: 0, lost: 0 })
const filters = reactive({ keyword: '', customerName: '', status: '', dateRange: [] })
const follow = reactive({ follow_type: 'call', content: '', next_follow_at: null })
const history = reactive({ follow_ups: [], transfers: [], events: [] })
const statuses = [{ value: 'lead', label: '潜在客户' }, { value: 'active', label: '正常跟进' }, { value: 'lost', label: '已流失' }]
const followTypes = [{ value: 'call', label: '电话' }, { value: 'visit', label: '拜访' }, { value: 'wechat', label: '微信' }, { value: 'email', label: '邮件' }, { value: 'meeting', label: '会议' }, { value: 'other', label: '其他' }]
const summaryCards = computed(() => statuses.map((item) => ({ key: item.value, label: item.label })))
const canManage = computed(() => !['finance', 'finance_super'].includes(auth.user?.role_code))
const recordSummaryLabel = computed(() => ({ follow_up: '跟进内容', transfer: '转交关系', created: '记录类型', lost: '状态变化' }[activeTab.value] || '内容'))

function statusTag(value) { return { lead: 'warning', active: 'success', lost: 'danger' }[value] || 'info' }
function crmStatusLabel(value) { return { lead: '潜在客户', active: '正常跟进', lost: '已流失' }[value] || value || '-' }
function followTypeLabel(value) { return followTypes.find((item) => item.value === value)?.label || value }

function queryString({ page = pagination.page, pageSize = pagination.pageSize } = {}) {
  const query = new URLSearchParams({ page: String(page), page_size: String(pageSize) })
  if (filters.keyword.trim()) query.set('keyword', filters.keyword.trim())
  if (filters.customerName.trim()) query.set('customer_name', filters.customerName.trim())
  if (filters.dateRange?.[0]) query.set('date_from', filters.dateRange[0])
  if (filters.dateRange?.[1]) query.set('date_to', filters.dateRange[1])
  return query
}

async function loadSummary() {
  const data = await get('/api/master-data/customer-crm?page=1&page_size=1', { force: true })
  Object.assign(summary, { lead: 0, active: 0, lost: 0 }, data.summary || {})
}

async function load() {
  loading.value = true
  try {
    const query = queryString()
    if (activeTab.value === 'overview') {
      if (filters.status) query.set('status', filters.status)
      const data = await get(`/api/master-data/customer-crm?${query}`, { force: true })
      rows.value = data.items || []
      total.value = Number(data.pagination?.total || 0)
      Object.assign(summary, { lead: 0, active: 0, lost: 0 }, data.summary || {})
    } else {
      query.set('record_type', activeTab.value)
      const [data] = await Promise.all([
        get(`/api/master-data/customer-crm/records?${query}`, { force: true }),
        loadSummary()
      ])
      rows.value = data.items || []
      total.value = Number(data.pagination?.total || 0)
    }
  } catch (error) {
    ElMessage.error(error.message || '加载CRM记录失败')
  } finally {
    loading.value = false
  }
}

function search() { pagination.page = 1; load() }
function resetFilters() { Object.assign(filters, { keyword: '', customerName: '', status: '', dateRange: [] }); search() }
function changeTab() { pagination.page = 1; load() }
async function changePage({ page, pageSize }) { pagination.page = page; pagination.pageSize = pageSize; await load() }
function openFollow(row) { active.value = row; Object.assign(follow, { follow_type: 'call', content: '', next_follow_at: null }); followVisible.value = true }

async function saveFollow() {
  if (!follow.content.trim()) { ElMessage.warning('请填写跟进内容'); return }
  try {
    await post(`/api/master-data/customers/${active.value.id}/follow-ups`, follow)
    ElMessage.success('跟进记录已保存')
    followVisible.value = false
    await load()
  } catch (error) { ElMessage.error(error.message || '保存跟进失败') }
}

async function openHistory(row) {
  active.value = row
  try {
    Object.assign(history, await get(`/api/master-data/customers/${row.id}/crm-history`, { force: true }))
    historyVisible.value = true
  } catch (error) { ElMessage.error(error.message || '加载客户历史失败') }
}

async function restore(row) {
  try {
    await ElMessageBox.confirm(`确认客户“${row.name}”已完成跟进并恢复为正常状态吗？`, '恢复客户状态', { type: 'warning' })
    await post(`/api/master-data/customers/${row.id}/crm-status`, { status: 'active', reason: '销售跟进后恢复正常' })
    ElMessage.success('客户已恢复为正常跟进')
    await load()
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') ElMessage.error(error.message || '恢复状态失败')
  }
}

async function fetchAllForExport() {
  const all = []
  for (let page = 1; ; page += 1) {
    const query = queryString({ page, pageSize: 500 })
    let data
    if (activeTab.value === 'overview') {
      if (filters.status) query.set('status', filters.status)
      data = await get(`/api/master-data/customer-crm?${query}`, { force: true })
    } else {
      query.set('record_type', activeTab.value)
      data = await get(`/api/master-data/customer-crm/records?${query}`, { force: true })
    }
    all.push(...(data.items || []))
    if (all.length >= Number(data.pagination?.total || 0) || !(data.items || []).length) break
  }
  return all
}

async function exportCurrent() {
  loading.value = true
  try {
    const dataRows = await fetchAllForExport()
    const overviewColumns = [
      { key: 'code', title: '客户编码' }, { key: 'name', title: '客户名称' },
      { key: 'owner_user_name', title: '销售负责人' }, { key: 'crm_status_label', title: 'CRM状态' },
      { key: 'created_at', title: '新增时间' }, { key: 'last_order_at', title: '最后下单时间' },
      { key: 'last_follow_at', title: '最近跟进时间' }, { key: 'last_follow_content', title: '最近跟进内容' },
      { key: 'next_follow_at', title: '下次跟进时间' }, { key: 'lost_at', title: '流失时间' },
      { key: 'lost_reason', title: '流失原因' }
    ]
    const recordColumns = [
      { key: 'occurred_at', title: '记录时间' }, { key: 'customer_code', title: '客户编码' },
      { key: 'customer_name', title: '客户名称' }, { key: 'owner_name', title: '销售负责人' },
      { key: 'operator_name', title: '记录/操作人' }, { key: 'summary', title: recordSummaryLabel.value },
      { key: 'detail', title: '说明' }, { key: 'next_follow_at', title: '下次跟进时间' },
      { key: 'status', title: '当前状态' }
    ]
    const response = await fetch('/api/export/excel', {
      method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ filename: `customer_crm_${activeTab.value}`, sheet_name: '客户CRM', columns: activeTab.value === 'overview' ? overviewColumns : recordColumns, rows: dataRows })
    })
    if (!response.ok) throw new Error('导出失败')
    const url = URL.createObjectURL(await response.blob())
    const link = document.createElement('a')
    link.href = url
    link.download = `customer_crm_${activeTab.value}.xlsx`
    document.body.appendChild(link)
    link.click()
    link.remove()
    URL.revokeObjectURL(url)
  } catch (error) { ElMessage.error(error.message || '导出失败') }
  finally { loading.value = false }
}

onMounted(load)
</script>

<style scoped>
.crm-page{display:flex;flex-direction:column;gap:16px}.summary-label{color:#7d8795;font-size:15px}.summary-value{font-size:32px;font-weight:800;color:#0f5c90;margin-top:8px}.filter-row{display:grid;grid-template-columns:minmax(220px,1fr) minmax(180px,.8fr) 160px minmax(280px,1.2fr);gap:12px;margin-bottom:12px}.muted{color:#909399;margin-top:5px}@media(max-width:1100px){.filter-row{grid-template-columns:1fr 1fr}}
</style>
