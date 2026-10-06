<script setup lang="ts">
import type { Component } from 'vue'

defineProps<{
  icon?: Component
  title: string
  description?: string
  compact?: boolean
}>()
</script>

<template>
  <div class="empty-state" :class="{ compact }">
    <span v-if="icon" class="empty-state-icon" aria-hidden="true">
      <component :is="icon" :size="20" />
    </span>
    <strong>{{ title }}</strong>
    <p v-if="description">{{ description }}</p>
    <div v-if="$slots.default" class="empty-state-actions"><slot /></div>
  </div>
</template>
