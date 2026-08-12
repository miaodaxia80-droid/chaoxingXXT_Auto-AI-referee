import { expect, test, type Page, type Route } from '@playwright/test'

import type { Account, StudyTask, SystemEvent } from '../src/api/types'

type ApiState = {
  authenticated: boolean
  setupRequired: boolean
}

type MockApiOptions = Partial<ApiState> & {
  accounts?: Account[]
  events?: SystemEvent[]
  streamEvents?: SystemEvent[]
  tasks?: StudyTask[]
  setupError?: unknown
  onSetupRequest?: () => void
  onTasksRequest?: (url: URL) => void
}

const jsonHeaders = { 'content-type': 'application/json' }
const CENTER_TOLERANCE_PX = 0.75

type CenterOffset = {
  x: number
  y: number
}

async function expectCenteredContent(
  page: Page,
  containerSelector: string,
  childSelector?: string,
): Promise<void> {
  const offsets = await page.locator(containerSelector).evaluateAll(
    (containers, options) =>
      containers.map((container) => {
        const containerRect = container.getBoundingClientRect()
        let contentRect: DOMRect

        if (options.childSelector) {
          const child = container.querySelector(options.childSelector)
          if (!child) {
            throw new Error(
              `${options.childSelector} not found inside ${options.containerSelector}`,
            )
          }
          contentRect = child.getBoundingClientRect()
        } else {
          const range = document.createRange()
          range.selectNodeContents(container)
          contentRect = range.getBoundingClientRect()
        }

        return {
          x:
            contentRect.left +
            contentRect.width / 2 -
            (containerRect.left + containerRect.width / 2),
          y:
            contentRect.top +
            contentRect.height / 2 -
            (containerRect.top + containerRect.height / 2),
        }
      }),
    { childSelector, containerSelector },
  )

  expect(offsets, `${containerSelector} should match at least one element`).not.toHaveLength(0)
  for (const offset of offsets as CenterOffset[]) {
    expect(Math.abs(offset.x), `${containerSelector} horizontal center offset`).toBeLessThanOrEqual(
      CENTER_TOLERANCE_PX,
    )
    expect(Math.abs(offset.y), `${containerSelector} vertical center offset`).toBeLessThanOrEqual(
      CENTER_TOLERANCE_PX,
    )
  }
}

async function json(route: Route, status: number, body: unknown): Promise<void> {
  await route.fulfill({ status, headers: jsonHeaders, body: JSON.stringify(body) })
}

async function mockApi(page: Page, initial: MockApiOptions = {}): Promise<ApiState> {
  const state: ApiState = {
    authenticated: initial.authenticated ?? false,
    setupRequired: initial.setupRequired ?? false,
  }

  await page.route('**/api/v1/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname.replace('/api/v1', '')
    const method = request.method()

    if (path === '/auth/me') {
      await json(
        route,
        state.authenticated ? 200 : 401,
        state.authenticated
          ? { username: 'admin', csrf_token: 'e2e-csrf' }
          : { detail: 'not authenticated' },
      )
      return
    }
    if (path === '/auth/setup' && method === 'GET') {
      await json(route, 200, { required: state.setupRequired })
      return
    }
    if (path === '/auth/setup' && method === 'POST') {
      initial.onSetupRequest?.()
      if (initial.setupError !== undefined) {
        await json(route, 422, { detail: initial.setupError })
        return
      }
      state.setupRequired = false
      await json(route, 201, { ok: true })
      return
    }
    if (path === '/auth/login' && method === 'POST') {
      state.authenticated = true
      await json(route, 200, {
        username: 'admin',
        csrf_token: 'e2e-csrf',
        expires_at: '2030-01-01T00:00:00Z',
      })
      return
    }
    if (path === '/auth/logout' && method === 'POST') {
      state.authenticated = false
      await route.fulfill({ status: 204 })
      return
    }
    if (path === '/health') {
      await json(route, 200, { status: 'ok', version: 'e2e' })
      return
    }
    if (path === '/operations/health') {
      await json(route, 200, {
        status: 'ok',
        cpu: { value: 12 },
        memory: { value: 34 },
        temperature: { value: null },
        worker: {
          service_running: true,
          active_process_count: 0,
          configured_capacity: 2,
          durable_active_run_count: 0,
          stale_run_count: 0,
        },
      })
      return
    }
    if (path === '/operations/interventions') {
      await json(route, 200, [])
      return
    }
    if (path === '/tasks') {
      initial.onTasksRequest?.(url)
      await json(route, 200, initial.tasks ?? [])
      return
    }
    if (path === '/accounts') {
      await json(route, 200, initial.accounts ?? [])
      return
    }
    if (path === '/events') {
      await json(route, 200, initial.events ?? [])
      return
    }
    if (path === '/events/stream') {
      const body = (initial.streamEvents ?? [])
        .map((event) => `id: ${event.id}\nevent: ${event.kind}\ndata: ${JSON.stringify(event)}\n\n`)
        .join('')
      await route.fulfill({
        status: 200,
        headers: { 'content-type': 'text/event-stream' },
        body: body || ': e2e\n\n',
      })
      return
    }
    if (path === '/settings') {
      await json(route, 200, {
        worker_enabled: true,
        run_window_enabled: false,
        run_window_start: '00:00',
        run_window_end: '00:00',
        timezone: 'Asia/Shanghai',
        event_retention_days: 30,
        updated_at: '2030-01-01T00:00:00Z',
      })
      return
    }
    if (path === '/settings/integrations/answer') {
      await json(route, 200, {
        enabled: false,
        provider: 'yanxi',
        submit_mode: 'save_only',
        threshold: 0.8,
        revision: 1,
        config: {
          endpoint: null,
          base_url: null,
          model: null,
          search: null,
          allow_unsafe_endpoint: false,
        },
        profile: null,
        has_tokens: false,
        has_token: false,
        has_api_key: false,
        updated_at: '2030-01-01T00:00:00Z',
      })
      return
    }
    if (path === '/settings/integrations/notifications') {
      await json(route, 200, [])
      return
    }
    await json(route, 404, { detail: `unmocked e2e endpoint: ${method} ${path}` })
  })
  return state
}

