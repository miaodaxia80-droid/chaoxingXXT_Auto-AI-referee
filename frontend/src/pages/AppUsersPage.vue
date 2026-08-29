<script setup lang="ts">
import { KeyRound, Plus, UsersRound } from 'lucide-vue-next'
import {
  NAlert,
  NButton,
  NDataTable,
  NForm,
  NFormItem,
  NInput,
  NInputNumber,
  NModal,
  NSwitch,
  NTag,
  useDialog,
  useMessage,
} from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import { computed, h, ref } from 'vue'
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'

import { ApiError, apiRequest } from '@/api/client'
import type { AppUserAdmin, CreateAppUserInput, UpdateAppUserInput } from '@/api/types'

const message = useMessage()
const dialog = useDialog()
const queryClient = useQueryClient()

const usersQuery = useQuery({
  queryKey: ['app-users'],
  queryFn: () => apiRequest<AppUserAdmin[]>('/app-users'),
})

function formatDateTime(value: string | null): string {
  if (!value) return '—'
  const date = new Date(value)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

function planText(user: AppUserAdmin): string {
  if (!user.plan_expires_at) return '未激活'
  const expires = new Date(user.plan_expires_at)
  return expires.getTime() > Date.now() ? formatDateTime(user.plan_expires_at) : '已过期'
}

// --- 创建用户 ---
const showCreate = ref(false)
const createForm = ref({ username: '', password: '', nickname: '' })
const createMutation = useMutation({
  mutationFn: (input: CreateAppUserInput) =>
    apiRequest<AppUserAdmin>('/app-users', { method: 'POST', body: JSON.stringify(input) }),
  onSuccess: (user) => {
    message.success(`用户 ${user.username ?? user.nickname} 已创建`)
    showCreate.value = false
    createForm.value = { username: '', password: '', nickname: '' }
    void queryClient.invalidateQueries({ queryKey: ['app-users'] })
  },
  onError: (error) =>
    message.error(error instanceof ApiError ? error.message : '创建失败，请稍后重试'),
})

function submitCreate(): void {
  const username = createForm.value.username.trim()
  const password = createForm.value.password
  if (username.length < 3) {
    message.warning('用户名至少 3 位（字母、数字、_.-）')
    return
  }
  if (password.length < 8) {
    message.warning('密码至少 8 位')
    return
  }
  createMutation.mutate({
    username,
    password,
    nickname: createForm.value.nickname.trim(),
  })
}

// --- 编辑用户 ---
const showEdit = ref(false)
const editing = ref<AppUserAdmin | null>(null)
const editForm = ref({
  nickname: '',
  newPassword: '',
  planExtendDays: 0,
  creditsAdd: 0,
})

const editMutation = useMutation({
  mutationFn: ({ id, input }: { id: number; input: UpdateAppUserInput }) =>
    apiRequest<AppUserAdmin>(`/app-users/${id}`, { method: 'PATCH', body: JSON.stringify(input) }),
  onSuccess: () => {
    message.success('已保存')
    showEdit.value = false
    void queryClient.invalidateQueries({ queryKey: ['app-users'] })
  },
  onError: (error) =>
    message.error(error instanceof ApiError ? error.message : '保存失败，请稍后重试'),
})

function openEdit(user: AppUserAdmin): void {
  editing.value = user
  editForm.value = { nickname: user.nickname, newPassword: '', planExtendDays: 0, creditsAdd: 0 }
  showEdit.value = true
}

function submitEdit(): void {
  if (!editing.value) return
  const input: UpdateAppUserInput = {}
  const nickname = editForm.value.nickname.trim()
  if (nickname && nickname !== editing.value.nickname) input.nickname = nickname
  if (editForm.value.newPassword) {
    if (editForm.value.newPassword.length < 8) {
      message.warning('新密码至少 8 位')
      return
    }
    input.password = editForm.value.newPassword
  }
  if (editForm.value.planExtendDays > 0) input.plan_extend_days = editForm.value.planExtendDays
  if (editForm.value.creditsAdd !== 0) input.task_credits_add = editForm.value.creditsAdd
  if (Object.keys(input).length === 0) {
    message.info('没有需要保存的修改')
    return
  }
  editMutation.mutate({ id: editing.value.id, input })
}

// --- 启停 ---
const toggleMutation = useMutation({
  mutationFn: ({ id, disabled }: { id: number; disabled: boolean }) =>
    apiRequest<AppUserAdmin>(`/app-users/${id}`, {
      method: 'PATCH',
      body: JSON.stringify({ disabled }),
    }),
  onSuccess: (user) => {
    message.success(user.disabled ? '已禁用（该用户所有会话已吊销）' : '已启用')
    void queryClient.invalidateQueries({ queryKey: ['app-users'] })
  },
  onError: (error) =>
    message.error(error instanceof ApiError ? error.message : '操作失败，请稍后重试'),
})

function requestToggle(user: AppUserAdmin): void {
  if (!user.disabled) {
    dialog.warning({
      title: '禁用用户',
      content: `禁用后 ${user.nickname} 将立即退出登录且无法再登录。确认禁用？`,
      positiveText: '禁用',
      negativeText: '取消',
      positiveButtonProps: { type: 'error' },
      onPositiveClick: () => toggleMutation.mutate({ id: user.id, disabled: true }),
    })
    return
  }
  toggleMutation.mutate({ id: user.id, disabled: false })
}

const columns = computed<DataTableColumns<AppUserAdmin>>(() => [
  { title: 'ID', key: 'id', width: 60 },
  {
    title: '用户',
    key: 'username',
    render: (user) =>
      h('div', { style: 'display:flex;flex-direction:column;gap:2px' }, [
        h('strong', null, user.nickname),
        h('span', { style: 'font-size:12px;opacity:.6' }, user.username ?? user.openid),
      ]),
  },
  {
    title: '状态',
    key: 'disabled',
    width: 90,
    render: (user) =>
      h(NSwitch, {
        value: !user.disabled,
        size: 'small',
        loading: toggleMutation.isPending.value,
        'onUpdate:value': () => requestToggle(user),
      }),
  },
  {
    title: '时间卡到期',
    key: 'plan_expires_at',
    width: 170,
    render: (user) => planText(user),
  },
  {
    title: '剩余次数',
    key: 'task_credits',
    width: 90,
    render: (user) => h('span', { style: 'font-variant-numeric:tabular-nums' }, String(user.task_credits)),
  },
  { title: '账号/任务', key: 'usage', width: 100, render: (user) => `${user.account_count} / ${user.active_task_count}` },
  {
    title: '注册时间',
    key: 'created_at',
    width: 150,
    render: (user) => formatDateTime(user.created_at),
  },
  {
    title: '',
    key: 'actions',
    width: 70,
    render: (user) =>
      h(
        NButton,
        { size: 'small', quaternary: true, onClick: () => openEdit(user) },
        { icon: () => h(KeyRound, { size: 15 }) },
      ),
  },
])

const isEmpty = computed(
  () => !usersQuery.isLoading.value && (usersQuery.data.value?.length ?? 0) === 0,
)
</script>

<template>
  <section class="content-section flush">
    <div class="section-heading padded">
      <div>
        <h2>用户管理</h2>
        <p>为普通用户创建账号、调整权益与配额</p>
      </div>
      <div class="heading-actions">
        <NButton type="primary" @click="showCreate = true">
          <template #icon><Plus :size="16" /></template>
          新建用户
        </NButton>
      </div>
    </div>

    <NAlert v-if="usersQuery.error.value" type="error" :bordered="false" class="padded-alert">
      {{ usersQuery.error.value instanceof ApiError ? usersQuery.error.value.message : '加载失败' }}
    </NAlert>

    <div v-if="isEmpty" class="empty-state">
      <UsersRound :size="28" />
      <strong>还没有普通用户</strong>
      <span>点击右上角「新建用户」创建第一个账号</span>
    </div>

    <NDataTable
      v-else
      :columns="columns"
      :data="usersQuery.data.value ?? []"
      :loading="usersQuery.isLoading.value"
      :bordered="false"
      :row-key="(row: AppUserAdmin) => row.id"
    />

    <NModal v-model:show="showCreate" preset="card" title="新建用户" class="form-modal" @after-leave="createForm = { username: '', password: '', nickname: '' }">
      <NForm label-placement="top">
        <NFormItem label="用户名（3-80 位，字母数字 _. -）">
          <NInput v-model:value="createForm.username" placeholder="student01" />
        </NFormItem>
        <NFormItem label="初始密码（至少 8 位）">
          <NInput v-model:value="createForm.password" type="password" show-password-on="click" placeholder="8-256 位可见字符" />
        </NFormItem>
        <NFormItem label="昵称（可选，默认同用户名）">
          <NInput v-model:value="createForm.nickname" placeholder="张同学" />
        </NFormItem>
        <div class="modal-actions">
          <NButton @click="showCreate = false">取消</NButton>
          <NButton type="primary" :loading="createMutation.isPending.value" @click="submitCreate">
            创建
          </NButton>
        </div>
      </NForm>
    </NModal>

    <NModal v-model:show="showEdit" preset="card" title="编辑用户" class="form-modal" @after-leave="editing = null">
      <NForm label-placement="top">
        <NFormItem label="昵称">
          <NInput v-model:value="editForm.nickname" />
        </NFormItem>
        <NFormItem label="重置密码（可选，重置后该用户所有会话失效）">
          <NInput v-model:value="editForm.newPassword" type="password" show-password-on="click" placeholder="留空表示不修改" />
        </NFormItem>
        <div class="edit-grid">
          <NFormItem label="时间卡延长（天）">
            <NInputNumber v-model:value="editForm.planExtendDays" :min="0" :max="3650" style="width: 100%" />
          </NFormItem>
          <NFormItem label="次数增减（负数为扣减）">
            <NInputNumber v-model:value="editForm.creditsAdd" :min="-100000" :max="100000" style="width: 100%" />
          </NFormItem>
        </div>
        <div v-if="editing" class="edit-summary">
          <NTag size="small" :bordered="false">当前到期：{{ planText(editing) }}</NTag>
          <NTag size="small" :bordered="false">当前次数：{{ editing.task_credits }}</NTag>
        </div>
        <div class="modal-actions">
          <NButton @click="showEdit = false">取消</NButton>
          <NButton type="primary" :loading="editMutation.isPending.value" @click="submitEdit">
            保存
          </NButton>
        </div>
      </NForm>
    </NModal>
  </section>
</template>

<style scoped>
.padded-alert {
  margin: 0 16px;
}

.edit-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}

.edit-summary {
  display: flex;
  gap: 8px;
  margin-bottom: 8px;
}

.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 8px;
}
</style>
