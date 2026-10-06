<script setup lang="ts">
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import { BellRing, MessageCircle, RefreshCw, Save, Send, SendHorizontal, Smartphone } from 'lucide-vue-next'
import {
  NAlert,
  NButton,
  NCheckbox,
  NForm,
  NFormItem,
  NInput,
  NSkeleton,
  NSwitch,
  NTag,
  useMessage,
} from 'naive-ui'
import { computed, reactive, ref, watch } from 'vue'
import type { Component } from 'vue'

import {
  ApiError,
  getNotificationIntegrations,
  integrationTestMessage,
  testNotificationIntegration,
  updateNotificationIntegration,
} from '@/api/client'
import type {
  IntegrationTestResult,
  NotificationChannelKind,
  NotificationIntegration,
  UpdateNotificationIntegrationInput,
} from '@/api/types'
import IconAction from '@/components/ui/IconAction.vue'

const emit = defineEmits<{ 'dirty-change': [dirty: boolean] }>()

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
    delete testResults[data.channel]
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

const dirtyChannels = computed(() => channelOrder.filter((channel) => hasChanges(channel)))
const anyDirty = computed(() => dirtyChannels.value.length > 0)
watch(anyDirty, (value) => emit('dirty-change', value), { immediate: true })

async function saveAll() {
  if (!anyDirty.value || saveNotification.isPending.value) return
  const channels = dirtyChannels.value
  if (!channels.every(validateChannel)) return
  const savedLabels: string[] = []
  for (const channel of channels) {
    try {
      await saveNotification.mutateAsync({ channel, input: buildPayload(channel) })
      savedLabels.push(channelMeta[channel].label)
    } catch {
      break
    }
  }
  if (savedLabels.length) message.success(`已保存：${savedLabels.join('、')}`)
}

function resetAll() {
  for (const channel of channelOrder) Object.assign(forms[channel], cloneForm(persisted[channel]))
}

const testResults = reactive<Partial<Record<NotificationChannelKind, IntegrationTestResult>>>({})
const testNotification = useMutation({
  mutationFn: (channel: NotificationChannelKind) => testNotificationIntegration(channel),
  onMutate(channel) {
    delete testResults[channel]
  },
  onSuccess(result, channel) {
    testResults[channel] = result
  },
  onError(error) {
    message.error(errorText(error, '测试请求失败'))
  },
})

function canTest(channel: NotificationChannelKind): boolean {
  return persisted[channel].enabled && isConfigured(channel) && !hasChanges(channel)
}

function testTooltip(channel: NotificationChannelKind): string {
  if (!persisted[channel].enabled || !isConfigured(channel)) return '启用并保存后可发送测试'
  if (hasChanges(channel)) return '请先保存更改'
  return '发送测试消息'
}

function isTesting(channel: NotificationChannelKind): boolean {
  return testNotification.isPending.value && testNotification.variables.value === channel
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
  <section class="content-section flush settings-card">
    <div class="settings-intro">
      <p>任务结束后，同时向所有已启用的渠道推送结果。凭据只写不读，留空即保持原值。</p>
      <IconAction
        label="刷新通知设置"
        :icon="RefreshCw"
        :loading="notifications.isFetching.value"
        @click="refresh"
      />
    </div>

    <NAlert v-if="notifications.isError.value" type="error" :bordered="false">
      {{ loadError }}
    </NAlert>

    <div v-if="notifications.isLoading.value" class="settings-loading">
      <NSkeleton text :repeat="6" />
    </div>

    <NForm v-else-if="initialized" label-placement="top" @submit.prevent="saveAll">
      <div v-for="channel in channelOrder" :key="channel" class="notification-channel">
        <div class="setting-row channel-heading">
          <span class="setting-icon">
            <component :is="channelMeta[channel].icon" :size="18" />
          </span>
          <div class="setting-copy">
            <strong>
              {{ channelMeta[channel].label }}
              <span v-if="hasChanges(channel)" class="dirty-dot" aria-label="有未保存的更改" />
            </strong>
            <span>{{ channelMeta[channel].description }}</span>
          </div>
          <div class="setting-control">
            <NTag size="small" :bordered="false" :type="isConfigured(channel) ? 'success' : 'default'">
              {{ isConfigured(channel) ? '已配置' : '未配置' }}
            </NTag>
            <IconAction
              :label="testTooltip(channel)"
              :icon="SendHorizontal"
              :disabled="!canTest(channel)"
              :loading="isTesting(channel)"
              @click="testNotification.mutate(channel)"
            />
            <NSwitch
              :value="forms[channel].enabled"
              :aria-label="`启用${channelMeta[channel].label}通知`"
              @update:value="(value) => updateEnabled(channel, value)"
            />
          </div>
        </div>

        <div v-if="channel === 'telegram'" class="setting-fields channel-fields">
          <NFormItem label="Bot Token">
            <div class="secret-control">
              <NInput
                v-model:value="forms[channel].botToken"
                type="password"
                show-password-on="click"
                :input-props="{ autocomplete: 'new-password' }"
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
                :input-props="{ autocomplete: 'off' }"
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

        <div v-else class="setting-fields channel-fields single">
          <NFormItem label="Webhook 地址">
            <div class="secret-control">
              <NInput
                v-model:value="forms[channel].webhookUrl"
                type="password"
                show-password-on="click"
                :input-props="{ autocomplete: 'off' }"
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

        <NAlert
          v-if="testResults[channel]"
          class="channel-test-result"
          :type="testResults[channel]!.ok ? 'success' : 'error'"
          :bordered="false"
          closable
          @close="delete testResults[channel]"
        >
          {{ testResults[channel]!.ok ? '测试消息已发送，请在对应应用中确认收到。' : integrationTestMessage(testResults[channel]!) }}
        </NAlert>
      </div>

      <div class="sticky-actions">
        <span v-if="anyDirty" class="dirty-hint">
          {{ dirtyChannels.map((channel) => channelMeta[channel].label).join('、') }} 有未保存的更改
        </span>
        <NButton :disabled="!anyDirty || saveNotification.isPending.value" @click="resetAll">
          撤销更改
        </NButton>
        <NButton
          attr-type="submit"
          type="primary"
          :loading="saveNotification.isPending.value"
          :disabled="!anyDirty"
        >
          <template #icon><Save /></template>
          保存
        </NButton>
      </div>
    </NForm>
  </section>
</template>

<style scoped>
.notification-channel {
  border-bottom: 1px solid var(--color-border-soft);
}

.notification-channel:last-of-type {
  border-bottom: 0;
}

.channel-heading {
  border-bottom: 0;
}

.channel-fields {
  border-bottom: 0;
  background: transparent;
  padding-top: 0;
}

.channel-fields.single {
  grid-template-columns: minmax(0, 1fr);
}

.secret-control {
  display: grid;
  width: 100%;
  gap: 7px;
}

.dirty-dot {
  display: inline-block !important;
  width: 6px;
  height: 6px;
  margin-left: 6px;
  border-radius: 50%;
  background: var(--color-warning-strong);
  vertical-align: middle;
}

.channel-test-result {
  margin: 0 20px 14px 70px;
}

@media (max-width: 680px) {
  .channel-fields {
    padding-top: 0;
  }

  .channel-test-result {
    margin: 0 14px 14px;
  }
}

@media (max-width: 460px) {
  .channel-heading {
    grid-template-columns: 36px minmax(0, 1fr);
  }

  .channel-heading .setting-control {
    grid-column: 2;
    justify-content: space-between;
  }
}
</style>
