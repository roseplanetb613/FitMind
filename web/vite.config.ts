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
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    // three 单独成 chunk（2026-09-17 手机端治理）：
    //  · 应用代码改动不再让 three 的缓存失效（指纹是按 chunk 算的）
    //  · 与主 chunk 并行下载，而不是全挤在一个 692KB 的文件里
    // 首屏关键路径约 533KB gzip 中 three 占大头。拆开是纯收益、零行为变化。
    //
    // three 那一块是**刻意**单独存在的，它必然超过默认的 500KB 阈值。
    // 调高阈值而不是关掉警告：真有别的 chunk 膨胀时还得看得见。
    chunkSizeWarningLimit: 700,
    rollupOptions: {
      output: {
        // ⚠ Vite 8 底层是 **rolldown**，`manualChunks` **只接受函数**。
        // 传对象（rollup 的经典写法）会在构建期直接失败：
        //   "For the manualChunks. Invalid type: Expected Function but received Object."
        // 所以这里用谓词而不是 `{ three: ['three'] }`。
        manualChunks: (id: string) =>
          /[\\/]node_modules[\\/]three[\\/]/.test(id) ? 'three' : undefined,
      },
    },
  },
})
