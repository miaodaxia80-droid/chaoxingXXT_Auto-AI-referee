<script setup lang="ts">
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import {
  BookOpenCheck,
  CheckSquare2,
  ChevronRight,
  CircleX,
  Clock3,
  GraduationCap,
  ListChecks,
  LockKeyhole,
  Pause,
  Play,
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
  NEmpty,
  NInput,
  NProgress,
  NSelect,
  NSpin,
  NTag,
  NTooltip,
  useDialog,
  useMessage,
} from 'naive-ui'
import type { DataTableColumns, SelectOption } from 'naive-ui'
import { computed, h, ref, watch } from 'vue'
import type { Component } from 'vue'

import { ApiError, apiRequest, getAllTasks } from '@/api/client'
import type {
  Account,
  BulkTaskActionResult,
  BulkTaskCreateItemResult,
  BulkTaskCreateResult,
  BulkTaskDeleteResult,
  Course,
  CourseOutline,
  CreateTaskInput,
  StudyTask,
  StudyTaskDetail,
  TaskAction,
  TaskHistoryCleanupResult,
} from '@/api/types'
import {
  isTerminalTask,
  statusMeta,
  taskProgress,
  taskProgressColor,
} from '@/domain/tasks'

const TASK_ACTION_SUCCESS: Record<TaskAction, string> = {
  pause: '已请求暂停任务',
  resume: '任务已恢复排队',
  cancel: '已请求取消任务',
}

const message = useMessage()
const dialog = useDialog()
const queryClient = useQueryClient()
const selectedAccountId = ref<number | null>(null)
const courses = ref<Course[]>([])
const coursesLoaded = ref(false)
const courseSearch = ref('')
const selectedCourse = ref<Course | null>(null)
const selectedCourseKeys = ref<string[]>([])
const outline = ref<CourseOutline | null>(null)
const selectedChapterIds = ref<string[]>([])
const pendingTaskActions = ref(new Map<string, TaskAction>())
const selectedTaskIds = ref<string[]>([])
const BULK_DISCOVERY_CONCURRENCY = 4
const BULK_CREATE_BATCH_SIZE = 100

const accounts = useQuery({
  queryKey: ['accounts'],
  queryFn: () => apiRequest<Account[]>('/accounts'),
})

const tasks = useQuery({
  queryKey: ['tasks'],
  queryFn: getAllTasks,
  refetchInterval: 10_000,
})

const enabledAccounts = computed(() =>
  (accounts.data.value ?? []).filter((account) => account.enabled),
)

const accountOptions = computed<SelectOption[]>(() =>
  (accounts.data.value ?? []).map((account) => ({
    label: account.remark
      ? `${account.remark} · ${account.username_hint}`
      : account.username_hint,
    value: account.id,
    disabled: !account.enabled,
  })),
)

const selectedAccount = computed(
  () => accounts.data.value?.find((account) => account.id === selectedAccountId.value) ?? null,
)

const filteredCourses = computed(() => {
  const keyword = courseSearch.value.trim().toLocaleLowerCase()
  if (!keyword) return courses.value
  return courses.value.filter((course) =>
    [course.title, course.teacher, course.description].some((value) =>
      value.toLocaleLowerCase().includes(keyword),
    ),
  )
})

const selectedChapterSet = computed(() => new Set(selectedChapterIds.value))
const selectedCourseSet = computed(() => new Set(selectedCourseKeys.value))
const chapterCount = computed(() => outline.value?.chapters.length ?? 0)
const allChaptersSelected = computed(
  () => chapterCount.value > 0 && selectedChapterIds.value.length === chapterCount.value,
)
const someChaptersSelected = computed(
  () => selectedChapterIds.value.length > 0 && !allChaptersSelected.value,
)
const canCreateTask = computed(
  () =>
    selectedAccountId.value !== null &&
    selectedCourse.value !== null &&
    selectedChapterIds.value.length > 0,
)
const selectedTasks = computed(() => {
  const selected = new Set(selectedTaskIds.value)
  return (tasks.data.value ?? []).filter((task) => selected.has(task.id))
})
const allTasksSelected = computed(
  () =>
    (tasks.data.value?.length ?? 0) > 0 &&
    selectedTaskIds.value.length === tasks.data.value?.length,
)
const someTasksSelected = computed(
  () => selectedTaskIds.value.length > 0 && !allTasksSelected.value,
)

const discoverChapters = useMutation({
  mutationFn: ({ accountId, course }: { accountId: number; course: Course }) =>
    apiRequest<CourseOutline>(
      `/accounts/${accountId}/courses/${encodeURIComponent(course.course_id)}/chapters`,
      {
        method: 'POST',
        body: JSON.stringify({
          class_id: course.class_id,
          cpi: course.cpi,
          title: course.title,
        }),
      },
    ),
  onSuccess(data, variables) {
    if (
      selectedAccountId.value !== variables.accountId ||
      courseKey(selectedCourse.value) !== courseKey(variables.course)
    ) {
      return
    }
    outline.value = data
    selectedChapterIds.value = data.chapters
      .filter((chapter) => !chapter.is_completed)
      .map((chapter) => chapter.chapter_id)
  },
  onError(error, variables) {
    if (
      selectedAccountId.value !== variables.accountId ||
      courseKey(selectedCourse.value) !== courseKey(variables.course)
    ) {
      return
    }
    message.error(errorMessage(error, '章节加载失败'))
  },
})

