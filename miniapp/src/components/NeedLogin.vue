<script setup lang="ts">
import { ref } from 'vue'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const loading = ref(false)

async function handleLogin() {
  if (loading.value) return
  loading.value = true
  try {
    await auth.login()
  } catch {
    uni.showToast({ title: auth.lastError || '登录失败', icon: 'none' })
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <view class="login-gate">
    <view class="logo">📚</view>
    <view class="title">学习助手</view>
    <view class="subtitle">登录后管理你的学习账号与任务</view>
    <button class="btn-primary login-btn" :loading="loading" :disabled="loading" @click="handleLogin">
      <!-- #ifdef MP-WEIXIN -->
      微信一键登录
      <!-- #endif -->
      <!-- #ifdef H5 -->
      进入预览（开发登录）
      <!-- #endif -->
    </button>
    <view v-if="auth.lastError" class="text-danger error-text">{{ auth.lastError }}</view>
  </view>
</template>

<style lang="scss" scoped>
.login-gate {
  padding: 200rpx 60rpx 0;
  text-align: center;

  .logo {
    font-size: 120rpx;
  }
  .title {
    margin-top: 24rpx;
    font-size: 44rpx;
    font-weight: 600;
  }
  .subtitle {
    margin-top: 12rpx;
    color: var(--text-muted);
    font-size: 26rpx;
  }
  .login-btn {
    margin-top: 80rpx;
  }
  .error-text {
    margin-top: 24rpx;
    font-size: 24rpx;
  }
}
</style>
