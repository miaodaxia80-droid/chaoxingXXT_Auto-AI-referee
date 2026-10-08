<script setup lang="ts">
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import { Bot, FlaskConical, Plus, RefreshCw, Save, Server, Trash2 } from 'lucide-vue-next'
import {
  NAlert,
  NButton,
  NCheckbox,
  NCollapse,
  NCollapseItem,
  NForm,
  NFormItem,
  NInput,
  NSelect,
  NSlider,
  NSkeleton,
  NSwitch,
  NTag,
  useMessage,
} from 'naive-ui'
import { computed, reactive, ref, watch } from 'vue'

import {
  ApiError,
  getAnswerIntegration,
  integrationTestMessage,
  testAnswerIntegration,
  updateAnswerIntegration,
} from '@/api/client'
import AnswerProfileFields from '@/components/settings/AnswerProfileFields.vue'
import IconAction from '@/components/ui/IconAction.vue'
import {
  cloneAnswerProfile,
  completeAnswerProfile,
  normalizedAnswerProfile,
} from '@/domain/answerProfiles'
import type {
  AnswerIntegration,
  AnswerProfile,
  AnswerProviderKind,
  AnswerSubmitMode,
  IntegrationTestResult,
  UpdateAnswerIntegrationInput,
} from '@/api/types'

const emit = defineEmits<{ 'dirty-change': [dirty: boolean] }>()

interface HeaderRow {
  name: string
  value: string
}

interface AnswerForm {
  enabled: boolean
  provider: AnswerProviderKind
  submitMode: AnswerSubmitMode
  threshold: number
  endpoint: string
  baseUrl: string
  model: string
  headers: HeaderRow[]
  search: boolean
  allowUnsafeEndpoint: boolean
  tokens: string
  token: string
  apiKey: string
  clearTokens: boolean
  clearToken: boolean
  clearApiKey: boolean
  profile: AnswerProfile
}

type SecretKind = 'tokens' | 'token' | 'api_key' | null

const queryKey = ['integration-settings', 'answer'] as const
const queryClient = useQueryClient()
const message = useMessage()
const initialized = ref(false)
const revision = ref<number | null>(null)
const saved = ref<AnswerForm | null>(null)
const credentialPresent = ref(false)

const form = reactive<AnswerForm>({
  enabled: false,
  provider: 'yanxi',
  submitMode: 'auto',
  threshold: 0.8,
  endpoint: '',
  baseUrl: '',
  model: '',
  headers: [],
  search: false,
  allowUnsafeEndpoint: false,
  tokens: '',
  token: '',
  apiKey: '',
  clearTokens: false,
  clearToken: false,
  clearApiKey: false,
  profile: completeAnswerProfile(),
})

const providerOptions: { label: string; value: AnswerProviderKind }[] = [
  { label: '言溪题库', value: 'yanxi' },
  { label: 'Like 题库', value: 'like' },
  { label: 'TikuAdapter', value: 'tiku_adapter' },
  { label: 'OpenAI 兼容接口', value: 'openai_compatible' },
  { label: '硅基流动', value: 'siliconflow' },
]

const submitModeOptions: { label: string; value: AnswerSubmitMode }[] = [
  { label: '自动判断', value: 'auto' },
  { label: '仅保存答案', value: 'save_only' },
  { label: '直接提交答案', value: 'submit' },
]

const providerNames: Record<AnswerProviderKind, string> = {
  yanxi: '言溪题库',
  like: 'Like 题库',
  tiku_adapter: 'TikuAdapter',
  openai_compatible: 'OpenAI 兼容接口',
  siliconflow: '硅基流动',
}

const answer = useQuery({
  queryKey,
  queryFn: getAnswerIntegration,
})

const saveAnswer = useMutation({
  mutationFn: updateAnswerIntegration,
  async onSuccess(data) {
    testResult.value = null
    applyAnswer(data)
    queryClient.setQueryData(queryKey, data)
    message.success('答案服务设置已保存')
  },
  async onError(error) {
    if (error instanceof ApiError && error.status === 409) {
      const result = await answer.refetch()
      if (result.data) applyAnswer(result.data)
      message.warning('配置已在其他页面更新，已刷新，请重新修改')
      return
    }
    message.error(errorText(error, '答案服务设置保存失败'))
  },
})

watch(
  answer.data,
  (value) => {
    if (value && !initialized.value) {
      applyAnswer(value)
      initialized.value = true
    }
  },
  { immediate: true },
)

