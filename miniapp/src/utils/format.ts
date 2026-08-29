import type { TaskStatus } from '@/api/types'

export const TASK_STATUS_LABELS: Record<TaskStatus, string> = {
  queued: '排队中',
  running: '进行中',
  pause_requested: '暂停中',
  paused: '已暂停',
  cancel_requested: '取消中',
  recovering: '恢复中',
  succeeded: '已完成',
  needs_attention: '需处理',
  failed: '失败',
  canceled: '已取消',
}

export const CHAPTER_STATUS_LABELS: Record<string, string> = {
  pending: '待处理',
  running: '进行中',
  succeeded: '已完成',
  already_completed: '已提前完成',
  unsubmitted: '未提交',
  skipped_not_open: '未开放跳过',
  failed: '失败',
  canceled: '已取消',
}

export const EVENT_KIND_LABELS: Record<string, string> = {
  'task.queued': '任务已排队',
  'task.claimed': '任务开始执行',
  'task.paused': '任务已暂停',
  'task.pause_requested': '请求暂停',
  'task.pause_canceled': '取消暂停',
  'task.resumed': '任务已恢复',
  'task.canceled': '任务已取消',
  'task.cancel_requested': '请求取消',
  'task.recovering': '任务恢复中',
  'task.requeued': '任务重新排队',
  'task.failed': '任务失败',
  'task.succeeded': '任务完成',
  'task.needs_attention': '任务需要处理',
  'task.run.completed': '执行结束',
  'task.run.released': '租约释放',
  'task.worker_launch_failed': 'Worker 启动失败',
  'task.worker_failed': 'Worker 异常退出',
  'task.worker_completed': 'Worker 正常退出',
  'task.lease_expired': '租约过期',
  'chapter.started': '章节开始',
  'chapter.running': '章节进行中',
  'chapter.succeeded': '章节完成',
  'chapter.already_completed': '章节已提前完成',
  'chapter.failed': '章节失败',
  'chapter.unsubmitted': '章节未提交',
  'chapter.skipped_not_open': '章节未开放已跳过',
  'chapter.canceled': '章节取消',
  'chapter.video.progress': '视频进度',
  'chapter.quiz.submitted': '测验已提交',
  'operator.intervention_resolved': '人工处理完成',
  'notification.sent': '通知已发送',
  'notification.failed': '通知发送失败',
}

export function eventKindLabel(kind: string): string {
  return EVENT_KIND_LABELS[kind] ?? kind
}

export function formatTime(value: string | null | undefined): string {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(
    date.getMinutes(),
  )}`
}

export function formatDuration(started: string | null, finished: string | null): string {
  if (!started) return '—'
  const end = finished ? new Date(finished).getTime() : Date.now()
  const seconds = Math.max(0, Math.floor((end - new Date(started).getTime()) / 1000))
  if (seconds < 60) return `${seconds} 秒`
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟`
  return `${(seconds / 3600).toFixed(1)} 小时`
}
