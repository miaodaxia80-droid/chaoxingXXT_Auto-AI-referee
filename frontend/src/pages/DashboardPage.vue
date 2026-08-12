<script setup lang="ts">
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import {
  CircleCheck,
  Clock3,
  Cpu,
  Gauge,
  HardDrive,
  ListChecks,
  Server,
  Thermometer,
  Users,
} from 'lucide-vue-next'
import { NAlert, NButton, NCheckbox, NProgress, NSkeleton, NTag, useMessage } from 'naive-ui'
import { computed, ref } from 'vue'

import { apiRequest, getAllTasks } from '@/api/client'
import type {
  Account,
  ManualIntervention,
  OperationsHealth,
  ResolveInterventionsResponse,
  StudyTask,
} from '@/api/types'
import {
  isRunningTask,
  isTerminalTask,
  statusMeta,
  taskProgress,
  taskProgressColor,
} from '@/domain/tasks'

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

const queryClient = useQueryClient()
const message = useMessage()
const selectedInterventions = ref<number[]>([])
const resolveMutation = useMutation({
  mutationFn: (ids: number[]) =>
    apiRequest<ResolveInterventionsResponse>('/operations/interventions/resolve', {
      method: 'POST',
      body: JSON.stringify({ ids }),
    }),
  onSuccess: async (result) => {
    selectedInterventions.value = []
    message.success(`已标记 ${result.resolved} 项为已处理`)
    await queryClient.invalidateQueries({ queryKey: ['manual-interventions'] })
  },
  onError: () => message.error('处理状态保存失败'),
})

const tasks = useQuery({
  queryKey: ['tasks'],
  queryFn: getAllTasks,
  refetchInterval: 10_000,
})

const runningTasks = computed(() => (tasks.data.value ?? []).filter(isRunningTask))
const currentTasks = computed(() => (tasks.data.value ?? []).filter((task) => !isTerminalTask(task)))
const todayFinishedCount = computed(
  () => (tasks.data.value ?? []).filter((task) => isFinishedToday(task.finished_at)).length,
)
const allInterventionsSelected = computed(
  () =>
    (interventions.data.value?.length ?? 0) > 0 &&
    selectedInterventions.value.length === interventions.data.value?.length,
)