const providerSwitchPending = computed(
  () => saved.value !== null && form.provider !== saved.value.provider,
)

const secretKind = computed<SecretKind>(() => {
  if (form.provider === 'yanxi') return 'tokens'
  if (form.provider === 'like') return 'token'
  if (form.provider === 'openai_compatible' || form.provider === 'siliconflow') {
    return 'api_key'
  }
  return null
})

const secretLabel = computed(() => {
  if (secretKind.value === 'tokens') return '访问令牌'
  if (secretKind.value === 'token') return 'Token'
  if (secretKind.value === 'api_key') return 'API Key'
  return ''
})

const secretValue = computed({
  get() {
    if (secretKind.value === 'tokens') return form.tokens
    if (secretKind.value === 'token') return form.token
    if (secretKind.value === 'api_key') return form.apiKey
    return ''
  },
  set(value: string) {
    if (secretKind.value === 'tokens') form.tokens = value
    else if (secretKind.value === 'token') form.token = value
    else if (secretKind.value === 'api_key') form.apiKey = value
  },
})

const clearSecret = computed({
  get() {
    if (secretKind.value === 'tokens') return form.clearTokens
    if (secretKind.value === 'token') return form.clearToken
    if (secretKind.value === 'api_key') return form.clearApiKey
    return false
  },
  set(value: boolean) {
    form.clearTokens = secretKind.value === 'tokens' ? value : false
    form.clearToken = secretKind.value === 'token' ? value : false
    form.clearApiKey = secretKind.value === 'api_key' ? value : false
    if (value) {
      secretValue.value = ''
      form.enabled = false
    }
  },
})

const isEndpointProvider = computed(
  () => form.provider === 'yanxi' || form.provider === 'like' || form.provider === 'tiku_adapter',
)
const isModelProvider = computed(
  () => form.provider === 'openai_compatible' || form.provider === 'siliconflow',
)
const supportsModelSelection = computed(
  () => isModelProvider.value || form.provider === 'like',
)
const hasChanges = computed(() => {
  if (!saved.value) return false
  return JSON.stringify(formSnapshot(form)) !== JSON.stringify(formSnapshot(saved.value))
})
watch(hasChanges, (value) => emit('dirty-change', value), { immediate: true })

const testResult = ref<IntegrationTestResult | null>(null)
const testAnswer = useMutation({
  mutationFn: testAnswerIntegration,
  onMutate() {
    testResult.value = null
  },
  onSuccess(result) {
    testResult.value = result
  },
  onError(error) {
    message.error(errorText(error, '测试请求失败'))
  },
})
const canTest = computed(
  () => saved.value?.enabled === true && !hasChanges.value && !saveAnswer.isPending.value,
)
const testDisabledReason = computed(() => {
  if (saved.value?.enabled !== true) return '启用并保存自动答题后可测试'
  if (hasChanges.value) return '请先保存更改'
  return ''
})

const thresholdLabel = computed(() => `${Math.round(form.threshold * 100)}%`)
const loadError = computed(() => errorText(answer.error.value, '答案服务设置读取失败'))

const OPENCODE_HOST_PATTERN = /(^|\.)opencode\.ai$/i
const OPENCODE_SESSION_HEADER = 'x-opencode-session'

const isOpenCodeEndpoint = computed(() => {
  if (form.provider !== 'openai_compatible' || !form.baseUrl.trim()) return false
  try {
    return OPENCODE_HOST_PATTERN.test(new URL(form.baseUrl.trim()).hostname)
  } catch {
    return false
  }
})

watch(isOpenCodeEndpoint, (detected) => {
  if (!detected) return
  const exists = form.headers.some(
    (row) => row.name.trim().toLowerCase() === OPENCODE_SESSION_HEADER,
  )
  if (!exists) {
    form.headers.push({
      name: OPENCODE_SESSION_HEADER,
      value: crypto.randomUUID(),
    })
  }
})

function serializeHeaders(): Record<string, string> {
  const result: Record<string, string> = {}
  for (const row of form.headers) {
    const name = row.name.trim()
    if (name) result[name] = row.value
  }
  return result
}

function addHeaderRow() {
  form.headers.push({ name: '', value: '' })
}

function removeHeaderRow(index: number) {
  form.headers.splice(index, 1)
}

function formSnapshot(value: AnswerForm): AnswerForm {
  return {
    ...value,
    headers: value.headers.map((row) => ({ ...row })),
    profile: cloneAnswerProfile(value.profile),
  }
}

