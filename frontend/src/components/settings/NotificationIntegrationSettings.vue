<script setup lang="ts">
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import { BellRing, MessageCircle, RefreshCw, Save, Send, Smartphone } from 'lucide-vue-next'
import {
  NAlert,
  NButton,
  NCheckbox,
  NForm,
  NFormItem,
  NInput,
  NSpin,
  NSwitch,
  NTag,
  NTooltip,
  useMessage,
} from 'naive-ui'
import { computed, reactive, ref, watch } from 'vue'
import type { Component } from 'vue'

import {
  ApiError,
  getNotificationIntegrations,
  updateNotificationIntegration,
} from '@/api/client'
import type {
  NotificationChannelKind,
  NotificationIntegration,
  UpdateNotificationIntegrationInput,
} from '@/api/types'

interface NotificationForm {
  enabled: boolean
  webhookUrl: string
  botToken: string
  chatId: string
  clearWebhookUrl: boolean
  clearBotToken: boolean
  clearChatId: boolean
  revision: number
  hasWebhookUrl: boolean
  hasBotToken: boolean
  hasChatId: boolean
}

type ClearField = 'webhook' | 'botToken' | 'chatId'

const queryKey = ['integration-settings', 'notifications'] as const
const channelOrder: NotificationChannelKind[] = ['server_chan', 'qmsg', 'bark', 'telegram']
const channelMeta: Record<
  NotificationChannelKind,
  { label: string; description: string; icon: Component }
> = {
  server_chan: {
    label: 'Server 酱',
    description: '通过 Server 酱 Webhook 推送任务结果',
    icon: BellRing,
  },
  qmsg: {
    label: 'Qmsg 酱',
    description: '通过 Qmsg Webhook 推送到 QQ',
    icon: MessageCircle,
  },
  bark: {
    label: 'Bark',
    description: '向 iPhone 或 iPad 推送任务结果',
    icon: Smartphone,
  },
  telegram: {
    label: 'Telegram',
    description: '使用 Telegram Bot 发送任务结果',
    icon: Send,
  },
}

function emptyForm(): NotificationForm {
  return {
    enabled: false,
    webhookUrl: '',
    botToken: '',
    chatId: '',
    clearWebhookUrl: false,
    clearBotToken: false,
    clearChatId: false,
    revision: 1,
    hasWebhookUrl: false,
    hasBotToken: false,
    hasChatId: false,
  }
}

const forms = reactive<Record<NotificationChannelKind, NotificationForm>>({
  server_chan: emptyForm(),
  qmsg: emptyForm(),
  bark: emptyForm(),
  telegram: emptyForm(),
})
const persisted = reactive<Record<NotificationChannelKind, NotificationForm>>({
  server_chan: emptyForm(),
  qmsg: emptyForm(),
  bark: emptyForm(),
  telegram: emptyForm(),
})
const initialized = ref(false)
const queryClient = useQueryClient()
const message = useMessage()

const notifications = useQuery({
  queryKey,
  queryFn: getNotificationIntegrations,
})

const saveNotification = useMutation({
  mutationFn: ({
    channel,
    input,
  }: {
    channel: NotificationChannelKind
    input: UpdateNotificationIntegrationInput
  }) => updateNotificationIntegration(channel, input),
  onSuccess(data) {
    applyChannel(data)
    queryClient.setQueryData<NotificationIntegration[]>(queryKey, (current) => {
      if (!current) return [data]
      return current.map((item) => (item.channel === data.channel ? data : item))
    })
    message.success(`${channelMeta[data.channel].label}设置已保存`)
  },
  async onError(error, variables) {
    if (error instanceof ApiError && error.status === 409) {
      const result = await notifications.refetch()
      const current = result.data?.find((item) => item.channel === variables.channel)
      if (current) applyChannel(current)
      message.warning(`${channelMeta[variables.channel].label}配置已更新，已刷新，请重新修改`)
      return
    }
    message.error(errorText(error, `${channelMeta[variables.channel].label}设置保存失败`))
  },
})

watch(
  notifications.data,
  (value) => {
    if (value && !initialized.value) {
      applyAll(value)
      initialized.value = true
    }
  },
  { immediate: true },
)

const loadError = computed(() => errorText(notifications.error.value, '通知设置读取失败'))

function cloneForm(value: NotificationForm): NotificationForm {
  return { ...value }
}

function editableSnapshot(value: NotificationForm) {
  return {
    enabled: value.enabled,
    webhookUrl: value.webhookUrl,
    botToken: value.botToken,
    chatId: value.chatId,
    clearWebhookUrl: value.clearWebhookUrl,
    clearBotToken: value.clearBotToken,
    clearChatId: value.clearChatId,
  }
}

function applyChannel(value: NotificationIntegration) {
  const next: NotificationForm = {
    enabled: value.enabled,
    webhookUrl: '',
    botToken: '',
    chatId: '',
    clearWebhookUrl: false,
    clearBotToken: false,
    clearChatId: false,
    revision: value.revision,
    hasWebhookUrl: value.has_webhook_url,
    hasBotToken: value.has_bot_token,
    hasChatId: value.has_chat_id,
  }
  Object.assign(forms[value.channel], next)
  Object.assign(persisted[value.channel], cloneForm(next))
}

