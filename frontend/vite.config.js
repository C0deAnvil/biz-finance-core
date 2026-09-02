import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

function ignoreTransientSocketReset(error) {
  if (error && error.code === 'ECONNRESET') {
    return
  }
  throw error
}

process.on('uncaughtException', ignoreTransientSocketReset)

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
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        configure(proxy) {
          proxy.on('error', ignoreTransientSocketReset)
        }
      },
      '/uploads': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        configure(proxy) {
          proxy.on('error', ignoreTransientSocketReset)
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


