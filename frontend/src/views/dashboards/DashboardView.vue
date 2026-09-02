<template>
  <div class="dashboard-page">
    <el-row :gutter="16">
      <el-col v-for="(value, key) in data.kpis" :key="key" :span="8">
        <el-card class="kpi-card" shadow="hover">
          <div class="kpi-label">{{ key }}</div>
          <div class="kpi-value">{{ typeof value === 'number' ? formatNumber(value) : value }}</div>
        </el-card>
      </el-col>
    </el-row>

    <PageSection title="我的工作台" description="按角色快速进入常用业务入口。">
      <div class="workbench-grid">
        <div
          v-for="entry in workbenchEntries"
          :key="entry.path"
          class="workbench-card"
          :style="{ '--accent': entry.color }"
          @click="router.push(entry.path)"
        >
          <div class="workbench-icon">{{ entry.icon }}</div>
          <div class="workbench-name">{{ entry.label }}</div>
          <div class="workbench-desc">{{ entry.desc }}</div>
        </div>
      </div>
    </PageSection>

    <PageSection title="待办中心" description="仅展示未完成事项；审批类待办进入审批中心侧边栏查看详情后处理。">
      <template #actions>
        <el-button @click="load">刷新</el-button>
        <el-button :disabled="!selectedCompletableTodoIds.length" @click="completeSelectedTodos">批量完成</el-button>
      </template>
      <el-table :data="pagedTodos" stripe @selection-change="todoSelection = $event">
        <el-table-column type="selection" width="55" :selectable="selectableTodo" />
        <el-table-column prop="title" label="标题" min-width="220" />
        <el-table-column prop="module" label="模块" width="120">
          <template #default="scope">
            <el-tag v-bind="moduleTagStyle(scope.row.module)">{{ moduleMeta(scope.row.module).label }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="status_label" label="状态" width="120">
          <template #default="scope">
            <el-tag :type="statusType(scope.row.status)">{{ scope.row.status_label }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="创建时间" min-width="180">
          <template #default="scope">{{ formatDateTime(scope.row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="280" fixed="right">
          <template #default="scope">
            <div class="todo-actions">
              <el-button size="small" @click="goTodo(scope.row)">前往</el-button>
              <el-button v-if="scope.row.requires_action_route" size="small" type="success" @click="goTodo(scope.row)">确认</el-button>
              <el-button v-else-if="scope.row.can_approve" size="small" type="success" @click="goTodo(scope.row)">通过</el-button>
              <el-button v-if="scope.row.can_reject" size="small" type="danger" @click="goTodo(scope.row)">驳回</el-button>
              <el-button v-if="!scope.row.can_approve && !scope.row.requires_action_route" size="small" type="primary" @click="completeTodo(scope.row.id)">完成</el-button>
            </div>
          </template>
        </el-table-column>
      </el-table>
      <AppPagination
        v-model:page="todoPagination.page"
        v-model:page-size="todoPagination.pageSize"
        :total="todoTotal"
        :page-sizes="pageSizes"
      />
    </PageSection>

    <PageSection title="通知中心" description="仅展示未读通知，支持批量已读。">
      <template #actions>
        <el-badge :value="unreadCount" :hidden="!unreadCount">
          <el-button>未读通知</el-button>
        </el-badge>
        <el-button :disabled="!selectedNotificationIds.length" @click="markSelectedNotificationsRead">批量已读</el-button>
      </template>
      <el-table :data="pagedNotifications" stripe @selection-change="notificationSelection = $event">
        <el-table-column type="selection" width="55" />
        <el-table-column prop="title" label="标题" min-width="180" />
        <el-table-column prop="module" label="模块" width="120">
          <template #default="scope">
            <el-tag v-bind="moduleTagStyle(scope.row.module)">{{ moduleMeta(scope.row.module).label }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="时间" min-width="180">
          <template #default="scope">{{ formatDateTime(scope.row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="状态" width="120">
          <template #default="scope">
            <el-tag type="warning">{{ scope.row.is_read ? '已读' : '未读' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="180">
          <template #default="scope">
            <el-button size="small" @click="openNotification(scope.row)">查看</el-button>
            <el-button size="small" type="primary" @click="markNotificationRead(scope.row.id)">标记已读</el-button>
          </template>
        </el-table-column>
      </el-table>
      <AppPagination
        v-model:page="notificationPagination.page"
        v-model:page-size="notificationPagination.pageSize"
        :total="notificationTotal"
        :page-sizes="pageSizes"
      />
    </PageSection>

    <PageSection v-if="showQualityCosts" title="质量成本提醒" description="财务、经理、董事长首页直接查看最近质量异常成本。">
      <el-table :data="dashboard.quality_costs" stripe>
        <el-table-column prop="quality_no" label="质检单号" width="150" />
        <el-table-column prop="task_no" label="生产单号" width="150" />
        <el-table-column prop="product_name" label="产品" min-width="140" />
        <el-table-column prop="disposition_type" label="类型" width="120" />
        <el-table-column prop="estimated_amount" label="预计成本" width="130">
          <template #default="scope">{{ formatNumber(scope.row.estimated_amount) }}</template>
        </el-table-column>
        <el-table-column prop="recognized_amount" label="确认成本" width="130">
          <template #default="scope">{{ formatNumber(scope.row.recognized_amount) }}</template>
        </el-table-column>
        <el-table-column prop="status_label" label="状态" width="120" />
        <el-table-column prop="created_at" label="创建时间" min-width="180">
          <template #default="scope">{{ formatDateTime(scope.row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="120">
          <template #default="scope">
            <el-button size="small" @click="goQualityCosts(scope.row)">进入台账</el-button>
          </template>
        </el-table-column>
      </el-table>
    </PageSection>

    <el-dialog v-model="notificationDialogVisible" title="通知详情" width="560px">
      <el-descriptions v-if="activeNotification" :column="1" border>
        <el-descriptions-item label="标题">{{ activeNotification.title }}</el-descriptions-item>
        <el-descriptions-item label="模块">{{ moduleMeta(activeNotification.module).label }}</el-descriptions-item>
        <el-descriptions-item label="时间">{{ formatDateTime(activeNotification.created_at) }}</el-descriptions-item>
        <el-descriptions-item label="内容">{{ activeNotification.content || '-' }}</el-descriptions-item>
      </el-descriptions>
      <template #footer>
        <el-button @click="notificationDialogVisible = false">关闭</el-button>
        <el-button type="primary" @click="confirmReadActiveNotification">标记已读</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import 'element-plus/es/components/badge/style/css'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/card/style/css'
import 'element-plus/es/components/col/style/css'
import 'element-plus/es/components/descriptions/style/css'
import 'element-plus/es/components/dialog/style/css'
import 'element-plus/es/components/message/style/css'
import 'element-plus/es/components/notification/style/css'
import 'element-plus/es/components/row/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/tag/style/css'

import { ElMessage } from 'element-plus/es/components/message/index.mjs'
import { ElNotification } from 'element-plus/es/components/notification/index.mjs'

import ElBadge from 'element-plus/es/components/badge/index.mjs'
import ElButton from 'element-plus/es/components/button/index.mjs'
import ElCard from 'element-plus/es/components/card/index.mjs'
import ElCol from 'element-plus/es/components/col/index.mjs'
import ElDescriptions, { ElDescriptionsItem } from 'element-plus/es/components/descriptions/index.mjs'
import ElDialog from 'element-plus/es/components/dialog/index.mjs'
import ElRow from 'element-plus/es/components/row/index.mjs'
import ElTable, { ElTableColumn } from 'element-plus/es/components/table/index.mjs'
import ElTag from 'element-plus/es/components/tag/index.mjs'

import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import AppPagination from '../../components/AppPagination.vue'
import PageSection from '../../components/PageSection.vue'
import { useLocalPagination } from '../../composables/useLocalPagination'
import { useAuthStore } from '../../stores/auth'
import { useDashboardStore } from '../../stores/dashboard'
import { formatDateTime, formatNumber, statusType } from '../../utils/format'
import { getModuleMeta, getModuleNotificationClass, getModuleTagStyle } from '../../utils/modules'

const router = useRouter()
const dashboard = useDashboardStore()
const auth = useAuthStore()

const WORKBENCH_BY_ROLE = {
  sales: [
    { path: '/sales/orders', icon: '📝', label: '销售订单', desc: '录入/编辑/提交客户订单', color: '#67c23a' },
    { path: '/sales/settlements', icon: '💰', label: '销售结算', desc: '收款登记与结算', color: '#b88230' },
    { path: '/sales/returns', icon: '↩️', label: '销售退货', desc: '退货申请与审批', color: '#e6a23c' },
    { path: '/flow-board', icon: '📊', label: '客户订单看板', desc: '财务/生产/质检全维度', color: '#409eff' },
  ],
  finance: [
    { path: '/finance/receivables', icon: '💳', label: '应收管理', desc: '回款跟踪与核销', color: '#b88230' },
    { path: '/finance/payables', icon: '🧾', label: '应付管理', desc: '付款申请与审批', color: '#e6a23c' },
    { path: '/finance/cash-flows', icon: '🏦', label: '现金流', desc: '收支流水与余额', color: '#67c23a' },
    { path: '/finance/reports', icon: '📈', label: '财务报表', desc: '利润/成本分析', color: '#409eff' },
    { path: '/approvals', icon: '✅', label: '审批中心', desc: '待审批单据处理', color: '#f56c6c' },
  ],
  inventory: [
    { path: '/inventory/queues', icon: '📦', label: '待置货/发货', desc: '分批次置货与发货', color: '#409eff' },
    { path: '/inventory/stocks', icon: '🏭', label: '库存查询', desc: '库存余量与预警', color: '#67c23a' },
    { path: '/inventory/transfers', icon: '🔁', label: '移库管理', desc: '仓库间转移与质检', color: '#e6a23c' },
    { path: '/inventory/stock-takes', icon: '🔍', label: '库存盘点', desc: '盘点任务与差异', color: '#f56c6c' },
  ],
  production: [
    { path: '/production/tasks', icon: '🏗️', label: '生产任务', desc: '转任务/领料/完工', color: '#f56c6c' },
    { path: '/production/plans', icon: '🗓️', label: '生产计划', desc: '计划排产与跟踪', color: '#409eff' },
    { path: '/production/tasks', icon: '📋', label: '生产补货需求', desc: '处理原料补货', color: '#e6a23c' },
  ],
  purchase: [
    { path: '/purchases/orders', icon: '🛒', label: '采购订单', desc: '采购下单与审批', color: '#e6a23c' },
    { path: '/purchases/receipts', icon: '🚚', label: '采购收货', desc: '到货登记与质检', color: '#67c23a' },
    { path: '/purchases/settlements', icon: '💳', label: '采购结算', desc: '付款申请与登记', color: '#b88230' },
    { path: '/purchases/orders', icon: '📋', label: '采购补货需求', desc: '处理采购补货', color: '#409eff' },
  ],
  quality: [
    { path: '/quality/tasks', icon: '🔬', label: '质检任务', desc: '来料/完工/移库质检', color: '#909399' },
    { path: '/quality/tasks', icon: '📥', label: '入库申请', desc: '质检合格入库', color: '#67c23a' },
  ],
  manager: [
    { path: '/flow-board', icon: '📊', label: '订单全链看板', desc: '全流程状态总览', color: '#409eff' },
    { path: '/approvals', icon: '✅', label: '审批中心', desc: '待审批单据处理', color: '#f56c6c' },
    { path: '/analytics', icon: '📈', label: '统计分析', desc: '经营指标分析', color: '#67c23a' },
    { path: '/finance/reports', icon: '📑', label: '财务报表', desc: '利润/成本分析', color: '#b88230' },
  ],
  chairman: [
    { path: '/flow-board', icon: '📊', label: '订单全链看板', desc: '全流程状态总览', color: '#409eff' },
    { path: '/approvals', icon: '✅', label: '审批中心', desc: '待审批单据处理', color: '#f56c6c' },
    { path: '/analytics', icon: '📈', label: '统计分析', desc: '经营指标分析', color: '#67c23a' },
    { path: '/admin/users', icon: '👥', label: '组织与用户', desc: '账号与权限管理', color: '#7d54ff' },
  ],
  system_admin: [
    { path: '/admin/users', icon: '👥', label: '组织与用户', desc: '账号与权限管理', color: '#7d54ff' },
    { path: '/admin/module-permissions', icon: '🔐', label: '模块权限', desc: '角色与菜单授权', color: '#409eff' },
    { path: '/admin/logs', icon: '📜', label: '操作日志', desc: '审计与追踪', color: '#909399' },
    { path: '/analytics', icon: '📈', label: '统计分析', desc: '经营指标分析', color: '#67c23a' },
  ],
}

const SUPER_BASE = { inventory_super: 'inventory', quality_super: 'quality', finance_super: 'finance' }
const workbenchEntries = computed(() => {
  const roleCode = auth.user?.role_code || ''
  const base = SUPER_BASE[roleCode] || roleCode
  return WORKBENCH_BY_ROLE[base] || WORKBENCH_BY_ROLE.manager || []
})

const data = dashboard
const todoSelection = ref([])
const notificationSelection = ref([])
const notificationDialogVisible = ref(false)
const activeNotification = ref(null)
const unreadCount = computed(() => dashboard.unreadCount)
const showQualityCosts = computed(() => dashboard.showQualityCosts)
const todoSource = computed(() => data.todos || [])
const notificationSource = computed(() => data.notifications || [])
const {
  pagination: todoPagination,
  total: todoTotal,
  pageSizes,
  pagedItems: pagedTodos,
} = useLocalPagination(todoSource)
const {
  pagination: notificationPagination,
  total: notificationTotal,
  pagedItems: pagedNotifications,
} = useLocalPagination(notificationSource)
const selectedCompletableTodoIds = computed(() => todoSelection.value.filter((item) => !item.can_approve).map((item) => item.id))
const selectedNotificationIds = computed(() => notificationSelection.value.map((item) => item.id))
const shownNotificationIds = new Set()

function moduleMeta(moduleCode) {
  return getModuleMeta(moduleCode)
}

function moduleTagStyle(moduleCode) {
  return getModuleTagStyle(moduleCode)
}

function notificationClass(moduleCode) {
  return getModuleNotificationClass(moduleCode)
}

function selectableTodo(row) {
  return !row.can_approve
}

async function load() {
  try {
    await dashboard.loadSummary({ primeTtlMs: 30 * 1000 })
    showUnreadPopup()
  } catch (error) {
    ElMessage.error(error.message)
  }
}

function showUnreadPopup() {
  data.notifications
    .filter((item) => !shownNotificationIds.has(item.id))
    .slice(0, 3)
    .forEach((item) => {
      const meta = moduleMeta(item.module)
      shownNotificationIds.add(item.id)
      ElNotification({
        title: `[${meta.label}] ${item.title || '未读通知'}`,
        message: item.content || '请尽快处理相关业务。',
        type: item.category === 'warning' ? 'warning' : item.category === 'success' ? 'success' : meta.notificationType,
        customClass: notificationClass(item.module),
        duration: 3500
      })
    })
}

async function completeTodo(id) {
  await dashboard.completeTodo(id)
  ElMessage.success('待办已完成')
}

async function completeSelectedTodos() {
  await dashboard.completeTodos(selectedCompletableTodoIds.value)
  ElMessage.success('已完成所选待办')
}

async function goTodo(todo) {
  await router.push({
    path: todo.action_route || todo.detail_route || '/dashboard',
    query: todo.action_query || todo.detail_query || {}
  })
}

function openNotification(row) {
  activeNotification.value = row
  notificationDialogVisible.value = true
}

async function markNotificationRead(id) {
  await dashboard.toggleNotificationReadState(id, false)
  ElMessage.success('通知已标记为已读')
}

async function confirmReadActiveNotification() {
  if (!activeNotification.value) return
  await markNotificationRead(activeNotification.value.id)
  notificationDialogVisible.value = false
}

async function markSelectedNotificationsRead() {
  await dashboard.markNotificationsRead(selectedNotificationIds.value)
  ElMessage.success('批量已读完成')
}

function goQualityCosts(row = null) {
  const query = {}
  if (row?.id != null) {
    query.focusId = String(row.id)
    query.focusType = 'quality_cost_record'
  }
  router.push({ path: '/finance/quality-costs', query })
}

function handleRealtime() {
  load()
}

onMounted(() => {
  load()
  window.addEventListener('dashboard-realtime', handleRealtime)
})

onBeforeUnmount(() => {
  window.removeEventListener('dashboard-realtime', handleRealtime)
})
</script>

<style scoped>
.dashboard-page { display: flex; flex-direction: column; gap: 16px; }
.kpi-card { min-height: 120px; }
.kpi-label { color: #909399; margin-bottom: 8px; }
.kpi-value { font-size: 28px; font-weight: 700; color: #0f5c90; }
.todo-actions { display: flex; align-items: center; gap: 4px; }

.workbench-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 12px;
}

.workbench-card {
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 12px;
  padding: 16px;
  cursor: pointer;
  transition: all 0.2s;
  background: #fff;
  border-left: 4px solid var(--accent, #409eff);
}

.workbench-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 6px 18px rgba(15, 92, 144, 0.12);
}

.workbench-icon {
  font-size: 26px;
  margin-bottom: 8px;
}

.workbench-name {
  font-size: 15px;
  font-weight: 600;
  color: #303133;
}

.workbench-desc {
  font-size: 12px;
  color: #909399;
  margin-top: 4px;
}
</style>
