<script setup lang="ts">
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import {
  ChevronRight,
  CircleCheck,
  CircleX,
  Clock3,
  ListChecks,
  Plus,
  TriangleAlert,
} from 'lucide-vue-next'
import { NAlert, NButton, NCheckbox, NProgress, NSkeleton, NTag, useMessage } from 'naive-ui'
import { computed, ref } from 'vue'
import type { Component } from 'vue'
import { useRouter } from 'vue-router'

import { apiRequest, getAllTasks } from '@/api/client'
import type {
  Account,
  ManualIntervention,
  OperationsHealth,
  ResolveInterventionsResponse,
} from '@/api/types'
import EmptyState from '@/components/ui/EmptyState.vue'
import { reasonMessage } from '@/domain/events'
import {
  isRunningTask,
  isTerminalTask,
  statusMeta,
  taskProgress,
  taskProgressColor,
  type TaskFilter,
} from '@/domain/tasks'
import { formatDuration, formatRelative, isToday } from '@/utils/format'

const router = useRouter()
const queryClient = useQueryClient()
const message = useMessage()

const accounts = useQuery({
  queryKey: ['accounts'],
  queryFn: () => apiRequest<Account[]>('/accounts'),
})

const health = useQuery({
  queryKey: ['operations-health'],
  queryFn: () => apiRequest<OperationsHealth>('/operations/health'),
  refetchInterval: 5_000,
})

const interventions = useQuery({
  queryKey: ['manual-interventions'],
  queryFn: () => apiRequest<ManualIntervention[]>('/operations/interventions'),
  refetchInterval: 10_000,
})

const tasks = useQuery({
  queryKey: ['tasks'],
  queryFn: getAllTasks,
  refetchInterval: 10_000,
})

const selectedInterventions = ref<number[]>([])
const resolvingIds = ref<number[]>([])
const resolveMutation = useMutation({
  mutationFn: (ids: number[]) =>
    apiRequest<ResolveInterventionsResponse>('/operations/interventions/resolve', {
      method: 'POST',
      body: JSON.stringify({ ids }),
    }),
  onMutate: (ids) => {
    resolvingIds.value = ids
  },
  onSuccess: async (result) => {
    selectedInterventions.value = []
    message.success(`已标记 ${result.resolved} 项为已处理`)
    await queryClient.invalidateQueries({ queryKey: ['manual-interventions'] })
  },
  onError: () => message.error('处理状态保存失败'),
  onSettled: () => {
    resolvingIds.value = []
  },
})

const allTasks = computed(() => tasks.data.value ?? [])
const runningCount = computed(() => allTasks.value.filter(isRunningTask).length)
const queuedCount = computed(() => allTasks.value.filter((task) => task.status === 'queued').length)
const failedCount = computed(() => allTasks.value.filter((task) => task.status === 'failed').length)
const todayFinishedCount = computed(
  () => allTasks.value.filter((task) => task.status === 'succeeded' && isToday(task.finished_at)).length,
)
const interventionCount = computed(() => interventions.data.value?.length ?? 0)
const currentTasks = computed(() =>
  allTasks.value
    .filter((task) => !isTerminalTask(task))
    .sort((left, right) => Number(isRunningTask(right)) - Number(isRunningTask(left))),
)
const visibleCurrentTasks = computed(() => currentTasks.value.slice(0, 6))
const enabledAccountCount = computed(
  () => (accounts.data.value ?? []).filter((account) => account.enabled).length,
)
const showOnboarding = computed(
  () => accounts.isSuccess.value && (accounts.data.value?.length ?? 0) === 0,
)
const allInterventionsSelected = computed(
  () => interventionCount.value > 0 && selectedInterventions.value.length === interventionCount.value,
)
const someInterventionsSelected = computed(
  () => selectedInterventions.value.length > 0 && !allInterventionsSelected.value,
)