function applyAnswer(value: AnswerIntegration) {
  const next: AnswerForm = {
    enabled: value.enabled,
    provider: value.provider,
    submitMode: value.submit_mode,
    threshold: value.threshold,
    endpoint: value.config.endpoint ?? '',
    baseUrl: value.config.base_url ?? '',
    model: value.config.model ?? '',
    headers: Object.entries(value.config.headers ?? {}).map(([name, value]) => ({
      name,
      value,
    })),
    search: value.config.search ?? false,
    allowUnsafeEndpoint: value.config.allow_unsafe_endpoint,
    tokens: '',
    token: '',
    apiKey: '',
    clearTokens: false,
    clearToken: false,
    clearApiKey: false,
    profile: completeAnswerProfile(value.profile),
  }
  Object.assign(form, next)
  saved.value = formSnapshot(next)
  revision.value = value.revision
  credentialPresent.value = value.has_tokens || value.has_token || value.has_api_key
}

function updateEnabled(enabled: boolean) {
  form.enabled = enabled
  if (enabled) clearSecret.value = false
}

function validateForm(): boolean {
  if (providerSwitchPending.value || !form.enabled) return true
  if (isEndpointProvider.value && !form.endpoint.trim()) {
    message.warning('请填写答案服务地址')
    return false
  }
  if (isModelProvider.value && (!form.baseUrl.trim() || !form.model.trim())) {
    message.warning('请填写 API 地址和模型名称')
    return false
  }
  if (secretKind.value && !credentialPresent.value && !secretValue.value.trim()) {
    message.warning(`请填写${secretLabel.value}`)
    return false
  }
  return true
}

function buildPayload(): UpdateAnswerIntegrationInput | null {
  if (revision.value === null) return null
  const payload: UpdateAnswerIntegrationInput = {
    expected_revision: revision.value,
    enabled: providerSwitchPending.value ? false : form.enabled,
    provider: form.provider,
    submit_mode: form.submitMode,
    threshold: form.threshold,
    profile: normalizedAnswerProfile(form.profile),
  }

  // The API only exposes the selected provider. Switch first so its stored
  // provider-specific settings can be loaded without overwriting them.
  if (providerSwitchPending.value) return payload

  payload.allow_unsafe_endpoint = form.allowUnsafeEndpoint
  if (isEndpointProvider.value) payload.endpoint = form.endpoint.trim()
  if (isModelProvider.value) {
    payload.base_url = form.baseUrl.trim()
    payload.model = form.model.trim()
  }
  if (form.provider === 'openai_compatible') {
    payload.headers = serializeHeaders()
  }
  if (form.provider === 'like') {
    payload.model = form.model.trim()
    payload.search = form.search
  }

  const value = secretValue.value.trim()
  if (secretKind.value === 'tokens') {
    if (value) payload.tokens = value
    if (form.clearTokens) payload.clear_tokens = true
  } else if (secretKind.value === 'token') {
    if (value) payload.token = value
    if (form.clearToken) payload.clear_token = true
  } else if (secretKind.value === 'api_key') {
    if (value) payload.api_key = value
    if (form.clearApiKey) payload.clear_api_key = true
  }
  return payload
}

function submit() {
  if (!hasChanges.value || saveAnswer.isPending.value || !validateForm()) return
  const payload = buildPayload()
  if (payload) saveAnswer.mutate(payload)
}

function resetForm() {
  if (saved.value) Object.assign(form, formSnapshot(saved.value))
}

async function refresh() {
  const result = await answer.refetch()
  if (result.data) {
    applyAnswer(result.data)
    message.success('答案服务设置已刷新')
  }
}

function errorText(error: unknown, fallback: string): string {
  if (error instanceof ApiError) return error.message
  if (error instanceof Error) return error.message
  return error ? fallback : ''
}
</script>

