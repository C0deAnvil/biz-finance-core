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
      <el-scrollbar class="menu-scroll">
        <el-menu
          :default-active="route.path"
          :default-openeds="defaultOpeneds"
          router
          unique-opened
          class="menu"
          background-color="transparent"
          text-color="#e8f7ff"
          active-text-color="#ffffff"
        >
          <template v-for="item in menuItems" :key="item.path">
            <el-sub-menu v-if="item.children?.length" :index="item.path">
              <template #title>
                <span>{{ item.title }}</span>
              </template>
              <el-menu-item
                v-for="child in item.children"
                :key="child.path"
                :index="child.path"
                @mouseenter="prefetchMenu(child.path)"
              >
                <span>{{ child.title }}</span>
              </el-menu-item>
            </el-sub-menu>
            <el-menu-item
              v-else
              :index="item.path"
              @mouseenter="prefetchMenu(item.path)"
            >
              <span>{{ item.title }}</span>
            </el-menu-item>
          </template>
        </el-menu>
      </el-scrollbar>
    </el-aside>

    <el-container class="content-shell">
      <el-header class="topbar">
        <div>
          <div class="page-title">{{ route.meta.title || '首页' }}</div>
          <div class="page-subtitle">{{ nowText }}</div>
        </div>
        <div class="top-actions">
          <el-badge :value="todoCount" :hidden="!todoCount">
            <el-button plain @click="router.push('/dashboard')">待办</el-button>
          </el-badge>
          <el-badge :value="unreadCount" :hidden="!unreadCount">
            <el-button plain @click="router.push('/dashboard')">通知</el-button>
          </el-badge>
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
import 'element-plus/es/components/menu/style/css'
import 'element-plus/es/components/scrollbar/style/css'

import { ElNotification } from 'element-plus/es/components/notification/index.mjs'

import ElAvatar from 'element-plus/es/components/avatar/index.mjs'
import ElBadge from 'element-plus/es/components/badge/index.mjs'
import ElButton from 'element-plus/es/components/button/index.mjs'
import ElContainer, { ElAside, ElHeader, ElMain } from 'element-plus/es/components/container/index.mjs'
import ElDropdown, { ElDropdownItem, ElDropdownMenu } from 'element-plus/es/components/dropdown/index.mjs'
import ElMenu, { ElMenuItem, ElSubMenu } from 'element-plus/es/components/menu/index.mjs'
import ElScrollbar from 'element-plus/es/components/scrollbar/index.mjs'

import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { post } from '../api/client'
import { preloadCommonDataForUser, preloadCommonRoutesForUser, preloadRouteComponent, warmRouteData } from '../router'
import { useAuthStore } from '../stores/auth'
import { useDashboardStore } from '../stores/dashboard'
import { getModuleMeta, getModuleNotificationClass } from '../utils/modules'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const dashboard = useDashboardStore()

const menuItems = computed(() => auth.menuItems || [])
const defaultOpeneds = computed(() => menuItems.value.filter((item) => item.children?.length).map((item) => item.path))
const unreadCount = computed(() => dashboard.unreadCount)
const todoCount = computed(() => dashboard.todoCount)
const nowText = ref('')

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
  height: 100vh;
  overflow: hidden;
}

.sidebar {
  height: 100vh;
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
  height: 100vh;
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
</style>
