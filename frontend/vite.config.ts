import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

// Dev server proxies /api to the FastAPI backend (started separately on :8000).
// In Docker, nginx performs the same proxy.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
});
