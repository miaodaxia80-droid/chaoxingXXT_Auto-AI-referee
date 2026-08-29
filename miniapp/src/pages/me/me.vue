<script setup lang="ts">
import { ref } from 'vue'
import { onShow } from '@dcloudio/uni-app'
import { api } from '@/api/client'
import type { AnswerPublic } from '@/api/types'
import { useAuthStore } from '@/stores/auth'
import NeedLogin from '@/components/NeedLogin.vue'

const auth = useAuthStore()
const answerPublic = ref<AnswerPublic | null>(null)

const PROVIDER_LABELS: Record<string, string> = {
  yanxi: '言溪',
  like: 'Like 题库',
  tiku_adapter: 'TikuAdapter',
  openai_compatible: 'OpenAI 兼容',
  siliconflow: '硅基流动',
}

const SUBMIT_MODE_LABELS: Record<string, string> = {
  auto: '自动提交',
  save_only: '仅保存',
  submit: '仅提交',
}

async function refresh() {
  if (!auth.isLoggedIn) return
  try {
    answerPublic.value = await api.answerPublic()
  } catch {
    // 平台可能未配置答题集成
  }
}

async function clearHistory() {
  const confirm = await uni.showModal({
    title: '清理历史',
    content: '删除全部已结束（成功/失败/取消）的任务记录，确定？',
  })
  if (!confirm.confirm) return
  try {
    const result = await api.clearTaskHistory()
    uni.showToast({ title: `已清理 ${result.deleted} 条`, icon: 'success' })
  } catch (error) {
    uni.showToast({ title: error instanceof Error ? error.message : '清理失败', icon: 'none' })
  }
}

async function logout() {
  const confirm = await uni.showModal({ title: '退出登录', content: '确定退出当前微信账号？' })
  if (!confirm.confirm) return
  await auth.logout()
}

onShow(refresh)
</script>

<template>
  <view>
    <NeedLogin v-if="!auth.isLoggedIn" />
    <template v-else>
      <view class="card profile">
        <view class="avatar">{{ auth.profile?.nickname?.slice(0, 1) || '微' }}</view>
        <view class="profile-info">
          <view class="nickname">{{ auth.profile?.nickname || '微信用户' }}</view>
          <view class="text-muted">
            账号上限 {{ auth.profile?.quotas?.max_accounts ?? 3 }} · 并发任务
            {{ auth.profile?.quotas?.max_active_tasks ?? 1 }}
          </view>
        </view>
      </view>

      <view class="section-title">平台答题服务</view>
      <view class="card">
        <template v-if="answerPublic?.enabled">
          <view class="row setting-row">
            <text class="text-muted">题库来源</text>
            <text>{{ PROVIDER_LABELS[answerPublic.provider] ?? answerPublic.provider }}</text>
          </view>
          <view class="row setting-row">
            <text class="text-muted">提交方式</text>
            <text>{{ SUBMIT_MODE_LABELS[answerPublic.submit_mode] ?? answerPublic.submit_mode }}</text>
          </view>
          <view class="row setting-row">
            <text class="text-muted">覆盖率阈值</text>
            <text>{{ Math.round(answerPublic.threshold * 100) }}%</text>
          </view>
        </template>
        <view v-else class="text-muted">平台暂未启用自动答题服务，任务将只完成视频等任务点</view>
      </view>

      <view class="section-title">数据</view>
      <view class="card setting" @click="clearHistory">
        <view>清理任务历史</view>
        <view class="text-muted">删除已结束的任务记录</view>
      </view>

      <view class="footer-btn">
        <button class="btn-plain" @click="logout">退出登录</button>
      </view>
      <view class="version text-muted">学习助手 · 自托管多账号学习工具</view>
    </template>
  </view>
</template>

<style lang="scss" scoped>
.profile {
  display: flex;
  align-items: center;
  gap: 24rpx;
  margin-top: 32rpx;
}
.avatar {
  width: 96rpx;
  height: 96rpx;
  border-radius: 50%;
  background: var(--primary);
  color: #ffffff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 40rpx;
}
.nickname {
  font-size: 32rpx;
  font-weight: 600;
  margin-bottom: 6rpx;
}
.setting-row {
  padding: 12rpx 0;
}
.setting {
  cursor: pointer;
}
.footer-btn {
  padding: 24rpx;
  margin-top: 40rpx;
}
.version {
  text-align: center;
  margin-top: 24rpx;
  font-size: 22rpx;
}
</style>
