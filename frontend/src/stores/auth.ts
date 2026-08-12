import { defineStore } from 'pinia'

import { ApiError, apiRequest, setCsrfToken } from '@/api/client'
import type { AuthResponse, CurrentUser, SetupStatus } from '@/api/types'

type AuthPhase = 'loading' | 'setup' | 'guest' | 'authenticated'

export const useAuthStore = defineStore('auth', {
  state: () => ({
    phase: 'loading' as AuthPhase,
    username: '',
    initialized: false,
  }),
  actions: {
    async bootstrap(): Promise<void> {
      if (this.initialized) return
      try {
        const current = await apiRequest<CurrentUser>('/auth/me')
        setCsrfToken(current.csrf_token)
        this.username = current.username
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
    },
    async logout(): Promise<void> {
      await apiRequest('/auth/logout', { method: 'POST' })
      setCsrfToken(null)
      this.username = ''
      this.phase = 'guest'
    },
  },
})
