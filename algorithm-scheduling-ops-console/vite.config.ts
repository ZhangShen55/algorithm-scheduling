import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  build: {
    chunkSizeWarningLimit: 560,
  },
  server: {
    port: 5174,
    proxy: {
      '/control': {
        target: process.env.VITE_CONTROL_URL || process.env.VITE_CONTROL_BASE_URL || 'http://127.0.0.1:18100',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/control/, ''),
      },
      '/gateway': {
        target: process.env.VITE_GATEWAY_URL || process.env.VITE_GATEWAY_BASE_URL || 'http://127.0.0.1:8001',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/gateway/, ''),
      },
    },
  },
})
