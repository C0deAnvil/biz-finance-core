<template>
  <div>
    <PageSection title="系统日志" description="系统管理员可查看跨模块关键操作日志，用于审计与追踪。">
      <template #actions>
        <el-button @click="load">刷新</el-button>
        <el-button @click="exportLogs">导出日志</el-button>
      </template>

      <el-row :gutter="12" class="filters-row">
        <el-col :span="8">
          <el-select v-model="filters.module" clearable placeholder="筛选模块" style="width: 100%">
            <el-option v-for="item in modules" :key="item" :label="item" :value="item" />
          </el-select>
        </el-col>
        <el-col :span="16">
          <el-input v-model="filters.keyword" clearable placeholder="搜索操作内容、动作或人员" />
        </el-col>
      </el-row>

      <el-table :data="pagedLogRows" stripe>
        <el-table-column prop="created_at" label="时间" min-width="180">
          <template #default="scope">{{ formatDateTime(scope.row.created_at) }}</template>
        </el-table-column>
        <el-table-column prop="user_name" label="操作人" width="140" />
        <el-table-column prop="module" label="模块" width="140" />
        <el-table-column prop="action_label" label="动作" width="160">
          <template #default="scope">{{ scope.row.action_label || actionLabel(scope.row.action) }}</template>
        </el-table-column>
        <el-table-column prop="content" label="内容" min-width="320" show-overflow-tooltip />
      </el-table>
      <AppPagination
        v-model:page="logPagination.page"
        v-model:page-size="logPagination.pageSize"
        :total="logTotal"
        :page-sizes="pageSizes"
      />
    </PageSection>

    <PageSection title="待办总览" description="查看全局待办，用于确认任务分派与处理进度。">
      <template #actions>
        <el-button @click="exportTodos">导出待办</el-button>
      </template>
      <el-table :data="pagedTodoRows" stripe>
        <el-table-column prop="title" label="标题" min-width="220" />
        <el-table-column prop="module" label="模块" width="140" />
        <el-table-column prop="assigned_user" label="负责人" width="140" />
        <el-table-column prop="status" label="状态" width="120">
          <template #default="scope">
            <el-tag :type="statusType(scope.row.status)">{{ scope.row.status_label || statusLabel(scope.row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="创建时间" min-width="180">
          <template #default="scope">{{ formatDateTime(scope.row.created_at) }}</template>
        </el-table-column>
      </el-table>
      <AppPagination
        v-model:page="todoPagination.page"
        v-model:page-size="todoPagination.pageSize"
        :total="todoTotal"
        :page-sizes="pageSizes"
      />
    </PageSection>

    <PageSection title="通知总览" description="查看系统通知的下发与阅读状态。">
      <template #actions>
        <el-button @click="exportNotifications">导出通知</el-button>
      </template>
      <el-table :data="pagedNotificationRows" stripe>
        <el-table-column prop="created_at" label="时间" min-width="180">
          <template #default="scope">{{ formatDateTime(scope.row.created_at) }}</template>
        </el-table-column>
        <el-table-column prop="user_name" label="接收人" width="140" />
        <el-table-column prop="module" label="模块" width="120" />
        <el-table-column prop="title" label="标题" min-width="180" />
        <el-table-column prop="content" label="内容" min-width="260" show-overflow-tooltip />
        <el-table-column prop="is_read" label="状态" width="100">
          <template #default="scope">
            <el-tag :type="scope.row.is_read ? 'success' : 'warning'">{{ scope.row.is_read ? '已读' : '未读' }}</el-tag>
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
  </div>
</template>

<script setup>
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/col/style/css'
import 'element-plus/es/components/input/style/css'
import 'element-plus/es/components/row/style/css'
import 'element-plus/es/components/select/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/tag/style/css'

import ElButton from 'element-plus/es/components/button/index.mjs'
import ElCol from 'element-plus/es/components/col/index.mjs'
import ElInput from 'element-plus/es/components/input/index.mjs'
import ElRow from 'element-plus/es/components/row/index.mjs'
import ElSelect, { ElOption } from 'element-plus/es/components/select/index.mjs'
import ElTable, { ElTableColumn } from 'element-plus/es/components/table/index.mjs'
import ElTag from 'element-plus/es/components/tag/index.mjs'

import { computed, onMounted, reactive, ref } from 'vue'

import { get } from '../../api/client'
import AppPagination from '../../components/AppPagination.vue'
import PageSection from '../../components/PageSection.vue'
import { useLocalPagination } from '../../composables/useLocalPagination'
import { exportRowsToCsv } from '../../utils/export'
import { actionLabel, formatDateTime, statusLabel, statusType } from '../../utils/format'

const logs = ref([])
const todos = ref([])
const notifications = ref([])
const filters = reactive({
  module: '',
  keyword: ''
})

const modules = computed(() => [...new Set(logs.value.map((item) => item.module).filter(Boolean))])

const filteredLogs = computed(() => {
  const keyword = filters.keyword.trim().toLowerCase()
  return logs.value.filter((item) => {
    if (filters.module && item.module !== filters.module) return false
    if (!keyword) return true
    return [item.user_name, item.action, item.content]
      .filter(Boolean)
      .some((value) => String(value).toLowerCase().includes(keyword))
  })
})
const {
  pagination: logPagination,
  total: logTotal,
  pageSizes,
  pagedItems: pagedLogRows
} = useLocalPagination(filteredLogs)
const {
  pagination: todoPagination,
  total: todoTotal,
  pagedItems: pagedTodoRows
} = useLocalPagination(todos)
const {
  pagination: notificationPagination,
  total: notificationTotal,
  pagedItems: pagedNotificationRows
} = useLocalPagination(notifications)

async function load() {
  const [logData, todoData, notificationData] = await Promise.all([
    get('/api/admin/logs'),
    get('/api/admin/todos'),
    get('/api/admin/notifications')
  ])
  logs.value = logData
  todos.value = todoData
  notifications.value = notificationData
}

function exportLogs() {
  exportRowsToCsv('operation_logs', filteredLogs.value)
}

function exportTodos() {
  exportRowsToCsv('admin_todos', todos.value)
}

function exportNotifications() {
  exportRowsToCsv('admin_notifications', notifications.value)
}

onMounted(load)
</script>

<style scoped>
.filters-row {
  margin-bottom: 16px;
}
</style>
