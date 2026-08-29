<script setup lang="ts">
import { computed, ref } from 'vue'
import { onHide, onPullDownRefresh, onShow } from '@dcloudio/uni-app'
import { api } from '@/api/client'
import type { Account, Course, CourseChapter, StudyTask } from '@/api/types'
import { useAuthStore } from '@/stores/auth'
import { TASK_STATUS_LABELS } from '@/utils/format'
import NeedLogin from '@/components/NeedLogin.vue'

const auth = useAuthStore()

const accounts = ref<Account[]>([])
const selectedAccountId = ref<number | null>(null)
const courses = ref<Course[]>([])
const discovering = ref(false)

const selectedCourse = ref<Course | null>(null)
const outline = ref<CourseChapter[] | null>(null)
const loadingChapters = ref(false)
const checkedChapters = ref<Set<string>>(new Set())

const tasks = ref<StudyTask[]>([])
const taskQuota = computed(() => {
  const quota = auth.profile?.quotas?.max_active_tasks
  return typeof quota === 'number' ? quota : 1
})
type StatusFilter = 'all' | 'active' | 'done' | 'attention'
const statusFilter = ref<StatusFilter>('all')
const STATUS_FILTERS: { key: StatusFilter; label: string }[] = [
  { key: 'all', label: '全部' },
  { key: 'active', label: '进行中' },
  { key: 'done', label: '已完成' },
  { key: 'attention', label: '需处理' },
]
const filteredTasks = computed(() => {
  if (statusFilter.value === 'all') return tasks.value
  if (statusFilter.value === 'active') {
    return tasks.value.filter((task) =>
      ['queued', 'running', 'pause_requested', 'paused', 'cancel_requested', 'recovering'].includes(
        task.status,
      ),
    )
  }
  if (statusFilter.value === 'done') return tasks.value.filter((task) => task.status === 'succeeded')
  return tasks.value.filter((task) =>
    ['needs_attention', 'failed'].includes(task.status),
  )
})

const activeTaskCount = computed(
  () =>
    tasks.value.filter((task) =>
      ['queued', 'running', 'pause_requested', 'paused', 'cancel_requested', 'recovering'].includes(
        task.status,
      ),
    ).length,
)
let taskTimer: ReturnType<typeof setInterval> | null = null

const selectedAccount = computed(
  () => accounts.value.find((account) => account.id === selectedAccountId.value) ?? null,
)

async function refreshAccounts() {
  accounts.value = await api.listAccounts()
  if (!accounts.value.length) selectedAccountId.value = null
  else if (selectedAccountId.value === null) selectedAccountId.value = accounts.value[0].id
}

async function refreshTasks() {
  if (!auth.isLoggedIn) return
  tasks.value = await api.listTasks({ limit: 200 })
}

function onAccountPick(index: number) {
  selectedAccountId.value = accounts.value[index]?.id ?? null
  onAccountChange()
}

async function onAccountChange() {
  courses.value = []
  selectedCourse.value = null
  outline.value = null
  checkedChapters.value = new Set()
}

async function discover() {
  if (selectedAccountId.value === null) {
    uni.showToast({ title: '请先选择账号', icon: 'none' })
    return
  }
  discovering.value = true
  try {
    courses.value = await api.discoverCourses(selectedAccountId.value)
    selectedCourse.value = null
    outline.value = null
    checkedChapters.value = new Set()
    if (!courses.value.length) uni.showToast({ title: '没有拉取到课程', icon: 'none' })
  } catch (error) {
    const message = error instanceof Error ? error.message : '拉取失败'
    uni.showToast({ title: message, icon: 'none' })
  } finally {
    discovering.value = false
  }
}

async function pickCourse(course: Course) {
  if (selectedAccountId.value === null) return
  selectedCourse.value = course
  outline.value = null
  checkedChapters.value = new Set()
  loadingChapters.value = true
  try {
    const result = await api.getCourseChapters(selectedAccountId.value, course)
    outline.value = result.chapters
  } catch (error) {
    uni.showToast({ title: error instanceof Error ? error.message : '拉取章节失败', icon: 'none' })
  } finally {
    loadingChapters.value = false
  }
}

function toggleChapter(chapterId: string) {
  const next = new Set(checkedChapters.value)
  if (next.has(chapterId)) next.delete(chapterId)
  else next.add(chapterId)
  checkedChapters.value = next
}

function toggleAll() {
  if (!outline.value) return
  const all = outline.value.map((chapter) => chapter.chapter_id)
  checkedChapters.value =
    checkedChapters.value.size === all.length ? new Set() : new Set(all)
}

