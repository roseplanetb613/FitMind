import { defineConfig } from 'vite'

export default defineConfig({
  // 必须与 app/server.py 的挂载点一致。默认 base '/' 会让 index.html 里的资源
  // 走绝对路径 /assets/...，而挂载点是 /app —— 浏览器请求 /assets/... 会 404，
  // 页面**空白且无报错**（/app/ 本身是 200，所以 curl 那一跳看不出来）。
  // 副作用：dev server 的联调入口因此是 http://localhost:5173/app/ 而不是 /。
  base: '/app/',
  server: {
    proxy: { '/v1': 'http://127.0.0.1:8000' },
  },
  build: { outDir: 'dist', emptyOutDir: true },
})
