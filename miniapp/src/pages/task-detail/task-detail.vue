<script setup lang="ts">
import { computed, onUnmounted, ref } from 'vue'
import { onHide, onLoad, onShow } from '@dcloudio/uni-app'
import { api } from '@/api/client'
import type { StudyTaskDetail, TaskChapter } from '@/api/types'
import { useAuthStore } from '@/stores/auth'
import { CHAPTER_STATUS_LABELS, TASK_STATUS_LABELS, formatDuration, formatTime } from '@/utils/format'
import NeedLogin from '@/components/NeedLogin.vue'

const auth = useAuthStore()

const taskId = ref('')
const task = ref<StudyTaskDetail | null>(null)
const loading = ref(true)
const acting = ref(false)
let timer: ReturnType<typeof setInterval> | null = null

const chapterStats = computed(() => {
  const detail = task.value
  if (!detail) return { total: 0, done: 0, attention: 0 }
  return {
    total: detail.chapters.length,
    done: detail.chapters.filter((chapter) =>
      ['succeeded', 'already_completed'].includes(chapter.status),
    ).length,
    attention: detail.chapters.filter((chapter) =>
      ['failed', 'unsubmitted', 'skipped_not_open'].includes(chapter.status),
    ).length,
  }
})

const terminal = computed(() =>
  task.value
    ? ['succeeded', 'needs_attention', 'failed', 'canceled'].includes(task.value.status)
    : false,
)

const actionButtons = computed(() => {
  const detail = task.value
  if (!detail) return []
  if (['queued', 'paused'].includes(detail.status)) {
    return [
      { label: '取消', action: 'cancel' as const, danger: true },
      {
        label: detail.status === 'paused' ? '继续' : '暂停',
        action: (detail.status === 'paused' ? 'resume' : 'pause') as 'resume' | 'pause',
      },
    ]
  }
  if (['running', 'recovering'].includes(detail.status)) {
    return [
      { label: '取消', action: 'cancel' as const, danger: true },
      { label: '暂停', action: 'pause' as const },
    ]
  }
  return []
})

async function refresh() {
  if (!taskId.value) return
  try {
    task.value = await api.getTask(taskId.value)
  } catch {
    // 会话失效或任务被删，回到上一页
  } finally {
    loading.value = false
  }
}

async function runAction(action: 'pause' | 'resume' | 'cancel') {
  if (acting.value) return
  acting.value = true
  try {
    await api.taskAction(taskId.value, action)
    await refresh()
  } catch (error) {
    uni.showToast({ title: error instanceof Error ? error.message : '操作失败', icon: 'none' })
  } finally {
    acting.value = false
  }
}

async function removeTask() {
  const confirm = await uni.showModal({
    title: '删除任务',
    content: '确定删除该任务及其章节记录？',
  })
  if (!confirm.confirm) return
  try {
    await api.deleteTask(taskId.value)
    uni.showToast({ title: '已删除', icon: 'success' })
    setTimeout(() => uni.navigateBack(), 600)
  } catch (error) {
    uni.showToast({ title: error instanceof Error ? error.message : '删除失败', icon: 'none' })
  }
}

function chapterClass(status: string): string {
  if (['succeeded', 'already_completed'].includes(status)) return 'done'
  if (['failed', 'unsubmitted', 'skipped_not_open'].includes(status)) return 'attention'
  if (status === 'running') return 'running'
  return ''
}

onLoad((options) => {
  taskId.value = String(options?.taskId ?? '')
})

onShow(() => {
  if (!auth.isLoggedIn || !taskId.value) return
  refresh()
  if (timer) clearInterval(timer)
  timer = setInterval(() => {
    if (task.value && !terminal.value) refresh()
  }, 5000)
})

onHide(() => {
  if (timer) {
    clearInterval(timer)
    timer = null
  }
})
onUnmounted(() => {
  if (timer) clearInterval(timer)
})
</script>

