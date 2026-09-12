import { defineConfig } from 'vite'

export default defineConfig({
  // 必须与 app/server.py 的挂载点一致。默认 base '/' 会让 index.html 里的资源
  // 走绝对路径 /assets/...，而挂载点是 /app —— 浏览器请求 /assets/... 会 404，
  // 页面**空白且无报错**（/app/ 本身是 200，所以 curl 那一跳看不出来）。
  // 副作用：dev server 的联调入口因此是 http://localhost:5173/app/ 而不是 /。
  base: '/app/',
  server: {
    // /media 也要代理 —— 动作演示 GIF 由后端挂载在 /media，
    // 只代理 /v1 的话 dev 下图片全是 404（生产形态 /app 下同源，不受影响）
    proxy: {
      '/v1': 'http://127.0.0.1:8000',
      '/media': 'http://127.0.0.1:8000',
    },
  },
  build: { outDir: 'dist', emptyOutDir: true },
})
