<script setup lang="ts">
import { CalendarClock, Check, Coins, ListChecks, RefreshCw, Ticket, UserRound } from 'lucide-vue-next'
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
import type { Component } from 'vue'
import { useQuery, useQueryClient } from '@tanstack/vue-query'
import { useRouter } from 'vue-router'

import { ApiError, apiRequest } from '@/api/client'
import type {
  Account,
  CardKeyRedeemResponse,
  EntitlementResponse,
  StudyTask,
} from '@/api/types'
import IconAction from '@/components/ui/IconAction.vue'
import { useAuthStore } from '@/stores/auth'

const message = useMessage()
const queryClient = useQueryClient()
const router = useRouter()
const auth = useAuthStore()

const entitlementQuery = useQuery({
  queryKey: ['entitlement'],
  queryFn: () => apiRequest<EntitlementResponse>('/cards/me'),
  refetchOnWindowFocus: true,
})

const accountsQuery = useQuery({
  queryKey: ['accounts'],
  queryFn: () => apiRequest<Account[]>('/accounts'),
})

const tasksPeekQuery = useQuery({
  queryKey: ['tasks', 'peek'],
  queryFn: () => apiRequest<StudyTask[]>('/tasks?limit=1'),
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
const hasAccount = computed(() => (accountsQuery.data.value?.length ?? 0) > 0)
const hasTask = computed(() => (tasksPeekQuery.data.value?.length ?? 0) > 0)

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

interface OnboardingStep {
  key: string
  label: string
  hint: string
  icon: Component
  done: boolean
  actionLabel: string
  action: () => void
}

const steps = computed<OnboardingStep[]>(() => [
  {
    key: 'redeem',
    label: '兑换卡密',
    hint: '激活时间卡或次数卡权益',
    icon: Ticket,
    done: hasEntitlement.value,
    actionLabel: '去兑换',
    action: () => {
      showRedeem.value = true
    },
  },
  {
    key: 'account',
    label: '添加学习通账号',
    hint: '绑定密码或 Cookie 登录凭据',
    icon: UserRound,
    done: hasAccount.value,
    actionLabel: '去添加',
    action: () => void router.push({ name: 'accounts', query: { create: '1' } }),
  },
  {
    key: 'task',
    label: '创建学习任务',
    hint: '选择课程章节加入队列',
    icon: ListChecks,
    done: hasTask.value,
    actionLabel: '去创建',
    action: () => void router.push({ name: 'tasks', query: { create: '1' } }),
  },
])

const allStepsDone = computed(() => steps.value.every((step) => step.done))

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
    <div class="portal-toolbar">
      <IconAction
        label="刷新权益"
        :icon="RefreshCw"
        :loading="entitlementQuery.isFetching.value"
        @click="entitlementQuery.refetch()"
      />
      <NButton type="primary" @click="showRedeem = true">
        <template #icon><Ticket :size="16" /></template>
        兑换卡密
      </NButton>
    </div>

    <NAlert
      v-if="entitlementQuery.error.value"
      type="error"
      :bordered="false"
      title="权益状态加载失败"
      class="portal-alert"
    >
      {{
        entitlementQuery.error.value instanceof ApiError
          ? entitlementQuery.error.value.message
          : '请稍后重试'
      }}
    </NAlert>

    <template v-else>
      <div v-if="!allStepsDone" class="onboarding">
        <p class="onboarding-title">三步上手</p>
        <div
          v-for="(step, index) in steps"
          :key="step.key"
          class="onboarding-step"
          :class="{ done: step.done }"
        >
          <span class="step-index">
            <Check v-if="step.done" :size="15" />
            <template v-else>{{ index + 1 }}</template>
          </span>
          <span class="step-icon"><component :is="step.icon" :size="16" /></span>
          <div class="step-copy">
            <strong>{{ step.label }}</strong>
            <span>{{ step.hint }}</span>
          </div>
          <NTag v-if="step.done" size="small" type="success" :bordered="false">已完成</NTag>
          <NButton v-else size="small" @click="step.action">{{ step.actionLabel }}</NButton>
        </div>
      </div>

      <div class="status-grid">
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
    </template>

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
.portal-toolbar {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  margin-bottom: 16px;
}

.portal-alert {
  margin-bottom: 16px;
}

.onboarding {
  display: grid;
  gap: 8px;
  margin-bottom: 16px;
  border: 1px solid var(--color-border-soft);
  border-radius: var(--radius-lg);
  background: var(--color-surface-muted);
  padding: 14px;
}

.onboarding-title {
  margin: 0 0 2px;
  color: var(--color-text-muted);
  font-size: var(--fs-xs);
  font-weight: 600;
}

.onboarding-step {
  display: flex;
  align-items: center;
  gap: 10px;
  border-radius: var(--radius-md);
  background: var(--color-surface);
  padding: 10px 12px;
}

.step-index {
  display: grid;
  width: 22px;
  height: 22px;
  flex: 0 0 auto;
  place-items: center;
  border-radius: 50%;
  background: var(--color-neutral-soft);
  color: var(--color-text-muted);
  font-size: var(--fs-xs);
  font-weight: 600;
}

.onboarding-step.done .step-index {
  background: var(--color-accent-soft);
  color: var(--color-accent-strong);
}

.step-icon {
  display: grid;
  width: 30px;
  height: 30px;
  flex: 0 0 auto;
  place-items: center;
  border-radius: var(--radius-md);
  background: var(--color-accent-muted);
  color: var(--color-accent);
}

.step-copy {
  min-width: 0;
  flex: 1;
}

.step-copy strong,
.step-copy span {
  display: block;
}

.step-copy strong {
  color: var(--color-text-strong);
  font-size: var(--fs-sm);
}

.step-copy span {
  color: var(--color-text-muted);
  font-size: var(--fs-xs);
}

.status-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: 12px;
}

.status-card {
  display: flex;
  gap: 14px;
  align-items: flex-start;
  padding: 16px;
  border: 1px solid var(--color-border-soft);
  border-radius: var(--radius-lg);
  background: var(--color-surface-muted);
}

.status-card.ok {
  border-color: var(--color-accent-border);
}

.status-icon {
  display: grid;
  width: 40px;
  height: 40px;
  flex: 0 0 auto;
  place-items: center;
  border-radius: var(--radius-md);
  background: var(--color-accent-muted);
  color: var(--color-accent);
}

.status-meta {
  display: flex;
  min-width: 0;
  flex-direction: column;
  align-items: flex-start;
  gap: 4px;
}

.status-label {
  color: var(--color-text-faint);
  font-size: var(--fs-xs);
}

.status-value {
  color: var(--color-text-strong);
  font-size: var(--fs-md);
}

.status-hint {
  color: var(--color-text-faint);
  font-size: var(--fs-xs);
}

.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 8px;
}
</style>
