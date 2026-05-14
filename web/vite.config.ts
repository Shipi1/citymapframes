import { defineConfig, loadEnv } from 'vite'
import { svelte } from '@sveltejs/vite-plugin-svelte'

// https://vite.dev/config/
//
// Three knobs, all driven by env vars (so .env.<mode> files Just Work):
//
//   VITE_BASE_PATH       — public base path for asset URLs in the built
//                           bundle. Defaults to `/`. The Docker web build
//                           sets `/map/`.
//
//   VITE_API_BASE        — prefix the frontend prepends to every API call.
//                           Dev default `/api` matches the local uvicorn
//                           proxy.  Production sets `/api/map`.
//
//   VITE_DEV_API_TARGET  — where Vite forwards intercepted API requests
//                           during `npm run dev`. Defaults to the local
//                           docker-compose api at http://127.0.0.1:8000.
//                           Override to `https://shipisnature.com` to
//                           develop against the deployed API.
//
// See web/.env.prod-api for the production-API combo, and run it via:
//     npm run dev:prod-api

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')

  const apiBase = env.VITE_API_BASE ?? '/api'
  const devApiTarget = env.VITE_DEV_API_TARGET ?? 'http://127.0.0.1:8000'

  return {
    plugins: [svelte()],
    base: env.VITE_BASE_PATH ?? '/',
    server: {
      proxy: {
        // Match whatever the frontend prepends. With apiBase='/api/map',
        // the SPA fetches /api/map/layers, Vite intercepts it here and
        // forwards the full path to the target — so the target should
        // NOT include /api/map itself (e.g. https://shipisnature.com,
        // not https://shipisnature.com/api/map).
        [apiBase]: {
          target: devApiTarget,
          changeOrigin: true,
          secure: true,
        },
      },
    },
  }
})
