<template>
  <div class="notifications-page">
    <PageSection title="通知中心" :description="desc">
      <!-- 工具栏 -->
      <div class="notif-toolbar">
        <div class="tabs">
          <span class="tab" :class="{ active: read === null }" @click="setRead(null, 1)">全部<em v-if="read === null">{{ total }}</em></span>
          <span class="tab" :class="{ active: read === 'unread' }" @click="setRead('unread', 1)">
            未读<em class="unread" v-if="read === 'unread'">{{ total }}</em>
          </span>
          <span class="tab" :class="{ active: read === 'read' }" @click="setRead('read', 1)">已读</span>
        </div>

        <div class="module-chips">
          <button class="mchip" :class="{ on: !module }" @click="setModule(null)">全部模块</button>
          <button
            v-for="code in MODULE_CODES"
            :key="code"
            class="mchip"
            :style="mchipStyle(code)"
            :class="{ on: module === code }"
            @click="setModule(code)"
          >
            {{ moduleLabel(code) }}
          </button>
        </div>

        <div class="toolbar-right">
          <span class="unread-summary" v-if="unreadCount > 0">未读 {{ unreadCount }}</span>
          <el-button size="small" type="primary" plain :disabled="!unreadCount" @click="markAllRead">全部已读</el-button>
        </div>
      </div>

      <!-- 列表 -->
      <div v-loading="loading" class="notif-list">
        <el-empty v-if="!loading && !items.length" description="暂无通知" />
        <div
          v-for="item in items"
          :key="item.id"
          class="notif-item"
          :class="{ unread: !item.is_read }"
          @click="toggleRead(item)"
          :title="item.is_read ? '点击标为未读' : '点击标为已读'"
        >
          <span class="notif-dot" v-if="!item.is_read"></span>
          <div class="notif-main">
            <div class="notif-title">
              <span class="notif-module" :style="tagStyle(item.module)">{{ moduleLabel(item.module) }}</span>
              <span class="notif-title-text" :class="{ bold: !item.is_read }">{{ item.title || '系统通知' }}</span>
            </div>
            <div class="notif-content">{{ item.content }}</div>
          </div>
          <div class="notif-meta">
            <span class="notif-time">{{ formatDateTime(item.created_at) }}</span>
            <span class="notif-status">{{ item.is_read ? '已读' : '未读' }}</span>
          </div>
        </div>
        <AppPagination
          :total="total"
          :page="page"
          :page-size="pageSize"
          :page-sizes="[15, 30, 50, 100]"
          layout="total, prev, pager, next"
          @change="onPageChange"
        />
      </div>
    </PageSection>
  </div>
</template>

<script setup>
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/empty/style/css'

import ElButton from 'element-plus/es/components/button/index.mjs'
import ElEmpty from 'element-plus/es/components/empty/index.mjs'

import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import { get, post } from '../api/client'
import AppPagination from '../components/AppPagination.vue'
import PageSection from '../components/PageSection.vue'
import { formatDateTime } from '../utils/format'
import { getModuleMeta } from '../utils/modules'

const MODULE_CODES = ['sales', 'purchase', 'inventory', 'production', 'quality', 'finance', 'admin', 'dashboard', 'analytics']

const loading = ref(false)
const items = ref([])
const total = ref(0)
const unreadCount = ref(0)
const page = ref(1)
const pageSize = ref(15)
const read = ref(null) // null | read | unread
const module = ref(null)

const desc = computed(() => {
  const parts = [`共 ${total.value} 条`]
  if (read.value === 'unread') parts.push('未读筛选')
  if (module.value) parts.push(`${moduleLabel(module.value)}模块`)
  return parts.join(' · ')
})

function moduleLabel(code) {
  return getModuleMeta(code).label
}
function tagStyle(code) {
  const accent = getModuleMeta(code).accent
  return {
    color: accent,
    backgroundColor: `${accent}1a`,
    borderColor: `${accent}44`
  }
}
function mchipStyle(code) {
  const accent = getModuleMeta(code).accent
  return { '--mchip-accent': accent }
}

function setRead(value) {
  read.value = value
  load()
}
function setModule(code) {
  module.value = module.value === code ? null : code
  load()
}
function onPageChange({ page: p }) {
  page.value = p
  load()
}

async function load() {
  loading.value = true
  try {
    const params = { page: page.value, page_size: pageSize.value }
    if (read.value) params.read = read.value
    if (module.value) params.module = module.value
    const data = await get('/api/dashboard/notifications', { params })
    items.value = data.items || []
    total.value = (data.pagination || {}).total || 0
    unreadCount.value = data.unread_count || 0
  } finally {
    loading.value = false
  }
}