test('first-run setup creates the administrator and opens the dashboard', async ({ page }) => {
  await mockApi(page, { setupRequired: true })
  await page.goto('/')

  await expect(page.getByRole('heading', { name: '初始化控制台' })).toBeVisible()
  const passwordHint = page.getByText('8–256 位，仅限 字母/数字/英文符号')
  await expect(passwordHint).toBeVisible()
  const hintLayout = await passwordHint.evaluate((element) => {
    const range = document.createRange()
    range.selectNodeContents(element)
    const textLineTops = new Set(
      Array.from(range.getClientRects())
        .filter((rect) => rect.width > 0 && rect.height > 0)
        .map((rect) => Math.round(rect.top)),
    )

    return {
      lineCount: textLineTops.size,
      whiteSpace: window.getComputedStyle(element).whiteSpace,
      overflowsHorizontally: element.scrollWidth > element.clientWidth + 1,
      overflowsVertically: element.scrollHeight > element.clientHeight + 1,
    }
  })
  expect(hintLayout).toEqual({
    lineCount: 1,
    whiteSpace: 'nowrap',
    overflowsHorizontally: false,
    overflowsVertically: false,
  })
  await page.getByLabel('管理员账号').fill('admin')
  await page.getByLabel('密码', { exact: true }).fill('abcdefgh')
  await page.getByLabel('确认密码').fill('abcdefgh')
  await page.getByRole('button', { name: '创建并登录' }).click()

  await expect(page).toHaveURL('/')
  await expect(page.getByRole('heading', { name: '概览' })).toBeVisible()
})

test('first-run setup updates password guidance while typing', async ({ page }) => {
  let setupRequests = 0
  await mockApi(page, {
    setupRequired: true,
    onSetupRequest: () => {
      setupRequests += 1
    },
  })
  await page.goto('/')

  const password = page.getByLabel('密码', { exact: true })
  const passwordControl = page.locator('.password-field .n-input')
  const passwordFeedback = page.locator('.password-field .n-form-item-feedback')
  const passwordStateBorder = page.locator('.password-field .n-input__state-border')

  await expect(passwordFeedback).toHaveText('8–256 位，仅限 字母/数字/英文符号')
  await expect(passwordControl).not.toHaveClass(/n-input--error-status/)
  await expect(password).toHaveAttribute('aria-invalid', 'false')

  await password.fill('abcdefg')
  await expect(passwordControl).toHaveClass(/n-input--error-status/)
  await expect(passwordFeedback).toHaveText('密码不符合要求，请重新输入')
  await expect(password).toHaveAttribute('aria-invalid', 'true')
  await expect(passwordStateBorder).toHaveCSS('transition-property', /border-color/)

  await password.fill('abcd efgh')
  await expect(passwordControl).toHaveClass(/n-input--error-status/)

  await password.fill('Abcd!234')
  await expect(passwordControl).not.toHaveClass(/n-input--error-status/)
  await expect(passwordFeedback).toHaveText('8–256 位，仅限 字母/数字/英文符号')
  await expect(password).toHaveAttribute('aria-invalid', 'false')
  expect(setupRequests).toBe(0)
})

