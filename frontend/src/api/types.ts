export interface SetupStatus {
  required: boolean
}

export interface AuthResponse {
  username: string
  csrf_token: string
  expires_at: string
}

export type SessionKind = 'admin' | 'app_user'

export interface AppUserProfile {
  id: number
  nickname: string
  avatar_url: string
  quotas: Record<string, number>
  username: string | null
  plan_expires_at: string | null
  task_credits: number
}

export interface AppUserAdmin extends AppUserProfile {
  openid: string
  has_password: boolean
  disabled: boolean
  account_count: number
  active_task_count: number
  created_at: string
  updated_at: string
}

export interface CreateAppUserInput {
  username: string
  password: string
  nickname?: string
  quotas?: Record<string, number>
}

export interface UpdateAppUserInput {
  disabled?: boolean
  nickname?: string
  quotas?: Record<string, number>
  password?: string
  plan_extend_days?: number
  task_credits_add?: number
}

export interface CurrentUser {
  username: string | null
  csrf_token: string
  kind: SessionKind
  user: AppUserProfile | null
}

export type CardKeyKind = 'time' | 'count'
export type CardKeyStatus = 'unused' | 'used' | 'revoked'

export interface CardKey {
  id: number
  code_hint: string
  kind: CardKeyKind
  value: number
  batch: string
  status: CardKeyStatus
  used_by: number | null
  used_at: string | null
  created_at: string
}

export interface CardKeyIssueItem {
  code: string
  kind: CardKeyKind
  value: number
}

export interface CardKeyGenerateResponse {
  created: number
  items: CardKeyIssueItem[]
}

export interface CreateCardKeysInput {
  kind: CardKeyKind
  value: number
  count: number
  batch?: string
}

export interface CardKeyRedeemResponse {
  kind: CardKeyKind
  value: number
  plan_expires_at: string | null
  task_credits: number
}

export interface EntitlementResponse {
  plan_expires_at: string | null
  task_credits: number
  active: boolean
}

export interface HealthResponse {
  status: string
  version: string
}

export interface ResourceMetric {
  value: number | null
  unit: 'percent' | 'celsius'
  available: boolean
}

export interface WorkerRunHealth {
  task_id: string
  worker_id: string
  process_id: number | null
  heartbeat_at: string
  lease_expires_at: string | null
  heartbeat_age_seconds: number
  lease_stale: boolean
}

