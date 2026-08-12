<script setup lang="ts">
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import {
  Download,
  FileSpreadsheet,
  KeyRound,
  Pencil,
  Plus,
  RefreshCw,
  Save,
  ShieldCheck,
  Trash2,
  Upload,
} from 'lucide-vue-next'
import {
  NAlert,
  NButton,
  NCheckbox,
  NDataTable,
  NForm,
  NFormItem,
  NInput,
  NInputNumber,
  NModal,
  NRadioButton,
  NRadioGroup,
  NSelect,
  NSwitch,
  NTag,
  NTooltip,
  useDialog,
  useMessage,
} from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import { computed, h, reactive, ref, watch } from 'vue'
import type { Component } from 'vue'

import { ApiError, apiRequest, getAnswerIntegration } from '@/api/client'
import AnswerProfileFields from '@/components/settings/AnswerProfileFields.vue'
import {
  cloneAnswerProfile,
  completeAnswerProfile,
  mergeAnswerProfile,
  normalizedAnswerProfile,
} from '@/domain/answerProfiles'
import type {
  Account,
  AccountImportResult,
  AccountImportRow,
  AnswerProfile,
  CreateAccountInput,
  UpdateAccountInput,
} from '@/api/types'

type AnswerProfileMode = 'inherit' | 'override'

interface EditAccountForm {
  remark: string
  username: string
  password: string
  cookies: string
  user_agent: string
  speed: number
  chapter_concurrency: number
  unopened_policy: 'retry' | 'skip'
  enabled: boolean
  clear_password: boolean
  clear_cookies: boolean
  answer_profile_mode: AnswerProfileMode
  answer_profile: AnswerProfile
}

const queryClient = useQueryClient()
const message = useMessage()
const dialog = useDialog()
const showCreate = ref(false)
const showEdit = ref(false)
const showImport = ref(false)
const editingAccount = ref<Account | null>(null)
const importFileInput = ref<HTMLInputElement | null>(null)
const selectedImportFile = ref<File | null>(null)
const importResult = ref<AccountImportResult | null>(null)
const initialAnswerProfileMode = ref<AnswerProfileMode>('inherit')
const initialAnswerProfile = ref<AnswerProfile>(completeAnswerProfile())
const answerProfileTouched = ref(false)
const createForm = reactive<CreateAccountInput>({
  username: '',
  password: '',
  cookies: '',
  remark: '',
  user_agent: '',
  speed: 1,
  chapter_concurrency: 1,
  unopened_policy: 'retry',
})
const editForm = reactive<EditAccountForm>({
  remark: '',
  username: '',
  password: '',
  cookies: '',
  user_agent: '',
  speed: 1,
  chapter_concurrency: 1,
  unopened_policy: 'retry' as 'retry' | 'skip',
  enabled: true,
  clear_password: false,
  clear_cookies: false,
  answer_profile_mode: 'inherit',
  answer_profile: completeAnswerProfile(),
})

const accounts = useQuery({
  queryKey: ['accounts'],
  queryFn: () => apiRequest<Account[]>('/accounts'),
})

const answerIntegration = useQuery({
  queryKey: ['integration-settings', 'answer'],
  queryFn: getAnswerIntegration,
})

const globalAnswerProfile = computed(() =>
  completeAnswerProfile(answerIntegration.data.value?.profile),
)
const supportsModelSelection = computed(() => {
  const provider = answerIntegration.data.value?.provider
  return provider === 'like' || provider === 'openai_compatible' || provider === 'siliconflow'
})

watch(answerIntegration.data, () => {
  const account = editingAccount.value
  if (!showEdit.value || !account || answerProfileTouched.value) return
  const effective = mergeAnswerProfile(globalAnswerProfile.value, account.answer_profile_override)
  editForm.answer_profile = cloneAnswerProfile(effective)
  initialAnswerProfile.value = cloneAnswerProfile(effective)
})

