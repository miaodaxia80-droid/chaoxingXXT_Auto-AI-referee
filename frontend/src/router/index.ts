import { createRouter, createWebHistory } from 'vue-router'

import AppShell from '@/layouts/AppShell.vue'
import { useAuthStore } from '@/stores/auth'

declare module 'vue-router' {
  interface RouteMeta {
    requiresAuth?: boolean
    requiresAdmin?: boolean
    userOnly?: boolean
    title?: string
    description?: string
  }
}

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
        {
          path: '',
          name: 'dashboard',
          component: DashboardPage,
          meta: { requiresAdmin: true, title: '概览', description: '任务进度、待处理事项与服务状态' },
        },
        {
          path: 'portal',
          name: 'portal',
          component: PortalPage,
          meta: { userOnly: true, title: '我的账户', description: '学习权益与上手指引' },
        },
        {
          path: 'accounts',
          name: 'accounts',
          component: AccountsPage,
          meta: { title: '账号', description: '学习通账号凭据加密保存，列表仅显示脱敏信息' },
        },
        {
          path: 'tasks/:taskId?',
          name: 'tasks',
          component: TasksPage,
          meta: { title: '任务', description: '创建、筛选与管理学习任务' },
        },
        {
          path: 'activity',
          name: 'activity',
          component: ActivityPage,
          meta: { title: '活动', description: '任务执行过程与系统事件' },
        },
        {
          path: 'app-users',
          name: 'app-users',
          component: AppUsersPage,
          meta: { requiresAdmin: true, title: '用户', description: '普通用户账号、权益与配额' },
        },
        {
          path: 'card-keys',
          name: 'card-keys',
          component: CardKeysPage,
          meta: { requiresAdmin: true, title: '卡密', description: '批量生成时间卡与次数卡，明文仅在生成时展示一次' },
        },
        {
          path: 'settings',
          name: 'settings',
          component: SettingsPage,
          meta: { requiresAdmin: true, title: '设置', description: '运行调度、答案服务与任务通知' },
        },
      ],
    },
    { path: '/:pathMatch(.*)*', redirect: '/' },
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
  if (to.meta.userOnly && auth.isAdmin) {
    return { name: 'dashboard' }
  }
})

export default router
