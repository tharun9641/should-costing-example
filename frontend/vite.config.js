import react from '@vitejs/plugin-react'
import { defineConfig, loadEnv } from 'vite'

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const target = loadEnv(mode, '.', '').VITE_API_PROXY || 'http://localhost:8000'
  return {
    plugins: [react()],
    server: {
      // Forward API calls to the FastAPI server (see ../server/README.md)
      proxy: { '/api': target, '/health': target },
    },
  }
})
