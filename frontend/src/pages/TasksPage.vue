<script setup lang="ts">
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import {
  CircleX,
  Clock3,
  ListFilter,
  Pause,
  Play,
  Plus,
  RefreshCw,
  Search,
  Trash2,
  UserRound,
} from 'lucide-vue-next'
import {
  NAlert,
  NButton,
  NCheckbox,
  NDataTable,
  NInput,
  NProgress,
  NSelect,
  NSkeleton,
  NTag,
  useDialog,
  useMessage,
} from 'naive-ui'
import type { DataTableColumns, SelectOption } from 'naive-ui'
import { computed, h, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { ApiError, apiRequest, getAllTasks } from '@/api/client'
import type {
  Account,
  BulkTaskActionResult,
  BulkTaskDeleteResult,
  StudyTask,
  TaskAction,
  TaskHistoryCleanupResult,
} from '@/api/types'
import TaskActionButtons from '@/components/tasks/TaskActionButtons.vue'
import TaskCreateDrawer from '@/components/tasks/TaskCreateDrawer.vue'
import TaskDetailDrawer from '@/components/tasks/TaskDetailDrawer.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import IconAction from '@/components/ui/IconAction.vue'
import { TASK_ACTION_SUCCESS, useTaskActions } from '@/composables/useTaskActions'
import {
  TASK_FILTERS,
  canPauseTask,
  canResumeTask,
  isTaskFilter,
  isTerminalTask,
  matchesTaskFilter,
  statusMeta,
  taskFilterOf,
  taskProgress,
  taskProgressColor,
  type TaskFilter,
} from '@/domain/tasks'
import { formatShortDateTime } from '@/utils/format'

const PAGE_SIZE = 20

const route = useRoute()
const router = useRouter()
const message = useMessage()
const dialog = useDialog()
const queryClient = useQueryClient()
const taskActions = useTaskActions()

const selectedTaskIds = ref<string[]>([])
const search = ref('')
const showCreate = ref(false)
const createAccountId = ref<number | null>(null)

const accounts = useQuery({
  queryKey: ['accounts'],
  queryFn: () => apiRequest<Account[]>('/accounts'),
})

const tasks = useQuery({
  queryKey: ['tasks'],
  queryFn: getAllTasks,
  refetchInterval: 10_000,
})

const statusFilter = computed<TaskFilter>({
  get: () => (isTaskFilter(route.query.status) ? route.query.status : 'all'),
  set: (value) => updateQuery({ status: value === 'all' ? undefined : value }),
})

const accountFilter = computed<number | null>({
  get: () => {
    const value = Number(route.query.account)
    return Number.isInteger(value) && value > 0 ? value : null
  },
  set: (value) => updateQuery({ account: value === null ? undefined : String(value) }),
})

const detailTaskId = computed(() => {
  const value = route.params.taskId
  return typeof value === 'string' && value ? value : null
})

function updateQuery(patch: Record<string, string | undefined>): void {
  const query = { ...route.query, ...patch }
  for (const [key, value] of Object.entries(query)) if (value === undefined) delete query[key]
  void router.replace({ name: 'tasks', params: route.params, query })
}

watch(
  () => route.query.create,
  (value) => {
    if (value !== '1') return
    const account = Number(route.query.account)
    createAccountId.value = Number.isInteger(account) && account > 0 ? account : null
    showCreate.value = true
    const { create: _create, account: _account, ...rest } = route.query
    void router.replace({ name: 'tasks', params: route.params, query: rest })
  },
  { immediate: true },
)

const allTasks = computed(() => tasks.data.value ?? [])
const hasAccounts = computed(() => (accounts.data.value ?? []).some((account) => account.enabled))

const accountOptions = computed<SelectOption[]>(() =>
  (accounts.data.value ?? []).map((account) => ({
    label: account.remark ? `${account.remark} · ${account.username_hint}` : account.username_hint,
    value: account.id,
  })),
)

const baseFiltered = computed(() => {
  const keyword = search.value.trim().toLocaleLowerCase()
  return allTasks.value.filter(
    (task) =>
      (accountFilter.value === null || task.account_id === accountFilter.value) &&
      (!keyword ||
        task.course_title.toLocaleLowerCase().includes(keyword) ||
        task.account_label.toLocaleLowerCase().includes(keyword)),
  )
})

const filterCounts = computed(() => {
  const counts: Record<TaskFilter, number> = {
    all: baseFiltered.value.length,
    active: 0,
    paused: 0,
    attention: 0,
    failed: 0,
    finished: 0,
  }
  for (const task of baseFiltered.value) counts[taskFilterOf(task)] += 1
  return counts
})

const filteredTasks = computed(() =>
  baseFiltered.value.filter((task) => matchesTaskFilter(task, statusFilter.value)),
)
const isFiltered = computed(
  () => statusFilter.value !== 'all' || accountFilter.value !== null || search.value.trim() !== '',
)

const selectedTasks = computed(() => {
  const selected = new Set(selectedTaskIds.value)
  return allTasks.value.filter((task) => selected.has(task.id))
})
const allVisibleSelected = computed(
  () =>
    filteredTasks.value.length > 0 &&
    filteredTasks.value.every((task) => selectedTaskIds.value.includes(task.id)),
)
const someVisibleSelected = computed(() => selectedTaskIds.value.length > 0 && !allVisibleSelected.value)
const historyCount = computed(() => allTasks.value.filter(isTerminalTask).length)

watch(
  () => tasks.data.value,
  (available) => {
    const ids = new Set((available ?? []).map((task) => task.id))
    selectedTaskIds.value = selectedTaskIds.value.filter((taskId) => ids.has(taskId))
  },
)

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback
}

