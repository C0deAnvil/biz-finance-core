import assert from 'node:assert/strict'

import { createSSRApp } from 'vue'
import { renderToString } from 'vue/server-renderer'
import { createServer } from 'vite'

// 检查页面真正完成组件渲染，而不仅仅是 Vite 能编译它。
// 未引入 ElTableColumn 时，scope.row 会在空数据状态下直接抛错，造成白屏。
const vite = await createServer({
  appType: 'custom',
  server: { middlewareMode: true },
  ssr: { noExternal: ['element-plus'] }
})
const oldWarn = console.warn
const oldError = console.error
const warnings = []
const captureWarning = (...args) => warnings.push(args.map((value) => (
  typeof value === 'string' ? value : value?.message || Object.prototype.toString.call(value)
)).join(' '))
console.warn = captureWarning
console.error = captureWarning

try {
  const { default: View } = await vite.ssrLoadModule('/src/views/sales/MyReceivablesView.vue')
  const html = await renderToString(createSSRApp(View))
  assert.match(html, /我的应收款/)
  assert.match(html, /搜索客户订单号/)
  assert.match(html, /暂无本人已转为应收/)
  assert.match(html, /class="el-table/)
  assert.equal(
    warnings.filter((message) => /Failed to resolve (component|directive)/.test(message)).length,
    0,
    '页面存在未注册的组件或指令'
  )
  oldWarn('我的应收款页面渲染检查通过')
} finally {
  console.warn = oldWarn
  console.error = oldError
  await vite.close()
}
