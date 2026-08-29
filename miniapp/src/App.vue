<script setup lang="ts">
import { onLaunch } from '@dcloudio/uni-app'
import { onUnauthorized } from '@/api/client'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()

// 任何请求命中 401 时，重置到未登录态，页面上的 NeedLogin 门自动接管
onUnauthorized(() => {
  auth.profile = null
  auth.phase = 'anonymous'
})

// 开发/体验版测试钩子：供 miniprogram-automator 从服务层驱动登录登出
// （正式版 envVersion === 'release' 时不挂载）
try {
  const account = uni.getAccountInfoSync()
  if (account.miniProgram.envVersion !== 'release') {
    ;(globalThis as Record<string, unknown>).__cxLogin = () => auth.login()
    ;(globalThis as Record<string, unknown>).__cxLogout = () => auth.logout()
  }
} catch {
  // 低版本基础库无此 API，忽略
}

onLaunch(() => {
  // 静默探测会话；页面通过 auth.isReady 等待探测完成
  auth.bootstrap()
})
</script>

<style lang="scss">
@import '@/styles/common.scss';
</style>