function metricLabel(value: number | null, suffix: string): string {
  return value === null ? '不可用' : `${value}${suffix}`
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

function resolveOne(id: number): void {
  resolveMutation.mutate([id])
}

function isFinishedToday(value: string | null): boolean {
  if (!value) return false
  const finishedAt = new Date(value)
  if (Number.isNaN(finishedAt.getTime())) return false
  const today = new Date()
  return (
    finishedAt.getFullYear() === today.getFullYear() &&
    finishedAt.getMonth() === today.getMonth() &&
    finishedAt.getDate() === today.getDate()
  )
}
</script>

<template>
  <section class="dashboard-grid">
    <div class="metric-block">
      <span class="metric-icon neutral"><Users :size="19" /></span>
      <div><span>账号</span><strong>{{ accounts.data.value?.length ?? 0 }}</strong></div>
    </div>
    <div class="metric-block">
      <span class="metric-icon accent"><Clock3 :size="19" /></span>
      <div>
        <span>运行中</span>
        <strong>{{ tasks.isLoading.value ? '—' : runningTasks.length }}</strong>
      </div>
    </div>
    <div class="metric-block">
      <span class="metric-icon success"><CircleCheck :size="19" /></span>
      <div>
        <span>今日完成</span>
        <strong>{{ tasks.isLoading.value ? '—' : todayFinishedCount }}</strong>
      </div>
    </div>
    <div class="metric-block">
      <span class="metric-icon warning"><Server :size="19" /></span>
      <div>
        <span>服务</span>
        <strong class="service-state">{{ health.data.value?.status === 'ok' ? '正常' : '需检查' }}</strong>
      </div>
    </div>
  </section>

  <section class="content-section operations-section">
    <div class="section-heading">
      <div><h2>Worker 健康</h2><p>进程、容量与持久化租约状态</p></div>
      <NTag
        :type="health.data.value?.status === 'ok' ? 'success' : 'warning'"
        size="small"
        :bordered="false"
      >
        {{ health.data.value?.status === 'ok' ? '正常' : '需检查' }}
      </NTag>
    </div>
    <NAlert v-if="health.isError.value" type="error" :bordered="false">健康指标加载失败。</NAlert>
    <div v-else class="worker-health-grid">
      <div><Gauge :size="17" /><span>CPU</span><strong>{{ metricLabel(health.data.value?.cpu.value ?? null, '%') }}</strong></div>
      <div><HardDrive :size="17" /><span>内存</span><strong>{{ metricLabel(health.data.value?.memory.value ?? null, '%') }}</strong></div>
      <div><Thermometer :size="17" /><span>温度</span><strong>{{ metricLabel(health.data.value?.temperature.value ?? null, '°C') }}</strong></div>
      <div><Server :size="17" /><span>调度服务</span><strong>{{ health.data.value?.worker.service_running ? '运行中' : '未运行' }}</strong></div>
      <div><Cpu :size="17" /><span>活跃进程</span><strong>{{ health.data.value?.worker.active_process_count ?? 0 }} / {{ health.data.value?.worker.configured_capacity ?? 0 }}</strong></div>
      <div><ListChecks :size="17" /><span>租约记录</span><strong>{{ health.data.value?.worker.durable_active_run_count ?? 0 }}</strong></div>
      <div><Gauge :size="17" /><span>过期租约</span><strong>{{ health.data.value?.worker.stale_run_count ?? 0 }}</strong></div>
    </div>
  </section>

  <section class="content-section flush interventions-section">
    <div class="section-heading padded intervention-heading">
      <div><h2>人工接管</h2><p>异常章节的独立待处理队列</p></div>
      <div class="heading-actions intervention-actions">
        <NCheckbox
          :checked="allInterventionsSelected"
          :disabled="!interventions.data.value?.length"
          @update:checked="toggleAllInterventions"
        >
          全选
        </NCheckbox>
        <NButton
          size="small"
          :disabled="selectedInterventions.length === 0"
          :loading="resolveMutation.isPending.value"
          @click="resolveMutation.mutate(selectedInterventions)"
        >
          标记已处理
        </NButton>
      </div>
    </div>
    <NAlert v-if="interventions.isError.value" type="error" :bordered="false" class="intervention-alert">待处理队列加载失败。</NAlert>
    <div v-else-if="interventions.data.value?.length" class="intervention-list">
      <article v-for="item in interventions.data.value" :key="item.id" class="intervention-row">
        <NCheckbox
          :checked="selectedInterventions.includes(item.id)"
          aria-label="选择待处理项"
          @update:checked="(checked) => toggleIntervention(item.id, checked)"
        />
        <div class="intervention-identity">
          <strong>{{ item.chapter_title }}</strong>
          <span>{{ item.course_title }} · {{ item.account_label }}</span>
        </div>
        <NTag type="warning" size="small" :bordered="false">{{ item.status }}</NTag>
        <NButton text type="primary" :loading="resolveMutation.isPending.value" @click="resolveOne(item.id)">已处理</NButton>
      </article>
    </div>
    <div v-else class="empty-state"><ListChecks :size="24" /><strong>暂无待人工处理章节</strong></div>
  </section>

  <section class="content-section dashboard-tasks-section">
    <div class="section-heading">
      <div><h2>当前任务</h2><p>等待、暂停及执行中的任务</p></div>
    </div>

    <NAlert v-if="tasks.isError.value" type="error" :bordered="false">
      任务状态加载失败，请稍后重试。
    </NAlert>
    <div v-else-if="tasks.isLoading.value" class="dashboard-task-skeletons">
      <NSkeleton text :repeat="3" />
    </div>
    <div v-else-if="currentTasks.length > 0" class="dashboard-task-list">
      <article v-for="task in currentTasks" :key="task.id" class="dashboard-task-row">
        <div class="dashboard-task-identity">
          <strong>{{ task.course_title }}</strong>
          <span>账号：{{ task.account_label }}</span>
        </div>
        <NTag :type="statusMeta(task.status).type" size="small" :bordered="false">
          {{ statusMeta(task.status).label }}
        </NTag>
        <div class="dashboard-task-progress">
          <NProgress
            :percentage="taskProgress(task)"
            :show-indicator="false"
            :height="6"
            :border-radius="3"
            :color="taskProgressColor(task)"
          />
          <span>
            {{ task.chapter_succeeded }} / {{ task.chapter_total }} 已完成
            <template v-if="task.chapter_needs_attention > 0">
              · {{ task.chapter_needs_attention }} 待处理
            </template>
          </span>
        </div>
      </article>
    </div>
    <div v-else class="empty-state">
      <Clock3 :size="24" />
      <strong>暂无运行任务</strong>
    </div>
  </section>
</template>

<style scoped>
.dashboard-tasks-section {
  padding-bottom: 0;
}

.operations-section {
  margin-top: 14px;
}

.worker-health-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 1px;
  overflow: hidden;
  border: 1px solid var(--color-border-soft);
  background: var(--color-border-soft);
}

