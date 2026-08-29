import { defineConfig } from 'vite'
import uni from '@dcloudio/vite-plugin-uni'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [uni()],
  server: {
    // H5 开发预览：把 /api 代理到本机后端，实现同源请求（携带会话 Cookie）
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:5002',
        changeOrigin: false,
      },
    },
  },
})
