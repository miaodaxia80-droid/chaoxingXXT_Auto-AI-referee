import automator from 'miniprogram-automator'
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
setTimeout(() => { console.log('\n⏰ 全局超时'); process.exit(2) }, 100_000).unref?.()

const mp = await automator.connect({ wsEndpoint: 'ws://127.0.0.1:9420' })
console.log('✅ 已连接自动化端口')
await sleep(2500)

// 0. 钩子存在性（envVersion 非 release 才有）
const hasHook = await mp.evaluate(() => typeof globalThis.__cxLogin === 'function')
console.log(hasHook ? '✅ 测试钩子 __cxLogin 已挂载（非正式版）' : '❌ 钩子未挂载')

// 1. mock wx.login → __cxLogin → 后端 503 → dev 降级
await mp.mockWxMethod('login', { code: 'automator-mock-code', errMsg: 'login:ok' })
const loginResult = await mp.evaluate(() => globalThis.__cxLogin().then(
  () => 'ok',
  (error) => `failed: ${error && error.message}`,
))
console.log(loginResult === 'ok' ? '✅ 登录流程完成（微信失败→开发降级）' : `❌ 登录失败: ${loginResult}`)
await sleep(1500)

// 2. 会话落库验证：storage 里有 csrf
const csrf = await mp.callWxMethod('getStorageSync', 'cx.csrf')
console.log(csrf ? '✅ CSRF 已写入 storage（会话建立）' : '❌ storage 无 csrf')

// 3. reLaunch 首页 → 截图
await mp.reLaunch('/pages/home/home')
await sleep(3500)
await mp.screenshot({ path: '/tmp/cx_mp_1_home.png' })
console.log('✅ 首页截图')

// 4. 各 tab 截图（switchTab 走服务层）
for (const [route, name] of [
  ['/pages/accounts/accounts', 'accounts'],
  ['/pages/study/study', 'study'],
  ['/pages/activity/activity', 'activity'],
  ['/pages/me/me', 'me'],
]) {
  await mp.switchTab(route)
  await sleep(3000)
  await mp.screenshot({ path: `/tmp/cx_mp_${name}.png` })
  console.log(`✅ ${name} 页截图`)
}

await mp.disconnect()
process.exit(0)