async function createTask() {
  const account = selectedAccount.value
  const course = selectedCourse.value
  if (!account || !course) return
  if (activeTaskCount.value >= taskQuota.value) {
    uni.showToast({ title: `运行中任务已达上限（${taskQuota.value} 个）`, icon: 'none' })
    return
  }
  if (!outline.value || checkedChapters.value.size === 0) {
    uni.showToast({ title: '请先勾选要学习的章节', icon: 'none' })
    return
  }
  const chapters = outline.value
    .filter((chapter) => checkedChapters.value.has(chapter.chapter_id))
    .map((chapter) => ({
      chapter_id: chapter.chapter_id,
      title: chapter.title,
      position: chapter.position,
    }))
  try {
    await api.createTask({
      account_id: account.id,
      course_id: course.course_id,
      class_id: course.class_id,
      cpi: course.cpi,
      course_title: course.title,
      chapters,
    })
    uni.showToast({ title: '任务已创建', icon: 'success' })
    selectedCourse.value = null
    outline.value = null
    checkedChapters.value = new Set()
    refreshTasks()
  } catch (error) {
    uni.showToast({ title: error instanceof Error ? error.message : '创建失败', icon: 'none' })
  }
}

async function taskAction(task: StudyTask, action: 'pause' | 'resume' | 'cancel') {
  try {
    await api.taskAction(task.id, action)
    refreshTasks()
  } catch (error) {
    uni.showToast({ title: error instanceof Error ? error.message : '操作失败', icon: 'none' })
  }
}

async function deleteTask(task: StudyTask) {
  const confirm = await uni.showModal({
    title: '删除任务',
    content: `确定删除「${task.course_title}」的任务记录？`,
  })
  if (!confirm.confirm) return
  try {
    await api.deleteTask(task.id)
    refreshTasks()
  } catch (error) {
    uni.showToast({ title: error instanceof Error ? error.message : '删除失败', icon: 'none' })
  }
}

function taskButtons(task: StudyTask) {
  if (['queued', 'paused'].includes(task.status)) {
    return [
      { label: '取消', action: 'cancel' as const, danger: true },
      { label: task.status === 'paused' ? '继续' : '暂停', action: task.status === 'paused' ? ('resume' as const) : ('pause' as const) },
    ]
  }
  if (['running', 'recovering'].includes(task.status)) {
    return [
      { label: '取消', action: 'cancel' as const, danger: true },
      { label: '暂停', action: 'pause' as const },
    ]
  }
  return []
}

function startTaskPolling() {
  if (taskTimer) clearInterval(taskTimer)
  refreshTasks()
  taskTimer = setInterval(refreshTasks, 10_000)
}

function goAddAccount() {
  uni.switchTab({ url: '/pages/accounts/accounts' })
}

function openTaskDetail(taskId: string) {
  uni.navigateTo({ url: `/pages/task-detail/task-detail?taskId=${taskId}` })
}

onPullDownRefresh(async () => {
  await refreshAccounts()
  await refreshTasks()
  uni.stopPullDownRefresh()
})

onShow(async () => {
  if (!auth.isLoggedIn) return
  try {
    await refreshAccounts()
    startTaskPolling()
  } catch {
    // 会话失效时静默，页面会回到登录门
  }
})
// tab 页不会 unmount，切走时停轮询
onHide(() => {
  if (taskTimer) {
    clearInterval(taskTimer)
    taskTimer = null
  }
})
</script>