function itemIndex(id) {
  return items.value.findIndex((item) => item.id === id)
}

async function toggleRead(item) {
  const nextUnread = item.is_read ? 'unread' : 'read'
  try {
    await post(`/api/dashboard/notifications/${item.id}/${nextUnread}`, {})
  } catch {
    return
  }
  item.is_read = !item.is_read
  unreadCount.value = Math.max(0, unreadCount.value + (item.is_read ? -1 : 1))
}

async function markAllRead() {
  const ids = items.value.filter((item) => !item.is_read).map((item) => item.id)
  if (!ids.length) return
  await post('/api/dashboard/notifications/batch-read', { ids })
  if (read.value === 'unread') {
    load()
  } else {
    items.value.forEach((item) => {
      item.is_read = true
    })
    unreadCount.value = 0
  }
}

function handleRealtime(event) {
  const detail = event.detail || {}
  const type = detail.type
  if (!type) return
  if (type === 'notification_created') {
    // 仅当正在查看全部/未读时上前插;其余情况重新拉当前页兜底
    if (read.value === 'unread' || read.value === null) {
      if (page.value === 1) load()
    } else {
      load()
    }
  } else if (type === 'notification_read' || type === 'notification_unread') {
    const idx = itemIndex(detail.id)
    if (idx >= 0) {
      const isRead = type === 'notification_read'
      items.value[idx].is_read = isRead
      unreadCount.value = Math.max(0, unreadCount.value + (isRead ? -1 : 1))
    }
  }
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
.notifications-page { padding: 4px 2px; }

.notif-toolbar { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; padding-bottom: 14px; }

.tabs { display: flex; gap: 2px; background: #f2f4f7; border-radius: 10px; padding: 3px; }
.tab { padding: 5px 16px; border-radius: 8px; font-size: 13px; color: #5c6b7a; cursor: pointer; }
.tab em { font-style: normal; font-size: 11px; margin-left: 4px; color: #9aa7b4; }
.tab em.unread { color: #e23b3b; }
.tab.active { background: #fff; color: #185f93; font-weight: 600; box-shadow: 0 1px 4px rgba(31,47,61,.1); }

.module-chips { display: flex; gap: 8px; flex-wrap: wrap; }
.mchip {
  border: 1px solid #e2e8f0; background: #fff; color: #5c6b7a;
  padding: 4px 12px; border-radius: 14px; font-size: 12px; cursor: pointer; transition: all .15s;
}
.mchip.on { border-color: var(--mchip-accent, #185f93); color: var(--mchip-accent, #185f93); background: color-mix(in srgb, var(--mchip-accent, #185f93) 8%, #fff); font-weight: 600; }

.toolbar-right { margin-left: auto; display: flex; align-items: center; gap: 10px; }
.unread-summary { font-size: 12px; color: #e23b3b; font-weight: 600; }

.notif-list { min-height: 120px; }
.notif-item { display: flex; gap: 12px; align-items: flex-start; padding: 13px 14px; border: 1px solid #edf1f6; border-radius: 12px; margin-bottom: 10px; cursor: pointer; transition: all .15s; background: #fff; }
.notif-item:hover { border-color: #c5d4e6; box-shadow: 0 4px 14px rgba(31,47,61,.06); }
.notif-item.unread { border-color: #cfe3ff; background: #f8fbff; }
.notif-dot { width: 8px; height: 8px; border-radius: 50%; background: #409eff; margin-top: 6px; flex-shrink: 0; }
.notif-main { flex: 1; min-width: 0; }
.notif-title { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.notif-module { font-size: 11px; padding: 1px 8px; border-radius: 10px; border: 1px solid; }
.notif-title-text { font-size: 14px; color: #303133; }
.notif-title-text.bold { font-weight: 700; }
.notif-content { margin-top: 6px; font-size: 13px; color: #6b7a8a; line-height: 1.6; word-break: break-word; }
.notif-meta { display: flex; flex-direction: column; align-items: flex-end; gap: 6px; flex-shrink: 0; }
.notif-time { font-size: 12px; color: #9aa7b4; white-space: nowrap; }
.notif-status { font-size: 11px; color: #a3b0bd; }
.notif-item.unread .notif-status { color: #409eff; font-weight: 600; }

@media (max-width: 640px) {
  .toolbar-right { margin-left: 0; }
  .notif-meta { flex-direction: row; align-items: center; }
}
</style>