test('first-run setup marks mismatched password confirmation while typing', async ({ page }) => {
  await mockApi(page, { setupRequired: true })
  await page.goto('/')

  const password = page.getByLabel('密码', { exact: true })
  const confirmation = page.getByLabel('确认密码')
  const confirmationControl = page.locator('.confirm-password-field .n-input')
  const confirmationFeedback = page.locator(
    '.confirm-password-field .n-form-item-feedback',
  )
  const confirmationBorder = page.locator(
    '.confirm-password-field .n-input__state-border',
  )

  await password.fill('Abcd!234')
  await expect(confirmationControl).not.toHaveClass(/n-input--error-status/)
  await expect(confirmation).toHaveAttribute('aria-invalid', 'false')

  await confirmation.fill('Abcd!235')
  await expect(confirmationControl).toHaveClass(/n-input--error-status/)
  await expect(confirmationFeedback).toHaveText('密码不一致')
  await expect(confirmation).toHaveAttribute('aria-invalid', 'true')
  await expect(confirmationBorder).toHaveCSS('transition-property', /border-color/)

  await confirmation.fill('Abcd!234')
  await expect(confirmationControl).not.toHaveClass(/n-input--error-status/)
  await expect(confirmation).toHaveAttribute('aria-invalid', 'false')
  await expect(page.getByText('密码不一致', { exact: true })).toHaveCount(0)

  await password.fill('Abcd!236')
  await expect(confirmationControl).toHaveClass(/n-input--error-status/)
  await expect(confirmationFeedback).toHaveText('密码不一致')

  await confirmation.fill('')
  await expect(confirmationControl).not.toHaveClass(/n-input--error-status/)
  await expect(confirmation).toHaveAttribute('aria-invalid', 'false')
  await expect(page.getByText('密码不一致', { exact: true })).toHaveCount(0)
})

test('first-run setup validates the password before sending a request', async ({ page }) => {
  let setupRequests = 0
  await mockApi(page, {
    setupRequired: true,
    onSetupRequest: () => {
      setupRequests += 1
    },
  })
  await page.goto('/')
  await page.getByLabel('管理员账号').fill('admin')

  for (const [password, message] of [
    ['abcdefg', '密码至少需要 8 个字符'],
    ['abcd efgh', '密码只能包含英文字母、数字和英文符号，且不能包含空格'],
    ['密码abcdef', '密码只能包含英文字母、数字和英文符号，且不能包含空格'],
  ]) {
    await page.getByLabel('密码', { exact: true }).fill(password)
    await page.getByLabel('确认密码').fill(password)
    await page.getByRole('button', { name: '创建并登录' }).click()
    await expect(page.getByText(message, { exact: true })).toBeVisible()
  }

  expect(setupRequests).toBe(0)
})

test('structured validation errors are rendered without object coercion', async ({ page }) => {
  await mockApi(page, {
    setupRequired: true,
    setupError: [
      {
        type: 'value_error',
        loc: ['body', 'password'],
        msg: 'Value error, 该密码不符合服务器策略',
        input: 'must-not-be-rendered',
      },
    ],
  })
  await page.goto('/')
  await page.getByLabel('管理员账号').fill('admin')
  await page.getByLabel('密码', { exact: true }).fill('abcdefgh')
  await page.getByLabel('确认密码').fill('abcdefgh')
  await page.getByRole('button', { name: '创建并登录' }).click()

  await expect(page.getByText('密码：该密码不符合服务器策略', { exact: true })).toBeVisible()
  await expect(page.getByText('[object Object]', { exact: true })).toHaveCount(0)
  await expect(page.getByText('must-not-be-rendered', { exact: true })).toHaveCount(0)
})

