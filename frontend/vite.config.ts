import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import pkg from './package.json'

export default defineConfig({
  plugins: [react()],
  define: {
    __APP_VERSION__: JSON.stringify(pkg.version),
  },
  server: {
    host: true,                 // listen on all interfaces (LAN / Tailscale), not just localhost
    allowedHosts: ['dionysus'],
    headers: {
      'Cache-Control': 'no-cache, must-revalidate',
    },
    proxy: {
      '/api': 'http://localhost:8001',
      '/ws': { target: 'ws://localhost:8001', ws: true },
    },
  },
})
