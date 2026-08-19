const MODULE_META = {
  sales: { label: '销售', tagType: 'success', notificationType: 'success', accent: '#67c23a' },
  purchase: { label: '采购', tagType: 'warning', notificationType: 'warning', accent: '#e6a23c' },
  inventory: { label: '库存', tagType: 'primary', notificationType: 'info', accent: '#409eff' },
  production: { label: '生产', tagType: 'danger', notificationType: 'error', accent: '#f56c6c' },
  quality: { label: '质检', tagType: 'info', notificationType: 'info', accent: '#909399' },
  finance: { label: '财务', tagType: 'warning', notificationType: 'warning', accent: '#b88230' },
  analytics: { label: '统计分析', tagType: 'primary', notificationType: 'info', accent: '#36a3f7' },
  admin: { label: '管理', tagType: 'danger', notificationType: 'error', accent: '#7d54ff' },
  dashboard: { label: '系统', tagType: 'info', notificationType: 'info', accent: '#606266' }
}

export function getModuleMeta(moduleCode) {
  return MODULE_META[moduleCode] || { label: moduleCode || '系统', tagType: 'info', notificationType: 'info', accent: '#606266' }
}

export function getModuleNotificationClass(moduleCode) {
  return `module-notification module-notification--${moduleCode || 'dashboard'}`
}

export function getModuleTagStyle(moduleCode) {
  const meta = getModuleMeta(moduleCode)
  return {
    color: '#ffffff',
    borderColor: `${meta.accent}88`,
    backgroundColor: meta.accent
  }
}
