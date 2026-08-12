import { createRouter, createWebHistory } from 'vue-router'

import AppShell from '@/layouts/AppShell.vue'
import { useAuthStore } from '@/stores/auth'

const AccountsPage = () => import('@/pages/AccountsPage.vue')
const ActivityPage = () => import('@/pages/ActivityPage.vue')
const AuthPage = () => import('@/pages/AuthPage.vue')
const DashboardPage = () => import('@/pages/DashboardPage.vue')
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
        { path: '', name: 'dashboard', component: DashboardPage },
        { path: 'accounts', name: 'accounts', component: AccountsPage },
        { path: 'tasks', name: 'tasks', component: TasksPage },
        { path: 'activity', name: 'activity', component: ActivityPage },
        { path: 'settings', name: 'settings', component: SettingsPage },
      ],
    },
  ],
})

router.beforeEach(async (to) => {
  const auth = useAuthStore()
  await auth.bootstrap()
  if (to.meta.requiresAuth && auth.phase !== 'authenticated') return { name: 'auth' }
  if (to.name === 'auth' && auth.phase === 'authenticated') return { name: 'dashboard' }
})

export default router