<template>
  <view>
    <NeedLogin v-if="!auth.isLoggedIn" />
    <template v-else>
      <!-- 新建任务 -->
      <view class="section-title">新建学习任务</view>
      <view class="card">
        <picker :range="accounts" range-key="remark" @change="(e: any) => onAccountPick(Number(e.detail.value))">
          <view class="row picker-row">
            <text class="text-muted">账号</text>
            <text>{{ selectedAccount?.remark || selectedAccount?.username_hint || '请选择账号' }}</text>
          </view>
        </picker>
        <button class="btn-plain discover-btn" size="mini" :loading="discovering" :disabled="selectedAccountId === null" @click="discover">
          拉取课程
        </button>

        <template v-if="courses.length">
          <view class="divider" />
          <view class="section-label">选择课程</view>
          <view
            v-for="course in courses"
            :key="`${course.course_id}-${course.class_id}`"
            class="course-item"
            :class="{ selected: selectedCourse?.course_id === course.course_id && selectedCourse?.class_id === course.class_id }"
            @click="pickCourse(course)"
          >
            <view class="course-title">{{ course.title }}</view>
            <view class="text-muted">{{ course.teacher || '—' }}</view>
          </view>
        </template>

        <template v-if="outline">
          <view class="divider" />
          <view class="row section-label">
            <text>勾选章节</text>
            <text class="text-muted" @click="toggleAll">
              {{ checkedChapters.size === outline.length ? '全不选' : '全选' }}（{{ checkedChapters.size }}/{{ outline.length }}）
            </text>
          </view>
          <view
            v-for="chapter in outline"
            :key="chapter.chapter_id"
            class="chapter-item"
            @click="!chapter.is_completed && toggleChapter(chapter.chapter_id)"
          >
            <view class="checkbox" :class="{ on: checkedChapters.has(chapter.chapter_id), done: chapter.is_completed }">
              {{ chapter.is_completed ? '✓' : checkedChapters.has(chapter.chapter_id) ? '✓' : '' }}
            </view>
            <view class="chapter-text">
              <view :class="{ 'text-muted': chapter.is_completed }">
                {{ chapter.title }}
                <text v-if="chapter.is_completed" class="badge succeeded">已完成</text>
                <text v-if="chapter.requires_unlock" class="badge paused">需解锁</text>
              </view>
              <view class="text-muted">任务点 {{ chapter.job_count }}</view>
            </view>
          </view>
          <button class="btn-primary create-btn" @click="createTask">
            创建任务（勾选 {{ checkedChapters.size }} 章）
          </button>
        </template>
        <view v-if="loadingChapters" class="empty">章节加载中…</view>
      </view>

      <!-- 任务列表 -->
      <view class="section-title">任务列表（活跃 {{ activeTaskCount }}/{{ taskQuota }}）</view>
      <view class="filter-chips">
        <text
          v-for="filter in STATUS_FILTERS"
          :key="filter.key"
          class="chip"
          :class="{ on: statusFilter === filter.key }"
          @click="statusFilter = filter.key"
        >
          {{ filter.label }}
        </text>
      </view>
      <view class="card task" v-for="task in filteredTasks" :key="task.id">
        <view class="row" @click="openTaskDetail(task.id)">
          <view class="task-info">
            <view class="task-title">{{ task.course_title }}</view>
            <view class="text-muted">
              {{ task.account_label }} · 章节 {{ task.chapter_succeeded }}/{{ task.chapter_total }}
            </view>
            <view v-if="task.last_error" class="text-danger task-error">{{ task.last_error }}</view>
          </view>
          <text class="badge" :class="task.status">{{ TASK_STATUS_LABELS[task.status] }}</text>
        </view>
        <view class="task-actions">
          <button
            v-for="button in taskButtons(task)"
            :key="button.label"
            size="mini"
            :class="button.danger ? 'mini-btn danger' : 'mini-btn'"
            @click="taskAction(task, button.action)"
          >
            {{ button.label }}
          </button>
          <button
            v-if="['succeeded', 'failed', 'needs_attention', 'canceled'].includes(task.status)"
            size="mini"
            class="mini-btn danger"
            @click="deleteTask(task)"
          >
            删除
          </button>
        </view>
      </view>
      <view v-if="!tasks.length" class="empty">
        <view>暂无任务</view>
        <button v-if="!accounts.length" class="btn-plain empty-action" size="mini" @click="goAddAccount">
          先去添加学习账号 →
        </button>
        <view v-else class="text-muted">在上方选择账号并拉取课程，创建第一个学习任务</view>
      </view>
    </template>
  </view>
</template>

<style lang="scss" scoped>
.picker-row {
  padding: 8rpx 0 24rpx;
}
.discover-btn {
  margin: 0;
}
.divider {
  height: 1rpx;
  background: #f0f1f3;
  margin: 24rpx 0;
}
.section-label {
  font-size: 28rpx;
  font-weight: 500;
  margin-bottom: 12rpx;
}
.course-item {
  padding: 16rpx;
  border-radius: 12rpx;
  margin-bottom: 12rpx;
  background: var(--bg-soft);

  &.selected {
    background: var(--badge-blue-bg);
  }
}
.course-title {
  font-size: 28rpx;
  margin-bottom: 4rpx;
}
.chapter-item {
  display: flex;
  align-items: flex-start;
  padding: 12rpx 0;
  border-bottom: 1rpx solid var(--border);
}
.checkbox {
  width: 36rpx;
  height: 36rpx;
  border: 2rpx solid var(--gray-3);
  border-radius: 8rpx;
  margin-right: 16rpx;
  text-align: center;
  line-height: 36rpx;
  font-size: 24rpx;
  color: #ffffff;
  flex-shrink: 0;

  &.on {
    background: var(--primary);
    border-color: var(--primary);
  }
  &.done {
    background: var(--gray-3);
    border-color: var(--gray-3);
  }
}
.chapter-text {
  flex: 1;
}
.create-btn {
  margin-top: 24rpx;
}
.task {
  margin-top: 0;
}
.task-info {
  flex: 1;
  margin-right: 16rpx;
}
.task-title {
  font-size: 28rpx;
  font-weight: 500;
  margin-bottom: 6rpx;
}
.task-error {
  margin-top: 6rpx;
  font-size: 22rpx;
}
.task-actions {
  display: flex;
  justify-content: flex-end;
  gap: 12rpx;
  margin-top: 16rpx;
}
.mini-btn {
  margin: 0;
  font-size: 22rpx;

  &.danger {
    color: var(--danger);
  }
}
.empty-action {
  margin-top: 20rpx;
}
.filter-chips {
  display: flex;
  gap: 16rpx;
  padding: 0 24rpx 8rpx;

  .chip {
    padding: 8rpx 28rpx;
    border-radius: 28rpx;
    background: var(--bg-card);
    color: var(--text-muted);
    font-size: 24rpx;

    &.on {
      background: var(--primary);
      color: #ffffff;
    }
  }
}
</style>