interface Metric {
  key: string
  label: string
  value: number | string
  hint: string
  tone: 'accent' | 'warning' | 'success' | 'danger'
  icon: Component
  action: () => void
}

const loadingValue = (value: number) => (tasks.isLoading.value ? '—' : value)

const metrics = computed<Metric[]>(() => [
  {
    key: 'running',
    label: '运行中',
    value: loadingValue(runningCount.value),
    hint: queuedCount.value > 0 ? `另有 ${queuedCount.value} 个排队` : '没有排队任务',
    tone: 'accent',
    icon: Clock3,
    action: () => openTasks('active'),
  },
  {
    key: 'attention',
    label: '需要处理',
    value: interventions.isLoading.value ? '—' : interventionCount.value,
    hint: interventionCount.value > 0 ? '异常章节等待人工确认' : '暂无待处理章节',
    tone: 'warning',
    icon: TriangleAlert,
    action: () => document.getElementById('interventions')?.scrollIntoView({ behavior: 'smooth' }),
  },
  {
    key: 'done',
    label: '今日完成',
    value: loadingValue(todayFinishedCount.value),
    hint: '今天成功结束的任务',
    tone: 'success',
    icon: CircleCheck,
    action: () => openTasks('finished'),
  },
  {
    key: 'failed',
    label: '失败',
    value: loadingValue(failedCount.value),
    hint: failedCount.value > 0 ? '点击查看失败原因' : '没有失败任务',
    tone: 'danger',
    icon: CircleX,
    action: () => openTasks('failed'),
  },
])

interface HealthRow {
  label: string
  value: string
  tone?: 'ok' | 'warn' | 'error'
}

const healthRows = computed<HealthRow[]>(() => {
  const data = health.data.value
  if (!data) return []
  const rows: HealthRow[] = [
    {
      label: '调度服务',
      value: !data.worker.enabled ? '已停止' : data.worker.service_running ? '运行中' : '未运行',
      tone: data.worker.enabled && data.worker.service_running ? 'ok' : 'warn',
    },
    {
      label: '执行进程',
      value: `${data.worker.active_process_count} / ${data.worker.configured_capacity}`,
    },
    {
      label: '学习账号',
      value: `${enabledAccountCount.value} 启用 / ${accounts.data.value?.length ?? 0} 个`,
    },
    {
      label: '过期租约',
      value: String(data.worker.stale_run_count),
      tone: data.worker.stale_run_count > 0 ? 'warn' : undefined,
    },
  ]
  if (data.cpu.value !== null) rows.push({ label: 'CPU', value: `${data.cpu.value}%` })
  if (data.memory.value !== null) rows.push({ label: '内存', value: `${data.memory.value}%` })
  if (data.temperature.value !== null) {
    rows.push({ label: '温度', value: `${data.temperature.value}°C` })
  }
  if (typeof data.uptime_seconds === 'number') {
    rows.push({ label: '已运行', value: formatDuration(data.uptime_seconds) })
  }
  return rows
})

const serviceTag = computed(() => {
  if (health.isError.value) return { type: 'error' as const, label: '不可达' }
  const data = health.data.value
  if (!data) return { type: 'default' as const, label: '检查中' }
  return data.status === 'ok'
    ? { type: 'success' as const, label: '正常' }
    : { type: 'warning' as const, label: '需检查' }
})

function openTasks(status: TaskFilter): void {
  void router.push({ name: 'tasks', query: { status } })
}

function openTask(taskId: string): void {
  void router.push({ name: 'tasks', params: { taskId } })
}

function toggleAllInterventions(checked: boolean): void {
  selectedInterventions.value = checked
    ? (interventions.data.value ?? []).map((item) => item.id)
    : []
}

function toggleIntervention(id: number, checked: boolean): void {
  selectedInterventions.value = checked
    ? [...new Set([...selectedInterventions.value, id])]
    : selectedInterventions.value.filter((itemId) => itemId !== id)
}
</script>

