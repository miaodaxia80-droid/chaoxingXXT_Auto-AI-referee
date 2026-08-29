// 与 backend/src/chaoxing_app/api 对齐的响应类型（仅小程序所需子集）

export interface AppUser {
  id: number
  nickname: string
  avatar_url: string
  quotas: Record<string, number>
}

export interface CurrentUser {
  username: string | null
  csrf_token: string
  kind: 'admin' | 'app_user'
  user: AppUser | null
}

export interface WxLoginResponse {
  csrf_token: string
  expires_at: string
  user: AppUser
}

export interface Account {
  id: number
  remark: string
  username_hint: string
  enabled: boolean
  user_agent: string
  speed: number
  chapter_concurrency: number
  unopened_policy: 'retry' | 'skip'
  has_password: boolean
  has_cookies: boolean
}

export interface CreateAccountInput {
  username: string
  password?: string
  cookies?: string
  remark: string
  user_agent?: string
  speed: number
  chapter_concurrency: number
  unopened_policy: 'retry' | 'skip'
}

export interface UpdateAccountInput {
  remark?: string
  username?: string
  password?: string
  cookies?: string
  user_agent?: string
  speed?: number
  chapter_concurrency?: number
  unopened_policy?: 'retry' | 'skip'
  enabled?: boolean
  clear_password?: boolean
  clear_cookies?: boolean
}

export interface Course {
  course_id: string
  class_id: string
  cpi: string
  title: string
  teacher: string
  description: string
}

export interface CourseChapter {
  chapter_id: string
  title: string
  position: number
  job_count: number
  is_completed: boolean
  requires_unlock: boolean
}

export interface CourseOutline {
  has_locked_chapters: boolean
  chapters: CourseChapter[]
}

export type TaskStatus =
  | 'queued'
  | 'running'
  | 'pause_requested'
  | 'paused'
  | 'cancel_requested'
  | 'recovering'
  | 'succeeded'
  | 'needs_attention'
  | 'failed'
  | 'canceled'

export interface StudyTask {
  id: string
  account_id: number
  account_label: string
  course_id: string
  class_id: string
  cpi: string
  course_title: string
  status: TaskStatus
  desired_state: string
  priority: number
  selected_chapter_ids: string[] | null
  chapter_total: number
  chapter_succeeded: number
  chapter_needs_attention: number
  created_at: string
  updated_at: string
  run_after: string
  started_at: string | null
  finished_at: string | null
  last_error: string | null
}

export interface TaskChapter {
  chapter_id: string
  title: string
  position: number
  status: string
  attempts: number
  last_error: string | null
  started_at: string | null
  finished_at: string | null
}

export interface StudyTaskDetail extends StudyTask {
  chapters: TaskChapter[]
}

export interface CreateTaskInput {
  account_id: number
  course_id: string
  class_id: string
  cpi: string
  course_title: string
  chapters: { chapter_id: string; title: string; position: number }[]
}

export interface BulkTaskCreateResult {
  total: number
  created: number
  failed: number
  results: {
    course_id: string
    class_id: string
    status: 'created' | 'failed'
    task: StudyTaskDetail | null
    error_code: string | null
    error: string | null
  }[]
}

export interface ManualIntervention {
  id: number
  task_id: string
  account_id: number
  account_label: string
  course_title: string
  chapter_id: string
  chapter_title: string
  status: string
  reason: string | null
  occurred_at: string
}

export type EventLevel = 'info' | 'warning' | 'error'

export interface SystemEvent {
  id: number
  task_id: string | null
  account_id: number | null
  chapter_id: string | null
  chapter_title: string | null
  kind: string
  level: EventLevel
  payload: Record<string, unknown>
  occurred_at: string
}

export interface AnswerPublic {
  enabled: boolean
  provider: string
  submit_mode: string
  threshold: number
  config: Record<string, unknown>
  profile: Record<string, unknown>
}
