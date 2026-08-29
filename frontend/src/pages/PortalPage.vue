<script setup lang="ts">
import { Ticket, CalendarClock, Coins, RefreshCw } from 'lucide-vue-next'
import {
  NAlert,
  NButton,
  NForm,
  NFormItem,
  NInput,
  NModal,
  NTag,
  useMessage,
} from 'naive-ui'
import { computed, ref } from 'vue'
import { useQuery, useQueryClient } from '@tanstack/vue-query'

import { ApiError, apiRequest } from '@/api/client'
import type { CardKeyRedeemResponse, EntitlementResponse } from '@/api/types'
import { useAuthStore } from '@/stores/auth'

const message = useMessage()
const queryClient = useQueryClient()
const auth = useAuthStore()

const entitlementQuery = useQuery({
  queryKey: ['entitlement'],
  queryFn: () => apiRequest<EntitlementResponse>('/cards/me'),
  refetchOnWindowFocus: true,
})

const showRedeem = ref(false)
const redeemCode = ref('')
const redeeming = ref(false)

const entitlement = computed(() => entitlementQuery.data.value)
const planActive = computed(() => {
  const expires = entitlement.value?.plan_expires_at
  return expires !== null && expires !== undefined && new Date(expires).getTime() > Date.now()
})
const hasEntitlement = computed(() => entitlement.value?.active === true)

const planExpiryText = computed(() => {
  const expires = entitlement.value?.plan_expires_at
  if (!expires) return '未激活'
  const date = new Date(expires)
  const diffMs = date.getTime() - Date.now()
  if (diffMs <= 0) return `已于 ${formatDateTime(date)} 过期`
  const days = Math.floor(diffMs / 86_400_000)
  const hours = Math.floor((diffMs % 86_400_000) / 3_600_000)
  return `${formatDateTime(date)}（剩 ${days} 天 ${hours} 小时）`
})

function formatDateTime(date: Date): string {
  const pad = (value: number) => String(value).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

async function submitRedeem(): Promise<void> {
  const code = redeemCode.value.trim()
  if (!code) {
    message.warning('请输入卡密')
    return
  }
  redeeming.value = true
  try {
    const result = await apiRequest<CardKeyRedeemResponse>('/cards/redeem', {
      method: 'POST',
      body: JSON.stringify({ code }),
    })
    message.success(
      result.kind === 'time'
        ? '兑换成功，时间卡已生效'
        : `兑换成功，+${result.value} 次任务额度`,
    )
    showRedeem.value = false
    redeemCode.value = ''
    await queryClient.invalidateQueries({ queryKey: ['entitlement'] })
    await auth.refreshProfile().catch(() => undefined)
  } catch (error) {
    message.error(error instanceof ApiError ? error.message : '兑换失败，请稍后重试')
  } finally {
    redeeming.value = false
  }
}
</script>

<template>
  <section class="content-section">
    <div class="section-heading padded">
      <div>
        <h2>我的账户</h2>
        <p>查看云端学习权益状态，兑换卡密后即可创建学习任务</p>
      </div>
      <div class="heading-actions">
        <NButton
          quaternary
          circle
          :loading="entitlementQuery.isFetching.value"
          aria-label="刷新"
          @click="queryClient.invalidateQueries({ queryKey: ['entitlement'] })"
        >
          <template #icon><RefreshCw :size="16" /></template>
        </NButton>
        <NButton type="primary" @click="showRedeem = true">
          <template #icon><Ticket :size="16" /></template>
          兑换卡密
        </NButton>
      </div>
    </div>

    <div class="padded portal-body">
      <NAlert
        v-if="entitlementQuery.error.value"
        type="error"
        :bordered="false"
        title="权益状态加载失败"
      >
        {{
          entitlementQuery.error.value instanceof ApiError
            ? entitlementQuery.error.value.message
            : '请稍后重试'
        }}
      </NAlert>

      <div v-else class="status-grid">
        <div class="status-card" :class="{ ok: planActive }">
          <div class="status-icon"><CalendarClock :size="20" /></div>
          <div class="status-meta">
            <span class="status-label">时间卡</span>
            <strong class="status-value">{{ planExpiryText }}</strong>
            <NTag v-if="planActive" size="small" type="success" :bordered="false">生效中</NTag>
            <NTag v-else size="small" :bordered="false">未激活</NTag>
          </div>
        </div>
        <div class="status-card" :class="{ ok: (entitlement?.task_credits ?? 0) > 0 }">
          <div class="status-icon"><Coins :size="20" /></div>
          <div class="status-meta">
            <span class="status-label">剩余任务次数</span>
            <strong class="status-value">{{ entitlement?.task_credits ?? '-' }} 次</strong>
            <span class="status-hint">时间卡生效期间不消耗次数</span>
          </div>
        </div>
      </div>

      <div v-if="entitlementQuery.data.value && !hasEntitlement" class="portal-tip">
        <NAlert type="warning" :bordered="false" title="尚无可用权益">
          创建学习任务前，请先兑换时间卡或次数卡。拿到卡密后点击右上角「兑换卡密」。
        </NAlert>
      </div>
    </div>

    <NModal
      v-model:show="showRedeem"
      preset="card"
      title="兑换卡密"
      class="form-modal"
      @after-leave="redeemCode = ''"
    >
      <NForm label-placement="top" @submit.prevent="submitRedeem">
        <NFormItem label="卡密">
          <NInput
            v-model:value="redeemCode"
            placeholder="例如 QLIT-9F3K-7D2M-XQ8P"
            :input-props="{ autocapitalize: 'characters', autocomplete: 'off' }"
            @keyup.enter="submitRedeem"
          />
        </NFormItem>
        <div class="modal-actions">
          <NButton @click="showRedeem = false">取消</NButton>
          <NButton type="primary" :loading="redeeming" @click="submitRedeem">立即兑换</NButton>
        </div>
      </NForm>
    </NModal>
  </section>
</template>

<style scoped>
.portal-body {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.status-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: 16px;
}

.status-card {
  display: flex;
  gap: 14px;
  align-items: flex-start;
  padding: 18px;
  border-radius: 14px;
  background: var(--surface-muted, rgba(128, 128, 128, 0.06));
  border: 1px solid rgba(128, 128, 128, 0.14);
}

.status-card.ok {
  border-color: rgba(48, 173, 99, 0.4);
}

.status-icon {
  display: grid;
  place-items: center;
  width: 40px;
  height: 40px;
  border-radius: 10px;
  background: rgba(128, 128, 128, 0.12);
}

.status-meta {
  display: flex;
  flex-direction: column;
  gap: 4px;
  align-items: flex-start;
}

.status-label {
  font-size: 12px;
  opacity: 0.65;
}

.status-value {
  font-size: 15px;
}

.status-hint {
  font-size: 12px;
  opacity: 0.55;
}

.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 8px;
}
</style>
