<script setup lang="ts">
import {
  Activity,
  CreditCard,
  Gauge,
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
import { useRoute, useRouter } from 'vue-router'

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
  icon: typeof Gauge
  adminOnly?: boolean
}

const allItems: NavItem[] = [
  { name: 'dashboard', label: '概览', icon: Gauge, adminOnly: true },
  { name: 'portal', label: '我的账户', icon: UserRound },
  { name: 'accounts', label: '账号', icon: Users },
  { name: 'tasks', label: '任务', icon: ListTodo },
  { name: 'activity', label: '活动', icon: Activity },
  { name: 'app-users', label: '用户', icon: UsersRound, adminOnly: true },
  { name: 'card-keys', label: '卡密', icon: CreditCard, adminOnly: true },
  { name: 'settings', label: '设置', icon: Settings, adminOnly: true },
]

const items = computed(() => allItems.filter((item) => !item.adminOnly || auth.isAdmin))

const pageTitle = computed(
  () => allItems.find((item) => item.name === route.name)?.label ?? '控制台',
)
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
    content: '确认结束当前后台会话？',
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
        <div class="brand-mark">CX</div>
        <div>
          <strong>学习任务控制台</strong>
          <span>本地管理</span>
        </div>
        <button class="mobile-close" type="button" aria-label="关闭导航" @click="mobileOpen = false">
          <X :size="18" />
        </button>
      </div>

      <nav class="primary-nav" aria-label="主导航">
        <button
          v-for="item in items"
          :key="item.name"
          type="button"
          :class="{ active: route.name === item.name }"
          @click="navigate(item.name)"
        >
          <component :is="item.icon" :size="18" />
          <span>{{ item.label }}</span>
        </button>
      </nav>

      <div class="sidebar-footer">
        <div class="admin-identity">
          <span class="avatar">{{ auth.displayName.slice(0, 1).toUpperCase() }}</span>
          <span class="admin-name">{{ auth.displayName }}</span>
          <NTag v-if="auth.isAppUser" size="tiny" :bordered="false" type="info">用户</NTag>
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
        <h1>{{ pageTitle }}</h1>
        <div class="topbar-tools">
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
