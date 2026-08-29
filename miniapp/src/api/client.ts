import { API_BASE } from '@/config'

const API_PREFIX = '/api/v1'
const CSRF_STORAGE_KEY = 'cx.csrf'

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message)
  }
}

function errorMessage(payload: unknown, status: number): string {
  if (
    typeof payload === 'object' &&
    payload !== null &&
    'detail' in payload &&
    typeof (payload as { detail: unknown }).detail === 'string'
  ) {
    return (payload as { detail: string }).detail
  }
  return `请求失败 (${status})`
}

export function setCsrfToken(token: string | null): void {
  if (token) uni.setStorageSync(CSRF_STORAGE_KEY, token)
  else uni.removeStorageSync(CSRF_STORAGE_KEY)
}

export function hasCsrfToken(): boolean {
  return Boolean(uni.getStorageSync(CSRF_STORAGE_KEY))
}

/**
 * 会话 Cookie 手动管理。
 *
 * 微信小程序的 wx.request 不会像浏览器那样自动存储/回传 Cookie（开发者工具
 * 模拟器与真机均如此），必须从登录响应的 Set-Cookie 中提取会话并在此后每个
 * 请求手动携带 Cookie 头。后端会话 Cookie 名默认 cx_session
 * （CX_SESSION_COOKIE_NAME 未改配置时）。
 */
const SESSION_COOKIE_KEY = 'cx.session_cookie'
const SESSION_COOKIE_NAME = 'cx_session'

function extractSessionCookie(header: Record<string, unknown> | undefined): string | null {
  if (!header) return null
  const raw = header['Set-Cookie'] ?? header['set-cookie']
  if (!raw) return null
  const list = Array.isArray(raw) ? raw : [raw]
  for (const item of list) {
    const match = String(item).match(new RegExp(`(?:^|;\\s*)(${SESSION_COOKIE_NAME}=[^;]+)`))
    if (match) return match[1]
  }
  return null
}

export function clearSessionCookie(): void {
  uni.removeStorageSync(SESSION_COOKIE_KEY)
}

/**
 * 会话过期回调：request 拦截到 401 时清掉本地凭据并通知（由 auth store 注册）。
 * 用回调注册避免 client ↔ store 循环依赖。
 */
let unauthorizedHandler: (() => void) | null = null

export function onUnauthorized(handler: (() => void) | null): void {
  unauthorizedHandler = handler
}

function handleUnauthorized(): void {
  uni.removeStorageSync(CSRF_STORAGE_KEY)
  clearSessionCookie()
  unauthorizedHandler?.()
}

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'PATCH' | 'PUT' | 'DELETE'
  body?: unknown
  params?: Record<string, string | number | boolean | undefined>
}

/**
 * 统一请求封装。
 *
 * 小程序端 Cookie 不自动管理：从响应 Set-Cookie 提取会话存 storage，
 * 每个请求带 Cookie 头；非 GET 请求带 X-CSRF-Token 头。
 */
export function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const method = (options.method ?? 'GET').toUpperCase()
    const headers: Record<string, string> = {}
    if (options.body !== undefined) {
      headers['Content-Type'] = 'application/json'
    }
    const sessionCookie = uni.getStorageSync(SESSION_COOKIE_KEY) as string
    if (sessionCookie) headers['Cookie'] = sessionCookie
    if (method !== 'GET') {
      const csrf = uni.getStorageSync(CSRF_STORAGE_KEY) as string
      if (csrf) headers['X-CSRF-Token'] = csrf
    }

    let url = `${API_BASE}${API_PREFIX}${path}`
    if (options.params) {
      const query = Object.entries(options.params)
        .filter(([, value]) => value !== undefined && value !== '')
        .map(([key, value]) => `${encodeURIComponent(key)}=${encodeURIComponent(String(value))}`)
        .join('&')
      if (query) url += `?${query}`
    }

    uni.request({
      url,
      method: method as UniApp.RequestOptions['method'],
      data: options.body as UniApp.RequestOptions['data'],
      header: headers,
      success: (res) => {
        // 登录类接口通过 Set-Cookie 下发会话，小程序端手动接住
        const cookie = extractSessionCookie(res.header as Record<string, unknown> | undefined)
        if (cookie) uni.setStorageSync(SESSION_COOKIE_KEY, cookie)
        if (res.statusCode >= 200 && res.statusCode < 300) {
          resolve(res.data as T)
        } else {
          // 会话过期/被吊销：统一清凭据并广播，各页面的 NeedLogin 门自动接管
          if (res.statusCode === 401) handleUnauthorized()
          reject(new ApiError(errorMessage(res.data, res.statusCode), res.statusCode))
        }
      },
      fail: (err) => {
        reject(new ApiError(err.errMsg || '网络连接失败', 0))
      },
    })
  })
}

