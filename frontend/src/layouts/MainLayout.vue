<template>
  <el-container class="layout-shell">
    <el-aside width="248px" class="sidebar">
      <div class="brand">业财一体化系统</div>
      <div class="user-block">
        <div class="user-name">{{ auth.user?.full_name || '未登录' }}</div>
        <div class="user-role">
          {{ auth.user?.role_name || '' }}
          <span v-if="auth.user?.department_name">｜{{ auth.user.department_name }}</span>
        </div>
      </div>
      <NavigationMenu
        :menu-items="menuItems"
        :default-openeds="defaultOpeneds"
        @prefetch="prefetchMenu"
        @select="handleMenuSelect"
      />
    </el-aside>
    <el-drawer
      v-model="mobileMenuVisible"
      direction="ltr"
      :size="'min(86vw, 300px)'"
      :with-header="false"
      class="mobile-menu-drawer"
    >
      <div class="drawer-navigation">
        <NavigationMenu
          :menu-items="menuItems"
          :default-openeds="defaultOpeneds"
          @prefetch="prefetchMenu"
          @select="handleMenuSelect"
        />
      </div>
    </el-drawer>
    <el-container class="content-shell">
      <el-header class="topbar">
        <div class="topbar-heading">
          <el-button class="mobile-menu-button" plain aria-label="Open navigation" @click="mobileMenuVisible = true">&#9776;</el-button>
          <div class="topbar-title-block">
          <div class="page-title">{{ route.meta.title || '首页' }}</div>
          <div class="page-subtitle">{{ nowText }}</div>
          </div>
        </div>
        <div class="top-actions">
          <el-badge :value="todoCount" :hidden="!todoCount">
            <el-button plain @click="router.push('/dashboard')">待办</el-button>
          </el-badge>
          <el-popover placement="bottom-end" :width="360" trigger="click" @show="refreshNotifications">
            <template #reference>
              <el-badge :value="unreadCount" :hidden="!unreadCount">
                <el-button plain>🔔 通知</el-button>
              </el-badge>
            </template>
            <div class="notification-panel">
              <div class="notification-panel-header">
                <span class="notification-panel-title">通知中心</span>
                <el-button v-if="unreadCount" size="small" text type="primary" @click="markAllRead">全部已读</el-button>
              </div>
              <div v-if="!notificationList.length" class="notification-empty">暂无通知</div>
              <div v-else class="notification-list">
                <div v-for="item in notificationList" :key="item.id" class="notification-item" :class="{ 'is-unread': !item.is_read }" @click="openNotification(item)">
                  <div class="notification-item-title">
                    <el-tag size="small" v-bind="moduleTagStyle(item.module)">{{ moduleMeta(item.module).label }}</el-tag>
                    <span class="notification-item-text">{{ item.title }}</span>
                    <span v-if="!item.is_read" class="notification-dot" />
                  </div>
                  <div class="notification-item-content">{{ item.content }}</div>
                  <div class="notification-item-time">{{ formatDateTime(item.created_at) }}</div>
                </div>
              </div>
              <div class="notification-panel-footer">
                <el-button size="small" text @click="goNotifications">查看全部</el-button>
              </div>
            </div>
          </el-popover>
          <el-button plain @click="refreshPage">刷新</el-button>
          <el-dropdown @command="handleCommand">
            <div class="user-trigger">
              <el-avatar size="small">{{ initials }}</el-avatar>
              <span>{{ auth.user?.full_name || '未登录' }}</span>
            </div>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item disabled>{{ auth.user?.role_name || '-' }}</el-dropdown-item>
                <el-dropdown-item divided command="logout">退出登录</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </el-header>

      <el-main class="content-area">
        <el-scrollbar class="content-scroll">
          <div class="content-inner">
            <router-view :key="route.fullPath" />
          </div>
        </el-scrollbar>
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup>
import 'element-plus/es/components/notification/style/css'
import 'element-plus/es/components/avatar/style/css'
import 'element-plus/es/components/badge/style/css'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/container/style/css'
import 'element-plus/es/components/dropdown/style/css'
import 'element-plus/es/components/drawer/style/css'
import 'element-plus/es/components/menu/style/css'
import 'element-plus/es/components/scrollbar/style/css'

