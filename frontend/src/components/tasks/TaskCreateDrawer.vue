<script setup lang="ts">
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import {
  BookOpenCheck,
  CheckSquare2,
  ChevronRight,
  GraduationCap,
  ListChecks,
  LockKeyhole,
  Play,
  RefreshCw,
  Search,
  Ticket,
  Users,
} from 'lucide-vue-next'
import {
  NAlert,
  NButton,
  NCheckbox,
  NDrawer,
  NDrawerContent,
  NInput,
  NSelect,
  NSkeleton,
  NTag,
  useMessage,
} from 'naive-ui'
import type { SelectOption } from 'naive-ui'
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import { ApiError, apiRequest } from '@/api/client'
import type {
  Account,
  BulkTaskCreateItemResult,
  BulkTaskCreateResult,
  Course,
  CourseOutline,
  CreateTaskInput,
  EntitlementResponse,
  StudyTaskDetail,
} from '@/api/types'
import EmptyState from '@/components/ui/EmptyState.vue'
import { useAuthStore } from '@/stores/auth'

const props = defineProps<{
  show: boolean
  initialAccountId?: number | null
}>()

const emit = defineEmits<{
  'update:show': [value: boolean]
}>()

const BULK_DISCOVERY_CONCURRENCY = 4
const BULK_CREATE_BATCH_SIZE = 100

const message = useMessage()
const queryClient = useQueryClient()
const router = useRouter()
const auth = useAuthStore()

const selectedAccountId = ref<number | null>(null)
const coursesByAccount = ref(new Map<number, Course[]>())
const courseSearch = ref('')
const selectedCourse = ref<Course | null>(null)
const selectedCourseKeys = ref<string[]>([])
const outline = ref<CourseOutline | null>(null)
const selectedChapterIds = ref<string[]>([])

const accounts = useQuery({
  queryKey: ['accounts'],
  queryFn: () => apiRequest<Account[]>('/accounts'),
})

const entitlement = useQuery({
  queryKey: ['entitlement'],
  queryFn: () => apiRequest<EntitlementResponse>('/cards/me'),
  enabled: computed(() => auth.isAppUser && props.show),
})

const lacksEntitlement = computed(
  () => auth.isAppUser && entitlement.data.value !== undefined && !entitlement.data.value.active,
)

const enabledAccounts = computed(() =>
  (accounts.data.value ?? []).filter((account) => account.enabled),
)

const accountOptions = computed<SelectOption[]>(() =>
  (accounts.data.value ?? []).map((account) => ({
    label: account.remark ? `${account.remark} · ${account.username_hint}` : account.username_hint,
    value: account.id,
    disabled: !account.enabled,
  })),
)

const courses = computed(() =>
  selectedAccountId.value === null ? [] : (coursesByAccount.value.get(selectedAccountId.value) ?? []),
)
const coursesLoaded = computed(
  () => selectedAccountId.value !== null && coursesByAccount.value.has(selectedAccountId.value),
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
const incompleteCount = computed(
  () => outline.value?.chapters.filter((chapter) => !chapter.is_completed).length ?? 0,
)
const allChaptersSelected = computed(
  () => chapterCount.value > 0 && selectedChapterIds.value.length === chapterCount.value,
)
const someChaptersSelected = computed(
  () => selectedChapterIds.value.length > 0 && !allChaptersSelected.value,
)
const canCreateTask = computed(
  () =>
    !lacksEntitlement.value &&
    selectedAccountId.value !== null &&
    selectedCourse.value !== null &&
    selectedChapterIds.value.length > 0,
)

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback
}

function courseKey(course: Course | null): string {
  return course ? `${course.course_id}:${course.class_id}` : ''
}

function discoverOutline(accountId: number, course: Course) {
  return apiRequest<CourseOutline>(
    `/accounts/${accountId}/courses/${encodeURIComponent(course.course_id)}/chapters`,
    {
      method: 'POST',
      body: JSON.stringify({ class_id: course.class_id, cpi: course.cpi, title: course.title }),
    },
  )
}

const discoverChapters = useMutation({
  mutationFn: ({ accountId, course }: { accountId: number; course: Course }) =>
    discoverOutline(accountId, course),
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
    const next = new Map(coursesByAccount.value)
    next.set(accountId, data)
    coursesByAccount.value = next
    if (selectedAccountId.value !== accountId) return
    const firstCourse = data[0]
    if (firstCourse && !selectedCourse.value) selectCourse(firstCourse)
  },
  onError(error, accountId) {
    if (selectedAccountId.value !== accountId) return
    message.error(errorMessage(error, '课程加载失败，请检查账号凭据后重试'))
  },
})

