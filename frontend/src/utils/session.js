const USER_KEY = 'current_user'
const SESSION_FLAG_KEY = 'auth_session'
const LEGACY_KEYS = ['access_token', 'refresh_token', 'current_user']

function getSessionStorage() {
  return typeof window === 'undefined' ? null : window.sessionStorage
}

function getLocalStorage() {
  return typeof window === 'undefined' ? null : window.localStorage
}

function parseUser(value) {
  if (!value) return null
  try {
    return JSON.parse(value)
  } catch {
    return null
  }
}

export function migrateLegacySession() {
  const sessionStorage = getSessionStorage()
  const localStorage = getLocalStorage()
  if (!sessionStorage || !localStorage) return

  const legacyUser = localStorage.getItem(USER_KEY)
  if (legacyUser && !sessionStorage.getItem(USER_KEY)) {
    sessionStorage.setItem(USER_KEY, legacyUser)
  }

  LEGACY_KEYS.forEach((key) => {
    localStorage.removeItem(key)
  })
}

export function readStoredUser() {
  return parseUser(getSessionStorage()?.getItem(USER_KEY) || null)
}

export function writeStoredUser(user) {
  const sessionStorage = getSessionStorage()
  if (!sessionStorage) return
  if (user == null) {
    sessionStorage.removeItem(USER_KEY)
    return
  }
  sessionStorage.setItem(USER_KEY, JSON.stringify(user))
}

export function hasStoredSession() {
  return getSessionStorage()?.getItem(SESSION_FLAG_KEY) === '1'
}

export function markStoredSession(user = null) {
  const sessionStorage = getSessionStorage()
  if (!sessionStorage) return
  sessionStorage.setItem(SESSION_FLAG_KEY, '1')
  if (user) {
    writeStoredUser(user)
  }
}

export function clearStoredSession() {
  const sessionStorage = getSessionStorage()
  if (sessionStorage) {
    sessionStorage.removeItem(SESSION_FLAG_KEY)
    sessionStorage.removeItem(USER_KEY)
  }

  const localStorage = getLocalStorage()
  if (localStorage) {
    LEGACY_KEYS.forEach((key) => {
      localStorage.removeItem(key)
    })
  }
}

export function getSessionCacheIdentity() {
  const user = readStoredUser()
  if (user?.id != null) {
    return `user:${user.id}`
  }
  return hasStoredSession() ? 'session' : 'guest'
}

migrateLegacySession()

