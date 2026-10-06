<script setup lang="ts">
import { useQuery } from '@tanstack/vue-query'
import { CircleDot, FileQuestion, ListTree, TriangleAlert } from 'lucide-vue-next'
import {
  NAlert,
  NButton,
  NDrawer,
  NDrawerContent,
  NProgress,
  NSkeleton,
  NTag,
} from 'naive-ui'
import { computed } from 'vue'

import { ApiError, getTask, getTaskEvents } from '@/api/client'
import type { StudyTaskDetail } from '@/api/types'
import TaskActionButtons from '@/components/tasks/TaskActionButtons.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import { useTaskActions } from '@/composables/useTaskActions'
import { eventLabel, reasonMessage, STATUS_MESSAGES } from '@/domain/events'
import {
  chapterStatusMeta,
  isTerminalTask,
  statusMeta,
  taskProgress,
  taskProgressColor,
} from '@/domain/tasks'
import { formatDateTime, formatFullDateTime, formatRelative, shortIdentifier } from '@/utils/format'

const props = defineProps<{ taskId: string | null }>()
const emit = defineEmits<{ close: [] }>()

const actions = useTaskActions()
const enabled = computed(() => props.taskId !== null)

const task = useQuery({
  queryKey: computed(() => ['task', props.taskId] as const),
  queryFn: () => getTask(props.taskId!),
  enabled,
  retry: (count, error) => !(error instanceof ApiError && error.status === 404) && count < 2,
  refetchInterval: (query) => {
    const data = query.state.data as StudyTaskDetail | undefined
    return data && isTerminalTask(data) ? false : 5_000
  },
})

const events = useQuery({
  queryKey: computed(() => ['task-events', props.taskId] as const),
  queryFn: () => getTaskEvents(props.taskId!),
  enabled,
  refetchInterval: () => (task.data.value && isTerminalTask(task.data.value) ? false : 10_000),
})

const detail = computed(() => task.data.value)
const notFound = computed(
  () => task.error.value instanceof ApiError && task.error.value.status === 404,
)
const sortedChapters = computed(() =>
  [...(detail.value?.chapters ?? [])].sort((left, right) => left.position - right.position),
)
const recentEvents = computed(() =>
  [...(events.data.value ?? [])].sort((left, right) => right.id - left.id).slice(0, 40),
)
const summary = computed(() => {
  const value = detail.value
  if (!value) return ''
  return reasonMessage(value.last_error) ?? STATUS_MESSAGES[value.status] ?? ''
})

function chapterError(code: string | null): string | null {
  if (!code) return null
  return reasonMessage(code) ?? code
}
</script>

<template>
  <NDrawer
    :show="taskId !== null"
    :width="640"
    placement="right"
    class="task-detail-drawer"
    @update:show="(value) => { if (!value) emit('close') }"
  >
    <NDrawerContent closable :native-scrollbar="false">
      <template #header>
        <div class="detail-header">
          <span class="detail-title">{{ detail?.course_title ?? '任务详情' }}</span>
          <NTag v-if="detail" size="small" :type="statusMeta(detail.status).type" :bordered="false">
            {{ statusMeta(detail.status).label }}
          </NTag>
        </div>
      </template>

      <EmptyState
        v-if="notFound"
        :icon="FileQuestion"
        title="任务不存在或已删除"
        description="该任务可能已被清理，请返回任务列表查看其他任务。"
      >
        <NButton @click="emit('close')">返回列表</NButton>
      </EmptyState>

      <NAlert v-else-if="task.isError.value" type="error" :bordered="false">
        任务详情加载失败，请稍后重试。
      </NAlert>

      <div v-else-if="task.isLoading.value" class="detail-loading">
        <NSkeleton text :repeat="2" />
        <NSkeleton :height="80" />
        <NSkeleton text :repeat="5" />
      </div>

      <div v-else-if="detail" class="detail-body" data-testid="task-detail">
        <section class="detail-card">
          <div class="detail-card-head">
            <div class="cell-stack">
              <strong>{{ detail.account_label }}</strong>
              <span :title="detail.id">任务 {{ shortIdentifier(detail.id) }}</span>
            </div>
            <TaskActionButtons
              :task="detail"
              :pending="actions.pendingAction(detail.id)"
              @action="(action) => actions.run(detail!, action)"
            />
          </div>
          <NProgress
            :percentage="taskProgress(detail)"
            :show-indicator="false"
            :height="8"
            :border-radius="4"
            :color="taskProgressColor(detail)"
          />
          <div class="progress-figures num">
            <span><b>{{ detail.chapter_succeeded }}</b> 已完成</span>
            <span v-if="detail.chapter_needs_attention > 0" class="warn">
              <b>{{ detail.chapter_needs_attention }}</b> 待处理
            </span>
            <span><b>{{ detail.chapter_total }}</b> 章节</span>
            <span class="progress-percent">{{ taskProgress(detail) }}%</span>
          </div>
          <p v-if="summary" class="detail-summary" :class="{ warn: detail.last_error }">
            <TriangleAlert v-if="detail.last_error" :size="14" />
            {{ summary }}
          </p>
          <dl class="detail-times">
            <div><dt>创建</dt><dd>{{ formatDateTime(detail.created_at) }}</dd></div>
            <div><dt>开始</dt><dd>{{ formatDateTime(detail.started_at) }}</dd></div>
            <div><dt>结束</dt><dd>{{ formatDateTime(detail.finished_at) }}</dd></div>
          </dl>
        </section>

        <section class="detail-section">
          <h3>章节（{{ sortedChapters.length }}）</h3>
          <ol class="chapter-status-list">
            <li v-for="chapter in sortedChapters" :key="chapter.chapter_id">
              <span class="chapter-index num">{{ chapter.position + 1 }}</span>
              <div class="chapter-status-copy">
                <strong>{{ chapter.title }}</strong>
                <span v-if="chapterError(chapter.last_error)" class="chapter-error">
                  {{ chapterError(chapter.last_error) }}
                </span>
                <span v-else-if="chapter.attempts > 1" class="chapter-sub">已尝试 {{ chapter.attempts }} 次</span>
              </div>
              <NTag size="small" :type="chapterStatusMeta(chapter.status).type" :bordered="false">
                {{ chapterStatusMeta(chapter.status).label }}
              </NTag>
            </li>
          </ol>
        </section>

        <section class="detail-section">
          <h3><ListTree :size="15" /> 最近事件</h3>
          <div v-if="events.isLoading.value" class="detail-loading"><NSkeleton text :repeat="4" /></div>
          <EmptyState v-else-if="recentEvents.length === 0" compact title="暂无事件记录" />
          <ol v-else class="event-list">
            <li v-for="event in recentEvents" :key="event.id" :class="`level-${event.level}`">
              <CircleDot :size="12" />
              <div>
                <div class="event-line">
                  <strong>{{ eventLabel(event.kind) }}</strong>
                  <time :datetime="event.occurred_at" :title="formatFullDateTime(event.occurred_at)">
                    {{ formatRelative(event.occurred_at) }}
                  </time>
                </div>
                <span v-if="event.chapter_title" class="event-chapter">{{ event.chapter_title }}</span>
              </div>
            </li>
          </ol>
        </section>
      </div>
    </NDrawerContent>
  </NDrawer>