test('existing administrator can log in', async ({ page }) => {
  await mockApi(page)
  await page.goto('/auth')

  await expect(page.getByRole('heading', { name: '管理员登录' })).toBeVisible()
  await page.getByLabel('管理员账号').fill('admin')
  await page.getByLabel('密码').fill('correct-horse-battery-staple')
  await page.getByRole('button', { name: '登录' }).click()
  await expect(page.getByRole('heading', { name: '概览' })).toBeVisible()
})

test('authenticated navigation reaches every primary view', async ({ page }) => {
  await mockApi(page, { authenticated: true })
  await page.goto('/')

  for (const label of ['账号', '任务', '活动', '设置', '概览']) {
    await page.getByRole('button', { name: label, exact: true }).click()
    await expect(page.getByRole('heading', { name: label, exact: true })).toBeVisible()
  }
})

test('activity groups a task run, preserves its outcome, and exposes technical details', async ({ page }) => {
  const task: StudyTask = {
    id: 'task-activity-1',
    account_id: 1,
    account_label: '主账号',
    course_id: 'course-activity-1',
    class_id: 'class-activity-1',
    cpi: 'cpi-activity-1',
    course_title: '测试课程',
    status: 'needs_attention',
    desired_state: 'run',
    priority: 0,
    selected_chapter_ids: ['chapter-activity-1'],
    chapter_total: 1,
    chapter_succeeded: 0,
    chapter_needs_attention: 1,
    created_at: '2030-01-01T00:00:00Z',
    updated_at: '2030-01-01T00:00:09Z',
    run_after: '2030-01-01T00:00:00Z',
    started_at: '2030-01-01T00:00:01Z',
    finished_at: '2030-01-01T00:00:09Z',
    last_error: 'platform_response_invalid',
  }
  const baseEvent = {
    task_id: task.id,
    account_id: 1,
    chapter_id: 'chapter-activity-1',
    chapter_title: '1.1 测试章节',
  }
  const taskEvents: SystemEvent[] = [
    {
      ...baseEvent,
      id: 14,
      kind: 'task.worker_completed',
      level: 'info',
      payload: { status: 'needs_attention', exit_reason: 'needs_attention' },
      occurred_at: '2030-01-01T00:00:09Z',
    },
    {
      ...baseEvent,
      id: 13,
      kind: 'task.needs_attention',
      level: 'warning',
      payload: { status: 'needs_attention' },
      occurred_at: '2030-01-01T00:00:08Z',
    },
    {
      ...baseEvent,
      id: 12,
      kind: 'chapter.failed',
      level: 'error',
      payload: { reason: 'platform_response_invalid' },
      occurred_at: '2030-01-01T00:00:07Z',
    },
    {
      ...baseEvent,
      id: 11,
      kind: 'chapter.started',
      level: 'info',
      payload: { status: 'running' },
      occurred_at: '2030-01-01T00:00:02Z',
    },
    {
      ...baseEvent,
      id: 10,
      kind: 'task.claimed',
      level: 'info',
      payload: { status: 'running' },
      occurred_at: '2030-01-01T00:00:01Z',
    },
  ]

  await mockApi(page, {
    authenticated: true,
    accounts: [
      {
        id: 1,
        remark: '主账号',
        username_hint: '138****0000',
        enabled: true,
        user_agent: 'e2e',
        speed: 1,
        chapter_concurrency: 1,
        unopened_policy: 'retry',
        has_password: true,
        has_cookies: true,
        answer_profile_override: null,
      },
    ],
    tasks: [task],
    events: taskEvents,
    // Replay the last persisted event to exercise id-based SSE deduplication.
    streamEvents: [taskEvents[0]!],
  })
  await page.goto('/activity')

  const group = page.getByTestId('activity-group').filter({ hasText: task.course_title })
  await expect(group).toHaveCount(1)
  await expect(group).toContainText('需要处理')
  await expect(group).not.toContainText('执行进程结束')
  await expect(page.getByTestId('activity-group')).toHaveCount(1)

  await group.getByRole('button', { name: '展开技术详情' }).click()
  await expect(group.getByTestId('activity-timeline')).toBeVisible()
  await expect(group.getByText('chapter.failed', { exact: true })).toBeVisible()
  await expect(group.getByText('task.worker_completed', { exact: true })).toBeVisible()
  await expect(group.getByText('platform_response_invalid', { exact: true })).toBeVisible()

  await group.getByRole('button', { name: '前往任务' }).click()
  await expect(page).toHaveURL('/tasks')
})

