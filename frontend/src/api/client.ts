import type {
  AnswerIntegration,
  NotificationChannelKind,
  NotificationIntegration,
  StudyTask,
  SystemSettings,
  UpdateAnswerIntegrationInput,
  UpdateNotificationIntegrationInput,
  UpdateSystemSettingsInput,
} from '@/api/types'

const TASK_PAGE_SIZE = 200

export async function getAllTasks(): Promise<StudyTask[]> {
  const all: StudyTask[] = []
  let offset = 0
  for (;;) {
    const page = await apiRequest<StudyTask[]>(
      `/tasks?limit=${TASK_PAGE_SIZE}&offset=${offset}`,
    )
    all.push(...page)
    if (page.length < TASK_PAGE_SIZE) return all
    offset += page.length
  }
}

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

const VALIDATION_FIELD_LABELS: Record<string, string> = {
  username: '管理员账号',
  password: '密码',
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function validationIssueMessage(issue: unknown): string | null {
  if (!isRecord(issue) || typeof issue.msg !== 'string') return null
  const location = Array.isArray(issue.loc) ? issue.loc : []
  const field = [...location].reverse().find((part) => typeof part === 'string')
  const label = typeof field === 'string' ? VALIDATION_FIELD_LABELS[field] : undefined
  const message = issue.msg.replace(/^Value error,\s*/i, '').trim()
  if (!message) return null
  return label ? `${label}：${message}` : message
}

function apiErrorMessage(payload: unknown, status: number): string {
  if (!isRecord(payload)) return `请求失败 (${status})`
  if (typeof payload.detail === 'string' && payload.detail.trim()) return payload.detail
  if (Array.isArray(payload.detail)) {
    const messages = payload.detail
      .map(validationIssueMessage)
      .filter((message): message is string => message !== null)
    if (messages.length > 0) return messages.join('；')
  }
  return `请求失败 (${status})`
}

export function setCsrfToken(token: string | null): void {
  if (token) sessionStorage.setItem(CSRF_STORAGE_KEY, token)
  else sessionStorage.removeItem(CSRF_STORAGE_KEY)
}

export async function apiRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
  const method = (init.method ?? 'GET').toUpperCase()
  const headers = new Headers(init.headers)
  if (init.body && !(init.body instanceof FormData) && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }
  if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) {
    const csrf = sessionStorage.getItem(CSRF_STORAGE_KEY)
    if (csrf) headers.set('X-CSRF-Token', csrf)
  }

  const response = await fetch(`${API_PREFIX}${path}`, {
    ...init,
    headers,
    credentials: 'include',
  })
  if (!response.ok) {
    const payload: unknown = await response.json().catch(() => null)
    throw new ApiError(apiErrorMessage(payload, response.status), response.status)
  }
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

export function getSystemSettings(): Promise<SystemSettings> {
  return apiRequest<SystemSettings>('/settings')
}

export function updateSystemSettings(
  input: UpdateSystemSettingsInput,
): Promise<SystemSettings> {
  return apiRequest<SystemSettings>('/settings', {
    method: 'PATCH',
    body: JSON.stringify(input),
  })
}

export function getAnswerIntegration(): Promise<AnswerIntegration> {
  return apiRequest<AnswerIntegration>('/settings/integrations/answer')
}

export function updateAnswerIntegration(
  input: UpdateAnswerIntegrationInput,
): Promise<AnswerIntegration> {
  return apiRequest<AnswerIntegration>('/settings/integrations/answer', {
    method: 'PATCH',
    body: JSON.stringify(input),
  })
}

export function getNotificationIntegrations(): Promise<NotificationIntegration[]> {
  return apiRequest<NotificationIntegration[]>('/settings/integrations/notifications')
}

export function updateNotificationIntegration(
  channel: NotificationChannelKind,
  input: UpdateNotificationIntegrationInput,
): Promise<NotificationIntegration> {
  return apiRequest<NotificationIntegration>(
    `/settings/integrations/notifications/${channel}`,
    {
      method: 'PATCH',
      body: JSON.stringify(input),
    },
  )
}
