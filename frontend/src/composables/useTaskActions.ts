import { useQueryClient } from '@tanstack/vue-query'
import { useDialog, useMessage } from 'naive-ui'
import { ref } from 'vue'

import { ApiError, apiRequest } from '@/api/client'
import type { StudyTask, StudyTaskDetail, TaskAction } from '@/api/types'

export const TASK_ACTION_SUCCESS: Record<TaskAction, string> = {
  pause: '已请求暂停任务',
  resume: '任务已恢复排队',
  cancel: '已请求取消任务',
}

export function useTaskActions() {
  const message = useMessage()
  const dialog = useDialog()
  const queryClient = useQueryClient()
  const pending = ref(new Map<string, TaskAction>())

  function pendingAction(taskId: string): TaskAction | null {
    return pending.value.get(taskId) ?? null
  }

  function setPending(taskId: string, action: TaskAction | null): void {
    const next = new Map(pending.value)
    if (action) next.set(taskId, action)
    else next.delete(taskId)
    pending.value = next
  }

  async function execute(task: Pick<StudyTask, 'id'>, action: TaskAction): Promise<boolean> {
    if (pendingAction(task.id)) return false
    setPending(task.id, action)
    try {
      await apiRequest<StudyTaskDetail>(`/tasks/${encodeURIComponent(task.id)}/${action}`, {
        method: 'POST',
      })
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['tasks'] }),
        queryClient.invalidateQueries({ queryKey: ['task', task.id] }),
      ])
      message.success(TASK_ACTION_SUCCESS[action])
      return true
    } catch (error) {
      message.error(error instanceof ApiError ? error.message : '任务操作失败')
      return false
    } finally {
      setPending(task.id, null)
    }
  }

  function run(task: Pick<StudyTask, 'id' | 'course_title'>, action: TaskAction): void {
    if (action !== 'cancel') {
      void execute(task, action)
      return
    }
    if (pendingAction(task.id)) return
    dialog.warning({
      title: '取消任务',
      content: `确认取消“${task.course_title}”的学习任务？正在执行的章节会在安全点停止。`,
      positiveText: '取消任务',
      negativeText: '返回',
      positiveButtonProps: { type: 'error' },
      async onPositiveClick() {
        return (await execute(task, 'cancel')) || false
      },
    })
  }

  return { pendingAction, execute, run }
}