const createAccount = useMutation({
  mutationFn: (payload: CreateAccountInput) =>
    apiRequest<Account>('/accounts', { method: 'POST', body: JSON.stringify(payload) }),
  async onSuccess() {
    showCreate.value = false
    resetCreateForm()
    await queryClient.invalidateQueries({ queryKey: ['accounts'] })
    message.success('账号已添加')
  },
  onError(error) {
    message.error(error instanceof ApiError ? error.message : '添加失败')
  },
})

const editAccount = useMutation({
  mutationFn: ({ id, payload }: { id: number; payload: UpdateAccountInput }) =>
    apiRequest<Account>(`/accounts/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),
  async onSuccess() {
    showEdit.value = false
    resetEditForm()
    await queryClient.invalidateQueries({ queryKey: ['accounts'] })
    message.success('账号设置已更新')
  },
  onError(error) {
    message.error(error instanceof ApiError ? error.message : '更新失败')
  },
})

const toggleAccount = useMutation({
  mutationFn: ({ id, enabled }: { id: number; enabled: boolean }) =>
    apiRequest<Account>(`/accounts/${id}`, {
      method: 'PATCH',
      body: JSON.stringify({ enabled } satisfies UpdateAccountInput),
    }),
  async onSuccess(account) {
    await queryClient.invalidateQueries({ queryKey: ['accounts'] })
    message.success(account.enabled ? '账号已启用' : '账号已停用')
  },
  onError(error) {
    message.error(error instanceof ApiError ? error.message : '状态更新失败')
  },
})

const deleteAccount = useMutation({
  mutationFn: (id: number) => apiRequest<void>(`/accounts/${id}`, { method: 'DELETE' }),
  async onSuccess() {
    await queryClient.invalidateQueries({ queryKey: ['accounts'] })
    message.success('账号已删除')
  },
  onError(error) {
    message.error(error instanceof ApiError ? error.message : '删除失败')
  },
})

const importAccounts = useMutation({
  async mutationFn(file: File) {
    return apiRequest<AccountImportResult>('/accounts/import', {
      method: 'POST',
      headers: { 'Content-Type': 'text/csv; charset=utf-8' },
      body: await file.text(),
    })
  },
  async onSuccess(result) {
    importResult.value = result
    await queryClient.invalidateQueries({ queryKey: ['accounts'] })
    if (result.invalid || result.duplicate) {
      message.warning(`已导入 ${result.created} 个账号，${result.invalid + result.duplicate} 行未导入`)
    } else {
      message.success(`已导入 ${result.created} 个账号`)
    }
  },
  onError(error) {
    message.error(error instanceof ApiError ? error.message : '导入失败')
  },
})

function iconButton(
  label: string,
  icon: Component,
  onClick: () => void,
  options?: { danger?: boolean; loading?: boolean; disabled?: boolean },
) {
  return h(
    NTooltip,
    null,
    {
      trigger: () =>
        h(
          NButton,
          {
            quaternary: true,
            circle: true,
            size: 'small',
            type: options?.danger ? 'error' : 'default',
            loading: options?.loading,
            disabled: options?.disabled,
            'aria-label': label,
            onClick,
          },
          { icon: () => h(icon, { size: 16 }) },
        ),
      default: () => label,
    },
  )
}

const columns: DataTableColumns<Account> = [
  {
    title: '账号',
    key: 'username_hint',
    render: (row) =>
      h('div', { class: 'account-cell' }, [
        h('strong', row.remark || row.username_hint),
        row.remark ? h('span', row.username_hint) : null,
      ]),
  },
  {
    title: '凭据',
    key: 'credentials',
    render: (row) =>
      h('div', { class: 'tag-row' }, [
        row.has_password ? h(NTag, { size: 'small' }, { default: () => '密码' }) : null,
        row.has_cookies ? h(NTag, { size: 'small' }, { default: () => 'Cookie' }) : null,
      ]),
  },
  { title: '倍速', key: 'speed', render: (row) => `${row.speed.toFixed(1)}x` },
  { title: '章节并发', key: 'chapter_concurrency' },
  {
    title: '状态',
    key: 'enabled',
    width: 112,
    render: (row) => {
      const loading =
        toggleAccount.isPending.value && toggleAccount.variables.value?.id === row.id
      return h('div', { class: 'account-status-control' }, [
        h(NSwitch, {
          value: row.enabled,
          size: 'small',
          loading,
          disabled: loading,
          'aria-label': row.enabled ? '停用账号' : '启用账号',
          onUpdateValue: (enabled: boolean) => toggleAccount.mutate({ id: row.id, enabled }),
        }),
        h('span', row.enabled ? '启用' : '停用'),
      ])
    },
  },
  {
    title: '操作',
    key: 'actions',
    width: 96,
    render: (row) => {
      const deleting =
        deleteAccount.isPending.value && deleteAccount.variables.value === row.id
      return h('div', { class: 'account-actions' }, [
        iconButton('编辑账号', Pencil, () => openEdit(row)),
        iconButton('删除账号', Trash2, () => requestDelete(row), {
          danger: true,
          loading: deleting,
          disabled: deleting,
        }),
      ])
    },
  },
]

const importColumns: DataTableColumns<AccountImportRow> = [
  { title: '行', key: 'line', width: 64 },
  {
    title: '账号',
    key: 'username_hint',
    render: (row) => row.username_hint ?? '-',
  },
  {
    title: '结果',
    key: 'status',
    width: 112,
    render: (row) => {
      const meta = {
        created: { label: '已导入', type: 'success' as const },
        duplicate: { label: '已存在', type: 'warning' as const },
        invalid: { label: '格式无效', type: 'error' as const },
      }[row.status]
      return h(NTag, { type: meta.type, size: 'small', bordered: false }, { default: () => meta.label })
    },
  },
]

function resetCreateForm() {
  Object.assign(createForm, {
    username: '',
    password: '',
    cookies: '',
    remark: '',
    user_agent: '',
    speed: 1,
    chapter_concurrency: 1,
    unopened_policy: 'retry',
  })
}

function resetEditForm() {
  editingAccount.value = null
  Object.assign(editForm, {
    remark: '',
    username: '',
    password: '',
    cookies: '',
    user_agent: '',
    speed: 1,
    chapter_concurrency: 1,
    unopened_policy: 'retry',
    enabled: true,
    clear_password: false,
    clear_cookies: false,
    answer_profile_mode: 'inherit',
    answer_profile: completeAnswerProfile(),
  })
  initialAnswerProfileMode.value = 'inherit'
  initialAnswerProfile.value = completeAnswerProfile()
  answerProfileTouched.value = false
}

function closeCreate() {
  showCreate.value = false
  resetCreateForm()
}

function closeEdit() {
  showEdit.value = false
  resetEditForm()
}

function resetImport() {
  selectedImportFile.value = null
  importResult.value = null
  if (importFileInput.value) importFileInput.value.value = ''
}

function closeImport() {
  if (importAccounts.isPending.value) return
  showImport.value = false
  resetImport()
}

function chooseImportFile() {
  importFileInput.value?.click()
}

function selectImportFile(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0] ?? null
  if (!file) return
  if (file.size > 1_048_576) {
    message.error('CSV 文件不能超过 1 MiB')
    input.value = ''
    return
  }
  selectedImportFile.value = file
  importResult.value = null
}

function submitImport() {
  if (selectedImportFile.value) importAccounts.mutate(selectedImportFile.value)
}

function downloadImportTemplate() {
  const header = [
    'username',
    'password',
    'cookies',
    'remark',
    'user_agent',
    'speed',
    'chapter_concurrency',
    'unopened_policy',
  ].join(',')
  const url = URL.createObjectURL(new Blob([`\ufeff${header}\r\n`], { type: 'text/csv;charset=utf-8' }))
  const link = document.createElement('a')
  link.href = url
  link.download = 'chaoxing-accounts-template.csv'
  link.click()
  URL.revokeObjectURL(url)
}

function openEdit(account: Account) {
  editingAccount.value = account
  const answerProfileMode: AnswerProfileMode = account.answer_profile_override
    ? 'override'
    : 'inherit'
  const effectiveAnswerProfile = mergeAnswerProfile(
    globalAnswerProfile.value,
    account.answer_profile_override,
  )
  Object.assign(editForm, {
    remark: account.remark,
    username: '',
    password: '',
    cookies: '',
    user_agent: account.user_agent,
    speed: account.speed,
    chapter_concurrency: account.chapter_concurrency,
    unopened_policy: account.unopened_policy,
    enabled: account.enabled,
    clear_password: false,
    clear_cookies: false,
    answer_profile_mode: answerProfileMode,
    answer_profile: cloneAnswerProfile(effectiveAnswerProfile),
  })
  initialAnswerProfileMode.value = answerProfileMode
  initialAnswerProfile.value = cloneAnswerProfile(effectiveAnswerProfile)
  answerProfileTouched.value = false
  showEdit.value = true
}

function updateAnswerProfileMode(mode: AnswerProfileMode) {
  if (mode === editForm.answer_profile_mode) return
  editForm.answer_profile_mode = mode
  answerProfileTouched.value = true
  if (mode === 'override' && initialAnswerProfileMode.value === 'inherit') {
    editForm.answer_profile = cloneAnswerProfile(globalAnswerProfile.value)
  }
}

function updateAccountAnswerProfile(profile: AnswerProfile) {
  editForm.answer_profile = profile
  answerProfileTouched.value = true
}

function submitCreate() {
  if (!createForm.username.trim()) {
    message.warning('请输入学习通账号')
    return
  }
  createAccount.mutate({ ...createForm })
}

function submitEdit() {
  const account = editingAccount.value
  if (!account) return

  const payload: UpdateAccountInput = {
    remark: editForm.remark.trim(),
    user_agent: editForm.user_agent.trim(),
    speed: editForm.speed,
    chapter_concurrency: editForm.chapter_concurrency,
    unopened_policy: editForm.unopened_policy,
    enabled: editForm.enabled,
    clear_password: editForm.clear_password,
    clear_cookies: editForm.clear_cookies,
  }
  const username = editForm.username.trim()
  if (username) payload.username = username
  if (editForm.password && !editForm.clear_password) payload.password = editForm.password
  const cookies = editForm.cookies.trim()
  if (cookies && !editForm.clear_cookies) payload.cookies = cookies

  const profileModeChanged = editForm.answer_profile_mode !== initialAnswerProfileMode.value
  const profileChanged = JSON.stringify(normalizedAnswerProfile(editForm.answer_profile))
    !== JSON.stringify(normalizedAnswerProfile(initialAnswerProfile.value))
  if (editForm.answer_profile_mode === 'inherit') {
    if (profileModeChanged) payload.clear_answer_profile_override = true
  } else if (profileModeChanged || profileChanged) {
    payload.answer_profile_override = normalizedAnswerProfile(editForm.answer_profile)
  }

  editAccount.mutate({ id: account.id, payload })
}

function requestDelete(account: Account) {
  dialog.warning({
    title: '删除账号',
    content: `确认删除 ${account.remark || account.username_hint}？此操作无法撤销。`,
    positiveText: '删除',
    negativeText: '取消',
    positiveButtonProps: { type: 'error' },
    async onPositiveClick() {
      try {
        await deleteAccount.mutateAsync(account.id)
      } catch {
        return false
      }
    },
  })
}
</script>

<template>
  <section class="content-section flush">
    <div class="section-heading padded">
      <div><h2>学习通账号</h2><p>凭据已加密保存，列表仅显示脱敏账号</p></div>
      <div class="heading-actions">
        <NButton quaternary circle title="刷新" @click="accounts.refetch()">
          <template #icon><RefreshCw /></template>
        </NButton>
        <NButton @click="showImport = true">
          <template #icon><Upload /></template>
          导入
        </NButton>
        <NButton type="primary" @click="showCreate = true">
          <template #icon><Plus /></template>
          添加账号
        </NButton>
      </div>
    </div>
    <NDataTable
      :columns="columns"
      :data="accounts.data.value ?? []"
      :loading="accounts.isLoading.value"
      :bordered="false"
      :row-key="(row: Account) => row.id"
    />
  </section>

  <NModal
    v-model:show="showImport"
    preset="card"
    title="批量导入账号"
    class="form-modal import-modal"
    :mask-closable="!importAccounts.isPending.value"
    @after-leave="resetImport"
  >
    <input
      ref="importFileInput"
      hidden
      type="file"
      accept=".csv,text/csv"
      @change="selectImportFile"
    />
    <div class="import-picker">
      <FileSpreadsheet :size="24" />
      <div>
        <strong>{{ selectedImportFile?.name ?? '尚未选择 CSV 文件' }}</strong>
        <span v-if="selectedImportFile">{{ Math.ceil(selectedImportFile.size / 1024) }} KiB</span>
        <span v-else>UTF-8，最多 500 个账号</span>
      </div>
      <NButton :disabled="importAccounts.isPending.value" @click="chooseImportFile">
        选择文件
      </NButton>
    </div>

    <div v-if="importResult" class="import-summary">
      <span><b>{{ importResult.created }}</b> 已导入</span>
      <span><b>{{ importResult.duplicate }}</b> 已存在</span>
      <span><b>{{ importResult.invalid }}</b> 无效</span>
    </div>
    <NDataTable
      v-if="importResult"
      class="import-result-table"
      :columns="importColumns"
      :data="importResult.results"
      :bordered="false"
      :max-height="260"
      :row-key="(row: AccountImportRow) => row.line"
    />

    <div class="modal-actions import-actions">
      <NButton quaternary @click="downloadImportTemplate">
        <template #icon><Download /></template>
        下载模板
      </NButton>
      <span class="action-spacer" />
      <NButton :disabled="importAccounts.isPending.value" @click="closeImport">关闭</NButton>
      <NButton
        type="primary"
        :disabled="!selectedImportFile || Boolean(importResult)"
        :loading="importAccounts.isPending.value"
        @click="submitImport"
      >
        <template #icon><Upload /></template>
        开始导入
      </NButton>
    </div>
  </NModal>

  <NModal
    v-model:show="showCreate"
    preset="card"
    title="添加学习通账号"
    class="form-modal"
    @after-leave="resetCreateForm"
  >
    <NForm :model="createForm" label-placement="top" autocomplete="off">
      <div class="form-grid two">
        <NFormItem label="账号">
          <NInput
            v-model:value="createForm.username"
            :input-props="{ name: 'cx-create-account', autocomplete: 'off' }"
            placeholder="手机号或登录账号"
          />
        </NFormItem>
        <NFormItem label="备注">
          <NInput
            v-model:value="createForm.remark"
            :input-props="{ name: 'cx-create-remark', autocomplete: 'off' }"
            placeholder="便于识别，可留空"
          />
        </NFormItem>
      </div>
      <NFormItem label="密码">
        <NInput
          v-model:value="createForm.password"
          type="password"
          show-password-on="click"
          :input-props="{ name: 'cx-create-password', autocomplete: 'new-password' }"
          placeholder="使用 Cookie 登录时可留空"
        />
      </NFormItem>
      <NFormItem label="Cookie">
        <NInput
          v-model:value="createForm.cookies"
          type="textarea"
          :autosize="{ minRows: 2, maxRows: 4 }"
          :input-props="{ name: 'cx-create-cookie', autocomplete: 'off' }"
          placeholder="可选，登录成功后会自动更新"
        />
      </NFormItem>
      <div class="form-grid three">
        <NFormItem label="视频倍速">
          <NInputNumber v-model:value="createForm.speed" :min="1" :max="2" :step="0.1" />
        </NFormItem>
        <NFormItem label="章节并发">
          <NInputNumber v-model:value="createForm.chapter_concurrency" :min="1" :max="8" />
        </NFormItem>
        <NFormItem label="未开放章节">
          <NSelect
            v-model:value="createForm.unopened_policy"
            :options="[
              { label: '稍后重试', value: 'retry' },
              { label: '跳过并标记', value: 'skip' },
            ]"
          />
        </NFormItem>
      </div>
      <div class="security-note">
        <ShieldCheck :size="18" />
        <span>账号、密码和 Cookie 分字段加密，普通接口无法读取原文。</span>
      </div>
      <div class="modal-actions">
        <NButton @click="closeCreate">取消</NButton>
        <NButton type="primary" :loading="createAccount.isPending.value" @click="submitCreate">
          <template #icon><KeyRound /></template>
          保存账号
        </NButton>
      </div>
    </NForm>
  </NModal>

  <NModal
    v-model:show="showEdit"
    preset="card"
    title="编辑学习通账号"
    class="form-modal account-edit-modal"
    style="width: min(760px, calc(100vw - 32px))"
    content-style="max-height: calc(100vh - 176px); overflow-y: auto"
    @after-leave="resetEditForm"
  >
    <NForm :model="editForm" label-placement="top" autocomplete="off">
      <div class="form-grid two">
        <NFormItem label="当前账号">
          <NInput :value="editingAccount?.username_hint ?? ''" disabled />
        </NFormItem>
        <NFormItem label="备注">
          <NInput
            v-model:value="editForm.remark"
            :input-props="{ name: 'cx-edit-remark', autocomplete: 'off' }"
            placeholder="便于识别，可留空"
          />
        </NFormItem>
      </div>
      <NFormItem label="新账号">
        <NInput
          v-model:value="editForm.username"
          :input-props="{ name: 'cx-edit-account', autocomplete: 'off' }"
          placeholder="留空保持当前账号"
        />
      </NFormItem>
      <div class="form-grid two">
        <NFormItem label="新密码">
          <div class="secret-edit-field">
            <NInput
              v-model:value="editForm.password"
              type="password"
              show-password-on="click"
              :input-props="{ name: 'cx-edit-password', autocomplete: 'new-password' }"
              placeholder="留空保持当前密码"
              :disabled="editForm.clear_password"
            />
            <NCheckbox
              v-if="editingAccount?.has_password"
              v-model:checked="editForm.clear_password"
            >
              清除已保存密码
            </NCheckbox>
          </div>
        </NFormItem>
        <NFormItem label="账号状态">
          <div class="edit-account-state">
            <NSwitch v-model:value="editForm.enabled" />
            <span>{{ editForm.enabled ? '启用' : '停用' }}</span>
          </div>
        </NFormItem>
      </div>
      <NFormItem label="新 Cookie">
        <div class="secret-edit-field">
          <NInput
            v-model:value="editForm.cookies"
            type="textarea"
            :autosize="{ minRows: 2, maxRows: 4 }"
            :input-props="{ name: 'cx-edit-cookie', autocomplete: 'off' }"
            placeholder="留空保持当前 Cookie"
            :disabled="editForm.clear_cookies"
          />
          <NCheckbox
            v-if="editingAccount?.has_cookies"
            v-model:checked="editForm.clear_cookies"
          >
            清除已保存 Cookie
          </NCheckbox>
        </div>
      </NFormItem>
      <NFormItem label="User-Agent">
        <NInput
          v-model:value="editForm.user_agent"
          :input-props="{ name: 'cx-edit-user-agent', autocomplete: 'off' }"
          placeholder="留空使用系统默认值"
        />
      </NFormItem>
      <div class="form-grid three">
        <NFormItem label="视频倍速">
          <NInputNumber v-model:value="editForm.speed" :min="1" :max="2" :step="0.1" />
        </NFormItem>
        <NFormItem label="章节并发">
          <NInputNumber
            v-model:value="editForm.chapter_concurrency"
            :min="1"
            :max="8"
          />
        </NFormItem>
        <NFormItem label="未开放章节">
          <NSelect
            v-model:value="editForm.unopened_policy"
            :options="[
              { label: '稍后重试', value: 'retry' },
              { label: '跳过并标记', value: 'skip' },
            ]"
          />
        </NFormItem>
      </div>
      <section class="answer-profile-setting">
        <strong class="account-section-label">答案设置</strong>
        <div class="answer-profile-mode">
          <NRadioGroup
            :value="editForm.answer_profile_mode"
            :disabled="!answerIntegration.data.value"
            name="answer-profile-mode"
            @update:value="updateAnswerProfileMode"
          >
            <NRadioButton value="inherit">继承全局</NRadioButton>
            <NRadioButton value="override">账号覆盖</NRadioButton>
          </NRadioGroup>
          <span class="answer-profile-summary">
            {{ editForm.answer_profile_mode === 'inherit'
              ? '任务始终使用全局答案增强设置'
              : '仅此账号使用下面的答案增强设置' }}
          </span>
        </div>
        <NAlert
          v-if="answerIntegration.isLoading.value"
          type="info"
          :bordered="false"
        >
          正在读取全局答案设置。
        </NAlert>
        <NAlert
          v-else-if="answerIntegration.isError.value && !answerIntegration.data.value"
          type="warning"
          :bordered="false"
        >
          全局答案设置暂时不可用；刷新页面后再修改账号答案策略。
        </NAlert>
        <AnswerProfileFields
          v-if="editForm.answer_profile_mode === 'override'"
          :model-value="editForm.answer_profile"
          :model-capable="supportsModelSelection"
          :disabled="!answerIntegration.data.value"
          compact
          @update:model-value="updateAccountAnswerProfile"
        />
      </section>
      <div class="security-note">
        <ShieldCheck :size="18" />
        <span>账号、密码和 Cookie 不会从服务端回填；留空时保持原值。</span>
      </div>
      <div class="modal-actions">
        <NButton @click="closeEdit">取消</NButton>
        <NButton type="primary" :loading="editAccount.isPending.value" @click="submitEdit">
          <template #icon><Save /></template>
          保存修改
        </NButton>
      </div>
    </NForm>
  </NModal>
</template>

<style scoped>
.import-picker {
  display: grid;
  grid-template-columns: 32px minmax(0, 1fr) auto;
  align-items: center;
  gap: 12px;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  background: var(--color-surface-alt);
  padding: 14px;
  color: var(--color-accent);
}

.import-picker > svg {
  place-self: center;
}

.import-picker strong,
.import-picker span {
  display: block;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.import-picker strong {
  color: var(--color-text-strong);
  font-size: 13px;
}

.import-picker span {
  margin-top: 3px;
  color: var(--color-text-muted);
  font-size: 11px;
}

.import-summary {
  display: flex;
  flex-wrap: wrap;
  gap: 10px 20px;
  margin: 16px 0 8px;
  color: var(--color-text-muted);
  font-size: 12px;
}

.import-summary b {
  margin-right: 3px;
  color: var(--color-text-strong);
  font-size: 15px;
}

.import-result-table {
  border: 1px solid var(--color-border-soft);
}

.import-actions .action-spacer {
  flex: 1;
}

.answer-profile-setting {
  display: grid;
  width: 100%;
  gap: 10px;
  margin-bottom: 18px;
}

.account-section-label {
  color: var(--color-text-strong);
  font-size: 14px;
  font-weight: 500;
}

.answer-profile-mode {
  display: grid;
  gap: 7px;
}

.answer-profile-summary {
  color: var(--color-text-muted);
  font-size: 11px;
  line-height: 1.5;
}

@media (max-width: 520px) {
  .import-picker {
    grid-template-columns: 28px minmax(0, 1fr);
  }

  .import-picker > :deep(.n-button) {
    grid-column: 1 / -1;
    width: 100%;
  }

  .import-actions {
    flex-wrap: wrap;
  }

  .import-actions .action-spacer {
    display: none;
  }
}
</style>
