<script setup lang="ts">
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import { Clock3, RefreshCw, Save, ServerCog } from 'lucide-vue-next'
import {
  NAlert,
  NButton,
  NForm,
  NFormItem,
  NInputNumber,
  NSelect,
  NSpin,
  NSwitch,
  NTimePicker,
  NTooltip,
  useMessage,
} from 'naive-ui'
import { computed, reactive, ref, watch } from 'vue'

import {
  ApiError,
  getSystemSettings,
  updateSystemSettings,
} from '@/api/client'
import AnswerIntegrationSettings from '@/components/settings/AnswerIntegrationSettings.vue'
import NotificationIntegrationSettings from '@/components/settings/NotificationIntegrationSettings.vue'
import type { SystemSettings, UpdateSystemSettingsInput } from '@/api/types'

const queryClient = useQueryClient()
const message = useMessage()
const saved = ref<UpdateSystemSettingsInput | null>(null)
const form = reactive<UpdateSystemSettingsInput>({
  worker_enabled: true,
  run_window_enabled: false,
  run_window_start: '00:00',
  run_window_end: '00:00',
  timezone: 'Asia/Shanghai',
  event_retention_days: 30,
})

const baseTimezoneOptions = [
  { label: '中国标准时间', value: 'Asia/Shanghai' },
  { label: '协调世界时', value: 'UTC' },
  { label: '香港时间', value: 'Asia/Hong_Kong' },
  { label: '东京时间', value: 'Asia/Tokyo' },
  { label: '伦敦时间', value: 'Europe/London' },
  { label: '纽约时间', value: 'America/New_York' },
]

const browserTimezone = Intl.DateTimeFormat().resolvedOptions().timeZone
const timezoneOptions = browserTimezone && !baseTimezoneOptions.some(
  (option) => option.value === browserTimezone,
)
  ? [{ label: `本机时区 (${browserTimezone})`, value: browserTimezone }, ...baseTimezoneOptions]
  : baseTimezoneOptions

const settings = useQuery({
  queryKey: ['system-settings'],
  queryFn: getSystemSettings,
})

const saveSettings = useMutation({
  mutationFn: updateSystemSettings,
  onSuccess(data: SystemSettings) {
    applySettings(data)
    queryClient.setQueryData(['system-settings'], data)
    message.success('设置已保存')
  },
})

watch(
  settings.data,
  (value) => {
    if (value) applySettings(value)
  },
  { immediate: true },
)

const hasChanges = computed(() => {
  if (!saved.value) return false
  return (Object.keys(saved.value) as (keyof UpdateSystemSettingsInput)[]).some(
    (key) => form[key] !== saved.value?.[key],
  )
})

const windowSummary = computed(() => {
  if (!form.worker_enabled) return '调度已停止'
  if (!form.run_window_enabled || form.run_window_start === form.run_window_end) {
    return '全天运行'
  }
  const crossesMidnight = form.run_window_start > form.run_window_end
  return `${form.run_window_start} - ${form.run_window_end}${crossesMidnight ? '（跨午夜）' : ''}`
})

const loadError = computed(() => errorText(settings.error.value))
const saveError = computed(() => errorText(saveSettings.error.value))

function editableSettings(value: SystemSettings): UpdateSystemSettingsInput {
  return {
    worker_enabled: value.worker_enabled,
    run_window_enabled: value.run_window_enabled,
    run_window_start: value.run_window_start,
    run_window_end: value.run_window_end,
    timezone: value.timezone,
    event_retention_days: value.event_retention_days,
  }
}

function applySettings(value: SystemSettings) {
  const next = editableSettings(value)
  Object.assign(form, next)
  saved.value = { ...next }
}

function updateStart(value: string | null) {
  if (value) form.run_window_start = value
}

function updateEnd(value: string | null) {
  if (value) form.run_window_end = value
}

function submit() {
  if (!saved.value || saveSettings.isPending.value || !hasChanges.value) return
  saveSettings.mutate({ ...form })
}

function resetForm() {
  if (saved.value) Object.assign(form, saved.value)
}

function errorText(error: unknown): string {
  if (error instanceof ApiError) return error.message
  if (error instanceof Error) return error.message
  return error ? '请求失败，请稍后重试' : ''
}
</script>

