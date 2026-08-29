<script setup lang="ts">
import { computed, onUnmounted, ref } from 'vue'
import { onHide, onPullDownRefresh, onShow } from '@dcloudio/uni-app'
import { api, setCsrfToken } from '@/api/client'
import type { ManualIntervention, SystemEvent } from '@/api/types'
import { useAuthStore } from '@/stores/auth'
import { startEventStream } from '@/utils/sse'
import { eventKindLabel, formatTime } from '@/utils/format'
import NeedLogin from '@/components/NeedLogin.vue'

const auth = useAuthStore()

type LevelFilter = 'all' | 'info' | 'warning' | 'error'
const levelFilter = ref<LevelFilter>('all')
const LEVEL_FILTERS: { key: LevelFilter; label: string }[] = [
  { key: 'all', label: '全部' },
  { key: 'info', label: '普通' },
  { key: 'warning', label: '警告' },
  { key: 'error', label: '错误' },
]

const events = ref<SystemEvent[]>([])
const interventions = ref<ManualIntervention[]>([])
const streamState = ref<'connecting' | 'connected' | 'offline'>('offline')
let stopStream: (() => void) | null = null
let pollTimer: ReturnType<typeof setInterval> | null = null
let afterId = 0

function mergeEvents(batch: SystemEvent[]) {
  if (!batch.length) return
  afterId = Math.max(afterId, ...batch.map((event) => event.id))
  const existing = new Set(events.value.map((event) => event.id))
  const fresh = batch.filter((event) => !existing.has(event.id))
  if (fresh.length) {
    events.value = [...fresh, ...events.value].slice(0, 300)
  }
}

async function refreshSnapshot() {
  try {
    const batch = await api.listEvents({ after_id: afterId, limit: 100 })
    mergeEvents(batch)
  } catch {
    // 慢轮询兜底，失败静默
  }
  try {
    interventions.value = await api.listInterventions()
  } catch {
    // 静默
  }
}

const filteredEvents = computed(() =>
  levelFilter.value === 'all'
    ? events.value
    : events.value.filter((event) => event.level === levelFilter.value),
)

function openTaskDetail(taskId: string) {
  uni.navigateTo({ url: `/pages/task-detail/task-detail?taskId=${taskId}` })
}

async function resolveIntervention(item: ManualIntervention) {
  try {
    await api.resolveInterventions([item.id])
    interventions.value = interventions.value.filter((other) => other.id !== item.id)
    uni.showToast({ title: '已标记处理', icon: 'success' })
  } catch (error) {
    uni.showToast({ title: error instanceof Error ? error.message : '操作失败', icon: 'none' })
  }
}

function start() {
  stopAll()
  void refreshSnapshot()
  pollTimer = setInterval(refreshSnapshot, 30_000)

  stopStream = startEventStream({
    onEvent: (event) => mergeEvents([event]),
    onState: (state) => {
      streamState.value = state
    },
    onAuthFailed: () => {
      setCsrfToken(null)
      auth.profile = null
      auth.phase = 'anonymous'
    },
  })
}

function stopAll() {
  stopStream?.()
  stopStream = null
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

onShow(() => {
  if (auth.isLoggedIn) start()
})
// tab 页不会 unmount，切走时停 SSE 与轮询
onHide(stopAll)
onPullDownRefresh(async () => {
  await refreshSnapshot()
  uni.stopPullDownRefresh()
})
onUnmounted(stopAll)
</script>

<template>
  <view>
    <NeedLogin v-if="!auth.isLoggedIn" />
    <template v-else>
      <template v-if="interventions.length">
        <view class="section-title">人工接管（{{ interventions.length }}）</view>
        <view class="card intervention" v-for="item in interventions" :key="item.id">
          <view class="intervention-title">{{ item.course_title }}</view>
          <view class="text-muted">
            {{ item.chapter_title }} · {{ item.account_label }}
          </view>
          <view v-if="item.reason" class="text-danger reason">{{ item.reason }}</view>
          <view class="row" style="margin-top: 12rpx">
            <text class="text-muted">{{ formatTime(item.occurred_at) }}</text>
            <button class="mini-btn" size="mini" @click="resolveIntervention(item)">
              标记已处理
            </button>
          </view>
        </view>
      </template>

      <view class="section-title">
        活动记录
        <text class="text-muted" style="margin-left: 16rpx; font-size: 22rpx">
          {{ streamState === 'connected' ? '实时' : streamState === 'connecting' ? '连接中…' : '轮询中' }}
        </text>
      </view>
      <view class="filter-chips">
        <text
          v-for="filter in LEVEL_FILTERS"
          :key="filter.key"
          class="chip"
          :class="{ on: levelFilter === filter.key }"
          @click="levelFilter = filter.key"
        >
          {{ filter.label }}
        </text>
      </view>
      <view
        class="card event"
        :class="{ clickable: event.task_id }"
        v-for="event in filteredEvents"
        :key="event.id"
        @click="event.task_id && openTaskDetail(event.task_id)"
      >
        <view class="row">
          <view class="event-info">
            <view>{{ eventKindLabel(event.kind) }}</view>
            <view class="text-muted event-detail">
              {{ event.chapter_title || (event.account_id ? `账号 #${event.account_id}` : '') }}
            </view>
          </view>
          <view class="row" style="gap: 12rpx">
            <text class="badge" :class="event.level">{{ event.level }}</text>
            <text class="text-muted">{{ formatTime(event.occurred_at) }}</text>
          </view>
        </view>
      </view>
      <view v-if="!events.length" class="empty">暂无活动记录，创建任务后这里会实时更新</view>
    </template>
  </view>
</template>

<style lang="scss" scoped>
.intervention {
  margin-top: 0;
}
.intervention-title {
  font-size: 28rpx;
  font-weight: 500;
  margin-bottom: 6rpx;
}
.reason {
  margin-top: 8rpx;
  font-size: 24rpx;
}
.mini-btn {
  margin: 0;
  font-size: 22rpx;
  color: var(--primary);
}
.event {
  margin-top: 0;
  padding: 20rpx 24rpx;

  &.clickable {
    cursor: pointer;
  }
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
.event-info {
  flex: 1;
  margin-right: 16rpx;
  font-size: 28rpx;
}
.event-detail {
  margin-top: 4rpx;
}
.badge.info {
  background: var(--badge-blue-bg);
  color: var(--primary);
}
.badge.warning {
  background: var(--badge-yellow-bg);
  color: var(--warning);
}
.badge.error {
  background: var(--badge-red-bg);
  color: var(--danger);
}
</style>
