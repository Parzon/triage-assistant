import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

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
      // The bundled identity provider (Keycloak, served under /auth), on
      // this same origin - as the TLS edge does in production shape.
      '/auth': {
        target: 'http://keycloak:8080',
        xfwd: true,
      },
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    coverage: {
      provider: 'v8',
      include: ['src/**/*.{ts,tsx}'],
      exclude: ['src/main.tsx', 'src/test/**', '**/*.test.{ts,tsx}'],
      reporter: ['text', 'cobertura'],
      thresholds: { lines: 85, branches: 80 },
    },
  },
})