.worker-health-grid > div {
  display: grid;
  grid-template-columns: 20px minmax(0, 1fr);
  gap: 4px 8px;
  padding: 13px;
  background: var(--color-surface);
}

.worker-health-grid > div > svg {
  grid-row: 1 / 3;
  place-self: center;
  color: var(--color-accent);
}

.worker-health-grid span { color: var(--color-text-muted); font-size: 10px; }
.worker-health-grid strong { color: var(--color-text-strong); font-size: 13px; }

.interventions-section { margin-top: 14px; }
.intervention-alert { margin: 16px 20px; }
.intervention-list { border-top: 1px solid var(--color-border-soft); }
.intervention-row {
  display: grid;
  grid-template-columns: 22px minmax(0, 1fr) auto auto;
  align-items: center;
  gap: 10px;
  padding: 12px 20px;
  border-bottom: 1px solid var(--color-border-soft);
}
.intervention-identity { min-width: 0; }
.intervention-identity strong,
.intervention-identity span { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.intervention-identity strong { color: var(--color-text-strong); font-size: 13px; }
.intervention-identity span { margin-top: 3px; color: var(--color-text-muted); font-size: 10px; }

.dashboard-task-skeletons {
  padding-bottom: 20px;
}

.dashboard-task-list {
  max-height: 480px;
  margin: 0 -20px;
  overflow-y: auto;
  border-top: 1px solid var(--color-border-soft);
}

.dashboard-task-row {
  display: grid;
  grid-template-columns: minmax(190px, 1.3fr) 104px minmax(190px, 1fr);
  align-items: center;
  gap: 18px;
  min-height: 70px;
  padding: 12px 20px;
  border-bottom: 1px solid var(--color-border-soft);
}

.dashboard-task-row:last-child {
  border-bottom: 0;
}

.dashboard-task-identity,
.dashboard-task-progress {
  min-width: 0;
}

.dashboard-task-identity strong,
.dashboard-task-identity span {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.dashboard-task-identity strong {
  color: var(--color-text-strong);
  font-size: 13px;
}

.dashboard-task-identity span,
.dashboard-task-progress span {
  margin-top: 4px;
  color: var(--color-text-muted);
  font-size: 10px;
}

.dashboard-task-progress {
  display: grid;
  gap: 3px;
}

@media (max-width: 680px) {
  .worker-health-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .intervention-heading { align-items: flex-start; }
  .intervention-actions { flex-wrap: wrap; justify-content: flex-end; }
  .intervention-row { grid-template-columns: 22px minmax(0, 1fr) auto; padding: 12px 14px; }
  .intervention-row > .n-button { grid-column: 2 / -1; justify-self: end; }

  .dashboard-task-row {
    grid-template-columns: minmax(0, 1fr) auto;
    gap: 10px;
    padding-right: 14px;
    padding-left: 14px;
  }

  .dashboard-task-progress {
    grid-column: 1 / -1;
  }
}
</style>
