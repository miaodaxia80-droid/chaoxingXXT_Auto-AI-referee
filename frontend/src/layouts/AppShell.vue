<script setup lang="ts">
import { useQuery } from '@tanstack/vue-query'
import {
  Activity,
  CreditCard,
  Gauge,
  GraduationCap,
  ListTodo,
  LogOut,
  Menu,
  Moon,
  Sun,
  SunMoon,
  Settings,
  UserRound,
  Users,
  UsersRound,
  X,
} from 'lucide-vue-next'
import { NButton, NDropdown, NTag, NTooltip, useDialog } from 'naive-ui'
import type { DropdownOption } from 'naive-ui'
import { computed, h, ref } from 'vue'
import type { Component as VueComponent } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { apiRequest } from '@/api/client'
import type { ManualIntervention, OperationsHealth } from '@/api/types'
import { useAuthStore } from '@/stores/auth'
import { useTheme, type ThemePreference } from '@/stores/theme'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const theme = useTheme()
const dialog = useDialog()
const mobileOpen = ref(false)

interface NavItem {
  name: string
  label: string
  icon: VueComponent
  audience: 'all' | 'admin' | 'user'
  group: 'work' | 'admin'
}

const allItems: NavItem[] = [
  { name: 'dashboard', label: '概览', icon: Gauge, audience: 'admin', group: 'work' },
  { name: 'portal', label: '我的账户', icon: UserRound, audience: 'user', group: 'work' },
  { name: 'accounts', label: '账号', icon: Users, audience: 'all', group: 'work' },
  { name: 'tasks', label: '任务', icon: ListTodo, audience: 'all', group: 'work' },
  { name: 'activity', label: '活动', icon: Activity, audience: 'all', group: 'work' },
  { name: 'app-users', label: '用户', icon: UsersRound, audience: 'admin', group: 'admin' },
  { name: 'card-keys', label: '卡密', icon: CreditCard, audience: 'admin', group: 'admin' },
  { name: 'settings', label: '设置', icon: Settings, audience: 'admin', group: 'admin' },
]

const items = computed(() =>
  allItems.filter(
    (item) =>
      item.audience === 'all' || (item.audience === 'admin' ? auth.isAdmin : auth.isAppUser),
  ),
)
const workItems = computed(() => items.value.filter((item) => item.group === 'work'))
const adminItems = computed(() => items.value.filter((item) => item.group === 'admin'))

const health = useQuery({
  queryKey: ['operations-health'],
  queryFn: () => apiRequest<OperationsHealth>('/operations/health'),
  refetchInterval: 15_000,
  enabled: computed(() => auth.isAdmin),
})
const interventions = useQuery({
  queryKey: ['manual-interventions'],
  queryFn: () => apiRequest<ManualIntervention[]>('/operations/interventions'),
  refetchInterval: 30_000,
  enabled: computed(() => auth.isAdmin),
})
const interventionCount = computed(() => interventions.data.value?.length ?? 0)

const serviceState = computed(() => {
  if (health.isError.value) return { tone: 'error', label: '服务不可达' }
  const data = health.data.value
  if (!data) return { tone: '', label: '检查中' }
  if (!data.worker.enabled) return { tone: 'warn', label: '调度已停止' }
  if (data.status !== 'ok' || !data.worker.service_running) return { tone: 'warn', label: '服务需检查' }
  return { tone: 'ok', label: '服务正常' }
})

const pageTitle = computed(() => route.meta.title ?? '控制台')
const pageDescription = computed(() => route.meta.description ?? '')
const brandSubtitle = computed(() => (auth.isAdmin ? '管理后台' : '学习空间'))
const roleLabel = computed(() => (auth.isAdmin ? '管理员' : '普通用户'))

const themeLabel = computed(() => ({ system: '跟随系统', light: '浅色', dark: '深色' })[theme.preference.value])
const activeThemeIcon = computed(() => {
  if (theme.preference.value === 'light') return Sun
  if (theme.preference.value === 'dark') return Moon
  return SunMoon
})
const themeOptions: DropdownOption[] = [
  { label: '跟随系统', key: 'system', icon: () => h(SunMoon, { size: 16 }) },
  { label: '浅色', key: 'light', icon: () => h(Sun, { size: 16 }) },
  { label: '深色', key: 'dark', icon: () => h(Moon, { size: 16 }) },
]

function navigate(name: string) {
  mobileOpen.value = false
  void router.push({ name })
}

function selectTheme(key: string | number): void {
  if (key === 'system' || key === 'light' || key === 'dark') {
    theme.setPreference(key as ThemePreference)
  }
}

function requestLogout() {
  dialog.warning({
    title: '退出登录',
    content: '确认结束当前会话？',
    positiveText: '退出',
    negativeText: '取消',
    async onPositiveClick() {
      await auth.logout()
      await router.replace({ name: 'auth' })
    },
  })
}
</script>

