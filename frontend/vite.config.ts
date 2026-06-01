import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
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
