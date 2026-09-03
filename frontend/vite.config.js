import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 后端地址:默认本机 8000(Windows 本机开发);
// 前后端分离部署 / WSL 访问 Windows 后端时,用环境变量覆盖:
//   VITE_API_PROXY_TARGET=http://172.31.208.1:8000
// 生产环境(vite build + 后端静态托管)不使用本配置,不会出现此问题。
const API_TARGET = process.env.VITE_API_PROXY_TARGET || 'http://127.0.0.1:8000'

// 代理转发失败(后端未启动 / 重启中 / 网络瞬断)时:
// 统一回 502 + 明确提示,绝不向上抛出导致 Node 进程崩溃。
// 后端随后启动也不需要重启 vite —— 每次请求都会重新尝试连接。
function handleProxyError(err, _req, res) {
  if (res && !res.headersSent) {
    res.writeHead(502, { 'Content-Type': 'application/json; charset=utf-8' })
    res.end(
      JSON.stringify({
        code: 502,
        msg: `后端服务不可达(${err.code || 'unknown'}),请先启动后端(uvicorn, 端口 ${API_TARGET})`,
        data: null,
      })
    )
  }
}

function manualChunks(id) {
  const normalizedId = id.replace(/\\/g, '/')

  if (normalizedId.includes('node_modules')) {
    if (normalizedId.includes('element-plus')) {
      if (
        normalizedId.includes('/components/form') ||
        normalizedId.includes('/components/input') ||
        normalizedId.includes('/components/input-number') ||
        normalizedId.includes('/components/select') ||
        normalizedId.includes('/components/option') ||
        normalizedId.includes('/components/date-picker') ||
        normalizedId.includes('/components/drawer') ||
        normalizedId.includes('/components/switch')
      ) {
        return 'vendor-ep-interactive'
      }
      return undefined
    }
    if (normalizedId.includes('@vue') || normalizedId.includes('/vue/')) return 'vendor-vue'
    if (normalizedId.includes('vue-router')) return 'vendor-router'
    if (normalizedId.includes('pinia')) return 'vendor-pinia'
    return 'vendor-misc'
  }
}

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: API_TARGET,
        changeOrigin: true,
        configure(proxy) {
          proxy.on('error', handleProxyError)
        }
      },
      '/uploads': {
        target: API_TARGET,
        changeOrigin: true,
        configure(proxy) {
          proxy.on('error', handleProxyError)
        }
      }
    }
  },
  build: {
    sourcemap: false,
    chunkSizeWarningLimit: 700,
    rollupOptions: {
      output: {
        manualChunks,
        chunkFileNames: 'assets/[name]-[hash].js',
        entryFileNames: 'assets/[name]-[hash].js',
        assetFileNames: 'assets/[name]-[hash][extname]'
      }
    }
  }
})