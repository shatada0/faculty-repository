import { fileURLToPath } from 'node:url'
import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  return {
    plugins: [react(), tailwindcss()],
    resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } },
    // .map files are built for decoding stacks from /api/client-errors, but not linked from the bundle; nginx does not serve them.
    build: { sourcemap: 'hidden' },
    // Without mocks, /api is proxied to nginx of the local stack. secure: false because Node does not trust
    // the local (mkcert or self-signed) certificate; the browser Origin is passed as is, so the backend
    // allows http://localhost:5173 in AUTH_ALLOWED_ORIGINS.
    server: env.VITE_API_PROXY
      ? { proxy: { '/api': { target: env.VITE_API_PROXY, secure: false, changeOrigin: true } } }
      : undefined,
  }
})
