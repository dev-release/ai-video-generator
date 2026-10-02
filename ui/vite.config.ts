import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Dev: frontend on :5173, FastAPI backend on :8000. A built UI is served by FastAPI itself.
const backend = 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [react()],
  server: { proxy: { '/api': backend, '/media': backend } },
})