<template>
  <div class="settings-page">
    <section class="content-section settings-section">
      <div class="section-heading settings-heading">
        <div>
          <h2>运行调度</h2>
          <p>控制后台任务的领取时间与运行状态</p>
        </div>
        <NTooltip trigger="hover">
          <template #trigger>
            <NButton
              quaternary
              circle
              aria-label="刷新设置"
              :loading="settings.isFetching.value"
              @click="settings.refetch()"
            >
              <template #icon><RefreshCw /></template>
            </NButton>
          </template>
          刷新设置
        </NTooltip>
      </div>

      <NAlert v-if="settings.isError.value" type="error" :bordered="false">
        {{ loadError }}
      </NAlert>

      <div v-if="settings.isLoading.value" class="settings-loading">
        <NSpin size="small" description="正在读取设置" />
      </div>

      <NForm v-else-if="saved" class="settings-form" label-placement="top" @submit.prevent="submit">
        <div class="setting-row">
          <span class="setting-icon"><ServerCog :size="19" /></span>
          <div class="setting-copy">
            <strong>后台任务</strong>
            <span>关闭后停止领取任务，并安全暂停正在执行的任务</span>
          </div>
          <NSwitch v-model:value="form.worker_enabled" aria-label="后台任务" />
        </div>

        <div class="setting-row window-toggle-row" :class="{ muted: !form.worker_enabled }">
          <span class="setting-icon"><Clock3 :size="19" /></span>
          <div class="setting-copy">
            <strong>限制运行时间</strong>
            <span>{{ windowSummary }}</span>
          </div>
          <NSwitch
            v-model:value="form.run_window_enabled"
            :disabled="!form.worker_enabled"
            aria-label="限制运行时间"
          />
        </div>

        <div class="window-fields" :class="{ muted: !form.worker_enabled || !form.run_window_enabled }">
          <NFormItem label="开始时间">
            <NTimePicker
              :formatted-value="form.run_window_start"
              format="HH:mm"
              :hours="Array.from({ length: 24 }, (_, hour) => hour)"
              :minutes="Array.from({ length: 60 }, (_, minute) => minute)"
              :disabled="!form.worker_enabled || !form.run_window_enabled"
              @update:formatted-value="updateStart"
            />
          </NFormItem>
          <NFormItem label="结束时间">
            <NTimePicker
              :formatted-value="form.run_window_end"
              format="HH:mm"
              :hours="Array.from({ length: 24 }, (_, hour) => hour)"
              :minutes="Array.from({ length: 60 }, (_, minute) => minute)"
              :disabled="!form.worker_enabled || !form.run_window_enabled"
              @update:formatted-value="updateEnd"
            />
          </NFormItem>
          <NFormItem label="时区">
            <NSelect
              v-model:value="form.timezone"
              filterable
              :options="timezoneOptions"
              :disabled="!form.worker_enabled || !form.run_window_enabled"
            />
          </NFormItem>
        </div>

        <div class="retention-row">
          <div class="setting-copy">
            <strong>活动记录保留</strong>
            <span>到期记录先归档，再经过同样时长后从数据库清理</span>
          </div>
          <NInputNumber
            v-model:value="form.event_retention_days"
            :min="1"
            :max="3650"
            :precision="0"
            aria-label="活动记录保留天数"
          >
            <template #suffix>天</template>
          </NInputNumber>
        </div>

        <NAlert v-if="saveSettings.isError.value" class="save-error" type="error" :bordered="false">
          {{ saveError }}
        </NAlert>

        <div class="settings-actions">
          <NButton :disabled="!hasChanges || saveSettings.isPending.value" @click="resetForm">
            撤销更改
          </NButton>
          <NButton
            attr-type="submit"
            type="primary"
            :loading="saveSettings.isPending.value"
            :disabled="!hasChanges"
          >
            <template #icon><Save /></template>
            保存
          </NButton>
        </div>
      </NForm>
    </section>

    <AnswerIntegrationSettings />
    <NotificationIntegrationSettings />
  </div>
</template>

<style scoped>
.settings-page {
  width: 100%;
  max-width: 860px;
  margin-inline: auto;
}

.settings-section {
  margin-top: 0;
  padding: 0;
  overflow: hidden;
}

.settings-heading {
  min-height: 72px;
  margin: 0;
  padding: 16px 20px;
  border-bottom: 1px solid var(--color-border-soft);
}

.settings-section > :deep(.n-alert) {
  border-radius: 0;
}

.settings-loading {
  display: grid;
  min-height: 240px;
  place-items: center;
}

.settings-form {
  min-width: 0;
}

.setting-row {
  display: grid;
  grid-template-columns: 38px minmax(0, 1fr) auto;
  min-height: 76px;
  align-items: center;
  gap: 12px;
  padding: 14px 20px;
  border-bottom: 1px solid var(--color-border-soft);
}

.setting-icon {
  display: grid;
  width: 36px;
  height: 36px;
  place-items: center;
  border-radius: 6px;
  background: var(--color-accent-muted);
  color: var(--color-accent);
}

.setting-copy {
  min-width: 0;
}

.setting-copy strong,
.setting-copy span {
  display: block;
  overflow-wrap: anywhere;
}

.setting-copy strong {
  color: var(--color-text-strong);
  font-size: 13px;
}

.setting-copy span {
  margin-top: 4px;
  color: var(--color-text-muted);
  font-size: 11px;
  line-height: 1.5;
}

.muted {
  opacity: 0.58;
}

.window-fields {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr)) minmax(180px, 1.25fr);
  gap: 14px;
  padding: 18px 20px 4px 70px;
  transition: opacity 160ms ease;
}

.window-fields :deep(.n-time-picker),
.window-fields :deep(.n-select) {
  width: 100%;
  min-width: 0;
}

.retention-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 150px;
  align-items: center;
  gap: 20px;
  padding: 14px 20px 18px 70px;
  border-top: 1px solid var(--color-border-soft);
}

.retention-row :deep(.n-input-number) {
  width: 100%;
}

.save-error {
  margin-top: 8px;
}

.settings-actions {
  display: flex;
  min-height: 68px;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  border-top: 1px solid var(--color-border-soft);
  background: var(--color-surface-muted);
  padding: 12px 20px;
}

@media (max-width: 680px) {
  .settings-heading,
  .setting-row,
  .settings-actions {
    padding-right: 14px;
    padding-left: 14px;
  }

  .window-fields {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    padding: 16px 14px 2px;
  }


  .retention-row {
    padding-right: 14px;
    padding-left: 14px;
  }

  .window-fields :deep(.n-form-item:last-child) {
    grid-column: 1 / -1;
  }
}

@media (max-width: 420px) {
  .window-fields {
    grid-template-columns: minmax(0, 1fr);
  }

  .window-fields :deep(.n-form-item:last-child) {
    grid-column: auto;
  }

  .settings-actions > :deep(.n-button) {
    min-width: 0;
    flex: 1;
  }


  .retention-row {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
