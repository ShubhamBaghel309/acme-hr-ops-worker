import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// In development the console runs on Vite (port 5173) and forwards API calls to the agent server.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8000',
      '/runs': 'http://127.0.0.1:8000',
    },
  },
})