function applyAll(values: NotificationIntegration[]) {
  for (const value of values) applyChannel(value)
}

function hasChanges(channel: NotificationChannelKind): boolean {
  return JSON.stringify(editableSnapshot(forms[channel])) !==
    JSON.stringify(editableSnapshot(persisted[channel]))
}

function isConfigured(channel: NotificationChannelKind): boolean {
  const form = forms[channel]
  if (channel === 'telegram') return form.hasBotToken && form.hasChatId
  return form.hasWebhookUrl
}

function updateEnabled(channel: NotificationChannelKind, enabled: boolean) {
  const form = forms[channel]
  form.enabled = enabled
  if (enabled) {
    form.clearWebhookUrl = false
    form.clearBotToken = false
    form.clearChatId = false
  }
}

function setClear(channel: NotificationChannelKind, field: ClearField, value: boolean) {
  const form = forms[channel]
  if (field === 'webhook') {
    form.clearWebhookUrl = value
    if (value) form.webhookUrl = ''
  } else if (field === 'botToken') {
    form.clearBotToken = value
    if (value) form.botToken = ''
  } else {
    form.clearChatId = value
    if (value) form.chatId = ''
  }
  if (value) form.enabled = false
}

function validateChannel(channel: NotificationChannelKind): boolean {
  const form = forms[channel]
  if (!form.enabled) return true
  if (channel === 'telegram') {
    if ((!form.hasBotToken && !form.botToken.trim()) || (!form.hasChatId && !form.chatId.trim())) {
      message.warning('请填写 Telegram Bot Token 和 Chat ID')
      return false
    }
    return true
  }
  if (!form.hasWebhookUrl && !form.webhookUrl.trim()) {
    message.warning(`请填写${channelMeta[channel].label} Webhook 地址`)
    return false
  }
  return true
}

function buildPayload(channel: NotificationChannelKind): UpdateNotificationIntegrationInput {
  const form = forms[channel]
  const payload: UpdateNotificationIntegrationInput = {
    expected_revision: form.revision,
    enabled: form.enabled,
  }
  if (channel === 'telegram') {
    const botToken = form.botToken.trim()
    const chatId = form.chatId.trim()
    if (botToken) payload.bot_token = botToken
    if (chatId) payload.chat_id = chatId
    if (form.clearBotToken) payload.clear_bot_token = true
    if (form.clearChatId) payload.clear_chat_id = true
  } else {
    const webhookUrl = form.webhookUrl.trim()
    if (webhookUrl) payload.webhook_url = webhookUrl
    if (form.clearWebhookUrl) payload.clear_webhook_url = true
  }
  return payload
}

function submitChannel(channel: NotificationChannelKind) {
  if (!hasChanges(channel) || saveNotification.isPending.value || !validateChannel(channel)) return
  saveNotification.mutate({ channel, input: buildPayload(channel) })
}

function resetChannel(channel: NotificationChannelKind) {
  Object.assign(forms[channel], cloneForm(persisted[channel]))
}

function isSaving(channel: NotificationChannelKind): boolean {
  return saveNotification.isPending.value && saveNotification.variables.value?.channel === channel
}

async function refresh() {
  const result = await notifications.refetch()
  if (result.data) {
    applyAll(result.data)
    message.success('通知设置已刷新')
  }
}

function errorText(error: unknown, fallback: string): string {
  if (error instanceof ApiError) return error.message
  if (error instanceof Error) return error.message
  return error ? fallback : ''
}
</script>

