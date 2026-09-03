import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { viteSingleFile } from 'vite-plugin-singlefile'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

// 开发期占位图 + 模拟桥：仅 dev server 生效，打包构建不受影响
function devFixtures() {
  return {
    name: 'fb-dev-fixtures',
    apply: 'serve',
    transformIndexHtml() {
      return [{ tag: 'script', attrs: { src: '/__mock-bridge.js' } }]
    },
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        try {
          const url = new URL(req.url, 'http://localhost')
          if (url.pathname === '/thumb' || url.pathname === '/preview') {
            const p = decodeURIComponent(url.searchParams.get('p') || 'file')
            const name = p.split(/[\\/]/).pop().slice(0, 20).replace(/[<>&]/g, '')
            const seed = [...p].reduce((a, c) => a + c.charCodeAt(0), 0)
            const hue = seed % 360
            const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="320" height="220"><rect width="100%" height="100%" fill="hsl(${hue},42%,70%)"/><text x="50%" y="52%" font-size="17" text-anchor="middle" fill="#fff" font-family="sans-serif">${name}</text></svg>`
            res.setHeader('Content-Type', 'image/svg+xml')
            res.end(svg)
            return
          }
          if (url.pathname === '/__mock-bridge.js') {
            res.setHeader('Content-Type', 'application/javascript')
            res.end(readFileSync(
              fileURLToPath(new URL('./mock-bridge.js', import.meta.url))))
            return
          }
        } catch { /* fallthrough */ }
        next()
      })
    },
  }
}

// 打包成单 HTML（内联 JS/CSS），pywebview 用 file:// 加载不受模块 CORS 限制
export default defineConfig({
  plugins: [vue(), viteSingleFile(), devFixtures()],
  base: './',
  server: {
    port: 5173,
    strictPort: true,
  },
  build: {
    chunkSizeWarningLimit: 4096,
    assetsInlineLimit: 100000000,  // logo 等资源内联进单 HTML，file:// 加载不依赖外部文件
  },
})