<template>
  <section class="content-section flush settings-card">
    <div class="settings-intro">
      <p>配置查题来源、答案可信度与提交策略。密钥只写不读，留空即保持原值。</p>
      <IconAction
        label="刷新答案服务设置"
        :icon="RefreshCw"
        :loading="answer.isFetching.value"
        @click="refresh"
      />
    </div>

    <NAlert v-if="answer.isError.value" type="error" :bordered="false">
      {{ loadError }}
    </NAlert>

    <div v-if="answer.isLoading.value" class="settings-loading">
      <NSkeleton text :repeat="6" />
    </div>

    <NForm v-else-if="saved" label-placement="top" @submit.prevent="submit">
      <div class="setting-row">
        <span class="setting-icon"><Bot :size="18" /></span>
        <div class="setting-copy">
          <strong>自动答题</strong>
          <span>答案覆盖率低于阈值时不会猜测或提交</span>
        </div>
        <div class="setting-control">
          <NTag size="small" :bordered="false" :type="form.enabled ? 'success' : 'default'">
            {{ form.enabled ? '已启用' : '已停用' }}
          </NTag>
          <NSwitch
            :value="form.enabled"
            :disabled="providerSwitchPending"
            aria-label="启用自动答题"
            @update:value="updateEnabled"
          />
        </div>
      </div>

      <div class="setting-fields common-fields">
        <NFormItem label="答案源">
          <NSelect v-model:value="form.provider" :options="providerOptions" />
        </NFormItem>
        <NFormItem label="答题策略">
          <NSelect v-model:value="form.submitMode" :options="submitModeOptions" />
        </NFormItem>
        <NFormItem>
          <template #label>
            <span class="field-label-with-value">
              <span>最低答案覆盖率</span>
              <strong>{{ thresholdLabel }}</strong>
            </span>
          </template>
          <NSlider v-model:value="form.threshold" :min="0" :max="1" :step="0.05" />
        </NFormItem>
      </div>

      <NAlert
        v-if="providerSwitchPending"
        class="provider-switch-note"
        type="info"
        :bordered="false"
      >
        保存后将先停用自动答题并切换到{{ providerNames[form.provider] }}，随后载入该答案源此前保存的独立配置。
      </NAlert>

      <template v-else>
        <div class="setting-row provider-row">
          <span class="setting-icon"><Server :size="18" /></span>
          <div class="setting-copy">
            <strong>{{ providerNames[form.provider] }}</strong>
            <span>连接参数与凭据</span>
          </div>
          <NTag v-if="secretKind" size="small" :bordered="false" :type="credentialPresent ? 'success' : 'warning'">
            {{ credentialPresent ? '凭据已保存' : '未保存凭据' }}
          </NTag>
          <NTag v-else size="small" :bordered="false">无需凭据</NTag>
        </div>

        <div class="setting-fields">
          <NFormItem v-if="isEndpointProvider" label="服务地址">
            <NInput
              v-model:value="form.endpoint"
              placeholder="https://example.com/query"
              :disabled="form.clearTokens || form.clearToken"
            />
          </NFormItem>
          <NFormItem v-if="isModelProvider" label="API 地址">
            <NInput v-model:value="form.baseUrl" placeholder="https://api.example.com/v1" />
          </NFormItem>
          <NFormItem v-if="isModelProvider || form.provider === 'like'" label="模型">
            <NInput
              v-model:value="form.model"
              :placeholder="form.provider === 'like' ? '可选' : '例如 gpt-4o-mini'"
            />
          </NFormItem>
          <NFormItem
            v-if="form.provider === 'openai_compatible'"
            label="自定义请求头"
            class="full"
          >
            <div class="headers-field">
              <NAlert
                v-if="isOpenCodeEndpoint"
                type="info"
                :bordered="false"
                class="opencode-note"
              >
                检测到 OpenCode 服务地址，已自动填入 x-opencode-session 路由头。删除该行即可停用。
              </NAlert>
              <div
                v-for="(row, index) in form.headers"
                :key="index"
                class="header-row"
              >
                <NInput
                  v-model:value="row.name"
                  placeholder="请求头名称"
                  aria-label="请求头名称"
                />
                <NInput
                  v-model:value="row.value"
                  placeholder="请求头值"
                  aria-label="请求头值"
                />
                <IconAction
                  label="删除请求头"
                  :icon="Trash2"
                  @click="removeHeaderRow(index)"
                />
              </div>
              <NButton quaternary size="small" @click="addHeaderRow">
                <template #icon><Plus /></template>
                添加请求头
              </NButton>
            </div>
          </NFormItem>
          <NFormItem v-if="form.provider === 'like'" label="联网搜索">
            <div class="inline-switch-control">
              <NSwitch v-model:value="form.search" aria-label="启用联网搜索" />
              <span>{{ form.search ? '已启用' : '已关闭' }}</span>
            </div>
          </NFormItem>
          <NFormItem v-if="secretKind" :label="secretLabel" class="full">
            <div class="secret-field-content">
              <NInput
                v-model:value="secretValue"
                type="password"
                show-password-on="click"
                :input-props="{ autocomplete: 'new-password' }"
                :placeholder="credentialPresent ? '已保存，留空保持不变' : `请输入${secretLabel}`"
                :disabled="clearSecret"
              />
              <NCheckbox v-if="credentialPresent" v-model:checked="clearSecret">
                清除已保存的{{ secretLabel }}（将同时停用自动答题）
              </NCheckbox>
            </div>
          </NFormItem>
          <NCollapse class="advanced-settings full" arrow-placement="right">
            <NCollapseItem title="高级设置" name="endpoint-policy">
              <NAlert type="warning" :bordered="false">
                允许不安全地址后，答案服务可以访问 HTTP、本机、.local 或私网地址，可能暴露内网服务。仅在地址由你控制且确实需要时开启。
              </NAlert>
              <div class="unsafe-setting-row">
                <div class="setting-copy">
                  <strong>允许不安全服务地址</strong>
                  <span>默认仅允许公网 HTTPS 地址</span>
                </div>
                <NSwitch
                  v-model:value="form.allowUnsafeEndpoint"
                  aria-label="允许不安全服务地址"
                />
              </div>
            </NCollapseItem>
          </NCollapse>
        </div>
      </template>

      <AnswerProfileFields
        v-model="form.profile"
        :model-capable="supportsModelSelection"
      />

      <NAlert
        v-if="testResult"
        class="answer-test-result"
        :type="testResult.ok ? 'success' : 'error'"
        :bordered="false"
        :title="integrationTestMessage(testResult)"
        closable
        @close="testResult = null"
      >
        <template v-if="testResult.ok && testResult.answer">
          测试题「中华人民共和国的首都是哪座城市？」返回：{{ testResult.answer }}
        </template>
        <template v-else-if="!testResult.ok">请检查答案源地址、凭据和网络后重试。</template>
      </NAlert>

      <div class="sticky-actions">
        <div class="footer-left">
          <NButton
            quaternary
            :disabled="!canTest"
            :loading="testAnswer.isPending.value"
            :title="testDisabledReason"
            @click="testAnswer.mutate()"
          >
            <template #icon><FlaskConical /></template>
            测试连接
          </NButton>
          <span v-if="hasChanges" class="dirty-hint">有未保存的更改</span>
        </div>
        <NButton :disabled="!hasChanges || saveAnswer.isPending.value" @click="resetForm">
          撤销更改
        </NButton>
        <NButton
          attr-type="submit"
          type="primary"
          :loading="saveAnswer.isPending.value"
          :disabled="!hasChanges"
        >
          <template #icon><Save /></template>
          {{ providerSwitchPending ? '保存并切换' : '保存' }}
        </NButton>
      </div>
    </NForm>
  </section>