const bulkTaskAction = useMutation({
  mutationFn: ({ action, taskIds }: { action: TaskAction; taskIds: string[] }) =>
    apiRequest<BulkTaskActionResult>('/tasks/bulk-action', {
      method: 'POST',
      body: JSON.stringify({ action, task_ids: taskIds }),
    }),
  async onSuccess(result, variables) {
    await queryClient.invalidateQueries({ queryKey: ['tasks'] })
    selectedTaskIds.value = []
    if (result.failed) {
      message.warning(`${TASK_ACTION_SUCCESS[variables.action]}：成功 ${result.succeeded}，失败 ${result.failed}`)
    } else {
      message.success(`${TASK_ACTION_SUCCESS[variables.action]}（${result.succeeded} 项）`)
    }
  },
  onError(error) {
    message.error(errorMessage(error, '批量操作失败'))
  },
})

const bulkDeleteTasks = useMutation({
  mutationFn: (taskIds: string[]) =>
    apiRequest<BulkTaskDeleteResult>('/tasks/bulk', {
      method: 'DELETE',
      body: JSON.stringify({ task_ids: taskIds }),
    }),
  async onSuccess(result) {
    await queryClient.invalidateQueries({ queryKey: ['tasks'] })
    selectedTaskIds.value = []
    if (result.failed) message.warning(`已删除 ${result.deleted} 项，${result.failed} 项仍在运行或不存在`)
    else message.success(`已删除 ${result.deleted} 项历史任务`)
  },
  onError(error) {
    message.error(errorMessage(error, '任务删除失败'))
  },
})

const cleanupTaskHistory = useMutation({
  mutationFn: () => apiRequest<TaskHistoryCleanupResult>('/tasks/history', { method: 'DELETE' }),
  async onSuccess(result) {
    await queryClient.invalidateQueries({ queryKey: ['tasks'] })
    selectedTaskIds.value = []
    message.success(result.deleted ? `已清理 ${result.deleted} 项历史任务` : '没有可清理的历史任务')
  },
  onError(error) {
    message.error(errorMessage(error, '历史清理失败'))
  },
})

const pendingBulkAction = computed(() =>
  bulkTaskAction.isPending.value ? (bulkTaskAction.variables.value?.action ?? null) : null,
)
const bulkEligible = computed(() => ({
  pause: selectedTasks.value.filter(canPauseTask).length,
  resume: selectedTasks.value.filter(canResumeTask).length,
  cancel: selectedTasks.value.filter((task) => !isTerminalTask(task)).length,
  delete: selectedTasks.value.filter(isTerminalTask).length,
}))

function toggleTask(taskId: string, checked: boolean): void {
  const next = new Set(selectedTaskIds.value)
  if (checked) next.add(taskId)
  else next.delete(taskId)
  selectedTaskIds.value = [...next]
}

function toggleAllVisible(checked: boolean): void {
  const visible = filteredTasks.value.map((task) => task.id)
  selectedTaskIds.value = checked
    ? [...new Set([...selectedTaskIds.value, ...visible])]
    : selectedTaskIds.value.filter((id) => !visible.includes(id))
}

