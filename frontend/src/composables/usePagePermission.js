import { computed } from 'vue'
import { useRoute } from 'vue-router'

import { useAuthStore } from '../stores/auth'

const ROLE_ACTIONS = {
  manager: {
    '/dashboard': ['approve'],
    '/approvals': ['approve'],
    '/sales/returns': ['approve'],
    '/purchases/approvals': ['approve'],
    '/production/approvals': ['approve']
  },
  chairman: {
    '/dashboard': ['approve'],
    '/approvals': ['approve'],
    '/sales/approvals': ['approve'],
    '/sales/returns': ['approve']
  }
}

function normalizePath(path) {
  if (!path) return ''
  return path.startsWith('/') ? path : `/${path}`
}

export function usePagePermission(pathOverride) {
  const route = useRoute()
  const auth = useAuthStore()

  const activePath = computed(() => normalizePath(pathOverride || route.path))
  const roleCode = computed(() => auth.user?.role_code || '')
  const roleActions = computed(() => ROLE_ACTIONS[roleCode.value] || null)
  const allowedActions = computed(() => new Set(roleActions.value?.[activePath.value] || []))
  const isRestrictedRole = computed(() => Boolean(roleActions.value))
  const isReadOnlyPage = computed(() => isRestrictedRole.value && allowedActions.value.size === 0)

  function can(action) {
    if (!action) return true
    if (!isRestrictedRole.value) return true
    return allowedActions.value.has(action)
  }

  function canAny(actions = []) {
    return actions.some((action) => can(action))
  }

  return {
    activePath,
    roleCode,
    allowedActions,
    isRestrictedRole,
    isReadOnlyPage,
    can,
    canAny
  }
}
