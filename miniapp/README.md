# 学习助手小程序（uni-app）

挂载 [chaoxingXXT_Auto-AI-referee](https://github.com/miaodaxia80-droid/chaoxingXXT_Auto-AI-referee) alpha 分支多租户后端的微信小程序客户端。

- 技术栈：uni-app (Vue3 + TS + Vite) + Pinia
- 构建目标：`mp-weixin`（微信小程序），保留 `h5` 目标作为审核受阻时的兜底
- 后端 API 前缀：`/api/v1`，会话为 HttpOnly Cookie（微信端自动维护）+ `X-CSRF-Token` 头

## 目录结构

```
miniapp/src/
├── api/            # client.ts（uni.request 封装）+ types.ts（与后端对齐）
├── stores/         # auth.ts（启动探测 / wx 静默登录 / 登出）
├── components/     # NeedLogin.vue 未登录引导
├── pages/
│   ├── home/       # 首页：账号/任务统计、进行中任务、人工接管入口
│   ├── accounts/   # 账号：增删改查、启停、配额提示
│   ├── study/      # 学习：选账号→拉课程→勾章节→建任务；任务控制
│   ├── activity/   # 动态：事件流（after_id 增量轮询）+ 人工接管处理
│   └── me/         # 我的：资料、平台答题服务只读、清理历史、登出
└── utils/format.ts # 任务状态/事件中文映射
```

## 本地联调（开发期）

### 1. 启动后端

```bash
cd ../backend
uv sync --locked --extra dev
CX_DATABASE_URL="sqlite:///data/chaoxing.db" uv run python -m alembic upgrade head
uv run python -m uvicorn chaoxing_app.main:create_app --factory --host 0.0.0.0 --port 5002
```

- 首次使用需在浏览器打开 `http://127.0.0.1:5002` 完成管理员初始化，并配置答题集成。
- 微信登录需要环境变量 `CX_WECHAT_APPID` / `CX_WECHAT_SECRET`（未配置时 `/auth/wx/login` 返回 503）。

### 2. 配置后端地址

编辑 `src/config.ts`：

- 模拟器联调：`API_BASE = 'http://127.0.0.1:5002'`（默认）
- 真机预览：改为本机局域网 IP，如 `http://192.168.x.x:5002`，手机与电脑同一 Wi-Fi

### 3. 打开微信开发者工具

1. `pnpm install && pnpm build:mp-weixin`（或 `pnpm dev:mp-weixin` 监听热更）
2. 微信开发者工具 → 导入项目 → 选择 `dist/build/mp-weixin` 目录
3. 右上角「详情 → 本地设置」勾选 **不校验合法域名、web-view（业务域名）、TLS 版本以及 HTTPS 证书**
4. `src/manifest.json` 的 `mp-weixin.appid` 填入自己的测试号 appid（留空则使用游客模式，wx.login 不可用，无法走登录流程）

### 4. 验证链路

开发者工具控制台可见：启动探测（`GET /auth/me`）→ 未登录显示登录门 → 点击「微信一键登录」→ `wx.login` → `POST /auth/wx/login` → 进入首页。

## H5 浏览器预览（开发）

不用微信开发者工具也能在浏览器里验证 UI 与 API 联调：

1. 后端以开发态启动并开启 H5 开发登录：

   ```bash
   CX_ENVIRONMENT=development CX_DEV_LOGIN_ENABLED=1 uv run python -m uvicorn chaoxing_app.main:create_app --factory --host 127.0.0.1 --port 5002
   ```

2. 启动 H5 dev server（vite 会把 `/api` 代理到 5002，同源携带会话 Cookie）：

   ```bash
   cd miniapp && pnpm dev:h5
   ```

3. 浏览器打开 `http://localhost:5173`，点击「进入预览（开发登录）」。
   - `/auth/dev-login` 用本机固定 token 创建/复用开发用户，配额与正式用户一致；
   - 该端点仅在 `CX_DEV_LOGIN_ENABLED=1` 且非 production 环境时存在，其余情况返回 404。

4. H5 生产部署：`pnpm build:h5` 产物与后端同域部署（同一反向代理下），`API_BASE` 自动为空（同源相对路径）。

## 体验版 / 正式版部署

1. 后端部署到微信云托管或云服务器（详见仓库根 `docker-compose.yml`），**必须 HTTPS**。
2. `src/config.ts` 的 `API_BASE` 换成云托管默认域名（免备案）或已备案域名，重新构建。
3. 小程序后台「开发管理 → 服务器域名」配置 request 合法域名。
4. 上传体验版：云托管默认域名可直接配置为合法域名；正式上架需备案域名 + 主体资质。

## 微信开发者工具自动化测试

`scripts/mp_e2e.mjs`（`miniprogram-automator`）可在开发者工具里跑无 UI 依赖的回归：

```bash
# 1. 后端以开发态启动（CX_DEV_LOGIN_ENABLED=1，微信密钥可不配）
# 2. 开自动化会话（首次会提示开启服务端口，输入 y）
/Applications/wechatwebdevtools.app/Contents/MacOS/cli auto \
  --project "$(pwd)/dist/build/mp-weixin" --auto-port 9420
# 3. 跑测试（mock wx.login → 登录降级 → 五页截图到 /tmp）
node scripts/mp_e2e.mjs
```

说明：
- 开发/体验版自动挂载 `__cxLogin`/`__cxLogout` 测试钩子（`envVersion !== 'release'` 才有，正式版不存在）；
- 游客 appid 或后端未配微信密钥时，`wxLogin` 会在微信登录 503 或 8 秒超时后走 `/auth/dev-login` 开发降级（后端门控：仅 `CX_DEV_LOGIN_ENABLED=1` 且非 production 存在，生产必 404）；
- 部分版本开发者工具的渲染层元素查询（`page.$`/`tap`）会挂起，因此断言依赖 storage、后端日志与截图，不用元素查询。

## 已知边界

- 实时日志采用 10 秒 `after_id` 增量轮询（`GET /events`），未接入 SSE（`uni.requestTask.onChunkReceived` 方案留待 P2）。
- H5 目标可编译，但 `wx.login` 在 H5 不可用，需另接 H5 登录方案（P2）。
- 密码/Cookie 仅在创建账号时提交，后端 AES-GCM 加密落库，接口永不回传明文。
- **小程序端 Cookie 由客户端手动管理**：`wx.request` 不自动存储/回传 Set-Cookie（工具与真机一致），`api/client.ts` 从登录响应提取 `cx_session` 存 storage，所有请求（含 SSE 流）手动带 `Cookie` 头。后端若改 `CX_SESSION_COOKIE_NAME`，需同步改 `client.ts` 的 `SESSION_COOKIE_NAME`。
