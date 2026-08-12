<script setup lang="ts">
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import {
  Activity,
  Archive,
  Bell,
  CheckCircle2,
  ChevronDown,
  CircleAlert,
  CircleDot,
  Clock3,
  ExternalLink,
  Info,
  ListTree,
  Radio,
  RefreshCw,
  TriangleAlert,
  WifiOff,
} from 'lucide-vue-next'
import {
  NAlert,
  NButton,
  NDatePicker,
  NEmpty,
  NModal,
  NSelect,
  NSkeleton,
  NTag,
  useMessage,
} from 'naive-ui'
import type { SelectOption } from 'naive-ui'
import { computed, onBeforeUnmount, ref } from 'vue'
import type { Component } from 'vue'
import { useRouter } from 'vue-router'

import { apiRequest, getAllTasks } from '@/api/client'
import { streamSystemEvents, type EventStreamStatus } from '@/api/events'
import type { Account, EventArchiveResponse, StudyTask, SystemEvent } from '@/api/types'

type ActivityCategory = 'attention' | 'running' | 'completed' | 'system'
type ActivityTone = 'danger' | 'warning' | 'success' | 'info' | 'neutral'

interface ActivityGroup {
  key: string
  events: SystemEvent[]
  latest: SystemEvent
  task: StudyTask | null
  category: ActivityCategory
  tone: ActivityTone
  title: string
  description: string
  chapterTitle: string | null
  accountLabel: string | null
}

const queryClient = useQueryClient()
const router = useRouter()
const message = useMessage()
const categoryFilter = ref<ActivityCategory | null>(null)
const streamStatus = ref<EventStreamStatus>('connecting')
const archiveOpen = ref(false)
const archiveBefore = ref<number>(Date.now() - 30 * 24 * 60 * 60 * 1000)
const expandedGroups = ref(new Set<string>())
let streamController: AbortController | null = null
let taskRefreshTimer: number | null = null

const categoryOptions: SelectOption[] = [
  { label: '需要处理', value: 'attention' },
  { label: '进行中', value: 'running' },
  { label: '已完成', value: 'completed' },
  { label: '系统记录', value: 'system' },
]

const events = useQuery({
  queryKey: ['events'],
  queryFn: () => apiRequest<SystemEvent[]>('/events?limit=200'),
})

const accounts = useQuery({
  queryKey: ['accounts'],
  queryFn: () => apiRequest<Account[]>('/accounts'),
  staleTime: 30_000,
})

const tasks = useQuery({
  queryKey: ['tasks'],
  queryFn: getAllTasks,
  staleTime: 10_000,
  refetchInterval: 10_000,
})

const archiveMutation = useMutation({
  mutationFn: () =>
    apiRequest<EventArchiveResponse>('/events/archive', {
      method: 'POST',
      body: JSON.stringify({ before: new Date(archiveBefore.value).toISOString(), limit: 10_000 }),
    }),
  async onSuccess(result) {
    archiveOpen.value = false
    message.success(`已归档 ${result.archived} 条事件`)
    await queryClient.invalidateQueries({ queryKey: ['events'] })
  },
  onError: () => message.error('事件归档失败'),
})

const accountById = computed(
  () => new Map((accounts.data.value ?? []).map((account) => [account.id, account])),
)
const taskById = computed(
  () => new Map((tasks.data.value ?? []).map((task) => [task.id, task])),
)

const EVENT_LABELS: Record<string, string> = {
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
  'operator.intervention_resolved': '待处理事项已确认',
  'notification.sent': '通知已发送',
  'notification.failed': '通知发送失败',
}

const REASON_MESSAGES: Record<string, string> = {
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
  provider_unconfigured: '尚未配置可用的答题服务。',
  coverage_below_threshold: '可回答题目比例不足，测验未自动提交。',
  provider_rejected: '通知服务拒绝了本次发送请求。',
  configuration_invalid: '相关服务配置无效，请检查设置。',
  max_attempts_exceeded: '多次尝试仍未成功，请检查配置或网络。',
}