test('activity result filters keep standalone system records separate', async ({ page }) => {
  const succeededTask: StudyTask = {
    id: 'task-activity-complete',
    account_id: 2,
    account_label: '学习账号',
    course_id: 'course-activity-complete',
    class_id: 'class-activity-complete',
    cpi: 'cpi-activity-complete',
    course_title: '高等数学',
    status: 'succeeded',
    desired_state: 'run',
    priority: 0,
    selected_chapter_ids: ['chapter-complete'],
    chapter_total: 1,
    chapter_succeeded: 1,
    chapter_needs_attention: 0,
    created_at: '2030-01-02T00:00:00Z',
    updated_at: '2030-01-02T00:00:03Z',
    run_after: '2030-01-02T00:00:00Z',
    started_at: '2030-01-02T00:00:01Z',
    finished_at: '2030-01-02T00:00:03Z',
    last_error: null,
  }
  const events: SystemEvent[] = [
    {
      id: 23,
      task_id: null,
      account_id: null,
      chapter_id: null,
      chapter_title: null,
      kind: 'notification.failed',
      level: 'error',
      payload: { reason: 'network_error' },
      occurred_at: '2030-01-02T00:00:05Z',
    },
    {
      id: 22,
      task_id: null,
      account_id: null,
      chapter_id: null,
      chapter_title: null,
      kind: 'notification.sent',
      level: 'info',
      payload: { status: 'delivered' },
      occurred_at: '2030-01-02T00:00:04Z',
    },
    {
      id: 21,
      task_id: succeededTask.id,
      account_id: succeededTask.account_id,
      chapter_id: 'chapter-complete',
      chapter_title: '第一章 极限',
      kind: 'task.worker_completed',
      level: 'info',
      payload: { status: 'succeeded' },
      occurred_at: '2030-01-02T00:00:03Z',
    },
    {
      id: 20,
      task_id: succeededTask.id,
      account_id: succeededTask.account_id,
      chapter_id: 'chapter-complete',
      chapter_title: '第一章 极限',
      kind: 'chapter.succeeded',
      level: 'info',
      payload: { status: 'succeeded' },
      occurred_at: '2030-01-02T00:00:02Z',
    },
  ]
  await mockApi(page, { authenticated: true, tasks: [succeededTask], events })
  await page.goto('/activity')

  await expect(page.getByTestId('activity-group')).toHaveCount(3)

  const filter = page.getByLabel('按活动状态筛选')
  await filter.click()
  await page.locator('.n-base-select-option').filter({ hasText: '已完成' }).click()
  await expect(page.getByTestId('activity-group')).toHaveCount(1)
  await expect(page.getByTestId('activity-group')).toContainText('任务已完成')
  await expect(page.getByTestId('activity-group')).toContainText(succeededTask.course_title)

  await filter.click()
  await page.locator('.n-base-select-option').filter({ hasText: '系统记录' }).click()
  await expect(page.getByTestId('activity-group')).toHaveCount(2)

  const failedNotification = page
    .getByTestId('activity-group')
    .filter({ hasText: '通知发送失败' })
  const deliveredNotification = page
    .getByTestId('activity-group')
    .filter({ hasText: '通知已发送' })
  await expect(failedNotification).toHaveCount(1)
  await expect(deliveredNotification).toHaveCount(1)
  await failedNotification.getByRole('button', { name: '展开技术详情' }).click()
  await deliveredNotification.getByRole('button', { name: '展开技术详情' }).click()
  await expect(
    failedNotification.getByText('notification.failed', { exact: true }),
  ).toBeVisible()
  await expect(
    deliveredNotification.getByText('notification.sent', { exact: true }),
  ).toBeVisible()
})

test('account credential forms opt out of saved administrator autofill', async ({ page }) => {
  await mockApi(page, { authenticated: true })
  await page.goto('/accounts')

  await page.getByRole('button', { name: '添加账号' }).click()
  const accountForm = page.getByRole('heading', { name: '添加学习通账号' }).locator('..').locator('form')
  await expect(accountForm).toHaveAttribute('autocomplete', 'off')
  await expect(accountForm.locator('input[name="cx-create-account"]')).toHaveAttribute(
    'autocomplete',
    'off',
  )
  await expect(accountForm.locator('input[name="cx-create-remark"]')).toHaveAttribute(
    'autocomplete',
    'off',
  )
  await expect(accountForm.locator('input[name="cx-create-password"]')).toHaveAttribute(
    'autocomplete',
    'new-password',
  )
  await expect(accountForm.locator('textarea[name="cx-create-cookie"]')).toHaveAttribute(
    'autocomplete',
    'off',
  )
})

