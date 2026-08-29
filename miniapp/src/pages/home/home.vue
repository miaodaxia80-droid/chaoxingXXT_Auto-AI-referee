<script setup lang="ts">
import { computed, onUnmounted, ref } from 'vue'
import { onHide, onPullDownRefresh, onShow } from '@dcloudio/uni-app'
import { api } from '@/api/client'
import type { ManualIntervention, StudyTask } from '@/api/types'
import { useAuthStore } from '@/stores/auth'
import { TASK_STATUS_LABELS } from '@/utils/format'
import NeedLogin from '@/components/NeedLogin.vue'

const auth = useAuthStore()

const accounts = ref(0)
const tasks = ref<StudyTask[]>([])
const interventions = ref<ManualIntervention[]>([])
const loading = ref(true)
let timer: ReturnType<typeof setInterval> | null = null

const runningTasks = computed(
  () =>
    tasks.value.filter((task) =>
      ['queued', 'running', 'pause_requested', 'paused', 'cancel_requested', 'recovering'].includes(
        task.status,
      ),
    ).length,
)
const attentionTasks = computed(
  () => tasks.value.filter((task) => task.status === 'needs_attention').length,
)

async function refresh() {
  if (!auth.isLoggedIn) return
  try {
    const [accountList, taskList, interventionList] = await Promise.all([
      api.listAccounts(),
      api.listTasks({ limit: 200 }),
      api.listInterventions(),
    ])
    accounts.value = accountList.length
    tasks.value = taskList
    interventions.value = interventionList
  } catch {
    // 轮询失败静默，下个周期重试
  } finally {
    loading.value = false
  }
}

function startPolling() {
  stopPolling()
  refresh()
  timer = setInterval(refresh, 10_000)
}

function openTaskDetail(taskId: string) {
  uni.navigateTo({ url: `/pages/task-detail/task-detail?taskId=${taskId}` })
}

function goActivity() {
  uni.switchTab({ url: '/pages/activity/activity' })
}

function stopPolling() {
  if (timer) {
    clearInterval(timer)
    timer = null
  }
}

onShow(() => {
  if (auth.isLoggedIn) startPolling()
})
// tab 页不会 unmount，切走时必须显式停轮询，否则后台持续请求
onHide(stopPolling)
onPullDownRefresh(async () => {
  await refresh()
  uni.stopPullDownRefresh()
})
onUnmounted(stopPolling)
</script>

<template>
  <view>
    <NeedLogin v-if="!auth.isLoggedIn" />
    <template v-else>
      <view class="card stats">
        <view class="stat">
          <view class="stat-value">{{ accounts }}</view>
          <view class="stat-label">学习账号</view>
        </view>
        <view class="stat">
          <view class="stat-value">{{ runningTasks }}</view>
          <view class="stat-label">运行中任务</view>
        </view>
        <view class="stat">
          <view class="stat-value">{{ attentionTasks }}</view>
          <view class="stat-label">需处理</view>
        </view>
      </view>

      <view class="section-title">进行中的任务</view>
      <view class="card" v-for="task in tasks.filter(t => !['succeeded','failed','canceled','needs_attention'].includes(t.status))" :key="task.id" @click="openTaskDetail(task.id)">
        <view class="row">
          <view>
            <view class="task-title">{{ task.course_title }}</view>
            <view class="text-muted">{{ task.account_label }} · 章节 {{ task.chapter_succeeded }}/{{ task.chapter_total }}</view>
          </view>
          <text class="badge" :class="task.status">{{ TASK_STATUS_LABELS[task.status] }}</text>
        </view>
      </view>
      <view v-if="runningTasks === 0 && !loading" class="empty">暂无进行中的任务</view>

      <view class="section-title">人工接管</view>
      <view class="card" v-if="interventions.length">
        <view v-for="item in interventions.slice(0, 5)" :key="item.id" class="intervention">
          <view class="task-title">{{ item.course_title }}</view>
          <view class="text-muted">{{ item.chapter_title }} · {{ item.account_label }}</view>
        </view>
        <view class="go-activity" @click="goActivity">
          共 {{ interventions.length }} 条待处理，去「动态」页处理 →
        </view>
      </view>
      <view v-else class="empty">暂无待处理项</view>
    </template>
  </view>
</template>

<style lang="scss" scoped>
.stats {
  display: flex;
  justify-content: space-around;
  padding: 40rpx 24rpx;

  .stat {
    text-align: center;
  }
  .stat-value {
    font-size: 52rpx;
    font-weight: 600;
    color: var(--primary);
  }
  .stat-label {
    margin-top: 8rpx;
    font-size: 24rpx;
    color: var(--text-muted);
  }
}
.task-title {
  font-size: 28rpx;
  font-weight: 500;
  margin-bottom: 6rpx;
}
.intervention {
  padding: 12rpx 0;
  border-bottom: 1rpx solid var(--border);

  &:last-child {
    border-bottom: none;
  }
}
.go-activity {
  text-align: center;
  margin-top: 12rpx;
  color: var(--primary);
  font-size: 24rpx;
}
</style>
