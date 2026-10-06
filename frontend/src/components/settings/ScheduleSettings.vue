<script setup lang="ts">
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import { Archive, Clock3, RefreshCw, Save, ServerCog } from 'lucide-vue-next'
import {
  NAlert,
  NButton,
  NForm,
  NFormItem,
  NInputNumber,
  NSelect,
  NSkeleton,
  NSwitch,
  NTimePicker,
  useMessage,
} from 'naive-ui'
import { computed, reactive, ref, watch } from 'vue'

import { ApiError, getSystemSettings, updateSystemSettings } from '@/api/client'
import type { SystemSettings, UpdateSystemSettingsInput } from '@/api/types'
import IconAction from '@/components/ui/IconAction.vue'

const emit = defineEmits<{ 'dirty-change': [dirty: boolean] }>()

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

const HOURS = Array.from({ length: 24 }, (_, hour) => hour)
const MINUTES = Array.from({ length: 60 }, (_, minute) => minute)

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
    void queryClient.invalidateQueries({ queryKey: ['operations-health'] })
    message.success('运行调度已保存')
  },
})

const hasChanges = computed(() => {
  if (!saved.value) return false
  return (Object.keys(saved.value) as (keyof UpdateSystemSettingsInput)[]).some(
    (key) => form[key] !== saved.value?.[key],
  )
})

watch(
  settings.data,
  (value) => {
    if (value && !hasChanges.value) applySettings(value)
  },
  { immediate: true },
)

watch(hasChanges, (value) => emit('dirty-change', value), { immediate: true })

const windowDisabled = computed(() => !form.worker_enabled || !form.run_window_enabled)
const windowSummary = computed(() => {
  if (!form.worker_enabled) return '调度已停止，不会领取新任务'
  if (!form.run_window_enabled || form.run_window_start === form.run_window_end) {
    return '当前全天运行'
  }
  const crossesMidnight = form.run_window_start > form.run_window_end
  return `仅在 ${form.run_window_start} – ${form.run_window_end}${crossesMidnight ? '（跨午夜）' : ''} 领取任务`
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

async function refresh() {
  const result = await settings.refetch()
  if (result.data) {
    applySettings(result.data)
    message.success('运行调度已刷新')
  }
}

function errorText(error: unknown): string {
  if (error instanceof ApiError) return error.message
  if (error instanceof Error) return error.message
  return error ? '请求失败，请稍后重试' : ''
}
</script>

<template>
  <section class="content-section flush settings-card">
    <div class="settings-intro">
      <p>控制后台何时领取任务、是否运行，以及活动记录保留多久。</p>
      <IconAction label="刷新设置" :icon="RefreshCw" :loading="settings.isFetching.value" @click="refresh" />
    </div>

    <NAlert v-if="settings.isError.value" type="error" :bordered="false">
      {{ loadError }}
    </NAlert>

    <div v-if="settings.isLoading.value" class="settings-loading">
      <NSkeleton text :repeat="5" />
    </div>

    <NForm v-else-if="saved" label-placement="top" @submit.prevent="submit">
      <div class="setting-row">
        <span class="setting-icon"><ServerCog :size="18" /></span>
        <div class="setting-copy">
          <strong>后台任务</strong>
          <span>关闭后停止领取任务，并在安全位置暂停正在执行的任务</span>
        </div>
        <NSwitch v-model:value="form.worker_enabled" aria-label="后台任务" />
      </div>

      <div class="setting-row" :class="{ muted: !form.worker_enabled }">
        <span class="setting-icon"><Clock3 :size="18" /></span>
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

      <div v-if="form.worker_enabled && form.run_window_enabled" class="setting-fields window-fields">
        <NFormItem label="开始时间">
          <NTimePicker
            :formatted-value="form.run_window_start"
            format="HH:mm"
            :hours="HOURS"
            :minutes="MINUTES"
            :disabled="windowDisabled"
            @update:formatted-value="updateStart"
          />
        </NFormItem>
        <NFormItem label="结束时间">
          <NTimePicker
            :formatted-value="form.run_window_end"
            format="HH:mm"
            :hours="HOURS"
            :minutes="MINUTES"
            :disabled="windowDisabled"
            @update:formatted-value="updateEnd"
          />
        </NFormItem>
        <NFormItem label="时区">
          <NSelect
            v-model:value="form.timezone"
            filterable
            :options="timezoneOptions"
            :disabled="windowDisabled"
          />
        </NFormItem>
      </div>

      <div class="setting-row">
        <span class="setting-icon"><Archive :size="18" /></span>
        <div class="setting-copy">
          <strong>活动记录保留</strong>
          <span>到期记录先归档，再经过同样时长后从数据库清理</span>
        </div>
        <NInputNumber
          v-model:value="form.event_retention_days"
          class="retention-input"
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

      <div class="sticky-actions">
        <span v-if="hasChanges" class="dirty-hint">有未保存的更改</span>
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
</template>

<style scoped>
.window-fields {
  grid-template-columns: repeat(2, minmax(0, 1fr)) minmax(180px, 1.25fr);
}

.window-fields :deep(.n-time-picker),
.window-fields :deep(.n-select) {
  width: 100%;
  min-width: 0;
}

.retention-input {
  width: 150px;
}

.save-error {
  margin: 12px 20px 0;
}

@media (max-width: 680px) {
  .window-fields {
    grid-template-columns: minmax(0, 1fr);
  }

  .retention-input {
    grid-column: 2 / -1;
    width: 100%;
  }
}
</style>