<template>
  <section v-if="showOnboarding" class="content-section onboarding">
    <div>
      <h2>开始使用</h2>
      <p>还没有学习通账号。添加账号后即可读取课程并创建学习任务。</p>
    </div>
    <NButton type="primary" @click="router.push({ name: 'accounts', query: { create: '1' } })">
      <template #icon><Plus /></template>
      添加学习通账号
    </NButton>
  </section>

  <section class="metric-grid" aria-label="任务概况">
    <button
      v-for="metric in metrics"
      :key="metric.key"
      type="button"
      class="metric-card"
      :class="`tone-${metric.tone}`"
      @click="metric.action"
    >
      <span class="metric-icon"><component :is="metric.icon" :size="18" /></span>
      <span class="metric-copy">
        <span class="metric-label">{{ metric.label }}</span>
        <strong class="metric-value num">{{ metric.value }}</strong>
        <span class="metric-hint">{{ metric.hint }}</span>
      </span>
      <ChevronRight :size="16" class="metric-chevron" />
    </button>
  </section>

  <div class="dashboard-columns">
    <section class="content-section flush">
      <div class="section-heading padded">
        <div><h2>当前任务</h2><p>执行中、排队与暂停的任务</p></div>
        <button type="button" class="section-link" @click="openTasks('all')">
          全部任务 <ChevronRight :size="14" />
        </button>
      </div>
      <NAlert v-if="tasks.isError.value" type="error" :bordered="false">
        任务状态加载失败，请稍后重试。
      </NAlert>
      <div v-else-if="tasks.isLoading.value" class="task-skeletons">
        <NSkeleton text :repeat="3" />
      </div>
      <div v-else-if="visibleCurrentTasks.length" class="dashboard-task-list">
        <button
          v-for="task in visibleCurrentTasks"
          :key="task.id"
          type="button"
          class="dashboard-task-row"
          @click="openTask(task.id)"
        >
          <span class="cell-stack">
            <strong>{{ task.course_title }}</strong>
            <span>{{ task.account_label }}</span>
          </span>
          <NTag :type="statusMeta(task.status).type" size="small" :bordered="false">
            {{ statusMeta(task.status).label }}
          </NTag>
          <span class="dashboard-task-progress">
            <NProgress
              :percentage="taskProgress(task)"
              :show-indicator="false"
              :height="6"
              :border-radius="3"
              :color="taskProgressColor(task)"
            />
            <span class="num">
              {{ task.chapter_succeeded }} / {{ task.chapter_total }} 章节
              <template v-if="task.chapter_needs_attention > 0">
                · {{ task.chapter_needs_attention }} 待处理
              </template>
            </span>
          </span>
        </button>
        <button
          v-if="currentTasks.length > visibleCurrentTasks.length"
          type="button"
          class="more-row"
          @click="openTasks('active')"
        >
          还有 {{ currentTasks.length - visibleCurrentTasks.length }} 个任务
        </button>
      </div>
      <EmptyState
        v-else
        compact
        :icon="Clock3"
        title="没有进行中的任务"
        description="在任务页选择账号和课程即可创建"
      >
        <NButton size="small" @click="router.push({ name: 'tasks', query: { create: '1' } })">
          新建任务
        </NButton>
      </EmptyState>
    </section>

    <section class="content-section flush">
      <div class="section-heading padded">
        <div><h2>运行状态</h2><p>调度服务与主机资源</p></div>
        <NTag :type="serviceTag.type" size="small" :bordered="false">{{ serviceTag.label }}</NTag>
      </div>
      <NAlert v-if="health.isError.value" type="error" :bordered="false">健康指标加载失败。</NAlert>
      <div v-else-if="health.isLoading.value" class="task-skeletons">
        <NSkeleton text :repeat="4" />
      </div>
      <dl v-else class="health-list">
        <div v-for="row in healthRows" :key="row.label">
          <dt>{{ row.label }}</dt>
          <dd>
            <span v-if="row.tone" class="status-dot" :class="row.tone" />
            {{ row.value }}
          </dd>
        </div>
      </dl>
    </section>
  </div>

  <section id="interventions" class="content-section flush">
    <div class="section-heading padded">
      <div><h2>人工接管</h2><p>自动执行未能完成的章节，处理后标记即可移出队列</p></div>
      <div v-if="interventionCount > 0" class="heading-actions">
        <NCheckbox
          :checked="allInterventionsSelected"
          :indeterminate="someInterventionsSelected"
          @update:checked="toggleAllInterventions"
        >
          全选
        </NCheckbox>
        <NButton
          size="small"
          :disabled="selectedInterventions.length === 0"
          :loading="resolveMutation.isPending.value && resolvingIds.length > 1"
          @click="resolveMutation.mutate(selectedInterventions)"
        >
          标记已处理{{ selectedInterventions.length ? `（${selectedInterventions.length}）` : '' }}
        </NButton>
      </div>
    </div>
    <NAlert v-if="interventions.isError.value" type="error" :bordered="false">待处理队列加载失败。</NAlert>
    <div v-else-if="interventionCount" class="intervention-list">
      <article v-for="item in interventions.data.value" :key="item.id" class="intervention-row">
        <NCheckbox
          :checked="selectedInterventions.includes(item.id)"
          :aria-label="`选择 ${item.chapter_title}`"
          @update:checked="(checked) => toggleIntervention(item.id, checked)"
        />
        <div class="cell-stack">
          <strong>{{ item.chapter_title }}</strong>
          <span>{{ item.course_title }} · {{ item.account_label }} · {{ formatRelative(item.occurred_at) }}</span>
          <span v-if="reasonMessage(item.reason)" class="intervention-reason">{{ reasonMessage(item.reason) }}</span>
        </div>
        <div class="intervention-actions">
          <NButton text size="small" @click="openTask(item.task_id)">查看任务</NButton>
          <NButton
            size="small"
            secondary
            type="primary"
            :loading="resolveMutation.isPending.value && resolvingIds.length === 1 && resolvingIds[0] === item.id"
            :disabled="resolveMutation.isPending.value"
            @click="resolveMutation.mutate([item.id])"
          >
            已处理
          </NButton>
        </div>
      </article>
    </div>
    <EmptyState
      v-else
      compact
      :icon="ListChecks"
      title="暂无待人工处理章节"
      description="任务遇到无法自动完成的章节时会出现在这里"
    />
  </section>