test('icons and step markers stay centered in their visual containers', async ({ page }) => {
  await mockApi(page, { authenticated: true })

  await page.goto('/')
  await expect(page.locator('.metric-icon')).toHaveCount(4)
  await expectCenteredContent(page, '.metric-icon', 'svg')

  await page.goto('/settings')
  await expect(page.locator('.profile-icon')).toBeVisible()
  await expectCenteredContent(page, '.profile-icon', 'svg')

  await page.goto('/tasks')
  await expect(page.locator('.step-index')).toHaveCount(3)
  await expectCenteredContent(page, '.step-index')
})

test('desktop task selection keeps the bulk toolbar and task table visible', async ({ page }) => {
  const taskRequests: URL[] = []
  const tasks: StudyTask[] = [
    {
      id: 'task-1',
      account_id: 1,
      account_label: '主账号',
      course_id: 'course-1',
      class_id: 'class-1',
      cpi: 'cpi-1',
      course_title: '线性代数',
      status: 'queued',
      desired_state: 'run',
      priority: 0,
      selected_chapter_ids: ['chapter-1'],
      chapter_total: 1,
      chapter_succeeded: 0,
      chapter_needs_attention: 0,
      created_at: '2030-01-01T00:00:00Z',
      updated_at: '2030-01-01T00:00:00Z',
      run_after: '2030-01-01T00:00:00Z',
      started_at: null,
      finished_at: null,
      last_error: null,
    },
    {
      id: 'task-2',
      account_id: 1,
      account_label: '主账号',
      course_id: 'course-2',
      class_id: 'class-2',
      cpi: 'cpi-2',
      course_title: '大学英语',
      status: 'paused',
      desired_state: 'pause',
      priority: 0,
      selected_chapter_ids: ['chapter-2'],
      chapter_total: 2,
      chapter_succeeded: 1,
      chapter_needs_attention: 0,
      created_at: '2029-12-31T00:00:00Z',
      updated_at: '2029-12-31T00:00:00Z',
      run_after: '2029-12-31T00:00:00Z',
      started_at: null,
      finished_at: null,
      last_error: null,
    },
  ]
  await mockApi(page, {
    authenticated: true,
    tasks,
    onTasksRequest: (url) => taskRequests.push(url),
  })

  await page.goto('/tasks')
  const table = page.locator('.desktop-task-table')
  await expect(table).toBeVisible()
  await expect(table.getByText('线性代数', { exact: true })).toBeVisible()
  await expect(table.getByText('大学英语', { exact: true })).toBeVisible()
  await expect
    .poll(() =>
      taskRequests.some(
        (url) => url.searchParams.get('limit') === '200' && url.searchParams.get('offset') === '0',
      ),
    )
    .toBe(true)

  await table.locator('tbody tr').filter({ hasText: '线性代数' }).locator('.n-checkbox').click()

  await expect(page.locator('.bulk-task-toolbar')).toBeVisible()
  await expect(page.getByText('已选 1 项', { exact: true })).toBeVisible()
  await expect(table).toBeVisible()
  await expect(table.locator('tbody tr')).toHaveCount(2)
  await expect(table.getByText('大学英语', { exact: true })).toBeVisible()
})

test('mobile views do not overflow at 390x844', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await mockApi(page, { authenticated: true })

  for (const path of ['/', '/accounts', '/tasks', '/activity', '/settings']) {
    await page.goto(path)
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
  }
})

test('theme choice persists and system mode follows the OS preference', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'dark', reducedMotion: 'reduce' })
  await mockApi(page, { authenticated: true })
  await page.goto('/')

  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
  await page.getByTestId('theme-menu').click()
  await page.getByText('浅色', { exact: true }).click()
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light')
  await expect.poll(() => page.evaluate(() => localStorage.getItem('cx.theme'))).toBe('light')

  await page.reload()
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light')
  await page.getByTestId('theme-menu').click()
  await page.getByText('跟随系统', { exact: true }).click()
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
  await expect.poll(() => page.evaluate(() => localStorage.getItem('cx.theme'))).toBe('system')
})