const STATUS_MESSAGES: Record<string, string> = {
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

const dateFormatter = new Intl.DateTimeFormat('zh-CN', {
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hour12: false,
})

const relativeFormatter = new Intl.RelativeTimeFormat('zh-CN', { numeric: 'auto' })

function eventTime(event: SystemEvent): number {
  const value = new Date(event.occurred_at).getTime()
  return Number.isNaN(value) ? 0 : value
}

function formatDate(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '-' : dateFormatter.format(date)
}

function formatRelativeDate(value: string): string {
  const timestamp = new Date(value).getTime()
  if (Number.isNaN(timestamp)) return '-'
  const seconds = Math.round((timestamp - Date.now()) / 1000)
  if (Math.abs(seconds) < 60) return relativeFormatter.format(seconds, 'second')
  const minutes = Math.round(seconds / 60)
  if (Math.abs(minutes) < 60) return relativeFormatter.format(minutes, 'minute')
  const hours = Math.round(minutes / 60)
  if (Math.abs(hours) < 24) return relativeFormatter.format(hours, 'hour')
  const days = Math.round(hours / 24)
  if (Math.abs(days) < 7) return relativeFormatter.format(days, 'day')
  return dateFormatter.format(new Date(timestamp)).slice(0, 10)
}

function formatIdentifier(value: string): string {
  if (value.length <= 20) return value
  return `${value.slice(0, 8)}...${value.slice(-6)}`
}

function displayValue(value: unknown): string | null {
  if (typeof value === 'boolean') return value ? '是' : '否'
  if (typeof value === 'number' || typeof value === 'string') return String(value)
  return null
}

function reasonFor(eventsInGroup: SystemEvent[], task: StudyTask | null): string {
  for (const event of eventsInGroup) {
    const reason = displayValue(event.payload.reason)
    if (reason) {
      const message = REASON_MESSAGES[reason]
      if (message) return message
    }
    const providerReason = displayValue(event.payload.provider_result_reason)
    if (providerReason) {
      const message = REASON_MESSAGES[providerReason]
      if (message) return message
    }
  }
  if (task?.last_error) {
    const message = REASON_MESSAGES[task.last_error]
    if (message) return message
  }
  if (task?.status) {
    const message = STATUS_MESSAGES[task.status]
    if (message) return message
  }
  const latestStatus = displayValue(eventsInGroup[0]?.payload.status)
  if (latestStatus) {
    const message = STATUS_MESSAGES[latestStatus]
    if (message) return message
  }
  return eventDescription(eventsInGroup[0]!)
}

function eventDescription(event: SystemEvent): string {
  const reason = displayValue(event.payload.reason)
  if (reason) {
    return (
      REASON_MESSAGES[reason] ??
      (event.level === 'error' || event.level === 'warning'
        ? '这项操作未能正常完成，请查看详情。'
        : '系统记录了一项状态变化。')
    )
  }
  const count = event.payload.count
  if (typeof count === 'number') return `共发现 ${count} 个需要处理的项目。`
  const answered = event.payload.answered_count
  const total = event.payload.total_questions
  if (typeof answered === 'number' && typeof total === 'number') {
    return `已完成 ${answered} / ${total} 道题。`
  }
  const status = displayValue(event.payload.status)
  if (status) return STATUS_MESSAGES[status] ?? '系统状态已更新。'
  const channel = displayValue(event.payload.channel)
  if (channel) return `通知渠道：${channel}`
  return EVENT_LABELS[event.kind] ?? '记录了一项系统活动。'
}

function categoryFor(eventsInGroup: SystemEvent[], task: StudyTask | null): ActivityCategory {
  if (!eventsInGroup[0]?.task_id) return 'system'
  if (task) {
    if (task.status === 'succeeded' || task.status === 'canceled') return 'completed'
    if (task.status === 'failed' || task.status === 'needs_attention') return 'attention'
    return 'running'
  }
  if (
    eventsInGroup.some(
      (event) =>
        event.level === 'error' ||
        event.level === 'warning' ||
        event.kind.endsWith('.failed') ||
        event.kind.endsWith('.unsubmitted') ||
        event.kind === 'task.needs_attention',
    )
  ) {
    return 'attention'
  }
  if (
    eventsInGroup.some(
      (event) => event.kind === 'task.succeeded' || event.kind === 'chapter.succeeded',
    )
  ) {
    return 'completed'
  }
  return 'running'
}

function toneFor(category: ActivityCategory, eventsInGroup: SystemEvent[]): ActivityTone {
  if (category === 'completed') return 'success'
  if (category === 'running') return 'info'
  if (category === 'system') {
    if (eventsInGroup.some((event) => event.level === 'error')) return 'danger'
    if (eventsInGroup.some((event) => event.level === 'warning')) return 'warning'
    return 'neutral'
  }
  return eventsInGroup.some((event) => event.level === 'error') ? 'danger' : 'warning'
}

function titleFor(
  category: ActivityCategory,
  eventsInGroup: SystemEvent[],
  task: StudyTask | null,
): string {
  if (category === 'system') return EVENT_LABELS[eventsInGroup[0]!.kind] ?? '系统记录'
  if (task?.status === 'succeeded') return '任务已完成'
  if (task?.status === 'failed') return '任务执行失败'
  if (task?.status === 'needs_attention') return '任务结束，需要处理'
  if (task?.status === 'canceled') return '任务已取消'
  if (task?.status === 'paused') return '任务已暂停'
  if (category === 'attention') {
    const failed = eventsInGroup.find((event) => event.kind === 'chapter.failed')
    return failed ? '章节执行失败' : '任务需要处理'
  }
  return category === 'completed' ? '任务已完成' : '任务正在执行'
}

function accountLabel(event: SystemEvent, task: StudyTask | null): string | null {
  if (task?.account_label) return task.account_label
  if (event.account_id === null) return null
  const account = accountById.value.get(event.account_id)
  return account?.remark || account?.username_hint || `账号 ${event.account_id}`
}

function buildGroup(key: string, groupEvents: SystemEvent[]): ActivityGroup {
  const sorted = [...groupEvents].sort((left, right) => eventTime(right) - eventTime(left))
  const latest = sorted[0]!
  const task = latest.task_id ? (taskById.value.get(latest.task_id) ?? null) : null
  const category = categoryFor(sorted, task)
  return {
    key,
    events: sorted,
    latest,
    task,
    category,
    tone: toneFor(category, sorted),
    title: titleFor(category, sorted, task),
    description: category === 'system' ? eventDescription(latest) : reasonFor(sorted, task),
    chapterTitle: sorted.find((event) => event.chapter_title)?.chapter_title ?? null,
    accountLabel: accountLabel(latest, task),
  }
}

const activityGroups = computed<ActivityGroup[]>(() => {
  const grouped = new Map<string, SystemEvent[]>()
  for (const event of events.data.value ?? []) {
    const key = event.task_id ? `task:${event.task_id}` : `event:${event.id}`
    const current = grouped.get(key) ?? []
    current.push(event)
    grouped.set(key, current)
  }
  return [...grouped.entries()]
    .map(([key, groupEvents]) => buildGroup(key, groupEvents))
    .sort((left, right) => eventTime(right.latest) - eventTime(left.latest))
})

const filteredGroups = computed(() => {
  if (!categoryFilter.value) return activityGroups.value
  return activityGroups.value.filter((group) => group.category === categoryFilter.value)
})

const categoryCounts = computed(() => {
  const counts: Record<ActivityCategory, number> = {
    attention: 0,
    running: 0,
    completed: 0,
    system: 0,
  }
  for (const group of activityGroups.value) counts[group.category] += 1
  return counts
})

const STREAM_META = {
  connected: { label: '实时更新', type: 'success', icon: Radio },
  connecting: { label: '正在连接', type: 'default', icon: RefreshCw },
  offline: { label: '实时更新已断开', type: 'error', icon: WifiOff },
} as const
const activeStreamMeta = computed(() => STREAM_META[streamStatus.value])

const TONE_META: Record<
  ActivityTone,
  { label: string; type: 'default' | 'info' | 'success' | 'warning' | 'error'; icon: Component }
> = {
  danger: { label: '失败', type: 'error', icon: CircleAlert },
  warning: { label: '需要处理', type: 'warning', icon: TriangleAlert },
  success: { label: '已完成', type: 'success', icon: CheckCircle2 },
  info: { label: '进行中', type: 'info', icon: Clock3 },
  neutral: { label: '系统', type: 'default', icon: Info },
}

function scheduleTaskRefresh(event: SystemEvent): void {
  if (!event.kind.startsWith('task.') && !event.kind.startsWith('chapter.')) return
  if (taskRefreshTimer !== null) return
  taskRefreshTimer = window.setTimeout(() => {
    taskRefreshTimer = null
    void queryClient.invalidateQueries({ queryKey: ['tasks'] })
  }, 500)
}

async function startEventStream(): Promise<void> {
  streamController?.abort()
  const controller = new AbortController()
  streamController = controller
  streamStatus.value = 'connecting'
  try {
    const initialEvents = await queryClient.fetchQuery({
      queryKey: ['events'],
      queryFn: () => apiRequest<SystemEvent[]>('/events?limit=200'),
    })
    if (controller.signal.aborted) return
    const afterId = initialEvents.reduce((latest, event) => Math.max(latest, event.id), 0)
    await streamSystemEvents({
      afterId,
      level: null,
      signal: controller.signal,
      onStatus(status) {
        if (streamController === controller) streamStatus.value = status
      },
      onEvent(event) {
        queryClient.setQueryData<SystemEvent[]>(['events'], (current = []) => {
          const withoutDuplicate = current.filter((item) => item.id !== event.id)
          return [event, ...withoutDuplicate].sort((left, right) => right.id - left.id).slice(0, 200)
        })
        scheduleTaskRefresh(event)
      },
    })
  } catch {
    if (!controller.signal.aborted && streamController === controller) streamStatus.value = 'offline'
  }
}

void startEventStream()

onBeforeUnmount(() => {
  streamController?.abort()
  if (taskRefreshTimer !== null) window.clearTimeout(taskRefreshTimer)
})

function toggleGroup(key: string): void {
  const next = new Set(expandedGroups.value)
  if (next.has(key)) next.delete(key)
  else next.add(key)
  expandedGroups.value = next
}

function groupIsExpanded(key: string): boolean {
  return expandedGroups.value.has(key)
}

function goToTasks(): void {
  void router.push({ name: 'tasks' })
}

function isFutureDate(timestamp: number): boolean {
  return timestamp > Date.now()
}

function payloadEntries(event: SystemEvent): Array<{ label: string; value: string }> {
  const labels: Record<string, string> = {
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
  }
  const entries: Array<{ label: string; value: string }> = []
  for (const [key, value] of Object.entries(event.payload)) {
    const formatted = displayValue(value)
    if (!labels[key] || formatted === null) continue
    entries.push({ label: labels[key], value: formatted })
  }
  return entries
}

function groupTagLabel(group: ActivityGroup): string {
  if (group.category === 'attention') {
    return group.task?.status === 'failed' ? '失败' : '需要处理'
  }
  return TONE_META[group.tone].label
}
</script>

<template>
  <section class="content-section flush activity-section">
    <div class="section-heading padded activity-heading">
      <div class="activity-title">
        <Activity :size="18" />
        <div>
          <h2>活动记录</h2>
          <span>{{ activityGroups.length }} 项活动 · {{ events.data.value?.length ?? 0 }} 条事件</span>
        </div>
      </div>
      <div class="heading-actions activity-actions">
        <NTag
          size="small"
          :type="activeStreamMeta.type"
          :bordered="false"
          class="stream-status"
        >
          <component :is="activeStreamMeta.icon" :size="12" />
          <span>{{ activeStreamMeta.label }}</span>
        </NTag>
        <NButton
          quaternary
          circle
          title="刷新活动"
          aria-label="刷新活动"
          :loading="events.isFetching.value"
          @click="events.refetch()"
        >
          <template #icon><RefreshCw :size="16" /></template>
        </NButton>
        <NButton
          quaternary
          circle
          title="归档历史活动"
          aria-label="归档历史活动"
          @click="archiveOpen = true"
        >
          <template #icon><Archive :size="16" /></template>
        </NButton>
      </div>
    </div>

    <div class="activity-toolbar">
      <NSelect
        v-model:value="categoryFilter"
        class="activity-filter"
        clearable
        size="small"
        :options="categoryOptions"
        placeholder="全部活动"
        aria-label="按活动状态筛选"
      />
      <div class="activity-stats" aria-label="活动概况">
        <button
          type="button"
          :class="{ active: categoryFilter === 'attention' }"
          @click="categoryFilter = categoryFilter === 'attention' ? null : 'attention'"
        >
          <TriangleAlert :size="13" />需要处理 {{ categoryCounts.attention }}
        </button>
        <span>进行中 {{ categoryCounts.running }}</span>
        <span>已完成 {{ categoryCounts.completed }}</span>
      </div>
    </div>

    <NModal v-model:show="archiveOpen" preset="dialog" title="归档历史活动">
      <p class="archive-description">
        截止日期之前的活动将从列表和实时流中隐藏，但仍按保留策略留存在数据库中。
      </p>
      <NDatePicker
        v-model:value="archiveBefore"
        type="date"
        :is-date-disabled="isFutureDate"
        class="archive-picker"
      />
      <template #action>
        <NButton @click="archiveOpen = false">取消</NButton>
        <NButton
          type="primary"
          :loading="archiveMutation.isPending.value"
          @click="archiveMutation.mutate()"
        >
          确认归档
        </NButton>
      </template>
    </NModal>

    <NAlert v-if="events.isError.value" type="error" :bordered="false" class="activity-alert">
      <span>活动记录加载失败。</span>
      <NButton text type="primary" class="activity-retry" @click="events.refetch()">重试</NButton>
    </NAlert>

    <div v-else-if="events.isLoading.value" class="activity-loading" aria-label="正在加载活动记录">
      <div v-for="index in 4" :key="index" class="activity-skeleton-row">
        <NSkeleton circle :width="34" :height="34" />
        <div>
          <NSkeleton text style="width: 34%" />
          <NSkeleton text :repeat="2" />
        </div>
      </div>
    </div>

    <NEmpty
      v-else-if="activityGroups.length === 0"
      description="任务开始后，执行结果会显示在这里"
      class="activity-empty"
    >
      <template #icon><CheckCircle2 :size="24" /></template>
    </NEmpty>

    <NEmpty
      v-else-if="filteredGroups.length === 0"
      description="当前筛选条件下没有活动"
      class="activity-empty"
    >
      <template #icon><ListTree :size="24" /></template>
      <template #extra>
        <NButton size="small" @click="categoryFilter = null">清除筛选</NButton>
      </template>
    </NEmpty>

    <div v-else class="activity-list">
      <article
        v-for="group in filteredGroups"
        :key="group.key"
        class="activity-group"
        :class="`tone-${group.tone}`"
        data-testid="activity-group"
      >
        <div class="activity-summary">
          <div class="activity-marker" :class="`tone-${group.tone}`">
            <component :is="TONE_META[group.tone].icon" :size="17" />
          </div>
          <div class="activity-body">
            <div class="activity-topline">
              <div class="activity-heading-copy">
                <strong>{{ group.task?.course_title ?? group.chapterTitle ?? group.title }}</strong>
                <span v-if="group.task || group.chapterTitle">{{ group.title }}</span>
              </div>
              <NTag
                v-if="group.category !== 'system' || group.tone !== 'neutral'"
                :type="TONE_META[group.tone].type"
                size="small"
                :bordered="false"
              >
                {{ groupTagLabel(group) }}
              </NTag>
              <time :datetime="group.latest.occurred_at" :title="formatDate(group.latest.occurred_at)">
                {{ formatRelativeDate(group.latest.occurred_at) }}
              </time>
            </div>

            <p class="activity-description">{{ group.description }}</p>

            <div class="activity-meta">
              <span v-if="group.accountLabel">{{ group.accountLabel }}</span>
              <span v-if="group.chapterTitle">{{ group.chapterTitle }}</span>
              <span v-if="group.events.length > 1">{{ group.events.length }} 个节点</span>
            </div>

            <div class="activity-controls">
              <NButton
                v-if="group.task"
                text
                size="small"
                class="activity-link"
                aria-label="前往任务"
                @click="goToTasks"
              >
                <template #icon><ExternalLink :size="14" /></template>
                前往任务
              </NButton>
              <NButton
                text
                size="small"
                class="activity-detail-toggle"
                :aria-label="groupIsExpanded(group.key) ? '收起技术详情' : '展开技术详情'"
                :aria-expanded="groupIsExpanded(group.key)"
                @click="toggleGroup(group.key)"
              >
                <template #icon>
                  <ChevronDown
                    :size="14"
                    class="detail-chevron"
                    :class="{ expanded: groupIsExpanded(group.key) }"
                  />
                </template>
                {{ groupIsExpanded(group.key) ? '收起详情' : '查看详情' }}
              </NButton>
            </div>
          </div>
        </div>

        <div
          v-if="groupIsExpanded(group.key)"
          class="activity-details"
          data-testid="activity-timeline"
        >
          <div class="detail-heading">
            <ListTree :size="14" />
            <span>事件时间线</span>
            <code v-if="group.latest.task_id" :title="group.latest.task_id">
              {{ formatIdentifier(group.latest.task_id) }}
            </code>
          </div>
          <ol class="event-timeline">
            <li v-for="event in [...group.events].reverse()" :key="event.id">
              <CircleDot :size="13" />
              <div>
                <div class="event-title">
                  <strong>{{ EVENT_LABELS[event.kind] ?? '系统活动' }}</strong>
                  <code>{{ event.kind }}</code>
                  <time :datetime="event.occurred_at">{{ formatDate(event.occurred_at) }}</time>
                </div>
                <div class="event-context">
                  <span v-if="event.chapter_title">章节：{{ event.chapter_title }}</span>
                  <span v-else-if="event.chapter_id" :title="event.chapter_id">
                    章节：{{ formatIdentifier(event.chapter_id) }}
                  </span>
                </div>
                <div v-if="payloadEntries(event).length" class="event-payload">
                  <span v-for="entry in payloadEntries(event)" :key="entry.label">
                    <b>{{ entry.label }}</b><code>{{ entry.value }}</code>
                  </span>
                </div>
              </div>
            </li>
          </ol>
        </div>
      </article>
    </div>
  </section>
</template>

<style scoped>
.activity-heading {
  align-items: center;
}

.activity-title {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 9px;
  color: var(--color-accent);
}

.activity-title h2,
.activity-title span {
  display: block;
}

.activity-title h2 {
  color: var(--color-text);
}

.activity-title span {
  margin-top: 3px;
  color: var(--color-text-muted);
  font-size: 11px;
  font-weight: 400;
}

.activity-actions {
  flex-shrink: 0;
}

.stream-status :deep(.n-tag__content) {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.activity-toolbar {
  display: flex;
  min-height: 52px;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 9px 20px;
  border-bottom: 1px solid var(--color-border-soft);
  background: var(--color-surface-muted);
}

.activity-filter {
  width: 136px;
}

.activity-stats {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 16px;
  color: var(--color-text-muted);
  font-size: 11px;
  white-space: nowrap;
}

.activity-stats button {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  border: 0;
  border-radius: 5px;
  background: transparent;
  padding: 4px 6px;
  color: var(--color-warning);
  cursor: pointer;
  transition: background-color 140ms ease, transform 140ms cubic-bezier(0.23, 1, 0.32, 1);
}

.activity-stats button.active {
  background: var(--color-warning-soft);
}

.activity-stats button:active,
.activity-controls :deep(.n-button):active {
  transform: scale(0.97);
}

.archive-description {
  margin: 0 0 14px;
  color: var(--color-text-muted);
  font-size: 12px;
  line-height: 1.6;
}

.archive-picker {
  width: 100%;
}

.activity-alert {
  margin: 16px 20px;
}

.activity-empty {
  min-height: 240px;
}

.activity-loading {
  padding: 4px 20px;
}

.activity-skeleton-row {
  display: grid;
  grid-template-columns: 34px minmax(0, 1fr);
  gap: 13px;
  padding: 17px 0;
  border-bottom: 1px solid var(--color-border-soft);
}

.activity-skeleton-row > div {
  display: grid;
  gap: 7px;
}

.activity-list {
  background: var(--color-surface);
}

.activity-group {
  border-bottom: 1px solid var(--color-border-soft);
  box-shadow: inset 3px 0 0 transparent;
}

.activity-group:last-child {
  border-bottom: 0;
}

.activity-group.tone-danger {
  box-shadow: inset 3px 0 0 var(--color-danger);
}

.activity-group.tone-warning {
  box-shadow: inset 3px 0 0 var(--color-warning);
}

.activity-summary {
  display: grid;
  grid-template-columns: 34px minmax(0, 1fr);
  gap: 13px;
  padding: 17px 20px 15px;
}

.activity-marker {
  display: grid;
  width: 34px;
  height: 34px;
  place-items: center;
  border-radius: 50%;
  background: var(--color-neutral-soft);
  color: var(--color-text-muted);
}

.activity-marker.tone-danger {
  background: var(--color-danger-soft);
  color: var(--color-danger);
}

.activity-marker.tone-warning {
  background: var(--color-warning-soft);
  color: var(--color-warning);
}

.activity-marker.tone-success {
  background: var(--color-accent-soft);
  color: var(--color-accent);
}

.activity-marker.tone-info {
  background: var(--color-blue-soft);
  color: var(--color-blue);
}

.activity-body {
  min-width: 0;
}

.activity-topline {
  display: flex;
  min-width: 0;
  align-items: flex-start;
  gap: 8px;
}

.activity-heading-copy {
  min-width: 0;
  flex: 1 1 auto;
}

.activity-heading-copy strong,
.activity-heading-copy span {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.activity-heading-copy strong {
  color: var(--color-text-strong);
  font-size: 13px;
  line-height: 1.4;
}

.activity-heading-copy span {
  margin-top: 2px;
  color: var(--color-text-muted);
  font-size: 11px;
}

.activity-topline time {
  flex: 0 0 auto;
  margin-left: 4px;
  color: var(--color-text-muted);
  font-size: 11px;
  line-height: 22px;
  white-space: nowrap;
}

.activity-description {
  margin: 8px 0 0;
  color: var(--color-text);
  font-size: 12px;
  line-height: 1.55;
}

.activity-meta {
  display: flex;
  min-width: 0;
  flex-wrap: wrap;
  gap: 5px 14px;
  margin-top: 7px;
  color: var(--color-text-faint);
  font-size: 11px;
}

.activity-meta span {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.activity-controls {
  display: flex;
  align-items: center;
  gap: 14px;
  margin-top: 8px;
}

.activity-controls :deep(.n-button) {
  font-size: 11px;
  transition: transform 140ms cubic-bezier(0.23, 1, 0.32, 1);
}

.activity-link {
  color: var(--color-accent);
}

.activity-detail-toggle {
  color: var(--color-text-muted);
}

.detail-chevron {
  transition: transform 180ms cubic-bezier(0.23, 1, 0.32, 1);
}

.detail-chevron.expanded {
  transform: rotate(180deg);
}

.activity-details {
  margin: 0 20px 17px 67px;
  border: 1px solid var(--color-border-soft);
  border-radius: 6px;
  background: var(--color-surface-subtle);
  overflow: hidden;
}

.detail-heading {
  display: flex;
  min-height: 38px;
  align-items: center;
  gap: 7px;
  padding: 8px 12px;
  border-bottom: 1px solid var(--color-border-soft);
  color: var(--color-text-muted);
  font-size: 11px;
}

.detail-heading code {
  min-width: 0;
  margin-left: auto;
  overflow: hidden;
  color: var(--color-text-disabled);
  font-size: 10px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.event-timeline {
  margin: 0;
  padding: 3px 12px;
  list-style: none;
}

.event-timeline li {
  position: relative;
  display: grid;
  grid-template-columns: 14px minmax(0, 1fr);
  gap: 8px;
  padding: 10px 0;
  color: var(--color-text-faint);
}

.event-timeline li:not(:last-child) {
  border-bottom: 1px solid var(--color-border-faint);
}

.event-timeline li > svg {
  margin-top: 2px;
}

.event-title {
  display: flex;
  min-width: 0;
  align-items: baseline;
  gap: 8px;
}

.event-title strong {
  color: var(--color-text-strong);
  font-size: 11px;
}

.event-title code {
  min-width: 0;
  overflow: hidden;
  color: var(--color-text-disabled);
  font-size: 10px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.event-title time {
  flex: 0 0 auto;
  margin-left: auto;
  color: var(--color-text-faint);
  font-size: 10px;
  white-space: nowrap;
}

.event-context,
.event-payload {
  display: flex;
  min-width: 0;
  flex-wrap: wrap;
  gap: 5px 12px;
  margin-top: 5px;
  color: var(--color-text-muted);
  font-size: 10px;
}

.event-payload span {
  max-width: 100%;
  overflow-wrap: anywhere;
}

.event-payload b {
  margin-right: 4px;
  color: var(--color-text-faint);
  font-weight: 500;
}

.event-payload code {
  color: var(--color-text-muted);
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
  font-size: inherit;
}

@media (hover: hover) and (pointer: fine) {
  .activity-group:hover {
    background: var(--color-surface-muted);
  }

  .activity-stats button:hover {
    background: var(--color-warning-soft);
  }
}

@media (max-width: 680px) {
  .activity-heading {
    align-items: flex-start;
    flex-wrap: wrap;
  }

  .activity-title {
    flex: 1 1 auto;
  }

  .activity-actions {
    gap: 4px;
  }

  .stream-status span {
    display: none;
  }

  .activity-toolbar {
    align-items: stretch;
    flex-direction: column;
    gap: 8px;
    padding: 10px 14px;
  }

  .activity-filter {
    width: 100%;
  }

  .activity-stats {
    justify-content: space-between;
    gap: 8px;
  }

  .activity-summary {
    grid-template-columns: 30px minmax(0, 1fr);
    gap: 10px;
    padding: 15px 14px 13px;
  }

  .activity-marker {
    width: 30px;
    height: 30px;
  }

  .activity-topline {
    flex-wrap: wrap;
  }

  .activity-heading-copy {
    flex-basis: calc(100% - 74px);
  }

  .activity-topline time {
    width: 100%;
    margin: 0;
    line-height: 1.2;
    order: 3;
  }

  .activity-heading-copy strong,
  .activity-heading-copy span {
    white-space: normal;
  }

  .activity-details {
    margin: 0 14px 14px 54px;
  }

  .event-title {
    flex-wrap: wrap;
  }

  .event-title time {
    width: 100%;
    margin-left: 0;
  }
}

@media (prefers-reduced-motion: reduce) {
  .activity-stats button,
  .activity-controls :deep(.n-button),
  .detail-chevron {
    transition-duration: 0ms;
  }
}
</style>
