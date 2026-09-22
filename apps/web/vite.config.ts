import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    // Local dev only: forwards /api/* to the backend by compose service
    // name, so the browser never needs CORS configured. In production
    // this exact job is done by nginx instead (same idea, different
    // tool — see the deferred nginx task in PLAN.md).
    proxy: {
      '/api': {
        target: 'http://api:8010',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
})
