import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    // Dev only: forwards /api/* to the api service by its compose name, so
    // the browser sees one origin and CORS never comes up. In production
    // nginx does the same job (apps/web/nginx/default.conf).
    proxy: {
      '/api': {
        target: 'http://api:8010',
        changeOrigin: true,
        // Send X-Forwarded-For like nginx does, so rate limiting sees the
        // real client in dev too.
        xfwd: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
})
