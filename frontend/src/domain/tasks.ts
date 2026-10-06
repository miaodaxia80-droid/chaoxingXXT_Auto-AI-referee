import type { StudyTask, TaskStatus } from '@/api/types'

export type TaskStatusTagType = 'default' | 'success' | 'warning' | 'error' | 'info'

const TASK_STATUS_META: Record<TaskStatus, { label: string; type: TaskStatusTagType }> = {
  queued: { label: '排队中', type: 'info' },
  running: { label: '执行中', type: 'success' },
  pause_requested: { label: '等待暂停', type: 'warning' },
  paused: { label: '已暂停', type: 'warning' },
  cancel_requested: { label: '等待取消', type: 'warning' },
  recovering: { label: '恢复中', type: 'info' },
  succeeded: { label: '已完成', type: 'success' },
  needs_attention: { label: '需要处理', type: 'warning' },
  failed: { label: '失败', type: 'error' },
  canceled: { label: '已取消', type: 'default' },
}

const TERMINAL_TASK_STATUSES: ReadonlySet<TaskStatus> = new Set([
  'succeeded',
  'needs_attention',
  'failed',
  'canceled',
])

const RUNNING_TASK_STATUSES: ReadonlySet<TaskStatus> = new Set([
  'running',
  'pause_requested',
  'cancel_requested',
  'recovering',
])

export function statusMeta(status: TaskStatus) {
  return TASK_STATUS_META[status]
}

export function isTerminalTask(task: Pick<StudyTask, 'status'>): boolean {
  return TERMINAL_TASK_STATUSES.has(task.status)
}

export function isRunningTask(task: Pick<StudyTask, 'status'>): boolean {
  return RUNNING_TASK_STATUSES.has(task.status)
}

export function canPauseTask(task: Pick<StudyTask, 'status'>): boolean {
  return task.status === 'queued' || task.status === 'running'
}

export function canResumeTask(task: Pick<StudyTask, 'status'>): boolean {
  return task.status === 'paused' || task.status === 'pause_requested'
}

export function taskProgress(task: StudyTask): number {
  if (task.chapter_total === 0) return 0
  return Math.round(
    ((task.chapter_succeeded + task.chapter_needs_attention) / task.chapter_total) * 100,
  )
}

export function taskProgressColor(task: StudyTask): string {
  if (task.status === 'failed') return 'var(--color-danger)'
  if (task.status === 'needs_attention' || task.chapter_needs_attention > 0) {
    return 'var(--color-warning-strong)'
  }
  return 'var(--color-accent)'
}

export type TaskFilter = 'all' | 'active' | 'paused' | 'attention' | 'failed' | 'finished'

export const TASK_FILTERS: { value: TaskFilter; label: string }[] = [
  { value: 'all', label: '全部' },
  { value: 'active', label: '进行中' },
  { value: 'paused', label: '已暂停' },
  { value: 'attention', label: '需处理' },
  { value: 'failed', label: '失败' },
  { value: 'finished', label: '已结束' },
]

export function isTaskFilter(value: unknown): value is TaskFilter {
  return TASK_FILTERS.some((filter) => filter.value === value)
}

export function taskFilterOf(task: Pick<StudyTask, 'status'>): Exclude<TaskFilter, 'all'> {
  switch (task.status) {
    case 'paused':
      return 'paused'
    case 'needs_attention':
      return 'attention'
    case 'failed':
      return 'failed'
    case 'succeeded':
    case 'canceled':
      return 'finished'
    default:
      return 'active'
  }
}

export function matchesTaskFilter(task: Pick<StudyTask, 'status'>, filter: TaskFilter): boolean {
  return filter === 'all' || taskFilterOf(task) === filter
}

const CHAPTER_STATUS_META: Record<string, { label: string; type: TaskStatusTagType }> = {
  pending: { label: '等待中', type: 'default' },
  running: { label: '执行中', type: 'info' },
  succeeded: { label: '已完成', type: 'success' },
  already_completed: { label: '此前已完成', type: 'success' },
  unsubmitted: { label: '需要处理', type: 'warning' },
  skipped_not_open: { label: '未开放', type: 'warning' },
  failed: { label: '失败', type: 'error' },
  canceled: { label: '已取消', type: 'default' },
}

export function chapterStatusMeta(status: string) {
  return CHAPTER_STATUS_META[status] ?? { label: status, type: 'default' as const }
}