const createTask = useMutation({
  mutationFn: (payload: CreateTaskInput) =>
    apiRequest<StudyTaskDetail>('/tasks', { method: 'POST', body: JSON.stringify(payload) }),
  async onSuccess(task) {
    selectedChapterIds.value = []
    await queryClient.invalidateQueries({ queryKey: ['tasks'] })
    if (auth.isAppUser) void queryClient.invalidateQueries({ queryKey: ['entitlement'] })
    message.success(`「${task.course_title}」已加入队列`)
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
            const discovered = await discoverOutline(accountId, course)
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
    if (auth.isAppUser) void queryClient.invalidateQueries({ queryKey: ['entitlement'] })
    const skippedParts = [
      alreadyComplete ? `${alreadyComplete} 门已全部完成` : '',
      discoveryFailed ? `${discoveryFailed} 门读取失败` : '',
      result.failed ? `${result.failed} 门创建失败` : '',
    ].filter(Boolean)
    if (skippedParts.length) {
      message.warning(`已创建 ${result.created} 个任务；${skippedParts.join('，')}`)
    } else {
      message.success(`已创建 ${result.created} 个课程任务`)
    }
  },
  onError(error) {
    message.error(errorMessage(error, '批量任务创建失败'))
  },
})

function resetCourseSelection(): void {
  courseSearch.value = ''
  selectedCourse.value = null
  outline.value = null
  selectedChapterIds.value = []
  selectedCourseKeys.value = []
}

function ensureCourses(): void {
  const accountId = selectedAccountId.value
  if (accountId === null || coursesByAccount.value.has(accountId) || discoverCourses.isPending.value) {
    return
  }
  discoverCourses.mutate(accountId)
}

function refreshCourses(): void {
  const accountId = selectedAccountId.value
  if (accountId === null) return
  resetCourseSelection()
  const next = new Map(coursesByAccount.value)
  next.delete(accountId)
  coursesByAccount.value = next
  discoverCourses.mutate(accountId)
}

function pickInitialAccount(): void {
  const available = accounts.data.value
  if (!available) return
  const preferred = props.initialAccountId
  if (preferred && available.some((account) => account.id === preferred && account.enabled)) {
    selectedAccountId.value = preferred
    return
  }
  const currentIsEnabled = available.some(
    (account) => account.id === selectedAccountId.value && account.enabled,
  )
  if (!currentIsEnabled) {
    selectedAccountId.value = available.find((account) => account.enabled)?.id ?? null
  }
}

watch(() => accounts.data.value, pickInitialAccount, { immediate: true })
watch(
  () => [props.show, props.initialAccountId] as const,
  ([show]) => {
    if (!show) return
    pickInitialAccount()
    ensureCourses()
  },
)
watch(selectedAccountId, () => {
  resetCourseSelection()
  if (props.show) ensureCourses()
})

function selectCourse(course: Course): void {
  const accountId = selectedAccountId.value
  if (accountId === null) return
  selectedCourse.value = course
  outline.value = null
  selectedChapterIds.value = []
  discoverChapters.mutate({ accountId, course })
}

function toggleCourse(course: Course, checked: boolean): void {
  const next = new Set(selectedCourseKeys.value)
  const key = courseKey(course)
  if (checked) next.add(key)
  else next.delete(key)
  selectedCourseKeys.value = [...next]
}

function submitBulkCourses(): void {
  const selected = selectedCourseSet.value
  const coursesToCreate = courses.value.filter((course) => selected.has(courseKey(course)))
  if (coursesToCreate.length === 0) {
    message.warning('至少勾选一门课程')
    return
  }
  bulkCreateTasks.mutate(coursesToCreate)
}

function toggleChapter(chapterId: string, checked: boolean): void {
  const next = new Set(selectedChapterIds.value)
  if (checked) next.add(chapterId)
  else next.delete(chapterId)
  selectedChapterIds.value = [...next]
}

function toggleAllChapters(checked: boolean): void {
  selectedChapterIds.value = checked
    ? (outline.value?.chapters.map((chapter) => chapter.chapter_id) ?? [])
    : []
}

function selectIncompleteChapters(): void {
  selectedChapterIds.value =
    outline.value?.chapters
      .filter((chapter) => !chapter.is_completed)
      .map((chapter) => chapter.chapter_id) ?? []
}