export const api = {
  me: () => request<import('@/api/types').CurrentUser>('/auth/me'),
  wxLogin: (code: string) =>
    request<import('@/api/types').WxLoginResponse>('/auth/wx/login', {
      method: 'POST',
      body: { code },
    }),
  devLogin: (token: string) =>
    request<import('@/api/types').WxLoginResponse>('/auth/dev-login', {
      method: 'POST',
      body: { token },
    }),
  logout: () => request<void>('/auth/logout', { method: 'POST' }),

  listAccounts: () => request<import('@/api/types').Account[]>('/accounts'),
  createAccount: (input: import('@/api/types').CreateAccountInput) =>
    request<import('@/api/types').Account>('/accounts', { method: 'POST', body: input }),
  updateAccount: (id: number, input: import('@/api/types').UpdateAccountInput) =>
    request<import('@/api/types').Account>(`/accounts/${id}`, { method: 'PATCH', body: input }),
  deleteAccount: (id: number) => request<void>(`/accounts/${id}`, { method: 'DELETE' }),

  discoverCourses: (accountId: number) =>
    request<import('@/api/types').Course[]>(`/accounts/${accountId}/courses/discover`, {
      method: 'POST',
    }),
  getCourseChapters: (
    accountId: number,
    course: import('@/api/types').Course,
  ) =>
    request<import('@/api/types').CourseOutline>(
      `/accounts/${accountId}/courses/${course.course_id}/chapters`,
      {
        method: 'POST',
        body: { class_id: course.class_id, cpi: course.cpi, title: course.title },
      },
    ),

  listTasks: (params: { account_id?: number; status?: string; limit?: number; offset?: number }) =>
    request<import('@/api/types').StudyTask[]>('/tasks', { params }),
  getTask: (id: string) => request<import('@/api/types').StudyTaskDetail>(`/tasks/${id}`),
  createTask: (input: import('@/api/types').CreateTaskInput) =>
    request<import('@/api/types').StudyTaskDetail>('/tasks', { method: 'POST', body: input }),
  bulkCreateTasks: (tasks: import('@/api/types').CreateTaskInput[]) =>
    request<import('@/api/types').BulkTaskCreateResult>('/tasks/bulk-create', {
      method: 'POST',
      body: { tasks },
    }),
  taskAction: (id: string, action: 'pause' | 'resume' | 'cancel') =>
    request<import('@/api/types').StudyTaskDetail>(`/tasks/${id}/${action}`, { method: 'POST' }),
  bulkTaskAction: (taskIds: string[], action: 'pause' | 'resume' | 'cancel') =>
    request<{ total: number; succeeded: number; failed: number; results: unknown[] }>(
      '/tasks/bulk-action',
      { method: 'POST', body: { action, task_ids: taskIds } },
    ),
  deleteTask: (id: string) =>
    request<{ total: number; deleted: number; failed: number; results: unknown[] }>('/tasks/bulk', {
      method: 'DELETE',
      body: { task_ids: [id] },
    }),
  clearTaskHistory: () =>
    request<{ deleted: number }>('/tasks/history', { method: 'DELETE' }),

  listEvents: (params: { after_id?: number; limit?: number; account_id?: number }) =>
    request<import('@/api/types').SystemEvent[]>('/events', { params }),
  listInterventions: () =>
    request<import('@/api/types').ManualIntervention[]>('/operations/interventions'),
  resolveInterventions: (ids: number[]) =>
    request<{ requested: number; resolved: number }>('/operations/interventions/resolve', {
      method: 'POST',
      body: { ids },
    }),

  answerPublic: () => request<import('@/api/types').AnswerPublic>('/settings/answer-public'),
}
