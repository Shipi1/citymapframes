import { defineConfig } from 'vite'
import { svelte } from '@sveltejs/vite-plugin-svelte'

// https://vite.dev/config/
export default defineConfig({
  plugins: [svelte()],

  // Public base path. Defaults to '/' for local dev; production Docker
  // build sets VITE_BASE_PATH=/map/ so asset URLs in index.html resolve
  // against shipisnature.com/map/ instead of the document root.
  base: process.env.VITE_BASE_PATH ?? '/',

  server: {
    // In dev, proxy API calls to the FastAPI server (server.py).
    // Start it separately:  uv run python ../scripts/server.py
    // (or:  docker compose up -d api)
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})