function submitTask(): void {
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

function goTo(name: 'accounts' | 'portal'): void {
  emit('update:show', false)
  void router.push(name === 'accounts' ? { name, query: { create: '1' } } : { name })
}
</script>

<template>
  <NDrawer
    :show="show"
    :width="980"
    placement="right"
    class="task-create-drawer"
    :auto-focus="false"
    @update:show="emit('update:show', $event)"
  >
    <NDrawerContent title="新建学习任务" closable :native-scrollbar="false">
      <NAlert v-if="accounts.isError.value" type="error" :bordered="false" class="drawer-alert">
        账号列表加载失败，请刷新页面后重试。
      </NAlert>

      <EmptyState
        v-else-if="accounts.isSuccess.value && enabledAccounts.length === 0"
        :icon="Users"
        title="还没有可用的学习通账号"
        description="创建任务前，请先添加并启用至少一个学习通账号。"
      >
        <NButton type="primary" @click="goTo('accounts')">去添加账号</NButton>
      </EmptyState>

      <template v-else>
        <NAlert
          v-if="lacksEntitlement"
          type="warning"
          :bordered="false"
          class="drawer-alert"
          title="尚无可用权益"
        >
          <div class="alert-with-action">
            <span>创建学习任务需要有效的时间卡或剩余任务次数。</span>
            <NButton size="small" @click="goTo('portal')">
              <template #icon><Ticket :size="14" /></template>
              去兑换卡密
            </NButton>
          </div>
        </NAlert>

        <div class="account-toolbar">
          <div class="toolbar-label">
            <span class="step-index">1</span>
            <strong>学习账号</strong>
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
            :loading="discoverCourses.isPending.value"
            :disabled="selectedAccountId === null"
            @click="refreshCourses"
          >
            <template #icon><RefreshCw /></template>
            重新读取课程
          </NButton>
        </div>

        <div class="task-builder-grid">
          <section class="builder-pane course-pane" aria-labelledby="course-pane-title">
            <div class="pane-heading">
              <div class="pane-title">
                <span class="step-index">2</span>
                <div>
                  <strong id="course-pane-title">选择课程</strong>
                  <span>{{ coursesLoaded ? `共 ${courses.length} 门 · 点击查看章节，勾选可批量创建` : '正在读取账号课程' }}</span>
                </div>
              </div>
              <NButton
                size="small"
                type="primary"
                secondary
                :loading="bulkCreateTasks.isPending.value"
                :disabled="selectedCourseKeys.length === 0 || lacksEntitlement"
                @click="submitBulkCourses"
              >
                <template #icon><CheckSquare2 /></template>
                批量创建{{ selectedCourseKeys.length ? ` ${selectedCourseKeys.length}` : '' }}
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
              <div v-if="discoverCourses.isPending.value" class="pane-skeleton" aria-label="正在读取课程">
                <div v-for="index in 4" :key="index" class="skeleton-row">
                  <NSkeleton :width="34" :height="34" :sharp="false" />
                  <div><NSkeleton text style="width: 70%" /><NSkeleton text style="width: 40%" /></div>
                </div>
              </div>
              <EmptyState
                v-else-if="!coursesLoaded"
                compact
                :icon="BookOpenCheck"
                title="尚未读取课程"
                description="选择账号后会自动读取课程列表"
              >
                <NButton size="small" :disabled="selectedAccountId === null" @click="ensureCourses">读取课程</NButton>
              </EmptyState>
              <EmptyState
                v-else-if="filteredCourses.length === 0"
                compact
                :icon="BookOpenCheck"
                :title="courses.length === 0 ? '该账号暂无课程' : '没有匹配的课程'"
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
                  <span>{{ selectedCourse ? selectedCourse.title : '选择课程后读取章节' }}</span>
                </div>
              </div>
              <span v-if="outline" class="selection-count">
                已选 {{ selectedChapterIds.length }} / {{ chapterCount }}
              </span>
            </div>

            <div class="chapter-content-area">
              <div v-if="discoverChapters.isPending.value" class="pane-skeleton" aria-label="正在读取章节">
                <NSkeleton text :repeat="6" />
              </div>
              <EmptyState
                v-else-if="!selectedCourse"
                compact
                :icon="ListChecks"
                title="请先选择一门课程"
                description="从左侧列表点击课程即可查看章节"
              />
              <EmptyState
                v-else-if="outline && outline.chapters.length === 0"
                compact
                :icon="ListChecks"
                title="该课程暂无章节"
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
                    仅选未完成（{{ incompleteCount }}）
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
                          <NTag v-if="chapter.is_completed" size="small" type="success" :bordered="false">
                            已完成
                          </NTag>
                          <NTag v-if="chapter.requires_unlock" size="small" type="warning" :bordered="false">
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
          </section>
        </div>
      </template>

      <template #footer>
        <div class="create-task-bar">
          <div>
            <strong>{{ selectedChapterIds.length }} 个章节</strong>
            <span>创建后进入账号互斥队列，同一账号的任务依次执行</span>
          </div>
          <NButton @click="emit('update:show', false)">完成</NButton>
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
      </template>
    </NDrawerContent>
  </NDrawer>
</template>

<style scoped>
.drawer-alert {
  margin-bottom: 14px;
}

.alert-with-action {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.account-toolbar {
  display: grid;
  grid-template-columns: auto minmax(220px, 1fr) auto;
  align-items: center;
  gap: 14px;
  margin-bottom: 14px;
}

.toolbar-label,
.pane-title {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 10px;
}

.toolbar-label strong,
.pane-title strong {
  color: var(--color-text-strong);
  font-size: var(--fs-sm);
}

.pane-title > div > strong,
.pane-title > div > span {
  display: block;
}

.pane-title > div > span {
  overflow: hidden;
  margin-top: 1px;
  color: var(--color-text-muted);
  font-size: var(--fs-xs);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pane-title > div {
  min-width: 0;
}

.step-index {
  display: grid;
  width: 24px;
  height: 24px;
  flex: 0 0 24px;
  place-items: center;
  border-radius: 50%;
  background: var(--color-accent-soft);
  color: var(--color-accent);
  font-size: var(--fs-xs);
  font-weight: 700;
  line-height: 1;
}

.account-select {
  min-width: 0;
}

.task-builder-grid {
  display: grid;
  grid-template-columns: minmax(280px, 0.85fr) minmax(360px, 1.15fr);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  overflow: hidden;
}

.builder-pane {
  display: flex;
  min-width: 0;
  flex-direction: column;
}

.course-pane {
  border-right: 1px solid var(--color-border-soft);
}

.pane-heading {
  display: flex;
  min-height: 60px;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 16px;
}

.chapter-heading {
  border-bottom: 1px solid var(--color-border-soft);
}

.selection-count {
  flex: 0 0 auto;
  border-radius: var(--radius-sm);
  background: var(--color-accent-muted);
  color: var(--color-accent);
  padding: 3px 8px;
  font-size: var(--fs-xs);
  font-weight: 600;
}

.course-search {
  width: calc(100% - 32px);
  margin: 0 16px 12px;
}

.course-list,
.chapter-content-area {
  display: grid;
  min-height: 420px;
  max-height: calc(100vh - 320px);
  align-content: start;
  overflow-y: auto;
}

.course-list {
  border-top: 1px solid var(--color-border-soft);
}

.course-list > .empty-state,
.chapter-content-area > .empty-state {
  min-height: 360px;
}

.pane-skeleton {
  display: grid;
  gap: 14px;
  padding: 16px;
}

.skeleton-row {
  display: grid;
  grid-template-columns: 34px minmax(0, 1fr);
  gap: 12px;
}

.skeleton-row > div {
  display: grid;
  gap: 6px;
}

.course-row {
  display: grid;
  grid-template-columns: 22px minmax(0, 1fr);
  align-items: center;
  gap: 10px;
  min-height: 68px;
  border-bottom: 1px solid var(--color-border-soft);
  background: var(--color-surface);
  padding-left: 16px;
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
  min-height: 67px;
  border: 0;
  background: transparent;
  color: inherit;
  cursor: pointer;
  padding: 10px 12px 10px 0;
  text-align: left;
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
  border-radius: var(--radius-sm);
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
  font-size: var(--fs-sm);
}

.course-copy span,
.course-copy small {
  margin-top: 2px;
  color: var(--color-text-muted);
  font-size: var(--fs-xs);
}

.course-copy small {
  color: var(--color-text-faint);
}

.course-chevron {
  justify-self: center;
  color: var(--color-text-disabled);
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
  padding: 8px 16px;
  border-bottom: 1px solid var(--color-border-soft);
}

.chapter-row {
  padding: 12px 16px;
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
  font-size: var(--fs-sm);
  font-weight: 500;
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
  margin-top: 5px;
  color: var(--color-text-faint);
  font-size: var(--fs-xs);
}

.create-task-bar {
  display: flex;
  width: 100%;
  align-items: center;
  gap: 10px;
}

.create-task-bar > div {
  min-width: 0;
  margin-right: auto;
}

.create-task-bar > div > strong,
.create-task-bar > div > span {
  display: block;
}

.create-task-bar > div > strong {
  color: var(--color-text-strong);
  font-size: var(--fs-sm);
}

.create-task-bar > div > span {
  overflow: hidden;
  color: var(--color-text-muted);
  font-size: var(--fs-xs);
  text-overflow: ellipsis;
  white-space: nowrap;
}

@media (max-width: 760px) {
  .account-toolbar {
    grid-template-columns: minmax(0, 1fr);
    gap: 10px;
  }

  .task-builder-grid {
    grid-template-columns: minmax(0, 1fr);
  }

  .course-pane {
    border-right: 0;
    border-bottom: 1px solid var(--color-border);
  }

  .course-list,
  .chapter-content-area {
    min-height: 260px;
    max-height: 380px;
  }

  .course-list > .empty-state,
  .chapter-content-area > .empty-state {
    min-height: 220px;
  }

  .create-task-bar > div > span {
    display: none;
  }
}
</style>

<style>
.task-create-drawer {
  max-width: 100vw;
}
</style>
