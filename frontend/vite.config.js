import react from '@vitejs/plugin-react'
import { defineConfig, loadEnv } from 'vite'

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const target = loadEnv(mode, '.', '').BACKEND_URL || 'http://localhost:8000'
  return {
    plugins: [react()],
    // Expose BACKEND_URL to the app (Vite only exposes VITE_* by default)
    envPrefix: ['VITE_', 'BACKEND_'],
    server: {
      // Forward API calls to the FastAPI server (see ../server/README.md)
      proxy: { '/api': target, '/health': target },
    },
  }
})
