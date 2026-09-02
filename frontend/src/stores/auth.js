import { defineStore } from 'pinia'

import { clearGetCache, get, post } from '../api/client'
import { clearStoredSession, hasStoredSession, markStoredSession, readStoredUser, writeStoredUser } from '../utils/session'
import { useDashboardStore } from './dashboard'

export const useAuthStore = defineStore('auth', {
  state: () => ({
    user: readStoredUser(),
    hasSession: hasStoredSession(),
    checkedSession: false
  }),
  getters: {
    isLoggedIn: (state) => state.hasSession,
    menuItems: (state) => state.user?.navigation || [],
    // 超级角色映射:inventory_super 视为 inventory,以此类推
    isRole: (state) => (base) => {
      const code = state.user?.role_code || ''
      if (code === base) return true
      const map = { inventory_super: 'inventory', quality_super: 'quality', finance_super: 'finance' }
      return map[code] === base
    }
  },
  actions: {
    async login(credentials) {
      const data = await post('/api/auth/login', credentials)
      clearGetCache()
      this.user = data.user
      this.hasSession = true
      this.checkedSession = true
      markStoredSession(data.user)
      return data
    },
    async fetchProfile() {
      const user = await get('/api/auth/me')
      this.user = user
      this.hasSession = true
      writeStoredUser(user)
      markStoredSession(user)
      return user
    },
    async bootstrapSession(force = false) {
      if (this.checkedSession && !force) {
        return this.user
      }
      try {
        await this.fetchProfile()
      } catch {
        this.user = null
        this.hasSession = false
        clearStoredSession()
      } finally {
        this.checkedSession = true
      }
      return this.user
    },
    logout() {
      const dashboard = useDashboardStore()
      clearGetCache()
      this.user = null
      this.hasSession = false
      this.checkedSession = true
      clearStoredSession()
      dashboard.reset()
    }
  }
})