</template>

<style scoped>
.detail-header {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 10px;
}

.detail-title {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.detail-loading {
  display: grid;
  gap: 14px;
}

.detail-body {
  display: grid;
  gap: 22px;
}

.detail-card {
  display: grid;
  gap: 12px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  background: var(--color-surface-muted);
  padding: 16px;
}

.detail-card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.progress-figures {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 16px;
  color: var(--color-text-muted);
  font-size: var(--fs-sm);
}

.progress-figures b {
  color: var(--color-text-strong);
}

.progress-figures .warn b {
  color: var(--color-warning);
}

.progress-percent {
  margin-left: auto;
  color: var(--color-text-strong);
  font-weight: 600;
}

.detail-summary {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  margin: 0;
  color: var(--color-text-muted);
  font-size: var(--fs-sm);
  line-height: 1.6;
}

.detail-summary.warn {
  border-radius: var(--radius-sm);
  background: var(--color-warning-soft);
  color: var(--color-warning);
  padding: 8px 10px;
}

.detail-summary > svg {
  flex: 0 0 auto;
  margin-top: 3px;
}

.detail-times {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
  margin: 0;
  border-top: 1px solid var(--color-border-soft);
  padding-top: 12px;
}

.detail-times dt {
  color: var(--color-text-faint);
  font-size: var(--fs-xs);
}

.detail-times dd {
  margin: 2px 0 0;
  color: var(--color-text-strong);
  font-size: var(--fs-sm);
  font-variant-numeric: tabular-nums;
}

.detail-section h3 {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0 0 10px;
  color: var(--color-text-strong);
  font-size: var(--fs-md);
}

.chapter-status-list,
.event-list {
  margin: 0;
  padding: 0;
  list-style: none;
}

.chapter-status-list {
  border: 1px solid var(--color-border-soft);
  border-radius: var(--radius-md);
}

.chapter-status-list li {
  display: grid;
  grid-template-columns: 28px minmax(0, 1fr) auto;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border-bottom: 1px solid var(--color-border-soft);
}

.chapter-status-list li:last-child {
  border-bottom: 0;
}

.chapter-index {
  color: var(--color-text-faint);
  font-size: var(--fs-xs);
  text-align: right;
}

.chapter-status-copy {
  min-width: 0;
}

.chapter-status-copy strong,
.chapter-status-copy span {
  display: block;
}

.chapter-status-copy strong {
  overflow-wrap: anywhere;
  color: var(--color-text-strong);
  font-size: var(--fs-sm);
  font-weight: 500;
}

.chapter-sub {
  margin-top: 2px;
  color: var(--color-text-faint);
  font-size: var(--fs-xs);
}

.chapter-error {
  margin-top: 2px;
  color: var(--color-warning);
  font-size: var(--fs-xs);
}

.event-list li {
  display: grid;
  grid-template-columns: 14px minmax(0, 1fr);
  gap: 8px;
  padding: 8px 0;
  border-bottom: 1px solid var(--color-border-faint);
  color: var(--color-text-faint);
}

.event-list li:last-child {
  border-bottom: 0;
}

.event-list li > svg {
  margin-top: 4px;
}

.event-list li.level-warning > svg {
  color: var(--color-warning);
}

.event-list li.level-error > svg {
  color: var(--color-danger);
}

.event-line {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 10px;
}

.event-line strong {
  color: var(--color-text-strong);
  font-size: var(--fs-sm);
  font-weight: 500;
}

.event-line time {
  flex: 0 0 auto;
  color: var(--color-text-faint);
  font-size: var(--fs-xs);
}

.event-chapter {
  display: block;
  overflow: hidden;
  margin-top: 1px;
  color: var(--color-text-muted);
  font-size: var(--fs-xs);
  text-overflow: ellipsis;
  white-space: nowrap;
}

@media (max-width: 520px) {
  .detail-times {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>

<style>
.task-detail-drawer {
  max-width: 100vw;
}
</style>
