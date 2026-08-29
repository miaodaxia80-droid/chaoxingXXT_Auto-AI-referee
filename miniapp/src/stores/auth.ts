import { defineStore } from 'pinia'
import { api, clearSessionCookie, hasCsrfToken, setCsrfToken } from '@/api/client'
import type { AppUser } from '@/api/types'

export type AuthPhase = 'bootstrapping' | 'authenticated' | 'anonymous'

interface AuthState {
  phase: AuthPhase
  profile: AppUser | null
  lastError: string
}

export const useAuthStore = defineStore('auth', {
  state: (): AuthState => ({
    phase: 'bootstrapping',
    profile: null,
    lastError: '',
  }),
  getters: {
    isLoggedIn: (state): boolean => state.phase === 'authenticated',
    isReady: (state): boolean => state.phase !== 'bootstrapping',
  },
  actions: {
    /** 启动探测：本地有 CSRF 则用 /auth/me 探活会话，失败回到未登录态。 */
    async bootstrap(): Promise<void> {
      if (!hasCsrfToken()) {
        this.phase = 'anonymous'
        return
      }
      try {
        const me = await api.me()
        if (me.kind !== 'app_user' || !me.user) {
          throw new Error('session is not an app user session')
        }
        setCsrfToken(me.csrf_token)
        this.profile = me.user
        this.phase = 'authenticated'
      } catch (error) {
        setCsrfToken(null)
        this.profile = null
        this.phase = 'anonymous'
      }
    },

    /** 统一登录入口：小程序走 wx.login，H5 走开发态登录。 */
    async login(): Promise<void> {
      // #ifdef MP-WEIXIN
      await this.wxLogin()
      // #endif
      // #ifdef H5
      await this.h5Login()
      // #endif
    },

    /** 微信静默登录：wx.login 换 code → 后端 code2session → 签发会话。
     *
     * 开发降级：后端未配置微信密钥（503，如开发者工具+游客 appid 场景）时，
     * 尝试 /auth/dev-login。该端点仅在开发态后端（CX_DEV_LOGIN_ENABLED=1 且非
     * production）存在，生产环境必 404，因此降级不会在生产生效。
     */
    async wxLogin(): Promise<void> {
      this.lastError = ''
      try {
        // wx.login 在部分环境（开发者工具自动化/游客 appid、弱网）可能永不返回，
        // 8 秒超时视同后端不可用，走开发降级分支。
        const login = await Promise.race([
          uni.login({ provider: 'weixin' }),
          new Promise<never>((_, reject) =>
            setTimeout(
              () =>
                reject(
                  Object.assign(new Error('wx.login timed out'), { status: 503 }),
                ),
              8_000,
            ),
          ),
        ])
        const response = await api.wxLogin(login.code)
        setCsrfToken(response.csrf_token)
        this.profile = response.user
        this.phase = 'authenticated'
      } catch (error) {
        const status = (error as { status?: number }).status
        if (status === 503) {
          try {
            await this.devLogin('devtools')
            uni.showToast({ title: '开发登录（后端未配置微信密钥）', icon: 'none', duration: 2500 })
            return
          } catch {
            // 降级也失败（后端未开 CX_DEV_LOGIN_ENABLED），落到原始错误
          }
        }
        this.lastError = error instanceof Error ? error.message : '登录失败，请重试'
        this.phase = 'anonymous'
        throw error
      }
    },

    /** 开发登录：固定用途前缀 token 走 /auth/dev-login（仅开发态后端开启）。 */
    async devLogin(purpose: string): Promise<void> {
      const storageKey = `cx.dev.token.${purpose}`
      let token = uni.getStorageSync(storageKey) as string
      if (!token) {
        token = `${purpose}-${Math.random().toString(36).slice(2, 12)}`
        uni.setStorageSync(storageKey, token)
      }
      const response = await api.devLogin(token)
      setCsrfToken(response.csrf_token)
      this.profile = response.user
      this.phase = 'authenticated'
    },

    /** H5 开发预览登录。 */
    async h5Login(): Promise<void> {
      this.lastError = ''
      try {
        await this.devLogin('h5')
      } catch (error) {
        this.lastError = error instanceof Error ? error.message : '登录失败，请重试'
        this.phase = 'anonymous'
        throw error
      }
    },

    async logout(): Promise<void> {
      try {
        await api.logout()
      } catch {
        // 会话可能已过期，忽略登出接口错误
      }
      setCsrfToken(null)
      clearSessionCookie()
      this.profile = null
      this.phase = 'anonymous'
    },
  },
})
