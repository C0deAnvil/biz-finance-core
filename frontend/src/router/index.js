import { createRouter, createWebHistory } from 'vue-router'

import { pinia } from '../stores'
import { useAuthStore } from '../stores/auth'
import { APP_TITLE, buildAppChildRoutes, hiddenPagePaths, rolePreloadPaths, routeDataWarmers, viewLoaders } from './route-config'
import { prefetchGet } from '../api/client'

const preloadedPaths = new Set()

function normalizePath(path) {
  if (!path) return ''
  return path.startsWith('/') ? path : `/${path}`
}

function uniquePaths(paths) {
  return [...new Set(paths.filter(Boolean))]
}

function flattenMenuPaths(items = []) {
  return items.flatMap((item) => {
    if (Array.isArray(item.children) && item.children.length) {
      return flattenMenuPaths(item.children)
    }
    return item.path ? [normalizePath(item.path)] : []
  })
}

function hasRoutePermission(user, path) {
  if (!user || path === '/dashboard' || path === '/approvals') return true
  const normalizedPath = normalizePath(path)
  // 隐藏页面(菜单 menu_roles 为空)不校验菜单权限
  if (hiddenPagePaths.has(normalizedPath)) return true
  return flattenMenuPaths(user.navigation || []).includes(normalizedPath)
}

function scheduleIdle(task) {
  if (typeof window === 'undefined') return
  if ('requestIdleCallback' in window) {
    window.requestIdleCallback(task, { timeout: 1500 })
    return
  }
  window.setTimeout(task, 400)
}

export function preloadRouteComponent(path) {
  const normalizedPath = normalizePath(path)
  const loader = viewLoaders[normalizedPath]
  if (!loader || preloadedPaths.has(normalizedPath)) {
    return Promise.resolve(null)
  }
  preloadedPaths.add(normalizedPath)
  return loader().catch((error) => {
    preloadedPaths.delete(normalizedPath)
    console.warn(`预加载页面失败：${normalizedPath}`, error)
    return null
  })
}

export function getLikelyPathsForUser(user, { exclude = [] } = {}) {
  const excluded = new Set(exclude.map(normalizePath))
  const menuPaths = flattenMenuPaths(user?.navigation || []).filter((path) => viewLoaders[path])
  const preferredPaths = rolePreloadPaths[user?.role_code] || []
  return uniquePaths([...preferredPaths, ...menuPaths])
    .filter((path) => path && path !== '/dashboard' && !excluded.has(path))
    .slice(0, 5)
}

export function preloadCommonRoutesForUser(user, options = {}) {
  const paths = getLikelyPathsForUser(user, options)
  if (!paths.length) return
  scheduleIdle(() => {
    paths.forEach((path, index) => {
      window.setTimeout(() => {
        void preloadRouteComponent(path)
      }, index * 180)
    })
  })
}

export function warmRouteData(path) {
  const normalizedPath = normalizePath(path)
  const tasks = routeDataWarmers[normalizedPath] || []
  return Promise.all(tasks.map((task) => prefetchGet(task.url, { ttlMs: task.ttlMs })))
}

export function preloadCommonDataForUser(user, { exclude = [] } = {}) {
  const paths = uniquePaths(['/dashboard', ...getLikelyPathsForUser(user, { exclude })])
  if (!paths.length) return
  scheduleIdle(() => {
    paths.forEach((path, index) => {
      window.setTimeout(() => {
        void warmRouteData(path)
      }, index * 220)
    })
  })
}

const routes = [
  { path: '/login', name: 'login', component: viewLoaders['/login'], meta: { title: '登录' } },
  {
    path: '/',
    component: viewLoaders['/__layout'],
    children: [
      { path: '', redirect: '/dashboard' },
      ...buildAppChildRoutes()
    ]
  },
  { path: '/:pathMatch(.*)*', redirect: '/dashboard' }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

router.beforeEach(async (to) => {
  const auth = useAuthStore(pinia)
  if (!auth.checkedSession) {
    await auth.bootstrapSession()
  }
  if (to.path !== '/login' && !auth.isLoggedIn) {
    return '/login'
  }
  if (to.path === '/login' && auth.isLoggedIn) {
    return '/dashboard'
  }
  if (to.path !== '/login' && auth.isLoggedIn && !hasRoutePermission(auth.user, to.path)) {
    return '/dashboard'
  }
  document.title = `${APP_TITLE} - ${to.meta.title || '首页'}`
})

export default router
