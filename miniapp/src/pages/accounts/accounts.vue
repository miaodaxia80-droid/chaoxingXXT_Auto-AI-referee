<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { onPullDownRefresh, onShow } from '@dcloudio/uni-app'
import { api } from '@/api/client'
import type { Account } from '@/api/types'
import { useAuthStore } from '@/stores/auth'
import NeedLogin from '@/components/NeedLogin.vue'

const auth = useAuthStore()

const accounts = ref<Account[]>([])
const loading = ref(true)
const showForm = ref(false)
const editing = ref<Account | null>(null)
const submitting = ref(false)

const form = reactive({
  username: '',
  password: '',
  cookies: '',
  remark: '',
  speed: 1.0,
  chapter_concurrency: 1,
  unopened_policy: 'retry' as 'retry' | 'skip',
  clear_password: false,
  clear_cookies: false,
})

const accountQuota = computed(() => {
  const quota = auth.profile?.quotas?.max_accounts
  return typeof quota === 'number' ? quota : 3
})
const quotaReached = computed(() => accounts.value.length >= accountQuota.value)

async function refresh() {
  if (!auth.isLoggedIn) return
  try {
    accounts.value = await api.listAccounts()
  } finally {
    loading.value = false
  }
}

function openCreate() {
  if (quotaReached.value) {
    uni.showToast({
      title: `账号数已达上限（${accountQuota.value} 个）`,
      icon: 'none',
    })
    return
  }
  editing.value = null
  Object.assign(form, {
    username: '',
    password: '',
    cookies: '',
    remark: '',
    speed: 1.0,
    chapter_concurrency: 1,
    unopened_policy: 'retry',
    clear_password: false,
    clear_cookies: false,
  })
  showForm.value = true
}

function openEdit(account: Account) {
  editing.value = account
  Object.assign(form, {
    username: '',
    password: '',
    cookies: '',
    remark: account.remark,
    speed: account.speed,
    chapter_concurrency: account.chapter_concurrency,
    unopened_policy: account.unopened_policy,
    clear_password: false,
    clear_cookies: false,
  })
  showForm.value = true
}

async function submit() {
  if (!form.username.trim()) {
    uni.showToast({ title: '请输入学习通账号', icon: 'none' })
    return
  }
  if (!form.password.trim() && !form.cookies.trim() && !editing.value) {
    uni.showToast({ title: '密码和 Cookie 至少填一项', icon: 'none' })
    return
  }
  submitting.value = true
  try {
    if (editing.value) {
      const payload: Record<string, unknown> = { remark: form.remark }
      if (form.username.trim()) payload.username = form.username.trim()
      if (form.password.trim()) payload.password = form.password.trim()
      if (form.cookies.trim()) payload.cookies = form.cookies.trim()
      if (form.clear_password) payload.clear_password = true
      if (form.clear_cookies) payload.clear_cookies = true
      payload.speed = form.speed
      payload.chapter_concurrency = form.chapter_concurrency
      payload.unopened_policy = form.unopened_policy
      await api.updateAccount(editing.value.id, payload)
    } else {
      await api.createAccount({
        username: form.username.trim(),
        password: form.password.trim() || undefined,
        cookies: form.cookies.trim() || undefined,
        remark: form.remark.trim(),
        speed: form.speed,
        chapter_concurrency: form.chapter_concurrency,
        unopened_policy: form.unopened_policy,
      })
    }
    showForm.value = false
    uni.showToast({ title: editing.value ? '已保存' : '已添加', icon: 'success' })
    refresh()
  } catch (error) {
    const message = error instanceof Error ? error.message : '操作失败'
    uni.showToast({ title: message, icon: 'none' })
  } finally {
    submitting.value = false
  }
}

async function toggleEnabled(account: Account) {
  try {
    await api.updateAccount(account.id, { enabled: !account.enabled })
    account.enabled = !account.enabled
  } catch (error) {
    uni.showToast({ title: error instanceof Error ? error.message : '操作失败', icon: 'none' })
  }
}

async function remove(account: Account) {
  const confirm = await uni.showModal({
    title: '删除账号',
    content: `确定删除「${account.remark || account.username_hint}」？其任务历史也会被删除。`,
  })
  if (!confirm.confirm) return
  try {
    await api.deleteAccount(account.id)
    uni.showToast({ title: '已删除', icon: 'success' })
    refresh()
  } catch (error) {
    uni.showToast({ title: error instanceof Error ? error.message : '删除失败', icon: 'none' })
  }
}

onShow(refresh)
onPullDownRefresh(async () => {
  await refresh()
  uni.stopPullDownRefresh()
})
</script>

