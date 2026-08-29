import { defineStore } from 'pinia'

import { ApiError, apiRequest, setCsrfToken } from '@/api/client'
import type { AppUserProfile, AuthResponse, CurrentUser, SessionKind, SetupStatus } from '@/api/types'

type AuthPhase = 'loading' | 'setup' | 'guest' | 'authenticated'

export const useAuthStore = defineStore('auth', {
  state: () => ({
    phase: 'loading' as AuthPhase,
    username: '',
    kind: 'admin' as SessionKind,
    user: null as AppUserProfile | null,
    initialized: false,
  }),
  getters: {
    isAdmin: (state) => state.kind === 'admin',
    isAppUser: (state) => state.kind === 'app_user',
    /** 侧栏与欢迎语展示名：普通用户优先昵称/用户名，管理员用用户名。 */
    displayName: (state) =>
      state.kind === 'app_user'
        ? state.user?.nickname || state.user?.username || '用户'
        : state.username,
  },
  actions: {
    async bootstrap(): Promise<void> {
      if (this.initialized) return
      try {
        const current = await apiRequest<CurrentUser>('/auth/me')
        setCsrfToken(current.csrf_token)
        this.applySession(current)
        this.phase = 'authenticated'
      } catch (error) {
        if (!(error instanceof ApiError) || error.status !== 401) throw error
        setCsrfToken(null)
        const setup = await apiRequest<SetupStatus>('/auth/setup')
        this.phase = setup.required ? 'setup' : 'guest'
      } finally {
        this.initialized = true
      }
    },
    applySession(current: CurrentUser): void {
      this.username = current.username ?? ''
      this.kind = current.kind
      this.user = current.user
    },
    async refreshProfile(): Promise<void> {
      const current = await apiRequest<CurrentUser>('/auth/me')
      setCsrfToken(current.csrf_token)
      this.applySession(current)
    },
    async setup(username: string, password: string): Promise<void> {
      await apiRequest('/auth/setup', {
        method: 'POST',
        body: JSON.stringify({ username, password }),
      })
      await this.login(username, password)
    },
    async login(username: string, password: string): Promise<void> {
      const result = await apiRequest<AuthResponse>('/auth/login', {
        method: 'POST',
        body: JSON.stringify({ username, password }),
      })
      setCsrfToken(result.csrf_token)
      this.username = result.username
      this.phase = 'authenticated'
      // 登录响应不含 kind/user，拉一次 /auth/me 补全身份信息
      try {
        const current = await apiRequest<CurrentUser>('/auth/me')
        this.applySession(current)
      } catch {
        this.kind = 'admin'
      }
    },
    async logout(): Promise<void> {
      await apiRequest('/auth/logout', { method: 'POST' })
      setCsrfToken(null)
      this.username = ''
      this.user = null
      this.kind = 'admin'
      this.phase = 'guest'
    },
  },
})