<template>
  <section class="content-section notification-section">
    <div class="section-heading notification-heading">
      <div>
        <h2>任务通知</h2>
        <p>任务结束后可同时向多个渠道发送结果</p>
      </div>
      <NTooltip trigger="hover">
        <template #trigger>
          <NButton
            quaternary
            circle
            aria-label="刷新通知设置"
            :loading="notifications.isFetching.value"
            @click="refresh"
          >
            <template #icon><RefreshCw /></template>
          </NButton>
        </template>
        刷新通知设置
      </NTooltip>
    </div>

    <NAlert v-if="notifications.isError.value" type="error" :bordered="false">
      {{ loadError }}
    </NAlert>

    <div v-if="notifications.isLoading.value" class="notification-loading">
      <NSpin size="small" description="正在读取通知设置" />
    </div>

    <NForm v-else-if="initialized" label-placement="top">
      <div v-for="channel in channelOrder" :key="channel" class="notification-channel">
        <div class="channel-heading">
          <span class="channel-icon">
            <component :is="channelMeta[channel].icon" :size="18" />
          </span>
          <div class="channel-copy">
            <strong>{{ channelMeta[channel].label }}</strong>
            <span>{{ channelMeta[channel].description }}</span>
          </div>
          <div class="channel-state">
            <NTag size="small" :type="isConfigured(channel) ? 'success' : 'warning'">
              {{ isConfigured(channel) ? '凭据已保存' : '未配置' }}
            </NTag>
            <NSwitch
              :value="forms[channel].enabled"
              :aria-label="`启用${channelMeta[channel].label}通知`"
              @update:value="(value) => updateEnabled(channel, value)"
            />
          </div>
        </div>

        <div v-if="channel === 'telegram'" class="channel-fields telegram-fields">
          <NFormItem label="Bot Token">
            <div class="secret-control">
              <NInput
                v-model:value="forms[channel].botToken"
                type="password"
                show-password-on="click"
                :disabled="forms[channel].clearBotToken"
                :placeholder="forms[channel].hasBotToken ? '已保存，留空保持不变' : '123456:bot-token'"
              />
              <NCheckbox
                v-if="forms[channel].hasBotToken"
                :checked="forms[channel].clearBotToken"
                @update:checked="(value) => setClear(channel, 'botToken', value)"
              >
                清除已保存的 Bot Token
              </NCheckbox>
            </div>
          </NFormItem>
          <NFormItem label="Chat ID">
            <div class="secret-control">
              <NInput
                v-model:value="forms[channel].chatId"
                type="password"
                show-password-on="click"
                :disabled="forms[channel].clearChatId"
                :placeholder="forms[channel].hasChatId ? '已保存，留空保持不变' : '例如 -100123456789'"
              />
              <NCheckbox
                v-if="forms[channel].hasChatId"
                :checked="forms[channel].clearChatId"
                @update:checked="(value) => setClear(channel, 'chatId', value)"
              >
                清除已保存的 Chat ID
              </NCheckbox>
            </div>
          </NFormItem>
        </div>

        <div v-else class="channel-fields">
          <NFormItem label="Webhook 地址">
            <div class="secret-control">
              <NInput
                v-model:value="forms[channel].webhookUrl"
                type="password"
                show-password-on="click"
                :disabled="forms[channel].clearWebhookUrl"
                :placeholder="forms[channel].hasWebhookUrl ? '已保存，留空保持不变' : '必须使用 HTTPS 地址'"
              />
              <NCheckbox
                v-if="forms[channel].hasWebhookUrl"
                :checked="forms[channel].clearWebhookUrl"
                @update:checked="(value) => setClear(channel, 'webhook', value)"
              >
                清除已保存的 Webhook（将同时停用该渠道）
              </NCheckbox>
            </div>
          </NFormItem>
        </div>

        <div class="channel-actions">
          <NButton
            size="small"
            :disabled="!hasChanges(channel) || isSaving(channel)"
            @click="resetChannel(channel)"
          >
            撤销
          </NButton>
          <NButton
            size="small"
            type="primary"
            :loading="isSaving(channel)"
            :disabled="!hasChanges(channel)"
            @click="submitChannel(channel)"
          >
            <template #icon><Save /></template>
            保存
          </NButton>
        </div>
      </div>
    </NForm>
  </section>
</template>

<style scoped>
.notification-section {
  max-width: 860px;
  padding: 0;
  overflow: hidden;
}

.notification-heading {
  min-height: 72px;
  margin: 0;
  padding: 16px 20px;
  border-bottom: 1px solid var(--color-border-soft);
}

.notification-section > :deep(.n-alert) {
  border-radius: 0;
}

.notification-loading {
  display: grid;
  min-height: 220px;
  place-items: center;
}

.notification-channel {
  border-bottom: 1px solid var(--color-border-soft);
}

.notification-channel:last-child {
  border-bottom: 0;
}

.channel-heading {
  display: grid;
  grid-template-columns: 38px minmax(0, 1fr) auto;
  min-height: 72px;
  align-items: center;
  gap: 12px;
  padding: 13px 20px;
}

.channel-icon {
  display: grid;
  width: 36px;
  height: 36px;
  place-items: center;
  border-radius: 6px;
  background: var(--color-accent-muted);
  color: var(--color-accent);
}

.channel-copy {
  min-width: 0;
}

.channel-copy strong,
.channel-copy span {
  display: block;
  overflow-wrap: anywhere;
}

.channel-copy strong {
  color: var(--color-text-strong);
  font-size: 13px;
}

.channel-copy span {
  margin-top: 4px;
  color: var(--color-text-muted);
  font-size: 11px;
  line-height: 1.5;
}

.channel-state {
  display: flex;
  align-items: center;
  gap: 9px;
}

.channel-fields {
  padding: 0 20px 0 70px;
}

.telegram-fields {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
}

.secret-control {
  display: grid;
  width: 100%;
  gap: 7px;
}

.channel-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  padding: 0 20px 14px 70px;
}

@media (max-width: 680px) {
  .notification-heading,
  .channel-heading,
  .channel-actions {
    padding-right: 14px;
    padding-left: 14px;
  }

  .channel-fields {
    padding-right: 14px;
    padding-left: 14px;
  }

  .telegram-fields {
    grid-template-columns: minmax(0, 1fr);
    gap: 0;
  }
}

@media (max-width: 460px) {
  .channel-heading {
    grid-template-columns: 38px minmax(0, 1fr);
  }

  .channel-state {
    grid-column: 2;
    justify-content: space-between;
  }

  .channel-actions > :deep(.n-button) {
    min-width: 0;
    flex: 1;
  }
}
</style>
