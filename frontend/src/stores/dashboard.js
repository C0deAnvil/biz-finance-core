import { defineStore } from 'pinia'

import { get, post, primeGetCache } from '../api/client'

const DASHBOARD_CACHE_KEY = '/api/dashboard'
const DEFAULT_TTL_MS = 30 * 1000

export const useDashboardStore = defineStore('dashboard', {
  state: () => ({
    roleCode: '',
    kpis: {},
    todos: [],
    notifications: [],
    quality_costs: [],
    loaded: false,
    lastLoadedAt: null
  }),
  getters: {
    unreadCount: (state) => state.notifications.filter((item) => !item.is_read).length,
    todoCount: (state) => state.todos.filter((item) => item.status !== 'completed').length,
    showQualityCosts: (state) => ['finance', 'manager', 'chairman'].includes(state.roleCode)
  },
  actions: {
    applyPayload(payload, { primeTtlMs = DEFAULT_TTL_MS } = {}) {
      this.roleCode = payload?.role_code || ''
      this.kpis = payload?.kpis || {}
      this.todos = payload?.todos || []
      this.notifications = payload?.notifications || []
      this.quality_costs = payload?.quality_costs || []
      this.loaded = true
      this.lastLoadedAt = new Date().toISOString()
      if (primeTtlMs > 0) {
        primeGetCache(DASHBOARD_CACHE_KEY, payload, { ttlMs: primeTtlMs })
      }
      return payload
    },
    async loadSummary(options = {}) {
      const payload = await get(DASHBOARD_CACHE_KEY, { force: options.force === true })
      return this.applyPayload(payload, { primeTtlMs: options.primeTtlMs ?? DEFAULT_TTL_MS })
    },
    async completeTodo(id) {
      await post(`/api/dashboard/todos/${id}/done`, {})
      return this.loadSummary({ primeTtlMs: 0, force: true })
    },
    async completeTodos(ids) {
      await post('/api/dashboard/todos/batch-done', { ids })
      return this.loadSummary({ primeTtlMs: 0, force: true })
    },
    async toggleNotificationReadState(id, isRead) {
      const endpoint = isRead ? 'unread' : 'read'
      await post(`/api/dashboard/notifications/${id}/${endpoint}`, {})
      return this.loadSummary({ primeTtlMs: 0, force: true })
    },
    async markNotificationsRead(ids) {
      await post('/api/dashboard/notifications/batch-read', { ids })
      return this.loadSummary({ primeTtlMs: 0, force: true })
    },
    async processApproval(approvalId, decision, payload = {}) {
      const action = decision === 'rejected' ? 'reject' : 'approve'
      await post(`/api/approvals/${approvalId}/${action}`, payload)
      return this.loadSummary({ primeTtlMs: 0, force: true })
    },
    reset() {
      this.roleCode = ''
      this.kpis = {}
      this.todos = []
      this.notifications = []
      this.quality_costs = []
      this.loaded = false
      this.lastLoadedAt = null
    }
  }
})