export interface OperationsHealth {
  status: 'ok' | 'degraded'
  sampled_at: string
  uptime_seconds: number
  cpu: ResourceMetric
  memory: ResourceMetric
  temperature: ResourceMetric
  worker: {
    enabled: boolean
    service_running: boolean
    configured_capacity: number
    active_process_count: number
    active_task_ids: string[]
    durable_active_run_count: number
    stale_run_count: number
    runs: WorkerRunHealth[]
  }
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

export interface ResolveInterventionsResponse {
  requested: number
  resolved: number
  already_resolved: number
  not_actionable: number
}

export interface EventArchiveResponse {
  archived: number
  archived_total: number
}

export interface SystemSettings {
  worker_enabled: boolean
  run_window_enabled: boolean
  run_window_start: string
  run_window_end: string
  timezone: string
  event_retention_days: number
  updated_at: string
}

export interface UpdateSystemSettingsInput {
  worker_enabled: boolean
  run_window_enabled: boolean
  run_window_start: string
  run_window_end: string
  timezone: string
  event_retention_days: number
}

export type AnswerProviderKind =
  | 'yanxi'
  | 'like'
  | 'tiku_adapter'
  | 'openai_compatible'
  | 'siliconflow'

export type AnswerSubmitMode = 'auto' | 'save_only' | 'submit'

export interface AnswerProviderPublicConfig {
  endpoint: string | null
  base_url: string | null
  model: string | null
  search: boolean | null
  allow_unsafe_endpoint: boolean
}

export interface AnswerProfile {
  ensemble_enabled: boolean
  models: string[]
  referee_model: string
  max_workers: number
  cache_enabled: boolean
  cache_ttl_seconds: number
  course_context_enabled: boolean
  web_search_enabled: boolean
}

export interface AnswerIntegration {
  enabled: boolean
  provider: AnswerProviderKind
  submit_mode: AnswerSubmitMode
  threshold: number
  revision: number
  config: AnswerProviderPublicConfig
  profile: AnswerProfile | null
  has_tokens: boolean
  has_token: boolean
  has_api_key: boolean
  updated_at: string
}

export interface UpdateAnswerIntegrationInput {
  expected_revision: number
  enabled?: boolean
  provider?: AnswerProviderKind
  submit_mode?: AnswerSubmitMode
  threshold?: number
  endpoint?: string
  base_url?: string
  model?: string
  search?: boolean
  allow_unsafe_endpoint?: boolean
  tokens?: string
  token?: string
  api_key?: string
  clear_tokens?: boolean
  clear_token?: boolean
  clear_api_key?: boolean
  profile?: AnswerProfile
}

export type NotificationChannelKind = 'server_chan' | 'qmsg' | 'bark' | 'telegram'

export interface NotificationIntegration {
  channel: NotificationChannelKind
  enabled: boolean
  revision: number
  has_webhook_url: boolean
  has_bot_token: boolean
  has_chat_id: boolean
  updated_at: string
}

export interface UpdateNotificationIntegrationInput {
  expected_revision: number
  enabled?: boolean
  webhook_url?: string
  bot_token?: string
  chat_id?: string
  clear_webhook_url?: boolean
  clear_bot_token?: boolean
  clear_chat_id?: boolean
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
  answer_profile_override: AnswerProfileOverride | null
}

export type AnswerProfileOverride = Partial<AnswerProfile>

export interface CreateAccountInput {
  username: string
  password?: string
  cookies?: string
  remark: string
  user_agent: string
  speed: number
  chapter_concurrency: number
  unopened_policy: 'retry' | 'skip'
  answer_profile_override?: AnswerProfileOverride | null
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
  answer_profile_override?: AnswerProfileOverride | null
  clear_answer_profile_override?: boolean
}

export type AccountImportStatus = 'created' | 'duplicate' | 'invalid'

export interface AccountImportRow {
  line: number
  status: AccountImportStatus
  username_hint: string | null
  account_id: number | null
  error_code: string | null
}

export interface AccountImportResult {
  total: number
  created: number
  duplicate: number
  invalid: number
  results: AccountImportRow[]
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

export interface CreateTaskChapterInput {
  chapter_id: string
  title: string
  position: number
}

export interface CreateTaskInput {
  account_id: number
  course_id: string
  class_id: string
  cpi: string
  course_title: string
  chapters: CreateTaskChapterInput[]
}

export interface BulkTaskCreateItemResult {
  course_id: string
  class_id: string
  status: 'created' | 'failed'
  task: StudyTaskDetail | null
  error_code: string | null
  error: string | null
}

export interface BulkTaskCreateResult {
  total: number
  created: number
  failed: number
  results: BulkTaskCreateItemResult[]
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

export type DesiredTaskState = 'run' | 'pause' | 'cancel'

export type TaskAction = 'pause' | 'resume' | 'cancel'

export interface BulkTaskActionItemResult {
  task_id: string
  status: 'succeeded' | 'failed'
  task: StudyTaskDetail | null
  error_code: string | null
  error: string | null
}

export interface BulkTaskActionResult {
  total: number
  succeeded: number
  failed: number
  results: BulkTaskActionItemResult[]
}

export interface BulkTaskDeleteItemResult {
  task_id: string
  status: 'deleted' | 'failed'
  error_code: string | null
  error: string | null
}

export interface BulkTaskDeleteResult {
  total: number
  deleted: number
  failed: number
  results: BulkTaskDeleteItemResult[]
}

export interface TaskHistoryCleanupResult {
  deleted: number
}

export interface StudyTask {
  id: string
  account_id: number
  account_label: string
  course_id: string
  class_id: string
  cpi: string
  course_title: string
  status: TaskStatus
  desired_state: DesiredTaskState
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
