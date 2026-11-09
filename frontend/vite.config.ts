import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'node:path'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const backendTarget = env.BACKEND_URL || 'http://localhost:8001'

  return {
    plugins: [react()],
    resolve: {
      alias: {
        '@': path.resolve(__dirname, './src'),
      },
    },
    build: {
      rollupOptions: {
        input: { app: path.resolve(__dirname, 'index.html'), bench: path.resolve(__dirname, 'bench.html') },
      },
    },
    server: {
      port: 5174,
      host: true,
      allowedHosts: ['host.docker.internal', 'frontend'],
      strictPort: true,
      proxy: {
        '/api': { target: backendTarget, changeOrigin: true },
        '/health': { target: backendTarget, changeOrigin: true },
      },
      watch: {
        // Polling is needed for reliable HMR through Docker bind mounts on macOS.
        usePolling: true,
        interval: 300,
      },
    },
  }
})
