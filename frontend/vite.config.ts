import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 3000,
    // 本地 npm run dev（未設 VITE_API_URL 時 client 走 /api）：
    // 模仿 production nginx——轉發到 backend 並剝掉 /api 前綴。
    // Docker dev 設有 VITE_API_URL 直連，不經此 proxy。
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./tests/setup.ts'],
    css: true,
  },
})