<template>
  <div class="app-shell">
    <div v-if="mobileOpen" class="sidebar-scrim" @click="mobileOpen = false" />
    <aside class="sidebar" :class="{ open: mobileOpen }">
      <div class="brand-row">
        <div class="brand-mark"><GraduationCap :size="19" /></div>
        <div>
          <strong>学习任务控制台</strong>
          <span>{{ brandSubtitle }}</span>
        </div>
        <button class="mobile-close" type="button" aria-label="关闭导航" @click="mobileOpen = false">
          <X :size="18" />
        </button>
      </div>

      <nav class="primary-nav" aria-label="主导航">
        <button
          v-for="item in workItems"
          :key="item.name"
          type="button"
          :class="{ active: route.name === item.name }"
          :aria-current="route.name === item.name ? 'page' : undefined"
          @click="navigate(item.name)"
        >
          <component :is="item.icon" :size="18" />
          <span>{{ item.label }}</span>
          <span
            v-if="item.name === 'dashboard' && interventionCount > 0"
            class="nav-badge"
            :aria-label="`${interventionCount} 项待处理`"
          >{{ interventionCount }}</span>
        </button>
        <template v-if="adminItems.length">
          <div class="nav-group-label">管理</div>
          <button
            v-for="item in adminItems"
            :key="item.name"
            type="button"
            :class="{ active: route.name === item.name }"
            :aria-current="route.name === item.name ? 'page' : undefined"
            @click="navigate(item.name)"
          >
            <component :is="item.icon" :size="18" />
            <span>{{ item.label }}</span>
          </button>
        </template>
      </nav>

      <div class="sidebar-footer">
        <div class="admin-identity">
          <span class="avatar">{{ auth.displayName.slice(0, 1).toUpperCase() }}</span>
          <div>
            <span class="admin-name">{{ auth.displayName }}</span>
            <span class="admin-role">{{ roleLabel }}</span>
          </div>
        </div>
        <NTooltip trigger="hover">
          <template #trigger>
            <NButton quaternary circle aria-label="退出登录" @click="requestLogout">
              <template #icon><LogOut /></template>
            </NButton>
          </template>
          退出登录
        </NTooltip>
      </div>
    </aside>

    <main class="main-column">
      <header class="topbar">
        <button class="menu-button" type="button" aria-label="打开导航" @click="mobileOpen = true">
          <Menu :size="20" />
        </button>
        <div class="topbar-title">
          <h1>{{ pageTitle }}</h1>
          <p v-if="pageDescription">{{ pageDescription }}</p>
        </div>
        <div class="topbar-tools">
          <button
            v-if="auth.isAdmin"
            type="button"
            class="service-pill"
            data-testid="service-pill"
            @click="navigate('dashboard')"
          >
            <span class="status-dot" :class="serviceState.tone" />
            <span class="service-label">{{ serviceState.label }}</span>
          </button>
          <NTag v-else-if="auth.isAppUser" size="small" :bordered="false" type="info">用户</NTag>
          <NDropdown
            trigger="click"
            :options="themeOptions"
            :value="theme.preference.value"
            placement="bottom-end"
            @select="selectTheme"
          >
            <NTooltip trigger="hover">
              <template #trigger>
                <NButton
                  quaternary
                  circle
                  :aria-label="`主题：${themeLabel}`"
                  data-testid="theme-menu"
                >
                  <template #icon><component :is="activeThemeIcon" /></template>
                </NButton>
              </template>
              主题：{{ themeLabel }}
            </NTooltip>
          </NDropdown>
        </div>
      </header>
      <div class="page-content">
        <div class="route-stage">
          <RouterView v-slot="{ Component, route: currentRoute }">
            <Transition name="page-route">
              <div :key="String(currentRoute.name ?? currentRoute.path)" class="route-page">
                <component :is="Component" />
              </div>
            </Transition>
          </RouterView>
        </div>
      </div>
    </main>
  </div>
</template>

<style scoped>
.route-stage {
  position: relative;
}

.route-page {
  position: relative;
  min-width: 0;
}

.page-route-enter-active {
  z-index: 1;
  transition:
    opacity 180ms cubic-bezier(0.23, 1, 0.32, 1),
    transform 180ms cubic-bezier(0.23, 1, 0.32, 1);
}

.page-route-leave-active {
  position: absolute;
  z-index: 0;
  inset: 0;
  width: 100%;
  pointer-events: none;
  transition: opacity 90ms linear;
}

.page-route-enter-from {
  opacity: 0;
  transform: translateY(6px);
}

.page-route-leave-to {
  opacity: 0;
}

@media (max-width: 680px) {
  .page-route-enter-active {
    transition:
      opacity 160ms cubic-bezier(0.23, 1, 0.32, 1),
      transform 160ms cubic-bezier(0.23, 1, 0.32, 1);
  }

  .page-route-enter-from {
    transform: translateY(4px);
  }
}

@media (prefers-reduced-motion: reduce) {
  .page-route-enter-active,
  .page-route-leave-active {
    transition: none;
  }

  .page-route-enter-from,
  .page-route-leave-to {
    opacity: 1;
    transform: none;
  }
}
</style>