import { ElNotification } from 'element-plus/es/components/notification/index.mjs'

import ElAvatar from 'element-plus/es/components/avatar/index.mjs'
import ElBadge from 'element-plus/es/components/badge/index.mjs'
import ElButton from 'element-plus/es/components/button/index.mjs'
import ElContainer, { ElAside, ElHeader, ElMain } from 'element-plus/es/components/container/index.mjs'
import ElDropdown, { ElDropdownItem, ElDropdownMenu } from 'element-plus/es/components/dropdown/index.mjs'
import ElDrawer from 'element-plus/es/components/drawer/index.mjs'
import ElMenu, { ElMenuItem, ElSubMenu } from 'element-plus/es/components/menu/index.mjs'
import ElScrollbar from 'element-plus/es/components/scrollbar/index.mjs'

import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { post } from '../api/client'
import { preloadCommonDataForUser, preloadCommonRoutesForUser, preloadRouteComponent, warmRouteData } from '../router'
import { useAuthStore } from '../stores/auth'
import { useDashboardStore } from '../stores/dashboard'
import { getModuleMeta, getModuleNotificationClass, getModuleTagStyle } from '../utils/modules'
import { formatDateTime } from '../utils/format'
import NavigationMenu from '../components/NavigationMenu.vue'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const dashboard = useDashboardStore()

const menuItems = computed(() => auth.menuItems || [])
const defaultOpeneds = computed(() => menuItems.value.filter((item) => item.children?.length).map((item) => item.path))
const unreadCount = computed(() => dashboard.unreadCount)
const todoCount = computed(() => dashboard.todoCount)
const notificationList = computed(() => dashboard.notifications || [])

function moduleMeta(moduleCode) {
  return getModuleMeta(moduleCode)
}

function moduleTagStyle(moduleCode) {
  return getModuleTagStyle(moduleCode)
}

function refreshNotifications() {
  void dashboard.loadSummary({ primeTtlMs: 0, force: true })
}

function openNotification(item) {
  router.push('/notifications')
  void markNotificationsScoped([item.id])
}

function goNotifications() {
  router.push('/notifications')
}

// 从弹窗跳到通知页时,顺手同步未读(不强制刷新列表)
async function markNotificationsScoped(ids) {
  if (!ids.length) return
  try {
    await post('/api/dashboard/notifications/batch-read', { ids })
    dashboard.loadSummary({ primeTtlMs: 0, force: true })
  } catch {
  }
}

async function markAllRead() {
  const ids = notificationList.value.filter((item) => !item.is_read).map((item) => item.id)
  if (!ids.length) return
  await dashboard.markNotificationsRead(ids)
}
const nowText = ref('')
const mobileMenuVisible = ref(false)

let refreshTimer = null
let clockTimer = null
let pingTimer = null
let reconnectTimer = null
let socket = null
let shouldReconnect = true

const initials = computed(() => {
  const name = auth.user?.full_name || '业财'
  return name.slice(-2)
})

function updateNowText() {
  nowText.value = new Date().toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' })
}

function prefetchMenu(path) {
  void preloadRouteComponent(path)
  void warmRouteData(path)
}

async function loadSummary() {
  if (!auth.isLoggedIn) {
    dashboard.reset()
    return
  }
  try {
    await dashboard.loadSummary({ primeTtlMs: 30 * 1000 })
  } catch {
    dashboard.reset()
  }
}

function buildRealtimeUrl() {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${protocol}//${window.location.host}/api/realtime/ws`
}

function notifyRealtime(detail) {
  window.dispatchEvent(new CustomEvent('dashboard-realtime', { detail }))
}

function notificationClass(moduleCode) {
  return getModuleNotificationClass(moduleCode)
}

function resolveNotificationType(detail, meta) {
  if (detail?.category === 'success') return 'success'
  if (detail?.category === 'warning') return 'warning'
  if (detail?.category === 'error') return 'error'
  return meta.notificationType || 'info'
}