<template>
  <view>
    <NeedLogin v-if="!auth.isLoggedIn" />
    <template v-else>
      <view v-if="loading" class="empty">加载中…</view>
      <template v-else-if="task">
        <view class="card summary">
          <view class="row">
            <view class="summary-info">
              <view class="course-title">{{ task.course_title }}</view>
              <view class="text-muted">
                {{ task.account_label }} · 创建于 {{ formatTime(task.created_at) }}
              </view>
              <view v-if="task.last_error" class="text-danger reason">{{ task.last_error }}</view>
            </view>
            <text class="badge" :class="task.status">{{ TASK_STATUS_LABELS[task.status] }}</text>
          </view>
          <view class="progress">
            <view class="progress-track">
              <view
                class="progress-fill"
                :style="{ width: chapterStats.total ? `${(chapterStats.done / chapterStats.total) * 100}%` : '0%' }"
              />
            </view>
            <text class="text-muted">
              章节 {{ chapterStats.done }}/{{ chapterStats.total }}
              <template v-if="chapterStats.attention"> · 需处理 {{ chapterStats.attention }}</template>
            </text>
          </view>
          <view class="actions">
            <button
              v-for="button in actionButtons"
              :key="button.label"
              size="mini"
              :class="button.danger ? 'mini-btn danger' : 'mini-btn'"
              :loading="acting"
              @click="runAction(button.action)"
            >
              {{ button.label }}
            </button>
            <button v-if="terminal" size="mini" class="mini-btn danger" @click="removeTask">
              删除任务
            </button>
          </view>
        </view>

        <view class="section-title">章节明细</view>
        <view class="card chapter" v-for="chapter in task.chapters" :key="chapter.chapter_id">
          <view class="row">
            <view class="chapter-info">
              <view class="chapter-title">{{ chapter.title }}</view>
              <view class="text-muted">
                {{ CHAPTER_STATUS_LABELS[chapter.status] ?? chapter.status }}
                · 尝试 {{ chapter.attempts }} 次
                <template v-if="chapter.started_at">
                  · 耗时 {{ formatDuration(chapter.started_at, chapter.finished_at) }}
                </template>
              </view>
              <view v-if="chapter.last_error" class="text-danger reason">{{ chapter.last_error }}</view>
            </view>
            <text class="chapter-dot" :class="chapterClass(chapter.status)" />
          </view>
        </view>
        <view v-if="!task.chapters.length" class="empty">该任务未包含章节</view>
      </template>
      <view v-else class="empty">任务不存在或已删除</view>
    </template>
  </view>
</template>

<style lang="scss" scoped>
.summary {
  margin-top: 24rpx;
}
.summary-info {
  flex: 1;
  margin-right: 16rpx;
}
.course-title {
  font-size: 32rpx;
  font-weight: 600;
  margin-bottom: 6rpx;
}
.reason {
  margin-top: 8rpx;
  font-size: 24rpx;
}
.progress {
  margin-top: 24rpx;
}
.progress-track {
  height: 12rpx;
  background: var(--track);
  border-radius: 6rpx;
  overflow: hidden;
  margin-bottom: 10rpx;
}
.progress-fill {
  height: 100%;
  background: var(--primary);
  border-radius: 6rpx;
  transition: width 0.3s;
}
.actions {
  display: flex;
  justify-content: flex-end;
  gap: 12rpx;
  margin-top: 20rpx;
}
.mini-btn {
  margin: 0;
  font-size: 22rpx;

  &.danger {
    color: var(--danger);
  }
}
.chapter {
  margin-top: 0;
  padding: 20rpx 24rpx;
}
.chapter-info {
  flex: 1;
  margin-right: 16rpx;
}
.chapter-title {
  font-size: 28rpx;
  margin-bottom: 6rpx;
}
.chapter-dot {
  width: 20rpx;
  height: 20rpx;
  border-radius: 50%;
  background: var(--gray-3);
  flex-shrink: 0;
  margin-top: 8rpx;

  &.done {
    background: var(--success);
  }
  &.running {
    background: var(--primary);
  }
  &.attention {
    background: #e54d42;
  }
}
</style>
