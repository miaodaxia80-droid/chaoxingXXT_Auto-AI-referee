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

export function taskProgress(task: StudyTask): number {
  if (task.chapter_total === 0) return 0
  return Math.round(
    ((task.chapter_succeeded + task.chapter_needs_attention) / task.chapter_total) * 100,
  )
}

export function taskProgressColor(task: StudyTask): string {
  if (task.status === 'failed') return '#d03050'
  if (task.status === 'needs_attention' || task.chapter_needs_attention > 0) return '#d9822b'
  if (task.status === 'succeeded') return '#18a058'
  return '#2f7f6c'
}