function clearRealtimeTimers() {
  if (pingTimer) clearInterval(pingTimer)
  pingTimer = null
  if (reconnectTimer) clearTimeout(reconnectTimer)
  reconnectTimer = null
}

function closeSocket() {
  clearRealtimeTimers()
  if (socket) {
    socket.close()
    socket = null
  }
}

function connectRealtime() {
  if (!auth.isLoggedIn || !shouldReconnect) return
  if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) return

  socket = new WebSocket(buildRealtimeUrl())

  socket.onopen = () => {
    pingTimer = setInterval(() => {
      if (socket?.readyState === WebSocket.OPEN) {
        socket.send('ping')
      }
    }, 30000)
  }

  socket.onmessage = async (event) => {
    try {
      const payload = JSON.parse(event.data)
      const detail = payload.data || {}
      if (!detail.type || detail.type === 'connected' || detail.type === 'pong') return

      notifyRealtime(detail)
      await loadSummary()

      if (detail.type === 'notification_created') {
        const meta = getModuleMeta(detail.module)
        ElNotification({
          title: `[${meta.label}] ${detail.title || '新通知'}`,
          message: detail.content || '请及时处理相关业务。',
          type: resolveNotificationType(detail, meta),
          customClass: notificationClass(detail.module),
          duration: 3500
        })
      }
    } catch {
    }
  }

  socket.onclose = () => {
    clearRealtimeTimers()
    if (shouldReconnect && auth.isLoggedIn) {
      reconnectTimer = setTimeout(connectRealtime, 3000)
    }
  }

  socket.onerror = () => {
    if (socket?.readyState !== WebSocket.CLOSED) {
      socket.close()
    }
  }
}

async function logout() {
  shouldReconnect = false
  closeSocket()
  try {
    await post('/api/auth/logout', {})
  } catch {
  }
  auth.logout()
  dashboard.reset()
  router.push('/login')
}

function refreshPage() {
  window.location.reload()
}

function handleCommand(command) {
  if (command === 'logout') {
    logout()
  }
}

function handleMenuSelect() {
  mobileMenuVisible.value = false
}

watch(() => route.path, () => {
  mobileMenuVisible.value = false
})

onMounted(async () => {
  try {
    await auth.bootstrapSession()
  } catch {
    auth.logout()
    router.push('/login')
    return
  }

  updateNowText()
  await loadSummary()
  preloadCommonDataForUser(auth.user, { exclude: [route.path] })
  preloadCommonRoutesForUser(auth.user, { exclude: [route.path] })
  connectRealtime()

  refreshTimer = setInterval(loadSummary, 5 * 60 * 1000)
  clockTimer = setInterval(updateNowText, 60 * 1000)
})

onBeforeUnmount(() => {
  shouldReconnect = false
  if (refreshTimer) clearInterval(refreshTimer)
  if (clockTimer) clearInterval(clockTimer)
  closeSocket()
})
</script>

<style scoped>
.layout-shell {
  height: 100dvh;
  overflow: hidden;
}

.sidebar {
  height: 100dvh;
  background: #185f93;
  color: #fff;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  box-shadow: 8px 0 24px rgba(24, 95, 147, 0.18);
}

.brand {
  font-size: 22px;
  font-weight: 700;
  padding: 26px 22px 14px;
  letter-spacing: 0.5px;
  color: #f5fbff;
}

.user-block {
  padding: 0 22px 18px;
  color: #d8edf9;
}

.user-name {
  font-size: 17px;
  font-weight: 600;
  margin-bottom: 4px;
}

.user-role {
  font-size: 14px;
}

.menu-scroll {
  flex: 1;
  min-height: 0;
}

.menu {
  border-right: none;
  font-size: 15.5px;
}

:deep(.el-menu-item),
:deep(.el-sub-menu__title) {
  height: 50px;
  line-height: 50px;
  font-size: 15.5px;
  border-radius: 10px;
  margin: 4px 10px;
}

:deep(.el-menu-item:hover),
:deep(.el-sub-menu__title:hover) {
  background: rgba(255, 255, 255, 0.12) !important;
}

:deep(.el-menu-item.is-active) {
  background: rgba(255, 255, 255, 0.18) !important;
  box-shadow: inset 4px 0 0 #ffffff;
}

