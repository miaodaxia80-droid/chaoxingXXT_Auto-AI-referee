<script setup lang="ts">
import { Bell, Clock3, Sparkles } from 'lucide-vue-next'
import { useDialog } from 'naive-ui'
import { computed, onBeforeUnmount, onMounted, reactive, watch } from 'vue'
import type { Component } from 'vue'
import { onBeforeRouteLeave, useRoute, useRouter } from 'vue-router'

import AnswerIntegrationSettings from '@/components/settings/AnswerIntegrationSettings.vue'
import NotificationIntegrationSettings from '@/components/settings/NotificationIntegrationSettings.vue'
import ScheduleSettings from '@/components/settings/ScheduleSettings.vue'

type SectionId = 'schedule' | 'answer' | 'notifications'

const SECTIONS: { id: SectionId; label: string; description: string; icon: Component }[] = [
  {
    id: 'schedule',
    label: '运行调度',
    description: '任务领取、时间窗与保留',
    icon: Clock3,
  },
  {
    id: 'answer',
    label: '答案服务',
    description: '题库与 AI 答题',
    icon: Sparkles,
  },
  {
    id: 'notifications',
    label: '任务通知',
    description: '状态推送渠道',
    icon: Bell,
  },
]

function isSectionId(value: unknown): value is SectionId {
  return SECTIONS.some((section) => section.id === value)
}

const route = useRoute()
const router = useRouter()
const dialog = useDialog()

const active = computed<SectionId>({
  get: () => (isSectionId(route.query.tab) ? route.query.tab : 'schedule'),
  set: (value) => {
    const query = { ...route.query }
    if (value === 'schedule') delete query.tab
    else query.tab = value
    void router.replace({ name: 'settings', query })
  },
})

// 分区首次激活时才挂载，之后用 v-show 保留表单状态
const visited = reactive<Record<SectionId, boolean>>({
  schedule: false,
  answer: false,
  notifications: false,
})
watch(
  active,
  (id) => {
    visited[id] = true
  },
  { immediate: true },
)

const dirty = reactive<Record<SectionId, boolean>>({
  schedule: false,
  answer: false,
  notifications: false,
})
const anyDirty = computed(() => SECTIONS.some((section) => dirty[section.id]))

function confirmDiscard(): Promise<boolean> {
  return new Promise((resolve) => {
    dialog.warning({
      title: '放弃未保存的更改？',
      content: '设置中有尚未保存的更改，离开后将会丢失。',
      positiveText: '放弃并离开',
      negativeText: '继续编辑',
      onPositiveClick: () => resolve(true),
      onNegativeClick: () => resolve(false),
      onClose: () => resolve(false),
      onMaskClick: () => resolve(false),
    })
  })
}

onBeforeRouteLeave(() => {
  if (!anyDirty.value) return true
  return confirmDiscard()
})

function onBeforeUnload(event: BeforeUnloadEvent) {
  if (anyDirty.value) event.preventDefault()
}

onMounted(() => window.addEventListener('beforeunload', onBeforeUnload))
onBeforeUnmount(() => window.removeEventListener('beforeunload', onBeforeUnload))
</script>

<template>
  <div class="settings-page">
    <nav class="settings-nav" aria-label="设置分区">
      <button
        v-for="section in SECTIONS"
        :key="section.id"
        type="button"
        :class="{ active: active === section.id }"
        :aria-current="active === section.id ? 'true' : undefined"
        @click="active = section.id"
      >
        <component :is="section.icon" :size="17" />
        <span class="settings-nav-text">
          <strong>{{ section.label }}</strong>
          <small>{{ section.description }}</small>
        </span>
        <i v-if="dirty[section.id]" class="dirty-dot" aria-label="有未保存的更改" />
      </button>
    </nav>

    <div class="segmented settings-tabs" role="tablist" aria-label="设置分区">
      <button
        v-for="section in SECTIONS"
        :key="section.id"
        type="button"
        role="tab"
        :aria-selected="active === section.id"
        @click="active = section.id"
      >
        {{ section.label }}
        <i v-if="dirty[section.id]" class="dirty-dot" aria-label="有未保存的更改" />
      </button>
    </div>

    <div class="settings-content">
      <ScheduleSettings
        v-if="visited.schedule"
        v-show="active === 'schedule'"
        @dirty-change="dirty.schedule = $event"
      />
      <AnswerIntegrationSettings
        v-if="visited.answer"
        v-show="active === 'answer'"
        @dirty-change="dirty.answer = $event"
      />
      <NotificationIntegrationSettings
        v-if="visited.notifications"
        v-show="active === 'notifications'"
        @dirty-change="dirty.notifications = $event"
      />
    </div>
  </div>
</template>

<style scoped>
.settings-page {
  display: grid;
  grid-template-columns: 216px minmax(0, 1fr);
  width: 100%;
  max-width: 1080px;
  align-items: start;
  gap: var(--section-gap);
  margin-inline: auto;
}

.settings-nav {
  position: sticky;
  top: calc(64px + var(--gutter));
  display: grid;
  gap: 2px;
}

.settings-nav button {
  display: flex;
  align-items: center;
  gap: 10px;
  border: 0;
  border-radius: var(--radius-md);
  background: transparent;
  color: var(--color-text-muted);
  cursor: pointer;
  padding: 9px 12px;
  text-align: left;
  transition: background-color 120ms ease, color 120ms ease;
}

.settings-nav button:hover {
  background: var(--color-surface-subtle);
  color: var(--color-text);
}

.settings-nav button:focus-visible {
  outline: 2px solid var(--color-accent);
  outline-offset: -2px;
}

.settings-nav button.active {
  background: var(--color-accent-soft);
  color: var(--color-accent-strong);
}

.settings-nav button > svg {
  flex: 0 0 auto;
  margin-top: 2px;
  align-self: flex-start;
}

.settings-nav-text {
  min-width: 0;
  flex: 1;
}

.settings-nav-text strong,
.settings-nav-text small {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.settings-nav-text strong {
  font-size: var(--fs-sm);
  font-weight: 600;
}

.settings-nav-text small {
  margin-top: 1px;
  font-size: var(--fs-xs);
  opacity: 0.75;
}

.dirty-dot {
  width: 7px;
  height: 7px;
  flex: 0 0 auto;
  border-radius: 50%;
  background: var(--color-warning-strong);
}

.settings-tabs {
  display: none;
}

.settings-content {
  min-width: 0;
}

@media (max-width: 860px) {
  .settings-page {
    grid-template-columns: minmax(0, 1fr);
  }

  .settings-nav {
    display: none;
  }

  .settings-tabs {
    display: inline-flex;
  }
}
</style>
