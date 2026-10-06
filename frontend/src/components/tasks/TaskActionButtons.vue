<script setup lang="ts">
import { CircleX, Pause, Play } from 'lucide-vue-next'

import type { StudyTask, TaskAction } from '@/api/types'
import IconAction from '@/components/ui/IconAction.vue'
import { canPauseTask, canResumeTask, isTerminalTask } from '@/domain/tasks'

defineProps<{
  task: Pick<StudyTask, 'status'>
  pending: TaskAction | null
}>()

defineEmits<{ action: [action: TaskAction] }>()
</script>

<template>
  <div v-if="!isTerminalTask(task)" class="cell-actions">
    <IconAction
      v-if="canPauseTask(task)"
      label="暂停任务"
      :icon="Pause"
      :loading="pending === 'pause'"
      :disabled="pending !== null"
      @click="$emit('action', 'pause')"
    />
    <IconAction
      v-if="canResumeTask(task)"
      label="恢复任务"
      :icon="Play"
      :loading="pending === 'resume'"
      :disabled="pending !== null"
      @click="$emit('action', 'resume')"
    />
    <IconAction
      label="取消任务"
      :icon="CircleX"
      danger
      :loading="pending === 'cancel'"
      :disabled="pending !== null"
      @click="$emit('action', 'cancel')"
    />
  </div>
  <span v-else class="cell-muted">—</span>
</template>