const discoverCourses = useMutation({
  mutationFn: (accountId: number) =>
    apiRequest<Course[]>(`/accounts/${accountId}/courses/discover`, { method: 'POST' }),
  onSuccess(data, accountId) {
    if (selectedAccountId.value !== accountId) return
    courses.value = data
    coursesLoaded.value = true
    const firstCourse = data[0]
    if (firstCourse) selectCourse(firstCourse)
  },
  onError(error, accountId) {
    if (selectedAccountId.value !== accountId) return
    coursesLoaded.value = true
    message.error(errorMessage(error, '课程加载失败'))
  },
})

const createTask = useMutation({
  mutationFn: (payload: CreateTaskInput) =>
    apiRequest<StudyTaskDetail>('/tasks', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  async onSuccess() {
    selectedChapterIds.value = []
    await queryClient.invalidateQueries({ queryKey: ['tasks'] })
    message.success('学习任务已加入队列')
  },
  onError(error) {
    message.error(errorMessage(error, '任务创建失败'))
  },
})

const bulkCreateTasks = useMutation({
  async mutationFn(coursesToCreate: Course[]) {
    const accountId = selectedAccountId.value
    if (accountId === null) throw new Error('account is required')
    const prepared: PromiseSettledResult<CreateTaskInput>[] = []
    for (let index = 0; index < coursesToCreate.length; index += BULK_DISCOVERY_CONCURRENCY) {
      const batch = coursesToCreate.slice(index, index + BULK_DISCOVERY_CONCURRENCY)
      prepared.push(
        ...(await Promise.allSettled(
          batch.map(async (course) => {
            const discovered = await apiRequest<CourseOutline>(
              `/accounts/${accountId}/courses/${encodeURIComponent(course.course_id)}/chapters`,
              {
                method: 'POST',
                body: JSON.stringify({
                  class_id: course.class_id,
                  cpi: course.cpi,
                  title: course.title,
                }),
              },
            )
            return {
              account_id: accountId,
              course_id: course.course_id,
              class_id: course.class_id,
              cpi: course.cpi,
              course_title: course.title,
              chapters: discovered.chapters
                .filter((chapter) => !chapter.is_completed)
                .sort((left, right) => left.position - right.position)
                .map((chapter) => ({
                  chapter_id: chapter.chapter_id,
                  title: chapter.title,
                  position: chapter.position,
                })),
            } satisfies CreateTaskInput
          }),
        )),
      )
    }
    const discoveryFailed = prepared.filter((result) => result.status === 'rejected').length
    const payloads = prepared
      .filter((result): result is PromiseFulfilledResult<CreateTaskInput> => result.status === 'fulfilled')
      .map((result) => result.value)
      .filter((payload) => payload.chapters.length > 0)
    const alreadyComplete = prepared.filter(
      (result) => result.status === 'fulfilled' && result.value.chapters.length === 0,
    ).length
    const result: BulkTaskCreateResult = { total: 0, created: 0, failed: 0, results: [] }
    for (let index = 0; index < payloads.length; index += BULK_CREATE_BATCH_SIZE) {
      const batch = payloads.slice(index, index + BULK_CREATE_BATCH_SIZE)
      try {
        const batchResult = await apiRequest<BulkTaskCreateResult>('/tasks/bulk-create', {
          method: 'POST',
          body: JSON.stringify({ tasks: batch }),
        })
        result.total += batchResult.total
        result.created += batchResult.created
        result.failed += batchResult.failed
        result.results.push(...batchResult.results)
      } catch {
        const failedResults: BulkTaskCreateItemResult[] = batch.map((payload) => ({
          course_id: payload.course_id,
          class_id: payload.class_id,
          status: 'failed',
          task: null,
          error_code: 'request_failed',
          error: '批量创建请求失败',
        }))
        result.total += failedResults.length
        result.failed += failedResults.length
        result.results.push(...failedResults)
      }
    }
    return { result, discoveryFailed, alreadyComplete }
  },
  async onSuccess({ result, discoveryFailed, alreadyComplete }) {
    selectedCourseKeys.value = []
    await queryClient.invalidateQueries({ queryKey: ['tasks'] })
    const skipped = discoveryFailed + alreadyComplete + result.failed
    if (skipped > 0) {
      message.warning(`已创建 ${result.created} 项，跳过 ${skipped} 项`)
    } else {
      message.success(`已创建 ${result.created} 个课程任务`)
    }
  },
  onError(error) {
    message.error(errorMessage(error, '批量任务创建失败'))
  },
})

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

watch(
  () => accounts.data.value,
  (available) => {
    if (!available) return
    const currentIsEnabled = available.some(
      (account) => account.id === selectedAccountId.value && account.enabled,
    )
    if (!currentIsEnabled) {
      selectedAccountId.value = available.find((account) => account.enabled)?.id ?? null
    }
  },
  { immediate: true },
)

watch(selectedAccountId, () => {
  courses.value = []
  coursesLoaded.value = false
  courseSearch.value = ''
  selectedCourse.value = null
  outline.value = null
  selectedChapterIds.value = []
  selectedCourseKeys.value = []
})

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

function courseKey(course: Course | null): string {
  return course ? `${course.course_id}:${course.class_id}` : ''
}

function refreshCourses() {
  const account = selectedAccount.value
  if (!account) {
    message.warning('请先选择一个账号')
    return
  }
  if (!account.enabled) {
    message.warning('该账号已停用')
    return
  }
  courses.value = []
  coursesLoaded.value = false
  courseSearch.value = ''
  selectedCourse.value = null
  outline.value = null
  selectedChapterIds.value = []
  selectedCourseKeys.value = []
  discoverCourses.mutate(account.id)
}

function selectCourse(course: Course) {
  const accountId = selectedAccountId.value
  if (accountId === null) return
  selectedCourse.value = course
  outline.value = null
  selectedChapterIds.value = []
  discoverChapters.mutate({ accountId, course })
}

function toggleCourse(course: Course, checked: boolean) {
  const next = new Set(selectedCourseKeys.value)
  const key = courseKey(course)
  if (checked) next.add(key)
  else next.delete(key)
  selectedCourseKeys.value = [...next]
}

function submitBulkCourses() {
  const selected = selectedCourseSet.value
  const coursesToCreate = courses.value.filter((course) => selected.has(courseKey(course)))
  if (coursesToCreate.length === 0) {
    message.warning('至少勾选一门课程')
    return
  }
  bulkCreateTasks.mutate(coursesToCreate)
}

function toggleChapter(chapterId: string, checked: boolean) {
  const next = new Set(selectedChapterIds.value)
  if (checked) next.add(chapterId)
  else next.delete(chapterId)
  selectedChapterIds.value = [...next]
}

function toggleAllChapters(checked: boolean) {
  selectedChapterIds.value = checked
    ? (outline.value?.chapters.map((chapter) => chapter.chapter_id) ?? [])
    : []
}

function selectIncompleteChapters() {
  selectedChapterIds.value =
    outline.value?.chapters
      .filter((chapter) => !chapter.is_completed)
      .map((chapter) => chapter.chapter_id) ?? []
}

function submitTask() {
  const accountId = selectedAccountId.value
  const course = selectedCourse.value
  const chapters = outline.value?.chapters
  if (accountId === null || !course || !chapters) return
  if (selectedChapterIds.value.length === 0) {
    message.warning('至少选择一个章节')
    return
  }

  const selected = selectedChapterSet.value
  createTask.mutate({
    account_id: accountId,
    course_id: course.course_id,
    class_id: course.class_id,
    cpi: course.cpi,
    course_title: course.title,
    chapters: chapters
      .filter((chapter) => selected.has(chapter.chapter_id))
      .sort((left, right) => left.position - right.position)
      .map((chapter) => ({
        chapter_id: chapter.chapter_id,
        title: chapter.title,
        position: chapter.position,
      })),
  })
}

function canPauseTask(task: StudyTask): boolean {
  return task.status === 'queued' || task.status === 'running'
}

function canResumeTask(task: StudyTask): boolean {
  return task.status === 'paused' || task.status === 'pause_requested'
}

function toggleTask(taskId: string, checked: boolean) {
  const next = new Set(selectedTaskIds.value)
  if (checked) next.add(taskId)
  else next.delete(taskId)
  selectedTaskIds.value = [...next]
}

function toggleAllTasks(checked: boolean) {
  selectedTaskIds.value = checked ? (tasks.data.value ?? []).map((task) => task.id) : []
}

function runBulkAction(action: TaskAction) {
  const eligible = selectedTasks.value.filter((task) => {
    if (action === 'pause') return canPauseTask(task)
    if (action === 'resume') return canResumeTask(task)
    return !isTerminalTask(task)
  })
  if (eligible.length === 0) {
    message.warning('所选任务中没有可执行此操作的项目')
    return
  }
  const execute = () =>
    bulkTaskAction.mutate({
      action,
      taskIds: eligible.map((task) => task.id),
    })
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

function deleteSelectedHistory() {
  const terminalIds = selectedTasks.value
    .filter((task) => isTerminalTask(task))
    .map((task) => task.id)
  if (terminalIds.length === 0) {
    message.warning('所选任务中没有可删除的历史项目')
    return
  }
  dialog.warning({
    title: '删除历史任务',
    content: `确认永久删除选中的 ${terminalIds.length} 个终态任务及其事件记录？`,
    positiveText: '删除',
    negativeText: '返回',
    positiveButtonProps: { type: 'error' },
    onPositiveClick: () => bulkDeleteTasks.mutate(terminalIds),
  })
}

function clearHistory() {
  dialog.warning({
    title: '清理全部历史',
    content: '确认永久删除全部已完成、失败或已取消任务？活动任务不会被删除。',
    positiveText: '清理历史',
    negativeText: '返回',
    positiveButtonProps: { type: 'error' },
    onPositiveClick: () => cleanupTaskHistory.mutate(),
  })
}

function pendingTaskAction(taskId: string): TaskAction | null {
  return pendingTaskActions.value.get(taskId) ?? null
}

function setPendingTaskAction(taskId: string, action: TaskAction | null) {
  const next = new Map(pendingTaskActions.value)
  if (action) next.set(taskId, action)
  else next.delete(taskId)
  pendingTaskActions.value = next
}

async function executeTaskAction(task: StudyTask, action: TaskAction): Promise<boolean> {
  if (pendingTaskAction(task.id)) return false
  setPendingTaskAction(task.id, action)
  try {
    await apiRequest<StudyTaskDetail>(`/tasks/${encodeURIComponent(task.id)}/${action}`, {
      method: 'POST',
    })
    await queryClient.invalidateQueries({ queryKey: ['tasks'] })
    message.success(TASK_ACTION_SUCCESS[action])
    return true
  } catch (error) {
    message.error(errorMessage(error, '任务操作失败'))
    return false
  } finally {
    setPendingTaskAction(task.id, null)
  }
}

function requestCancelTask(task: StudyTask) {
  if (pendingTaskAction(task.id)) return
  dialog.warning({
    title: '取消任务',
    content: `确认取消“${task.course_title}”的学习任务？正在执行的章节会在安全点停止。`,
    positiveText: '取消任务',
    negativeText: '返回',
    positiveButtonProps: { type: 'error' },
    async onPositiveClick() {
      return (await executeTaskAction(task, 'cancel')) || false
    },
  })
}

function taskActionButton(
  task: StudyTask,
  action: TaskAction,
  label: string,
  icon: Component,
  danger = false,
) {
  const pending = pendingTaskAction(task.id)
  return h(
    NTooltip,
    null,
    {
      trigger: () =>
        h(
          NButton,
          {
            quaternary: true,
            circle: true,
            size: 'small',
            type: danger ? 'error' : 'default',
            loading: pending === action,
            disabled: pending !== null,
            'aria-label': label,
            onClick: () => {
              if (action === 'cancel') requestCancelTask(task)
              else void executeTaskAction(task, action)
            },
          },
          { icon: () => h(icon, { size: 16 }) },
        ),
      default: () => label,
    },
  )
}

function renderTaskActions(task: StudyTask) {
  const controls = []
  if (canPauseTask(task)) {
    controls.push(taskActionButton(task, 'pause', '暂停任务', Pause))
  }
  if (canResumeTask(task)) {
    controls.push(taskActionButton(task, 'resume', '恢复任务', Play))
  }
  if (!isTerminalTask(task)) {
    controls.push(taskActionButton(task, 'cancel', '取消任务', CircleX, true))
  }
  return controls.length > 0
    ? h('div', { class: 'task-action-buttons' }, controls)
    : h('span', { class: 'task-no-actions' }, '—')
}

const dateFormatter = new Intl.DateTimeFormat('zh-CN', {
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
})

function formatDate(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '-' : dateFormatter.format(date)
}

const taskColumns: DataTableColumns<StudyTask> = [
  {
    type: 'selection',
    width: 42,
  },
  {
    title: '课程',
    key: 'course_title',
    minWidth: 220,
    render: (row) =>
      h('div', { class: 'task-course-cell' }, [
        h('strong', row.course_title),
        h('span', `账号：${row.account_label}`),
      ]),
  },
  {
    title: '状态',
    key: 'status',
    width: 112,
    render: (row) => {
      const meta = statusMeta(row.status)
      return h(NTag, { size: 'small', type: meta.type, bordered: false }, { default: () => meta.label })
    },
  },
  {
    title: '章节进度',
    key: 'progress',
    minWidth: 210,
    render: (row) =>
      h('div', { class: 'task-progress-cell' }, [
        h(NProgress, {
          percentage: taskProgress(row),
          showIndicator: false,
          height: 6,
          borderRadius: 3,
          color: taskProgressColor(row),
        }),
        h('span', [
          `${row.chapter_succeeded} / ${row.chapter_total} 已完成`,
          row.chapter_needs_attention > 0 ? ` · ${row.chapter_needs_attention} 待处理` : '',
        ]),
      ]),
  },
  {
    title: '创建时间',
    key: 'created_at',
    width: 132,
    render: (row) => formatDate(row.created_at),
  },
  {
    title: '操作',
    key: 'actions',
    width: 104,
    render: renderTaskActions,
  },
]
</script>

<template>
  <section class="content-section flush task-create-section">
    <div class="section-heading padded task-heading">
      <div>
        <h2>创建学习任务</h2>
        <p>选择账号和课程，再将需要执行的章节加入队列</p>
      </div>
    </div>

    <div class="account-toolbar">
      <div class="toolbar-label">
        <span class="step-index">1</span>
        <div>
          <strong>学习账号</strong>
          <span>仅显示脱敏账号信息</span>
        </div>
      </div>
      <NSelect
        v-model:value="selectedAccountId"
        class="account-select"
        :options="accountOptions"
        :loading="accounts.isLoading.value"
        placeholder="选择已启用的账号"
        filterable
      />
      <NButton
        type="primary"
        :loading="discoverCourses.isPending.value"
        :disabled="selectedAccountId === null"
        @click="refreshCourses"
      >
        <template #icon><RefreshCw /></template>
        刷新课程
      </NButton>
    </div>

    <NAlert v-if="accounts.isError.value" type="error" :bordered="false">
      账号列表加载失败，请刷新页面后重试。
    </NAlert>
    <NAlert
      v-else-if="!accounts.isLoading.value && enabledAccounts.length === 0"
      type="warning"
      :bordered="false"
    >
      暂无可用账号，请先在“账号”页面添加并启用学习账号。
    </NAlert>

    <div class="task-builder-grid">
      <section class="builder-pane course-pane" aria-labelledby="course-pane-title">
        <div class="pane-heading course-pane-heading">
          <div class="pane-title">
            <span class="step-index">2</span>
            <div>
              <strong id="course-pane-title">选择课程</strong>
              <span>{{ coursesLoaded ? `共 ${courses.length} 门` : '刷新后读取课程' }}</span>
            </div>
          </div>
          <NButton
            size="small"
            type="primary"
            secondary
            :loading="bulkCreateTasks.isPending.value"
            :disabled="selectedCourseKeys.length === 0"
            @click="submitBulkCourses"
          >
            <template #icon><CheckSquare2 /></template>
            批量创建 {{ selectedCourseKeys.length || '' }}
          </NButton>
        </div>

        <NInput
          v-model:value="courseSearch"
          class="course-search"
          clearable
          :disabled="courses.length === 0"
          placeholder="搜索课程、教师或简介"
        >
          <template #prefix><Search :size="16" /></template>
        </NInput>

        <div class="course-list">
          <NSpin v-if="discoverCourses.isPending.value" size="small" description="正在读取课程" />
          <NEmpty
            v-else-if="!coursesLoaded"
            size="small"
            description="选择账号并刷新课程"
          >
            <template #icon><BookOpenCheck :size="24" /></template>
          </NEmpty>
          <NEmpty
            v-else-if="filteredCourses.length === 0"
            size="small"
            :description="courses.length === 0 ? '该账号暂无课程' : '没有匹配的课程'"
          />
          <div
            v-for="course in filteredCourses"
            v-else
            :key="courseKey(course)"
            class="course-row"
            :class="{ selected: courseKey(selectedCourse) === courseKey(course) }"
          >
            <span class="course-select">
              <NCheckbox
                :checked="selectedCourseSet.has(courseKey(course))"
                :aria-label="`勾选课程 ${course.title}`"
                @update:checked="toggleCourse(course, $event)"
              />
            </span>
            <button
              type="button"
              class="course-open-button"
              :aria-pressed="courseKey(selectedCourse) === courseKey(course)"
              @click="selectCourse(course)"
            >
              <span class="course-icon"><GraduationCap :size="18" /></span>
              <span class="course-copy">
                <strong>{{ course.title }}</strong>
                <span>{{ course.teacher || '教师信息未提供' }}</span>
                <small v-if="course.description">{{ course.description }}</small>
              </span>
              <ChevronRight :size="17" class="course-chevron" />
            </button>
          </div>
        </div>
      </section>

      <section class="builder-pane chapter-pane" aria-labelledby="chapter-pane-title">
        <div class="pane-heading chapter-heading">
          <div class="pane-title">
            <span class="step-index">3</span>
            <div>
              <strong id="chapter-pane-title">选择章节</strong>
              <span v-if="selectedCourse">{{ selectedCourse.title }}</span>
              <span v-else>选择课程后读取章节</span>
            </div>
          </div>
          <span v-if="outline" class="selection-count">
            已选 {{ selectedChapterIds.length }} / {{ chapterCount }}
          </span>
        </div>

        <div class="chapter-content-area">
          <NSpin
            v-if="discoverChapters.isPending.value"
            size="small"
            description="正在读取章节"
          />
          <NEmpty v-else-if="!selectedCourse" size="small" description="请先选择一门课程">
            <template #icon><ListChecks :size="24" /></template>
          </NEmpty>
          <NEmpty
            v-else-if="outline && outline.chapters.length === 0"
            size="small"
            description="该课程暂无章节"
          />
          <template v-else-if="outline">
            <NAlert
              v-if="outline.has_locked_chapters"
              class="locked-alert"
              type="warning"
              :bordered="false"
            >
              部分章节尚未开放，执行时将遵循该账号的未开放章节策略。
            </NAlert>
            <div class="chapter-toolbar">
              <NCheckbox
                :checked="allChaptersSelected"
                :indeterminate="someChaptersSelected"
                @update:checked="toggleAllChapters"
              >
                全选
              </NCheckbox>
              <NButton text type="primary" @click="selectIncompleteChapters">
                仅选未完成
              </NButton>
            </div>
            <div class="chapter-list">
              <div
                v-for="chapter in outline.chapters"
                :key="chapter.chapter_id"
                class="chapter-row"
                :class="{ completed: chapter.is_completed }"
              >
                <NCheckbox
                  :checked="selectedChapterSet.has(chapter.chapter_id)"
                  @update:checked="toggleChapter(chapter.chapter_id, $event)"
                >
                  <div class="chapter-copy">
                    <strong>{{ chapter.title }}</strong>
                    <div class="chapter-meta">
                      <span>第 {{ chapter.position + 1 }} 节</span>
                      <span>{{ chapter.job_count }} 个任务点</span>
                      <NTag
                        v-if="chapter.is_completed"
                        size="small"
                        type="success"
                        :bordered="false"
                      >
                        已完成
                      </NTag>
                      <NTag
                        v-if="chapter.requires_unlock"
                        size="small"
                        type="warning"
                        :bordered="false"
                      >
                        <template #icon><LockKeyhole :size="12" /></template>
                        未开放
                      </NTag>
                    </div>
                  </div>
                </NCheckbox>
              </div>
            </div>
          </template>
        </div>

        <div class="create-task-bar">
          <div>
            <strong>{{ selectedChapterIds.length }} 个章节</strong>
            <span>创建后进入账号互斥队列</span>
          </div>
          <NButton
            type="primary"
            :loading="createTask.isPending.value"
            :disabled="!canCreateTask"
            @click="submitTask"
          >
            <template #icon><Play /></template>
            创建任务
          </NButton>
        </div>
      </section>
    </div>
  </section>

  <section class="content-section flush task-list-section">
    <div class="section-heading padded">
      <div>
        <h2>任务状态</h2>
        <p>任务执行期间每 10 秒自动更新</p>
      </div>
      <div class="task-heading-actions">
        <NButton
          size="small"
          secondary
          type="error"
          :loading="cleanupTaskHistory.isPending.value"
          @click="clearHistory"
        >
          <template #icon><Trash2 /></template>
          清理历史
        </NButton>
        <NTooltip trigger="hover">
          <template #trigger>
            <NButton
              quaternary
              circle
              aria-label="刷新任务"
              :loading="tasks.isFetching.value"
              @click="tasks.refetch()"
            >
              <template #icon><RefreshCw /></template>
            </NButton>
          </template>
          刷新任务
        </NTooltip>
      </div>
    </div>

    <NAlert v-if="tasks.isError.value" type="error" :bordered="false">
      任务列表加载失败，请稍后重试。
    </NAlert>
    <div v-if="!tasks.isError.value && selectedTaskIds.length > 0" class="bulk-task-toolbar">
      <div>
        <NCheckbox
          :checked="allTasksSelected"
          :indeterminate="someTasksSelected"
          @update:checked="toggleAllTasks"
        >
          已选 {{ selectedTaskIds.length }} 项
        </NCheckbox>
      </div>
      <div class="bulk-task-actions">
        <NButton
          size="small"
          :loading="bulkTaskAction.isPending.value"
          @click="runBulkAction('pause')"
        >
          <template #icon><Pause /></template>
          暂停
        </NButton>
        <NButton
          size="small"
          :loading="bulkTaskAction.isPending.value"
          @click="runBulkAction('resume')"
        >
          <template #icon><Play /></template>
          恢复
        </NButton>
        <NButton
          size="small"
          type="warning"
          :loading="bulkTaskAction.isPending.value"
          @click="runBulkAction('cancel')"
        >
          <template #icon><CircleX /></template>
          取消
        </NButton>
        <NButton
          size="small"
          type="error"
          secondary
          :loading="bulkDeleteTasks.isPending.value"
          @click="deleteSelectedHistory"
        >
          <template #icon><Trash2 /></template>
          删除
        </NButton>
      </div>
    </div>
    <NDataTable
      v-if="!tasks.isError.value"
      class="desktop-task-table"
      :columns="taskColumns"
      :data="tasks.data.value ?? []"
      :loading="tasks.isLoading.value"
      :bordered="false"
      :row-key="(row: StudyTask) => row.id"
      :checked-row-keys="selectedTaskIds"
      :scroll-x="860"
      :pagination="{ pageSize: 10 }"
      @update:checked-row-keys="selectedTaskIds = $event.map(String)"
    >
      <template #empty>
        <NEmpty size="small" description="暂无学习任务">
          <template #icon><Clock3 :size="24" /></template>
        </NEmpty>
      </template>
    </NDataTable>

    <div v-if="!tasks.isError.value" class="mobile-task-list">
      <NSpin v-if="tasks.isLoading.value" size="small" />
      <NEmpty v-else-if="(tasks.data.value?.length ?? 0) === 0" size="small" description="暂无学习任务" />
      <article v-for="task in tasks.data.value ?? []" v-else :key="task.id" class="mobile-task-item">
        <div class="mobile-task-heading">
          <NCheckbox
            class="mobile-task-checkbox"
            :checked="selectedTaskIds.includes(task.id)"
            :aria-label="`勾选任务 ${task.course_title}`"
            @update:checked="toggleTask(task.id, $event)"
          />
          <div>
            <strong>{{ task.course_title }}</strong>
            <span><UserRound :size="13" />{{ task.account_label }}</span>
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
        <div class="mobile-task-meta">
          <span>{{ task.chapter_succeeded }} / {{ task.chapter_total }} 已完成</span>
          <span v-if="task.chapter_needs_attention > 0">
            {{ task.chapter_needs_attention }} 待处理
          </span>
          <time :datetime="task.created_at">{{ formatDate(task.created_at) }}</time>
        </div>
        <div v-if="!isTerminalTask(task)" class="mobile-task-actions">
          <NTooltip v-if="canPauseTask(task)" trigger="hover">
            <template #trigger>
              <NButton
                quaternary
                circle
                size="small"
                aria-label="暂停任务"
                :loading="pendingTaskAction(task.id) === 'pause'"
                :disabled="pendingTaskAction(task.id) !== null"
                @click="executeTaskAction(task, 'pause')"
              >
                <template #icon><Pause /></template>
              </NButton>
            </template>
            暂停任务
          </NTooltip>
          <NTooltip v-if="canResumeTask(task)" trigger="hover">
            <template #trigger>
              <NButton
                quaternary
                circle
                size="small"
                aria-label="恢复任务"
                :loading="pendingTaskAction(task.id) === 'resume'"
                :disabled="pendingTaskAction(task.id) !== null"
                @click="executeTaskAction(task, 'resume')"
              >
                <template #icon><Play /></template>
              </NButton>
            </template>
            恢复任务
          </NTooltip>
          <NTooltip trigger="hover">
            <template #trigger>
              <NButton
                quaternary
                circle
                size="small"
                type="error"
                aria-label="取消任务"
                :loading="pendingTaskAction(task.id) === 'cancel'"
                :disabled="pendingTaskAction(task.id) !== null"
                @click="requestCancelTask(task)"
              >
                <template #icon><CircleX /></template>
              </NButton>
            </template>
            取消任务
          </NTooltip>
        </div>
      </article>
    </div>
  </section>
</template>

<style scoped>
.task-create-section {
  margin-top: 0;
}

.account-toolbar {
  display: grid;
  grid-template-columns: minmax(190px, 1fr) minmax(260px, 1.4fr) auto;
  align-items: center;
  gap: 18px;
  min-height: 78px;
  padding: 14px 20px;
  background: var(--color-surface-subtle);
}

.toolbar-label,
.pane-title {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 10px;
}

.toolbar-label > div > strong,
.toolbar-label > div > span,
.pane-title > div > strong,
.pane-title > div > span,
.create-task-bar > div > strong,
.create-task-bar > div > span {
  display: block;
}

.toolbar-label > div > strong,
.pane-title > div > strong {
  color: var(--color-text-strong);
  font-size: 13px;
}

.toolbar-label > div > span,
.pane-title > div > span,
.create-task-bar > div > span {
  margin-top: 2px;
  overflow: hidden;
  color: var(--color-text-muted);
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.step-index {
  display: grid;
  width: 25px;
  height: 25px;
  flex: 0 0 25px;
  place-items: center;
  border-radius: 50%;
  background: var(--color-accent-soft);
  color: var(--color-accent);
  font-size: 11px;
  font-weight: 750;
  line-height: 1;
}

.account-select {
  width: 100%;
}

.task-create-section > :deep(.n-alert),
.task-list-section > :deep(.n-alert) {
  border-radius: 0;
}

.task-builder-grid {
  display: grid;
  grid-template-columns: minmax(290px, 0.8fr) minmax(390px, 1.2fr);
  border-top: 1px solid var(--color-border-soft);
}

.builder-pane {
  min-width: 0;
}

.course-pane {
  border-right: 1px solid var(--color-border-soft);
}

.pane-heading {
  display: flex;
  min-height: 68px;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 14px 18px;
}

.course-pane-heading {
  flex-wrap: wrap;
}

.chapter-heading {
  border-bottom: 1px solid var(--color-border-soft);
}

.selection-count {
  flex: 0 0 auto;
  border-radius: 4px;
  background: var(--color-accent-muted);
  color: var(--color-accent);
  padding: 4px 7px;
  font-size: 11px;
  font-weight: 650;
}

.course-search {
  width: calc(100% - 36px);
  margin: 0 18px 12px;
}

.course-list {
  display: grid;
  max-height: 468px;
  min-height: 356px;
  align-content: start;
  overflow-y: auto;
  border-top: 1px solid var(--color-border-soft);
}

.course-list > :deep(.n-empty),
.course-list > :deep(.n-spin-container),
.chapter-content-area > :deep(.n-empty),
.chapter-content-area > :deep(.n-spin-container) {
  align-self: center;
  margin: auto;
}

.course-row {
  display: grid;
  grid-template-columns: 22px minmax(0, 1fr);
  align-items: center;
  gap: 10px;
  width: 100%;
  min-height: 74px;
  border-bottom: 1px solid var(--color-border-soft);
  background: var(--color-surface);
  color: inherit;
  padding-left: 18px;
}

.course-select {
  display: grid;
  place-items: center;
}

.course-open-button {
  display: grid;
  grid-template-columns: 34px minmax(0, 1fr) 18px;
  align-items: center;
  gap: 10px;
  min-width: 0;
  min-height: 73px;
  border: 0;
  background: transparent;
  color: inherit;
  padding: 10px 14px 10px 0;
  text-align: left;
  cursor: pointer;
}

.course-chevron {
  justify-self: center;
}

.course-row:hover {
  background: var(--color-surface-subtle);
}

.course-row.selected {
  background: var(--color-accent-row);
  box-shadow: inset 3px 0 var(--color-accent);
}

.course-icon {
  display: grid;
  width: 34px;
  height: 34px;
  place-items: center;
  border-radius: 6px;
  background: var(--color-neutral-row);
  color: var(--color-text-muted);
}

.course-row.selected .course-icon {
  background: var(--color-accent-chip);
  color: var(--color-accent);
}

.course-copy {
  min-width: 0;
}

.course-copy strong,
.course-copy span,
.course-copy small {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.course-copy strong {
  color: var(--color-text-strong);
  font-size: 13px;
}

.course-copy span {
  margin-top: 3px;
  color: var(--color-text-muted);
  font-size: 11px;
}

.course-copy small {
  margin-top: 2px;
  color: var(--color-text-disabled);
  font-size: 10px;
}

.course-chevron {
  color: var(--color-text-disabled);
}

.chapter-content-area {
  display: grid;
  min-height: 400px;
  align-content: start;
}

.locked-alert {
  margin: 12px 16px 0;
}

.chapter-toolbar {
  display: flex;
  min-height: 46px;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 8px 18px;
  border-bottom: 1px solid var(--color-border-soft);
}

.chapter-list {
  max-height: 354px;
  overflow-y: auto;
}

.chapter-row {
  padding: 12px 18px;
  border-bottom: 1px solid var(--color-border-soft);
}

.chapter-row.completed {
  background: var(--color-surface-muted);
}

.chapter-row :deep(.n-checkbox) {
  width: 100%;
  align-items: flex-start;
}

.chapter-row :deep(.n-checkbox__label) {
  min-width: 0;
  width: 100%;
  padding-left: 10px;
}

.chapter-copy strong {
  display: block;
  overflow-wrap: anywhere;
  color: var(--color-text-strong);
  font-size: 12px;
  line-height: 1.45;
}

.chapter-row.completed .chapter-copy strong {
  color: var(--color-text-muted);
}

.chapter-meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px 10px;
  margin-top: 6px;
  color: var(--color-text-faint);
  font-size: 10px;
}

.create-task-bar {
  display: flex;
  min-height: 70px;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  border-top: 1px solid var(--color-border);
  background: var(--color-surface-muted);
  padding: 12px 18px;
}

.create-task-bar > div > strong {
  color: var(--color-text-strong);
  font-size: 13px;
}

.task-list-section {
  margin-top: 20px;
}

.task-heading-actions,
.bulk-task-actions {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 6px;
}

.bulk-task-toolbar {
  display: flex;
  min-height: 50px;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  border-top: 1px solid var(--color-border-soft);
  border-bottom: 1px solid var(--color-border-soft);
  background: var(--color-surface-subtle);
  padding: 8px 18px;
}

.task-course-cell strong,
.task-course-cell span {
  display: block;
}

.task-course-cell strong {
  overflow: hidden;
  color: var(--color-text-strong);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.task-course-cell span {
  margin-top: 3px;
  color: var(--color-text-muted);
  font-size: 11px;
}

.task-progress-cell {
  display: grid;
  gap: 6px;
}

.task-progress-cell span {
  color: var(--color-text-muted);
  font-size: 10px;
}

.task-action-buttons,
.mobile-task-actions {
  display: flex;
  align-items: center;
  gap: 2px;
}

.task-no-actions {
  color: var(--color-text-disabled);
}

.mobile-task-list {
  display: none;
}

@media (max-width: 900px) {
  .account-toolbar {
    grid-template-columns: minmax(170px, 0.8fr) minmax(220px, 1.2fr) auto;
    gap: 12px;
  }

  .task-builder-grid {
    grid-template-columns: minmax(250px, 0.72fr) minmax(340px, 1.28fr);
  }
}

@media (max-width: 680px) {
  .task-heading {
    align-items: flex-start;
  }

  .account-toolbar {
    grid-template-columns: minmax(0, 1fr) auto;
    gap: 10px;
    padding: 14px;
  }

  .toolbar-label {
    grid-column: 1 / -1;
  }

  .account-select {
    min-width: 0;
  }

  .task-builder-grid {
    grid-template-columns: 1fr;
  }

  .course-pane {
    border-right: 0;
    border-bottom: 1px solid var(--color-border);
  }

  .pane-heading {
    min-height: 62px;
    padding: 12px 14px;
  }

  .course-search {
    width: calc(100% - 28px);
    margin: 0 14px 10px;
  }

  .course-list {
    max-height: 286px;
    min-height: 230px;
  }

  .course-row {
    min-height: 68px;
    padding-left: 14px;
  }

  .course-open-button {
    min-height: 67px;
  }

  .chapter-content-area {
    min-height: 320px;
  }

  .chapter-list {
    max-height: 350px;
  }

  .chapter-row,
  .chapter-toolbar {
    padding-right: 14px;
    padding-left: 14px;
  }

  .locked-alert {
    margin-right: 12px;
    margin-left: 12px;
  }

  .create-task-bar {
    padding: 12px 14px;
  }

  .desktop-task-table {
    display: none;
  }

  .mobile-task-list {
    display: grid;
    min-height: 120px;
  }

  .mobile-task-list > :deep(.n-empty),
  .mobile-task-list > :deep(.n-spin-container) {
    margin: 30px auto;
  }

  .mobile-task-item {
    display: grid;
    gap: 12px;
    padding: 15px 14px;
    border-bottom: 1px solid var(--color-border-soft);
  }

  .mobile-task-item:last-child {
    border-bottom: 0;
  }

  .mobile-task-heading {
    display: flex;
    min-width: 0;
    align-items: flex-start;
    justify-content: space-between;
    gap: 12px;
  }

  .mobile-task-checkbox {
    flex: 0 0 auto;
    margin-top: 1px;
  }

  .mobile-task-heading > .mobile-task-checkbox + div {
    flex: 1 1 auto;
  }

  .mobile-task-heading > div {
    min-width: 0;
  }

  .mobile-task-heading strong,
  .mobile-task-heading span {
    display: flex;
    min-width: 0;
  }

  .mobile-task-heading strong {
    overflow: hidden;
    color: var(--color-text-strong);
    font-size: 13px;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .mobile-task-heading span {
    align-items: center;
    gap: 5px;
    margin-top: 4px;
    color: var(--color-text-muted);
    font-size: 11px;
  }

  .mobile-task-meta {
    display: flex;
    flex-wrap: wrap;
    gap: 6px 12px;
    color: var(--color-text-muted);
    font-size: 10px;
  }

  .mobile-task-meta time {
    margin-left: auto;
  }

  .mobile-task-actions {
    justify-content: flex-end;
    min-height: 28px;
    margin-top: -4px;
  }
}

@media (max-width: 420px) {
  .account-toolbar {
    grid-template-columns: 1fr;
  }

  .toolbar-label {
    grid-column: 1;
  }

  .account-toolbar > :deep(.n-button) {
    width: 100%;
  }

  .chapter-heading {
    align-items: flex-start;
  }

  .selection-count {
    margin-top: 2px;
  }

  .create-task-bar {
    align-items: stretch;
    flex-direction: column;
    gap: 10px;
  }

  .create-task-bar > :deep(.n-button) {
    width: 100%;
  }

  .task-heading-actions {
    width: 100%;
    justify-content: flex-end;
  }

  .bulk-task-toolbar {
    align-items: stretch;
    flex-direction: column;
    padding: 10px 14px;
  }

  .bulk-task-actions {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