</template>

<style scoped>
.onboarding {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  border-color: var(--color-accent-border);
  background: var(--color-accent-softer);
}

.onboarding h2,
.onboarding p {
  margin: 0;
}

.onboarding h2 {
  color: var(--color-text-strong);
  font-size: var(--fs-lg);
}

.onboarding p {
  margin-top: 4px;
  color: var(--color-text-muted);
  font-size: var(--fs-sm);
}

.metric-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
}

.metric-card {
  display: grid;
  grid-template-columns: 40px minmax(0, 1fr) 16px;
  align-items: center;
  gap: 12px;
  min-height: 96px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  background: var(--color-surface);
  box-shadow: var(--shadow-card);
  color: inherit;
  cursor: pointer;
  padding: 16px;
  text-align: left;
  transition: border-color 140ms ease, transform 140ms ease;
}

.metric-card:hover {
  border-color: var(--color-accent-border);
}

.metric-card:active {
  transform: scale(0.99);
}

.metric-card:focus-visible {
  outline: 2px solid var(--color-accent);
  outline-offset: 2px;
}

.metric-icon {
  display: grid;
  width: 40px;
  height: 40px;
  place-items: center;
  border-radius: var(--radius-md);
}

.tone-accent .metric-icon { background: var(--color-blue-soft); color: var(--color-blue); }
.tone-warning .metric-icon { background: var(--color-warning-soft); color: var(--color-warning); }
.tone-success .metric-icon { background: var(--color-accent-soft); color: var(--color-accent); }
.tone-danger .metric-icon { background: var(--color-danger-soft); color: var(--color-danger); }

