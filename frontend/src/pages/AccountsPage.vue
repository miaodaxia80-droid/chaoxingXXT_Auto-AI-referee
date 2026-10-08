<script setup lang="ts">
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import {
  Download,
  FileSpreadsheet,
  KeyRound,
  Pencil,
  Play,
  Plus,
  RefreshCw,
  Save,
  Search,
  SearchX,
  ShieldCheck,
  Trash2,
  Upload,
  Users,
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
  NSkeleton,
  NSwitch,
  NTag,
  useDialog,
  useMessage,
} from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import { computed, h, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { ApiError, apiRequest, getAnswerIntegration } from '@/api/client'
import AnswerProfileFields from '@/components/settings/AnswerProfileFields.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import IconAction from '@/components/ui/IconAction.vue'
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
  AnswerIntegration,
  AnswerProfile,
  CreateAccountInput,
  UpdateAccountInput,
} from '@/api/types'
import { useAuthStore } from '@/stores/auth'

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
  discussion_auto_reply: boolean
  enabled: boolean
  clear_password: boolean
  clear_cookies: boolean
  answer_profile_mode: AnswerProfileMode
  answer_profile: AnswerProfile
}

type LoginMethod = 'password' | 'cookie'

const UNOPENED_OPTIONS = [
  { label: '稍后重试', value: 'retry' },
  { label: '跳过并标记', value: 'skip' },
]

const queryClient = useQueryClient()
const message = useMessage()
const dialog = useDialog()
const route = useRoute()
const router = useRouter()
const search = ref('')
const createLoginMethod = ref<LoginMethod>('password')
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
  discussion_auto_reply: false,
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
  discussion_auto_reply: false,
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

const auth = useAuthStore()
const answerIntegration = useQuery<Pick<AnswerIntegration, 'provider' | 'profile'>>({
  queryKey: computed(() =>
    auth.isAdmin ? ['integration-settings', 'answer'] : ['integration-settings', 'answer-public'],
  ),
  queryFn: () =>
    auth.isAdmin
      ? getAnswerIntegration()
      : apiRequest<Pick<AnswerIntegration, 'provider' | 'profile'>>('/settings/answer-public'),
})

const filteredAccounts = computed(() => {
  const keyword = search.value.trim().toLocaleLowerCase()
  const list = accounts.data.value ?? []
  if (!keyword) return list
  return list.filter((account) =>
    [account.remark, account.username_hint].some((value) =>
      value.toLocaleLowerCase().includes(keyword),
    ),
  )
})
const enabledCount = computed(
  () => (accounts.data.value ?? []).filter((account) => account.enabled).length,
)

watch(
  () => route.query.create,
  (value) => {
    if (value !== '1') return
    showCreate.value = true
    const { create: _create, ...rest } = route.query
    void router.replace({ query: rest })
  },
  { immediate: true },
)

function createTaskFor(account: Account): void {
  void router.push({ name: 'tasks', query: { create: '1', account: String(account.id) } })
}

function credentialLabel(account: Account): string {
  if (account.has_password && account.has_cookies) return '密码 + Cookie'
  if (account.has_password) return '密码'
  if (account.has_cookies) return 'Cookie'
  return '未保存凭据'
}

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

function isToggling(account: Account): boolean {
  return toggleAccount.isPending.value && toggleAccount.variables.value?.id === account.id
}

function isDeleting(account: Account): boolean {
  return deleteAccount.isPending.value && deleteAccount.variables.value === account.id
}

function renderActions(row: Account) {
  return h('div', { class: 'cell-actions' }, [
    h(IconAction, {
      label: '为该账号创建任务',
      icon: Play,
      disabled: !row.enabled,
      onClick: () => createTaskFor(row),
    }),
    h(IconAction, { label: '编辑账号', icon: Pencil, onClick: () => openEdit(row) }),
    h(IconAction, {
      label: '删除账号',
      icon: Trash2,
      danger: true,
      loading: isDeleting(row),
      disabled: isDeleting(row),
      onClick: () => requestDelete(row),
    }),
  ])
}

function renderStatus(row: Account) {
  const loading = isToggling(row)
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
}

