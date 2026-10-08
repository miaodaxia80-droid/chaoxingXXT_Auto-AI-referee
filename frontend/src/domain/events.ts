import type { SystemEvent } from '@/api/types'

export const EVENT_LABELS: Record<string, string> = {
  'task.queued': '任务已加入队列',
  'task.claimed': '任务开始执行',
  'task.paused': '任务已暂停',
  'task.pause_requested': '正在暂停任务',
  'task.pause_canceled': '任务继续执行',
  'task.resumed': '任务已恢复',
  'task.canceled': '任务已取消',
  'task.cancel_requested': '正在取消任务',
  'task.recovering': '任务正在恢复',
  'task.requeued': '任务已重新排队',
  'task.failed': '任务执行失败',
  'task.succeeded': '任务已完成',
  'task.needs_attention': '任务需要处理',
  'task.run.completed': '本次运行结束',
  'task.run.released': '本次运行已释放',
  'task.worker_launch_failed': '执行进程启动失败',
  'task.worker_failed': '执行进程异常退出',
  'task.worker_completed': '执行进程结束',
  'task.lease_expired': '任务运行超时',
  'chapter.started': '章节开始执行',
  'chapter.running': '章节执行中',
  'chapter.succeeded': '章节已完成',
  'chapter.already_completed': '章节此前已完成',
  'chapter.failed': '章节执行失败',
  'chapter.unsubmitted': '章节需要处理',
  'chapter.skipped_not_open': '章节尚未开放，已跳过',
  'chapter.canceled': '章节已取消',
  'chapter.unresolved_task_points': '发现无法识别的任务点',
  'chapter.unsupported_task_point': '发现暂不支持的任务点',
  'chapter.task_point_completed': '任务点已完成',
  'chapter.video.started': '视频开始播放',
  'chapter.video.progress': '视频进度已更新',
  'chapter.video.completion_retry': '正在确认视频完成状态',
  'chapter.audio.started': '音频开始播放',
  'chapter.audio.progress': '音频进度已更新',
  'chapter.audio.completion_retry': '正在确认音频完成状态',
  'chapter.document.completed': '文档任务已完成',
  'chapter.reading.completed': '阅读任务已完成',
  'chapter.empty_page.completed': '页面任务已完成',
  'chapter.quiz.submitted': '测验已提交',
  'chapter.quiz.saved': '测验答案已保存',
  'chapter.quiz.unsubmitted': '测验暂未提交',
  'chapter.quiz.rejected': '测验提交失败',
  'chapter.discussion.completed': '讨论回复已发布',
  'chapter.discussion.auto_reply_disabled': '讨论任务未自动回帖',
  'chapter.discussion.pending_review': '讨论回复等待审核',
  'chapter.completion_unverified': '平台未确认完成',
  'operator.intervention_resolved': '待处理事项已确认',
  'notification.sent': '通知已发送',
  'notification.failed': '通知发送失败',
}

export const REASON_MESSAGES: Record<string, string> = {
  platform_authentication_failed: '学习通登录状态已失效，请检查账号凭据后再试。',
  account_runtime_unavailable: '账号运行环境暂不可用，请稍后再试。',
  platform_response_invalid: '学习通返回了无法识别的数据，请刷新课程后再试。',
  platform_completion_rejected: '学习通未接受本次完成记录，请稍后再试。',
  platform_request_failed: '请求学习通失败，请检查网络后再试。',
  internal_execution_error: '执行过程中发生内部错误，请查看技术详情。',
  chapter_not_found: '课程中已找不到这个章节，请重新获取课程目录。',
  chapter_not_open: '章节尚未开放，已按账号设置处理。',
  quiz_requires_answers: '测验仍有题目无法作答，需要手动处理。',
  unsupported_task_point: '章节包含当前版本暂不支持的任务点。',
  unresolved_task_points: '章节中有任务点无法识别，需要检查页面内容。',
  discussion_auto_reply_disabled: '章节包含讨论任务，账号未开启自动回帖，请手动回复或在账号设置中开启。',
  discussion_reply_pending_review: '讨论回复已提交，正在等待平台审核。',
  completion_unverified: '已执行完成操作，但学习通尚未确认完成，将自动重试。',
  provider_unconfigured: '尚未配置可用的答题服务。',
  coverage_below_threshold: '可回答题目比例不足，测验未自动提交。',
  provider_rejected: '通知服务拒绝了本次发送请求。',
  configuration_invalid: '相关服务配置无效，请检查设置。',
  max_attempts_exceeded: '多次尝试仍未成功，请检查配置或网络。',
}

export const STATUS_MESSAGES: Record<string, string> = {
  queued: '任务正在等待执行。',
  running: '任务正在执行中。',
  pause_requested: '任务将在安全位置暂停。',
  paused: '任务已暂停，可在任务页面恢复。',
  cancel_requested: '任务将在安全位置取消。',
  recovering: '任务正在从上次中断处恢复。',
  succeeded: '所选章节已全部处理完成。',
  needs_attention: '部分章节未能自动完成，需要进一步处理。',
  failed: '任务未能正常完成，请查看失败原因。',
  canceled: '任务已取消。',
}

export function eventLabel(kind: string): string {
  return EVENT_LABELS[kind] ?? '系统活动'
}

/** Translate a backend reason code; unknown codes fall back to a neutral sentence. */
export function reasonMessage(code: string | null | undefined): string | null {
  if (!code) return null
  return REASON_MESSAGES[code] ?? null
}

export function eventTime(event: SystemEvent): number {
  const value = new Date(event.occurred_at).getTime()
  return Number.isNaN(value) ? 0 : value
}

export function displayValue(value: unknown): string | null {
  if (typeof value === 'boolean') return value ? '是' : '否'
  if (typeof value === 'number' || typeof value === 'string') return String(value)
  return null
}

const PAYLOAD_LABELS: Record<string, string> = {
  status: '状态',
  reason: '原因码',
  count: '数量',
  answered_count: '已回答',
  total_questions: '题目总数',
  coverage: '覆盖率',
  provider_error_count: '答题服务错误',
  provider_result_reason: '答题结果',
  channel: '通知渠道',
  exit_reason: '结束原因',
  run_id: '运行编号',
  attempt: '尝试次数',
  task_type: '任务点类型',
  play_time: '播放进度',
  duration: '总时长',
  unopened_policy: '未开放策略',
  title: '标题',
  reply_source: '回复来源',
}

export function payloadEntries(event: SystemEvent): Array<{ label: string; value: string }> {
  const entries: Array<{ label: string; value: string }> = []
  for (const [key, value] of Object.entries(event.payload)) {
    const formatted = displayValue(value)
    const label = PAYLOAD_LABELS[key]
    if (!label || formatted === null) continue
    entries.push({ label, value: formatted })
  }
  return entries
}
