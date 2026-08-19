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
import { useDashboardStore } from '../../stores/dashboard'
import { formatDateTime, formatNumber, statusType } from '../../utils/format'
import { getModuleMeta, getModuleNotificationClass, getModuleTagStyle } from '../../utils/modules'

const router = useRouter()
const dashboard = useDashboardStore()

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
</style>
