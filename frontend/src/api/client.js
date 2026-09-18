import { clearStoredSession, getSessionCacheIdentity, markStoredSession } from '../utils/session'

const BASE_HEADERS = {
  'Content-Type': 'application/json'
}

const PREFETCH_TTL_MS = 60 * 1000
const prefetchedGetCache = new Map()
const inflightGetRequests = new Map()
let refreshPromise = null

function shouldTryRefresh(url, meta = {}) {
  return !meta._retried && !['/api/auth/login', '/api/auth/refresh', '/api/auth/logout'].some((prefix) => url.startsWith(prefix))
}

function cloneData(data) {
  if (data == null) return data
  if (typeof structuredClone === 'function') {
    return structuredClone(data)
  }
  return JSON.parse(JSON.stringify(data))
}

function getCacheKey(url) {
  return `${getSessionCacheIdentity()}::${url}`
}

function clearExpiredPrefetchCache() {
  const now = Date.now()
  for (const [key, entry] of prefetchedGetCache.entries()) {
    if (entry.expiresAt <= now) {
      prefetchedGetCache.delete(key)
    }
  }
}

function takePrefetchedGet(url) {
  clearExpiredPrefetchCache()
  const cacheKey = getCacheKey(url)
  const entry = prefetchedGetCache.get(cacheKey)
  if (!entry) return null
  prefetchedGetCache.delete(cacheKey)
  return cloneData(entry.data)
}

export function primeGetCache(url, data, { ttlMs = PREFETCH_TTL_MS } = {}) {
  clearExpiredPrefetchCache()
  prefetchedGetCache.set(getCacheKey(url), {
    data: cloneData(data),
    expiresAt: Date.now() + ttlMs
  })
}

export function clearGetCache(prefixes = []) {
  if (!prefixes.length) {
    prefetchedGetCache.clear()
    return
  }

  for (const key of prefetchedGetCache.keys()) {
    const url = key.split('::').slice(1).join('::')
    if (prefixes.some((prefix) => url.startsWith(prefix))) {
      prefetchedGetCache.delete(key)
    }
  }
}

async function parseResponsePayload(response) {
  const text = await response.text()
  if (!text) {
    return null
  }

  try {
    return JSON.parse(text)
  } catch {
    if (response.ok) {
      throw new Error('服务返回了非 JSON 数据，请确认后端服务是否正常启动')
    }
    const snippet = text.slice(0, 120).trim()
    throw new Error(snippet || '服务响应异常，请稍后重试')
  }
}

async function attemptRefreshSession() {
  if (refreshPromise) {
    return refreshPromise
  }
  refreshPromise = fetch('/api/auth/refresh', {
    method: 'POST',
    headers: { ...BASE_HEADERS },
    credentials: 'include',
    body: '{}'
  })
    .then(async (response) => {
      const payload = await parseResponsePayload(response)
      if (!response.ok || !payload || payload.code >= 400) {
        throw new Error(payload?.msg || '刷新登录态失败')
      }
      if (payload.data?.user) {
        markStoredSession(payload.data.user)
      }
      return payload.data
    })
    .finally(() => {
      refreshPromise = null
    })
  return refreshPromise
}

async function performRequest(url, options = {}, meta = {}) {
  const headers = { ...BASE_HEADERS, ...(options.headers || {}) }
  const response = await fetch(url, {
    ...options,
    headers,
    credentials: 'include'
  })

  if (response.status === 401) {
    if (shouldTryRefresh(url, meta)) {
      try {
        await attemptRefreshSession()
        return performRequest(url, options, { ...meta, _retried: true })
      } catch {
      }
    }
    clearStoredSession()
    clearGetCache()
    if (typeof window !== 'undefined' && window.location.pathname !== '/login') {
      window.location.href = '/login'
    }
    throw new Error('登录已失效，请重新登录')
  }

  const payload = await parseResponsePayload(response)
  if (!payload) {
    throw new Error('服务无响应，请确认后端已启动且接口可访问')
  }
  if (!response.ok || payload.code >= 400) {
    const error = new Error(payload.msg || '请求失败')
    error.code = payload.code
    error.data = payload.data
    throw error
  }
  return payload.data
}

export async function request(url, options = {}, meta = {}) {
  const method = (options.method || 'GET').toUpperCase()
  const cacheKey = getCacheKey(url)

  if (method === 'GET') {
    if (!meta.force) {
      const prefetched = takePrefetchedGet(url)
      if (prefetched !== null) {
        return prefetched
      }
      if (inflightGetRequests.has(cacheKey)) {
        return inflightGetRequests.get(cacheKey)
      }
    }

    const pendingRequest = performRequest(url, { ...options, method, headers: options.headers || {} }, meta).finally(() => {
      inflightGetRequests.delete(cacheKey)
    })

    inflightGetRequests.set(cacheKey, pendingRequest)
    return pendingRequest
  }

  const data = await performRequest(url, { ...options, method }, meta)
  clearGetCache()
  return data
}

export function get(url, options = {}) {
  return request(url, { method: 'GET', headers: {} }, options)
}

export async function prefetchGet(url, options = {}) {
  try {
    const data = await request(url, { method: 'GET', headers: {} }, { force: options.force })
    primeGetCache(url, data, { ttlMs: options.ttlMs })
    return data
  } catch (error) {
    if (options.silent === false) {
      throw error
    }
    return null
  }
}

export function post(url, data) {
  return request(url, {
    method: 'POST',
    body: JSON.stringify(data)
  })
}

export function put(url, data) {
  return request(url, {
    method: 'PUT',
    body: JSON.stringify(data)
  })
}

export function patch(url, data) {
  return request(url, {
    method: 'PATCH',
    body: JSON.stringify(data)
  })
}

export function remove(url) {
  return request(url, {
    method: 'DELETE',
    headers: {}
  })
}

export async function upload(url, formData) {
  const response = await fetch(url, {
    method: 'POST',
    body: formData,
    credentials: 'include'
  })
  const payload = await parseResponsePayload(response)
  if (!payload) {
    throw new Error('服务无响应，请确认后端已启动且接口可访问')
  }
  if (!response.ok || payload.code >= 400) {
    throw new Error(payload.msg || '上传失败')
  }
  clearGetCache()
  return payload.data
}

export async function postForm(url, formData) {
  return upload(url, formData)
}