function runBulkAction(action: TaskAction): void {
  const eligible = selectedTasks.value.filter((task) => {
    if (action === 'pause') return canPauseTask(task)
    if (action === 'resume') return canResumeTask(task)
    return !isTerminalTask(task)
  })
  if (eligible.length === 0) {
    message.warning('所选任务中没有可执行此操作的项目')
    return
  }
  const execute = () => bulkTaskAction.mutate({ action, taskIds: eligible.map((task) => task.id) })
  if (action !== 'cancel') {
    execute()
    return
  }
  dialog.warning({
    title: '批量取消任务',
    content: `确认取消选中的 ${eligible.length} 个活动任务？`,
    positiveText: '取消任务',
    negativeText: '返回',
    positiveButtonProps: { type: 'error' },
    onPositiveClick: execute,
  })
}

function deleteSelectedHistory(): void {
  const terminalIds = selectedTasks.value.filter(isTerminalTask).map((task) => task.id)
  if (terminalIds.length === 0) {
    message.warning('所选任务中没有可删除的历史项目')
    return
  }
  dialog.warning({
    title: '删除历史任务',
    content: `确认永久删除选中的 ${terminalIds.length} 个已结束任务及其事件记录？`,
    positiveText: '删除',
    negativeText: '返回',
    positiveButtonProps: { type: 'error' },
    onPositiveClick: () => bulkDeleteTasks.mutate(terminalIds),
  })
}

function clearHistory(): void {
  dialog.warning({
    title: '清理全部历史',
    content: `确认永久删除全部 ${historyCount.value} 个已结束任务（完成、需处理、失败或已取消）？进行中的任务不会被删除。`,
    positiveText: '清理历史',
    negativeText: '返回',
    positiveButtonProps: { type: 'error' },
    onPositiveClick: () => cleanupTaskHistory.mutate(),
  })
}

function clearFilters(): void {
  search.value = ''
  void router.replace({ name: 'tasks', params: route.params, query: {} })
}

function openCreate(): void {
  createAccountId.value = accountFilter.value
  showCreate.value = true
}

function openDetail(taskId: string): void {
  void router.push({ name: 'tasks', params: { taskId }, query: route.query })
}

function closeDetail(): void {
  void router.push({ name: 'tasks', params: {}, query: route.query })
}

function isInteractiveTarget(event: MouseEvent): boolean {
  const target = event.target as HTMLElement | null
  return Boolean(target?.closest('button, a, input, .n-checkbox, .n-data-table-td--selection'))
}

const taskColumns: DataTableColumns<StudyTask> = [
  { type: 'selection', width: 44 },
  {
    title: '课程',
    key: 'course_title',
    minWidth: 240,
    render: (row) =>
      h('div', { class: 'cell-stack' }, [
        h('strong', row.course_title),
        h('span', row.account_label),
      ]),
  },
  {
    title: '状态',
    key: 'status',
    width: 104,
    render: (row) => {
      const meta = statusMeta(row.status)
      return h(NTag, { size: 'small', type: meta.type, bordered: false }, { default: () => meta.label })
    },
  },
  {
    title: '章节进度',
    key: 'progress',
    minWidth: 200,
    render: (row) =>
      h('div', { class: 'task-progress-cell' }, [
        h(NProgress, {
          percentage: taskProgress(row),
          showIndicator: false,
          height: 6,
          borderRadius: 3,
          color: taskProgressColor(row),
        }),
        h('span', { class: 'num' }, [
          `${row.chapter_succeeded} / ${row.chapter_total} 已完成`,
          row.chapter_needs_attention > 0 ? ` · ${row.chapter_needs_attention} 待处理` : '',
        ]),
      ]),
  },
  {
    title: '创建时间',
    key: 'created_at',
    width: 120,
    render: (row) => h('span', { class: 'cell-muted num' }, formatShortDateTime(row.created_at)),
  },
  {
    title: '操作',
    key: 'actions',
    width: 108,
    render: (row) =>
      h(TaskActionButtons, {
        task: row,
        pending: taskActions.pendingAction(row.id),
        onAction: (action: TaskAction) => taskActions.run(row, action),
      }),
  },
]

const rowProps = (row: StudyTask) => ({
  onClick: (event: MouseEvent) => {
    if (!isInteractiveTarget(event)) openDetail(row.id)
  },
})

