<script setup lang="ts">
import { NButton, NTooltip } from 'naive-ui'
import type { Component } from 'vue'

withDefaults(
  defineProps<{
    label: string
    icon: Component
    danger?: boolean
    loading?: boolean
    disabled?: boolean
    size?: 'tiny' | 'small' | 'medium'
  }>(),
  { danger: false, loading: false, disabled: false, size: 'small' },
)

defineEmits<{ click: [event: MouseEvent] }>()
</script>

<template>
  <NTooltip trigger="hover">
    <template #trigger>
      <NButton
        quaternary
        circle
        :size="size"
        :type="danger ? 'error' : 'default'"
        :loading="loading"
        :disabled="disabled"
        :aria-label="label"
        @click="$emit('click', $event)"
      >
        <template #icon><component :is="icon" :size="16" /></template>
      </NButton>
    </template>
    {{ label }}
  </NTooltip>
</template>