const columns: DataTableColumns<Account> = [
  {
    title: '账号',
    key: 'username_hint',
    minWidth: 200,
    render: (row) =>
      h('div', { class: 'cell-stack' }, [
        h('strong', row.remark || row.username_hint),
        h('span', row.remark ? row.username_hint : '未设置备注'),
      ]),
  },
  {
    title: '登录方式',
    key: 'credentials',
    width: 150,
    render: (row) =>
      h(
        NTag,
        {
          size: 'small',
          bordered: false,
          type: row.has_password || row.has_cookies ? 'default' : 'warning',
        },
        { default: () => credentialLabel(row) },
      ),
  },
  {
    title: '倍速',
    key: 'speed',
    width: 80,
    render: (row) => h('span', { class: 'num' }, `${row.speed.toFixed(1)}x`),
  },
  { title: '章节并发', key: 'chapter_concurrency', width: 96 },
  {
    title: '未开放章节',
    key: 'unopened_policy',
    width: 110,
    render: (row) =>
      h('span', { class: 'cell-muted' }, row.unopened_policy === 'retry' ? '稍后重试' : '跳过'),
  },
  { title: '状态', key: 'enabled', width: 108, render: renderStatus },
  { title: '操作', key: 'actions', width: 124, render: renderActions },
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
    discussion_auto_reply: false,
  })
  createLoginMethod.value = 'password'
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
    discussion_auto_reply: false,
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
    'discussion_auto_reply',
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
    discussion_auto_reply: account.discussion_auto_reply,
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
  const usePassword = createLoginMethod.value === 'password'
  if (usePassword ? !createForm.password : !createForm.cookies?.trim()) {
    message.warning(usePassword ? '请输入学习通密码' : '请粘贴 Cookie')
    return
  }
  createAccount.mutate({
    ...createForm,
    password: usePassword ? createForm.password : '',
    cookies: usePassword ? '' : (createForm.cookies ?? '').trim(),
  })
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
    discussion_auto_reply: editForm.discussion_auto_reply,
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
    <div class="list-toolbar">
      <NInput
        v-model:value="search"
        class="toolbar-search"
        clearable
        placeholder="搜索备注或账号"
        :disabled="!accounts.data.value?.length"
        :input-props="{ 'aria-label': '搜索账号' }"
      >
        <template #prefix><Search :size="16" /></template>
      </NInput>
      <span v-if="accounts.data.value?.length" class="list-count">
        共 {{ accounts.data.value.length }} 个 · {{ enabledCount }} 个启用
      </span>
      <span class="toolbar-spacer" />
      <IconAction
        label="刷新账号"
        :icon="RefreshCw"
        size="medium"
        :loading="accounts.isFetching.value"
        @click="accounts.refetch()"
      />
      <NButton @click="showImport = true">
        <template #icon><Upload /></template>
        批量导入
      </NButton>
      <NButton type="primary" @click="showCreate = true">
        <template #icon><Plus /></template>
        添加账号
      </NButton>
    </div>

    <NAlert v-if="accounts.isError.value" type="error" :bordered="false">
      账号列表加载失败，请稍后重试。
    </NAlert>
    <div v-else-if="accounts.isLoading.value" class="list-skeleton">
      <NSkeleton v-for="index in 3" :key="index" text :height="44" />
    </div>
    <EmptyState
      v-else-if="!accounts.data.value?.length"
      :icon="Users"
      title="还没有学习通账号"
      description="添加账号后即可读取课程并创建学习任务。也可以通过 CSV 一次导入多个账号。"
    >
      <NButton type="primary" @click="showCreate = true">
        <template #icon><Plus /></template>
        添加账号
      </NButton>
      <NButton @click="showImport = true">批量导入</NButton>
    </EmptyState>
    <EmptyState
      v-else-if="filteredAccounts.length === 0"
      :icon="SearchX"
      title="没有匹配的账号"
    >
      <NButton size="small" @click="search = ''">清除搜索</NButton>
    </EmptyState>
    <template v-else>
      <NDataTable
        class="desktop-only"
        :columns="columns"
        :data="filteredAccounts"
        :bordered="false"
        :row-key="(row: Account) => row.id"
        :scroll-x="820"
      />
      <div class="mobile-card-list mobile-only">
        <article v-for="account in filteredAccounts" :key="account.id" class="mobile-card">
          <div class="mobile-card-head">
            <div class="cell-stack">
              <strong>{{ account.remark || account.username_hint }}</strong>
              <span>{{ account.remark ? account.username_hint : '未设置备注' }}</span>
            </div>
            <NSwitch
              :value="account.enabled"
              size="small"
              :loading="isToggling(account)"
              :aria-label="account.enabled ? '停用账号' : '启用账号'"
              @update:value="(enabled: boolean) => toggleAccount.mutate({ id: account.id, enabled })"
            />
          </div>
          <div class="mobile-card-meta">
            <span>{{ credentialLabel(account) }}</span>
            <span class="num">{{ account.speed.toFixed(1) }}x 倍速</span>
            <span>并发 {{ account.chapter_concurrency }}</span>
          </div>
          <div class="mobile-card-actions">
            <NButton size="small" :disabled="!account.enabled" @click="createTaskFor(account)">
              <template #icon><Play :size="14" /></template>
              创建任务
            </NButton>
            <IconAction label="编辑账号" :icon="Pencil" @click="openEdit(account)" />
            <IconAction
              label="删除账号"
              :icon="Trash2"
              danger
              :loading="isDeleting(account)"
              @click="requestDelete(account)"
            />
          </div>
        </article>
      </div>
    </template>
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
      <NFormItem label="登录方式">
        <NRadioGroup v-model:value="createLoginMethod" name="cx-create-login-method">
          <NRadioButton value="password">账号密码</NRadioButton>
          <NRadioButton value="cookie">Cookie</NRadioButton>
        </NRadioGroup>
      </NFormItem>
      <NFormItem v-show="createLoginMethod === 'password'" label="密码">
        <NInput
          v-model:value="createForm.password"
          type="password"
          show-password-on="click"
          :input-props="{ name: 'cx-create-password', autocomplete: 'new-password' }"
          placeholder="学习通登录密码"
        />
      </NFormItem>
      <NFormItem v-show="createLoginMethod === 'cookie'" label="Cookie">
        <NInput
          v-model:value="createForm.cookies"
          type="textarea"
          :autosize="{ minRows: 3, maxRows: 5 }"
          :input-props="{ name: 'cx-create-cookie', autocomplete: 'off' }"
          placeholder="从已登录的浏览器复制完整 Cookie，执行任务时会自动续期"
        />
      </NFormItem>
      <div class="form-grid three">
        <NFormItem label="视频倍速">
          <NInputNumber
            v-model:value="createForm.speed"
            :min="1"
            :max="2"
            :step="0.1"
            :precision="1"
          >
            <template #suffix>x</template>
          </NInputNumber>
        </NFormItem>
        <NFormItem label="章节并发">
          <NInputNumber v-model:value="createForm.chapter_concurrency" :min="1" :max="8" :precision="0" />
        </NFormItem>
        <NFormItem label="未开放章节">
          <NSelect v-model:value="createForm.unopened_policy" :options="UNOPENED_OPTIONS" />
        </NFormItem>
      </div>
      <NFormItem label="章节讨论题">
        <div>
          <div class="edit-account-state">
            <NSwitch v-model:value="createForm.discussion_auto_reply" />
            <span>{{ createForm.discussion_auto_reply ? '自动回帖' : '手动处理' }}</span>
          </div>
          <p class="form-hint">开启后自动发布一条通用回复完成讨论任务点，回帖内容对班级可见。</p>
        </div>
      </NFormItem>
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
          <NInputNumber
            v-model:value="editForm.speed"
            :min="1"
            :max="2"
            :step="0.1"
            :precision="1"
          >
            <template #suffix>x</template>
          </NInputNumber>
        </NFormItem>
        <NFormItem label="章节并发">
          <NInputNumber
            v-model:value="editForm.chapter_concurrency"
            :min="1"
            :max="8"
            :precision="0"
          />
        </NFormItem>
        <NFormItem label="未开放章节">
          <NSelect v-model:value="editForm.unopened_policy" :options="UNOPENED_OPTIONS" />
        </NFormItem>
      </div>
      <NFormItem label="章节讨论题">
        <div>
          <div class="edit-account-state">
            <NSwitch v-model:value="editForm.discussion_auto_reply" />
            <span>{{ editForm.discussion_auto_reply ? '自动回帖' : '手动处理' }}</span>
          </div>
          <p class="form-hint">开启后自动发布一条通用回复完成讨论任务点，回帖内容对班级可见。</p>
        </div>
      </NFormItem>
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