</template>

<style scoped>
.common-fields {
  grid-template-columns: repeat(2, minmax(0, 1fr)) minmax(190px, 1.2fr);
}

.provider-row {
  border-bottom: 0;
}

.field-label-with-value {
  display: flex;
  width: 100%;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.field-label-with-value strong {
  color: var(--color-accent);
  font-size: var(--fs-xs);
}

.inline-switch-control {
  display: flex;
  min-height: 34px;
  align-items: center;
  gap: 9px;
}

.inline-switch-control span {
  color: var(--color-text-muted);
  font-size: var(--fs-xs);
}

.secret-field-content {
  display: grid;
  width: 100%;
  gap: 7px;
}

.headers-field {
  display: grid;
  width: 100%;
  gap: 8px;
}

.headers-field .opencode-note {
  margin: 0;
}

.header-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) auto;
  align-items: center;
  gap: 8px;
}

.provider-switch-note {
  margin: 16px 20px;
}

.advanced-settings {
  margin-bottom: 14px;
}

.advanced-settings :deep(.n-collapse-item__header-main) {
  color: var(--color-text-muted);
  font-size: var(--fs-sm);
  font-weight: 600;
}

.unsafe-setting-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  gap: 16px;
  padding: 14px 2px 4px;
}

.answer-test-result {
  margin: 14px 20px;
}

.footer-left {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 10px;
  margin-right: auto;
}

.footer-left .dirty-hint {
  margin-right: 0;
}

@media (max-width: 720px) {
  .common-fields {
    grid-template-columns: minmax(0, 1fr);
  }

  .provider-switch-note,
  .answer-test-result {
    margin-right: 14px;
    margin-left: 14px;
  }
}

@media (max-width: 460px) {
  .sticky-actions {
    flex-wrap: wrap;
  }

  .footer-left {
    width: 100%;
  }
}
</style>