.metric-copy {
  display: grid;
  min-width: 0;
}

.metric-label {
  color: var(--color-text-muted);
  font-size: var(--fs-sm);
}

.metric-value {
  color: var(--color-text-strong);
  font-size: var(--fs-2xl);
  line-height: 1.2;
}

.metric-hint {
  overflow: hidden;
  color: var(--color-text-faint);
  font-size: var(--fs-xs);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.metric-chevron {
  color: var(--color-text-disabled);
}

.dashboard-columns {
  display: grid;
  grid-template-columns: minmax(0, 1.7fr) minmax(280px, 1fr);
  gap: var(--section-gap);
  align-items: start;
}

.task-skeletons {
  padding: 16px 20px 20px;
}

.dashboard-task-list {
  display: grid;
}

.dashboard-task-row {
  display: grid;
  grid-template-columns: minmax(0, 1.3fr) 84px minmax(150px, 1fr);
  align-items: center;
  gap: 16px;
  min-height: 64px;
  border: 0;
  border-bottom: 1px solid var(--color-border-soft);
  background: transparent;
  color: inherit;
  cursor: pointer;
  padding: 10px 20px;
  text-align: left;
}

.dashboard-task-row:hover {
  background: var(--color-surface-subtle);
}

.dashboard-task-progress {
  display: grid;
  gap: 4px;
  min-width: 0;
}

.dashboard-task-progress > span {
  color: var(--color-text-muted);
  font-size: var(--fs-xs);
}

.more-row {
  border: 0;
  background: transparent;
  color: var(--color-accent);
  cursor: pointer;
  padding: 12px 20px;
  font-size: var(--fs-sm);
  text-align: left;
}

.health-list {
  display: grid;
  margin: 0;
  padding: 6px 20px 10px;
}

.health-list > div {
  display: flex;
  min-height: 40px;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  border-bottom: 1px solid var(--color-border-faint);
}

.health-list > div:last-child {
  border-bottom: 0;
}

.health-list dt {
  color: var(--color-text-muted);
  font-size: var(--fs-sm);
}

.health-list dd {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin: 0;
  color: var(--color-text-strong);
  font-size: var(--fs-sm);
  font-variant-numeric: tabular-nums;
  font-weight: 600;
}

.intervention-list {
  display: grid;
}

.intervention-row {
  display: grid;
  grid-template-columns: 22px minmax(0, 1fr) auto;
  align-items: center;
  gap: 12px;
  padding: 12px 20px;
  border-bottom: 1px solid var(--color-border-soft);
}

.intervention-row:last-child {
  border-bottom: 0;
}

.intervention-reason {
  color: var(--color-warning) !important;
}

.intervention-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

@media (max-width: 1080px) {
  .metric-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .dashboard-columns {
    grid-template-columns: minmax(0, 1fr);
  }
}

@media (max-width: 680px) {
  .onboarding {
    align-items: stretch;
    flex-direction: column;
  }

  .metric-grid {
    gap: 10px;
  }

  .metric-card {
    grid-template-columns: minmax(0, 1fr);
    min-height: 0;
    padding: 14px;
  }

  .metric-icon,
  .metric-chevron,
  .metric-hint {
    display: none;
  }

  .metric-value {
    font-size: var(--fs-xl);
  }

  .dashboard-task-row {
    grid-template-columns: minmax(0, 1fr) auto;
    gap: 8px 10px;
    padding: 12px 14px;
  }

  .dashboard-task-progress {
    grid-column: 1 / -1;
  }

  .health-list {
    padding: 4px 14px 8px;
  }

  .intervention-row {
    grid-template-columns: 22px minmax(0, 1fr);
    padding: 12px 14px;
  }

  .intervention-actions {
    grid-column: 2;
    justify-content: flex-end;
  }
}
</style>