<template>
  <view>
    <NeedLogin v-if="!auth.isLoggedIn" />
    <template v-else>
      <view class="section-title">
        学习通账号（{{ accounts.length }}/{{ accountQuota }}）
      </view>
      <view class="card account" v-for="account in accounts" :key="account.id">
        <view class="row">
          <view>
            <view class="account-name">
              {{ account.remark || account.username_hint }}
              <text v-if="!account.enabled" class="badge canceled">已停用</text>
            </view>
            <view class="text-muted">
              {{ account.username_hint }} · 速度 {{ account.speed }}x · 并发 {{ account.chapter_concurrency }}
            </view>
          </view>
          <view class="actions">
            <button class="mini-btn" size="mini" @click="toggleEnabled(account)">
              {{ account.enabled ? '停用' : '启用' }}
            </button>
            <button class="mini-btn" size="mini" @click="openEdit(account)">编辑</button>
            <button class="mini-btn danger" size="mini" @click="remove(account)">删除</button>
          </view>
        </view>
      </view>
      <view v-if="!accounts.length && !loading" class="empty">还没有账号，点下方按钮添加</view>

      <view class="footer-btn">
        <button class="btn-primary" :disabled="quotaReached" @click="openCreate">
          添加账号
        </button>
      </view>

      <view v-if="showForm" class="mask" @click.self="showForm = false">
        <view class="sheet">
          <view class="sheet-title">{{ editing ? '编辑账号' : '添加账号' }}</view>
          <input v-model="form.username" class="field" placeholder="学习通账号（手机号/学号）" />
          <input v-model="form.password" class="field" password :placeholder="editing ? '密码（留空则不修改）' : '密码（可留空）'" />
          <textarea
            v-model="form.cookies"
            class="field textarea"
            :placeholder="editing ? 'Cookie（留空则不修改）' : 'Cookie（可留空，与密码二选一）'"
          />
          <view v-if="editing" class="row field-label credential-row">
            <text class="text-muted">已存凭据</text>
            <view>
              <text v-if="editing.has_password" class="badge queued">密码</text>
              <text v-if="editing.has_cookies" class="badge queued">Cookie</text>
              <text v-if="!editing.has_password && !editing.has_cookies" class="text-muted">
                无（拉取课程会失败）
              </text>
            </view>
          </view>
          <view v-if="editing && editing.has_password" class="row field-label">
            <text class="text-danger">清除已存密码</text>
            <switch :checked="form.clear_password" @change="(e: any) => (form.clear_password = Boolean(e.detail.value))" />
          </view>
          <view v-if="editing && editing.has_cookies" class="row field-label">
            <text class="text-danger">清除已存 Cookie</text>
            <switch :checked="form.clear_cookies" @change="(e: any) => (form.clear_cookies = Boolean(e.detail.value))" />
          </view>
          <input v-model="form.remark" class="field" placeholder="备注（选填）" />
          <view class="row field-label">
            <text>视频倍速</text>
            <slider
              class="slider"
              :min="10"
              :max="20"
              :value="form.speed * 10"
              :step="1"
              @change="(e: any) => (form.speed = Number(e.detail.value) / 10)"
            />
            <text class="text-muted">{{ form.speed.toFixed(1) }}x</text>
          </view>
          <view class="row field-label">
            <text>章节并发</text>
            <radio-group @change="(e: any) => (form.chapter_concurrency = Number(e.detail.value))">
              <label v-for="n in 4" :key="n" class="radio-label">
                <radio :value="String(n)" :checked="form.chapter_concurrency === n" />{{ n }}
              </label>
            </radio-group>
          </view>
          <view class="row field-label">
            <text>未开放章节</text>
            <radio-group @change="(e: any) => (form.unopened_policy = e.detail.value)">
              <label class="radio-label">
                <radio value="retry" :checked="form.unopened_policy === 'retry'" />稍后重试
              </label>
              <label class="radio-label">
                <radio value="skip" :checked="form.unopened_policy === 'skip'" />直接跳过
              </label>
            </radio-group>
          </view>
          <view class="sheet-actions">
            <button class="btn-plain" size="mini" @click="showForm = false">取消</button>
            <button class="btn-primary" size="mini" :loading="submitting" @click="submit">
              保存
            </button>
          </view>
        </view>
      </view>
    </template>
  </view>
</template>

<style lang="scss" scoped>
.account {
  margin-top: 0;
}
.account-name {
  font-size: 30rpx;
  font-weight: 500;
  margin-bottom: 6rpx;
}
.actions {
  display: flex;
  gap: 12rpx;
}
.mini-btn {
  margin: 0;
  font-size: 22rpx;

  &.danger {
    color: var(--danger);
  }
}
.footer-btn {
  padding: 24rpx;
  margin-top: 40rpx;
}
.mask {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.45);
  z-index: 1000;
  display: flex;
  align-items: flex-end;
}
.sheet {
  width: 100%;
  background: var(--bg-card);
  border-radius: 24rpx 24rpx 0 0;
  padding: 32rpx 32rpx 48rpx;
  box-sizing: border-box;
  max-height: 85vh;
  overflow-y: auto;
}
.sheet-title {
  font-size: 32rpx;
  font-weight: 600;
  margin-bottom: 24rpx;
  text-align: center;
}
.field {
  border: 1rpx solid var(--border-input);
  border-radius: 12rpx;
  padding: 18rpx 20rpx;
  margin-bottom: 20rpx;
  font-size: 28rpx;
  width: 100%;
  box-sizing: border-box;

  &.textarea {
    height: 120rpx;
  }
}
.field-label {
  margin-bottom: 12rpx;
  font-size: 28rpx;
}
.slider {
  flex: 1;
  margin: 0 16rpx;
}
.radio-label {
  margin-right: 24rpx;
  font-size: 26rpx;
}
.credential-row {
  margin-bottom: 12rpx;
}
.sheet-actions {
  display: flex;
  justify-content: flex-end;
  gap: 20rpx;
  margin-top: 16rpx;
}
</style>
