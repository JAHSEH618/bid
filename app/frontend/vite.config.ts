import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'node:path'

// dev 代理:/api / /health 转发到本地 backend(uvicorn 默认 12123)。
//
// 注意 SSE 长连接(/api/projects/{id}/stream):
//   - changeOrigin: true — 改 Host header 防 starlette 中间件按 origin 过滤
//   - ws: true — 兼容某些 WebSocket 升级路径(SSE 不需要,但开发期 HMR 客户端
//     和未来如改用 WS 时无副作用)
//   - configurer 注册 proxy 'error' hook 把 ECONNREFUSED 等打到 vite 控制台,
//     避免开发期看到「pending → 网络错误」无线索
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:12123',
        changeOrigin: true,
        ws: true,
        configure: (proxy) => {
          proxy.on('error', (err, _req, res) => {
            // 控制台可见,且回写 502 让前端能进 ApiError 分支
            // eslint-disable-next-line no-console
            console.error('[vite proxy /api error]', err.message)
            if (res && 'writeHead' in res && !res.headersSent) {
              res.writeHead(502, { 'Content-Type': 'application/json' })
              res.end(
                JSON.stringify({
                  detail: `proxy error: ${err.message}`,
                }),
              )
            }
          })
        },
      },
      '/health': {
        target: 'http://127.0.0.1:12123',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
  },
})
