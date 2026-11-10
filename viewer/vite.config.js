import { defineConfig } from 'vite';

// `npm run dev` proxies the API and session data to a local `scanmap serve` on :8765.
export default defineConfig({
  base: './',
  server: {
    proxy: {
      '/api': 'http://localhost:8765',
      '/data': 'http://localhost:8765',
    },
  },
  build: { target: 'es2022', chunkSizeWarningLimit: 4000 },
});
