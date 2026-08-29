<script setup lang="ts">
import { LockKeyhole } from 'lucide-vue-next'
import { NAlert, NButton, NForm, NFormItem, NInput } from 'naive-ui'
import { computed, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'

import { ApiError } from '@/api/client'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const router = useRouter()
const loading = ref(false)
const errorMessage = ref('')
const form = reactive({ username: '', password: '', confirmPassword: '' })
const isSetup = computed(() => auth.phase === 'setup')
const passwordRequirement = '8–256 位，仅限 字母/数字/英文符号'
const visibleAsciiPassword = /^[\x21-\x7e]+$/
const setupPasswordInvalid = computed(() => {
  const password = form.password
  return (
    isSetup.value &&
    password.length > 0 &&
    (password.length < 8 || password.length > 256 || !visibleAsciiPassword.test(password))
  )
})
const passwordFeedback = computed(() =>
  setupPasswordInvalid.value ? '密码不符合要求，请重新输入' : passwordRequirement,
)
const confirmPasswordMismatch = computed(
  () =>
    isSetup.value &&
    form.confirmPassword.length > 0 &&
    form.confirmPassword !== form.password,
)

async function submit() {
  errorMessage.value = ''
  const username = form.username.trim()
  if (isSetup.value) {
    if (username.length < 3) {
      errorMessage.value = '账号至少需要 3 个字符'
      return
    }
    if (username.length > 80) {
      errorMessage.value = '账号不能超过 80 个字符'
      return
    }
  }
  if (form.password.length < 8) {
    errorMessage.value = '密码至少需要 8 个字符'
    return
  }
  if (form.password.length > 256) {
    errorMessage.value = '密码不能超过 256 个字符'
    return
  }
  if (!visibleAsciiPassword.test(form.password)) {
    errorMessage.value = '密码只能包含英文字母、数字和英文符号，且不能包含空格'
    return
  }
  if (isSetup.value && form.password !== form.confirmPassword) {
    errorMessage.value = '密码不一致'
    return
  }
  loading.value = true
  try {
    if (isSetup.value) await auth.setup(username, form.password)
    else await auth.login(username, form.password)
    await router.replace({ name: 'dashboard' })
  } catch (error) {
    errorMessage.value = error instanceof ApiError ? error.message : '操作失败，请稍后重试'
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <main class="auth-page">
    <section class="auth-panel" aria-labelledby="auth-title">
      <div class="auth-heading">
        <span class="auth-icon"><LockKeyhole :size="22" /></span>
        <div>
          <h1 id="auth-title">{{ isSetup ? '初始化控制台' : '登录' }}</h1>
          <p>{{ isSetup ? '创建首个本地管理员账号' : '进入学习任务控制台' }}</p>
        </div>
      </div>
      <NAlert v-if="errorMessage" type="error" :show-icon="false">{{ errorMessage }}</NAlert>
      <NForm :model="form" label-placement="top" @submit.prevent="submit">
        <NFormItem label="账号">
          <NInput
            v-model:value="form.username"
            autocomplete="username"
            :input-props="{ 'aria-label': '管理员账号' }"
            placeholder="请输入账号"
          />
        </NFormItem>
        <NFormItem
          class="password-field"
          label="密码"
          :validation-status="setupPasswordInvalid ? 'error' : undefined"
          :show-feedback="isSetup"
        >
          <template #feedback>
            <span id="password-feedback" aria-live="polite">{{ passwordFeedback }}</span>
          </template>
          <NInput
            v-model:value="form.password"
            type="password"
            show-password-on="click"
            :autocomplete="isSetup ? 'new-password' : 'current-password'"
            :input-props="{
              'aria-label': '密码',
              'aria-invalid': setupPasswordInvalid ? 'true' : 'false',
              'aria-describedby': isSetup ? 'password-feedback' : undefined,
            }"
            :maxlength="256"
            :placeholder="isSetup ? '设置管理员密码' : '请输入密码'"
            @keyup.enter="submit"
          />
        </NFormItem>
        <NFormItem
          v-if="isSetup"
          class="confirm-password-field"
          label="确认密码"
          :validation-status="confirmPasswordMismatch ? 'error' : undefined"
          show-feedback
        >
          <template #feedback>
            <span
              v-if="confirmPasswordMismatch"
              id="confirm-password-feedback"
              aria-live="polite"
            >密码不一致</span>
          </template>
          <NInput
            v-model:value="form.confirmPassword"
            type="password"
            autocomplete="new-password"
            :input-props="{
              'aria-label': '确认密码',
              'aria-invalid': confirmPasswordMismatch ? 'true' : 'false',
              'aria-describedby': confirmPasswordMismatch
                ? 'confirm-password-feedback'
                : undefined,
            }"
            :maxlength="256"
            placeholder="再次输入密码"
            @keyup.enter="submit"
          />
        </NFormItem>
        <NButton type="primary" block attr-type="submit" :loading="loading">
          {{ isSetup ? '创建并登录' : '登录' }}
        </NButton>
      </NForm>
    </section>
  </main>
</template>
