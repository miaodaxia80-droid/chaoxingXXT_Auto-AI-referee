import { createRouter, createWebHistory } from 'vue-router'

import AppShell from '@/layouts/AppShell.vue'
import { useAuthStore } from '@/stores/auth'

const AccountsPage = () => import('@/pages/AccountsPage.vue')
const ActivityPage = () => import('@/pages/ActivityPage.vue')
const AppUsersPage = () => import('@/pages/AppUsersPage.vue')
const AuthPage = () => import('@/pages/AuthPage.vue')
const CardKeysPage = () => import('@/pages/CardKeysPage.vue')
const DashboardPage = () => import('@/pages/DashboardPage.vue')
const PortalPage = () => import('@/pages/PortalPage.vue')
const SettingsPage = () => import('@/pages/SettingsPage.vue')
const TasksPage = () => import('@/pages/TasksPage.vue')

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/auth', name: 'auth', component: AuthPage },
    {
      path: '/',
      component: AppShell,
      meta: { requiresAuth: true },
      children: [
        { path: '', name: 'dashboard', component: DashboardPage, meta: { requiresAdmin: true } },
        { path: 'accounts', name: 'accounts', component: AccountsPage },
        { path: 'tasks', name: 'tasks', component: TasksPage },
        { path: 'activity', name: 'activity', component: ActivityPage },
        { path: 'portal', name: 'portal', component: PortalPage },
        { path: 'app-users', name: 'app-users', component: AppUsersPage, meta: { requiresAdmin: true } },
        { path: 'card-keys', name: 'card-keys', component: CardKeysPage, meta: { requiresAdmin: true } },
        { path: 'settings', name: 'settings', component: SettingsPage, meta: { requiresAdmin: true } },
      ],
    },
  ],
})

router.beforeEach(async (to) => {
  const auth = useAuthStore()
  await auth.bootstrap()
  if (to.meta.requiresAuth && auth.phase !== 'authenticated') return { name: 'auth' }
  if (to.name === 'auth' && auth.phase === 'authenticated') {
    return { name: auth.isAdmin ? 'dashboard' : 'portal' }
  }
  // 管理员专属页面：普通用户一律送往自己的面板
  if (to.meta.requiresAdmin && auth.phase === 'authenticated' && !auth.isAdmin) {
    return { name: 'portal' }
  }
  // 管理员无需看用户面板
  if (to.name === 'portal' && auth.isAdmin) {
    return { name: 'dashboard' }
  }
})

export default router