.content-shell {
  height: 100dvh;
  overflow: hidden;
}

.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: #fff;
  border-bottom: 1px solid #ebeef5;
  padding: 0 24px;
  flex-shrink: 0;
}

.page-title {
  font-size: 23px;
  font-weight: 700;
  color: #303133;
}

.page-subtitle {
  font-size: 13px;
  color: #909399;
  margin-top: 4px;
}

.top-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

.user-trigger {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
}

.content-area {
  padding: 0;
  background: #f3f8fc;
  min-height: 0;
  overflow: hidden;
}

.content-scroll {
  height: 100%;
}

.content-inner {
  min-height: 100%;
  padding: 22px;
  box-sizing: border-box;
}

:global(body) {
  font-size: 15.5px;
  color: #1f2f3d;
}

:global(.el-table) {
  font-size: 15px;
}

:global(.el-button) {
  font-size: 14.5px;
}

:global(.module-notification .el-notification__title),
:global(.module-notification .el-notification__content) {
  color: #303133;
}

.mobile-menu-button {
  display: none;
}

.topbar-heading {
  display: flex;
  align-items: center;
  min-width: 0;
}

.topbar-title-block {
  min-width: 0;
}

.mobile-menu-drawer :deep(.el-drawer__body) {
  padding: 0;
  background: #185f93;
}

.drawer-navigation {
  height: 100%;
  display: flex;
  flex-direction: column;
  background: #185f93;
}

@media (max-width: 768px) {
  .sidebar {
    display: none;
  }

  .topbar {
    min-height: 64px;
    height: auto !important;
    padding: 8px 12px;
    gap: 8px;
    flex-wrap: wrap;
  }

  .topbar-heading {
    flex: 1 1 180px;
    gap: 8px;
  }

  .mobile-menu-button {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    min-width: 40px;
    min-height: 40px;
    padding: 8px;
    flex-shrink: 0;
  }

  .page-title {
    font-size: 18px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .page-subtitle {
    max-width: 100%;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .top-actions {
    flex: 1 1 100%;
    justify-content: flex-end;
    gap: 6px;
    flex-wrap: wrap;
  }

  .top-actions .el-button {
    min-height: 40px;
    padding: 8px 10px;
  }

  .user-trigger {
    min-height: 40px;
  }

  .user-trigger > span {
    max-width: 84px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .content-inner {
    padding: 12px;
  }
}

@media (max-width: 420px) {
  .top-actions {
    justify-content: space-between;
  }

  .top-actions .el-button {
    padding-left: 8px;
    padding-right: 8px;
  }
}

.notification-panel {
  max-height: 420px;
  display: flex;
  flex-direction: column;
}

.notification-panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 4px 4px 10px;
  border-bottom: 1px solid var(--el-border-color-lighter);
}

.notification-panel-title {
  font-weight: 600;
  font-size: 14px;
}

.notification-list {
  flex: 1;
  overflow-y: auto;
  padding: 4px 0;
}

.notification-item {
  padding: 10px 8px;
  border-bottom: 1px solid var(--el-border-color-lighter);
  cursor: pointer;
  border-radius: 8px;
  transition: background-color 0.15s;
}

.notification-item:hover {
  background-color: var(--el-fill-color-light);
}

.notification-item.is-unread {
  background-color: rgba(64, 158, 255, 0.05);
}

.notification-item-title {
  display: flex;
  align-items: center;
  gap: 6px;
}

.notification-item-text {
  font-size: 13px;
  font-weight: 500;
  color: var(--el-text-color-primary);
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.notification-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #f56c6c;
  flex-shrink: 0;
}

.notification-item-content {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-top: 4px;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.notification-item-time {
  font-size: 11px;
  color: var(--el-text-color-placeholder);
  margin-top: 4px;
}

.notification-empty {
  text-align: center;
  color: var(--el-text-color-placeholder);
  padding: 24px 0;
  font-size: 13px;
}

.notification-panel-footer {
  border-top: 1px solid var(--el-border-color-lighter);
  padding-top: 8px;
  text-align: center;
}
</style>