const pagination = computed(() =>
  filteredTasks.value.length > PAGE_SIZE ? { pageSize: PAGE_SIZE } : false,
)
</script>

<template>
  <section class="content-section flush task-list-section">
    <div class="list-toolbar">
      <div class="segmented" role="tablist" aria-label="按任务状态筛选">
        <button
          v-for="filter in TASK_FILTERS"
          :key="filter.value"
          type="button"
          role="tab"
          :aria-selected="statusFilter === filter.value"
          @click="statusFilter = filter.value"
        >
          {{ filter.label }}
          <span
            class="count"
            :class="{
              warn: filter.value === 'attention' && filterCounts.attention > 0,
              error: filter.value === 'failed' && filterCounts.failed > 0,
            }"
          >{{ filterCounts[filter.value] }}</span>
        </button>
      </div>
      <span class="toolbar-spacer" />
      <NButton type="primary" @click="openCreate">
        <template #icon><Plus /></template>
        新建任务
      </NButton>
    </div>

    <div class="list-toolbar secondary-toolbar">
      <NInput
        v-model:value="search"
        class="toolbar-search"
        clearable
        placeholder="搜索课程或账号"
        :input-props="{ 'aria-label': '搜索任务' }"
      >
        <template #prefix><Search :size="16" /></template>
      </NInput>
      <NSelect
        v-model:value="accountFilter"
        class="toolbar-select"
        clearable
        filterable
        :options="accountOptions"
        placeholder="全部账号"
        aria-label="按账号筛选"
      />
      <span class="toolbar-spacer" />
      <span class="list-count">每 10 秒自动刷新</span>
      <IconAction
        label="刷新任务"
        :icon="RefreshCw"
        size="medium"
        :loading="tasks.isFetching.value"
        @click="tasks.refetch()"
      />
      <NButton
        quaternary
        type="error"
        :disabled="historyCount === 0"
        :loading="cleanupTaskHistory.isPending.value"
        @click="clearHistory"
      >
        <template #icon><Trash2 /></template>
        清理历史
      </NButton>
    </div>

    <div v-if="!tasks.isError.value && selectedTaskIds.length > 0" class="bulk-task-toolbar">
      <NCheckbox
        :checked="allVisibleSelected"
        :indeterminate="someVisibleSelected"
        @update:checked="toggleAllVisible"
      >
        已选 {{ selectedTaskIds.length }} 项
      </NCheckbox>
      <div class="bulk-task-actions">
        <NButton
          size="small"
          :disabled="bulkEligible.pause === 0 || bulkTaskAction.isPending.value"
          :loading="pendingBulkAction === 'pause'"
          @click="runBulkAction('pause')"
        >
          <template #icon><Pause /></template>
          暂停
        </NButton>
        <NButton
          size="small"
          :disabled="bulkEligible.resume === 0 || bulkTaskAction.isPending.value"
          :loading="pendingBulkAction === 'resume'"
          @click="runBulkAction('resume')"
        >
          <template #icon><Play /></template>
          恢复
        </NButton>
        <NButton
          size="small"
          type="warning"
          secondary
          :disabled="bulkEligible.cancel === 0 || bulkTaskAction.isPending.value"
          :loading="pendingBulkAction === 'cancel'"
          @click="runBulkAction('cancel')"
        >
          <template #icon><CircleX /></template>
          取消
        </NButton>
        <NButton
          size="small"
          type="error"
          secondary
          :disabled="bulkEligible.delete === 0"
          :loading="bulkDeleteTasks.isPending.value"
          @click="deleteSelectedHistory"
        >
          <template #icon><Trash2 /></template>
          删除
        </NButton>
        <NButton size="small" quaternary @click="selectedTaskIds = []">取消选择</NButton>
      </div>
    </div>

    <NAlert v-if="tasks.isError.value" type="error" :bordered="false">
      任务列表加载失败，请稍后重试。
    </NAlert>

    <div v-else-if="tasks.isLoading.value" class="list-skeleton" aria-label="正在加载任务">
      <NSkeleton v-for="index in 5" :key="index" text :height="44" />
    </div>

    <EmptyState
      v-else-if="allTasks.length === 0"
      :icon="Clock3"
      title="还没有学习任务"
      :description="hasAccounts ? '选择账号、课程和章节即可创建第一个任务。' : '先添加一个学习通账号，然后创建学习任务。'"
    >
      <NButton v-if="hasAccounts || accounts.isLoading.value" type="primary" @click="openCreate">
        <template #icon><Plus /></template>
        新建任务
      </NButton>
      <NButton v-else type="primary" @click="router.push({ name: 'accounts', query: { create: '1' } })">
        去添加账号
      </NButton>
    </EmptyState>

    <EmptyState
      v-else-if="filteredTasks.length === 0"
      :icon="ListFilter"
      title="没有符合条件的任务"
      description="试试调整状态、账号或搜索关键词。"
    >
      <NButton v-if="isFiltered" @click="clearFilters">清除筛选</NButton>
    </EmptyState>

    <template v-else>
      <NDataTable
        class="desktop-task-table desktop-only clickable-rows"
        :columns="taskColumns"
        :data="filteredTasks"
        :bordered="false"
        :row-key="(row: StudyTask) => row.id"
        :row-props="rowProps"
        :checked-row-keys="selectedTaskIds"
        :scroll-x="860"
        :pagination="pagination"
        @update:checked-row-keys="selectedTaskIds = $event.map(String)"
      />

      <div class="mobile-card-list mobile-only">
        <article
          v-for="task in filteredTasks"
          :key="task.id"
          class="mobile-card mobile-task-item"
          @click="(event) => { if (!isInteractiveTarget(event)) openDetail(task.id) }"
        >
          <div class="mobile-card-head">
            <NCheckbox
              :checked="selectedTaskIds.includes(task.id)"
              :aria-label="`勾选任务 ${task.course_title}`"
              @update:checked="toggleTask(task.id, $event)"
            />
            <div class="cell-stack mobile-task-title">
              <strong>{{ task.course_title }}</strong>
              <span><UserRound :size="12" class="inline-icon" />{{ task.account_label }}</span>
            </div>
            <NTag :type="statusMeta(task.status).type" size="small" :bordered="false">
              {{ statusMeta(task.status).label }}
            </NTag>
          </div>
          <NProgress
            :percentage="taskProgress(task)"
            :show-indicator="false"
            :height="6"
            :border-radius="3"
            :color="taskProgressColor(task)"
          />
          <div class="mobile-card-meta">
            <span class="num">{{ task.chapter_succeeded }} / {{ task.chapter_total }} 已完成</span>
            <span v-if="task.chapter_needs_attention > 0" class="warn-text">
              {{ task.chapter_needs_attention }} 待处理
            </span>
            <time :datetime="task.created_at" class="push-right">{{ formatShortDateTime(task.created_at) }}</time>
          </div>
          <div v-if="!isTerminalTask(task)" class="mobile-card-actions">
            <TaskActionButtons
              :task="task"
              :pending="taskActions.pendingAction(task.id)"
              @action="(action) => taskActions.run(task, action)"
            />
          </div>
        </article>
      </div>
    </template>
  </section>

  <TaskCreateDrawer v-model:show="showCreate" :initial-account-id="createAccountId" />
  <TaskDetailDrawer :task-id="detailTaskId" @close="closeDetail" />
</template>

<style scoped>
.secondary-toolbar {
  min-height: 56px;
  background: var(--color-surface-muted);
}

.bulk-task-toolbar {
  display: flex;
  min-height: 50px;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  border-bottom: 1px solid var(--color-border-soft);
  background: var(--color-accent-softer);
  padding: 8px 20px;
}

.bulk-task-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
}

.desktop-task-table :deep(.task-progress-cell) {
  display: grid;
  gap: 5px;
}

.desktop-task-table :deep(.task-progress-cell span) {
  color: var(--color-text-muted);
  font-size: var(--fs-xs);
}

.mobile-task-item {
  cursor: pointer;
}

.mobile-task-title {
  flex: 1 1 auto;
}

.mobile-task-title span {
  display: flex !important;
  align-items: center;
  gap: 4px;
}

.inline-icon {
  flex: 0 0 auto;
}

.warn-text {
  color: var(--color-warning);
}

.push-right {
  margin-left: auto;
}

@media (max-width: 680px) {
  .bulk-task-toolbar {
    align-items: stretch;
    flex-direction: column;
    padding: 10px 14px;
  }

  .secondary-toolbar .list-count {
    display: none;
  }
}
</style>
