# 项目重写状态

最后更新：2026-08-13

## 当前结论

项目处于**交付候选**状态。FastAPI + Vue 重写版的核心业务链路、批量工作流、持久化调度、安全基线、答案运行时、运维可观测性、响应式主题 UI、数据库迁移和备份恢复已经完成；旧 Flask 控制面不再参与新应用运行。

这里不使用模糊的“100% 完成”表述。目标功能矩阵除本机 Docker 实构建外均已覆盖，但 legacy importer 有明确的安全/映射边界，单实例与平台协议限制仍然存在，详见 [`FEATURE_PARITY.md`](FEATURE_PARITY.md)。当前状态适合继续做目标环境部署验证和实际账号的受控验收。

## 已交付范围

### 后端与执行模型

- FastAPI `/api/v1` API、首次管理员设置、会话鉴权、登录失败限流、CSRF 与 Origin 校验。
- 超星账号 CRUD、CSV 导入、AES-GCM 凭据存储、密码/Cookie 登录和刷新 Cookie 持久化。
- 旧 Flask SQLite 一次性 importer：默认只读 dry-run、显式 `--apply`、重复账号不覆盖、受支持账号/调度设置映射和凭据重新加密；`use_cookies=false` 时不导入残留 Cookie。
- 课程与章节发现；视频、音频、文档、阅读、空页面和测验任务点。
- SQLite 持久化队列、同账号互斥、活跃任务唯一索引、账号租约、fencing token 和心跳。
- 每个活跃账号独立 worker 子进程；有界章节并发使用隔离 Session。
- Supervisor 操作使用可重入操作锁串行化，运行进程表使用短持有锁保护；健康读取不会被数据库或进程 I/O 长时间阻塞。
- 多课程未完成章节批量建任务；单项/批量暂停、恢复、取消，终态历史批量删除与清理；运行时间窗、安全点控制、崩溃恢复和重试上限。
- 结构化事件、全局/任务 SSE、UI 手动事件归档与双周期保留语义。
- 人工接管投影、单项/批量标记已处理、追加式管理员审计；处理操作不改写章节或任务执行结果。
- CPU、内存、可选温度、进程容量、持久运行、心跳、租约与过期状态健康接口。
- ServerChan、Qmsg、Bark、Telegram 和可重试通知 outbox。

### 答案运行时

- Yanxi、Like、TikuAdapter、OpenAI-compatible、SiliconFlow provider。
- 自动/仅保存/提交模式和覆盖率阈值。
- SQLite 答案缓存、TTL、课程上下文隔离和缓存命中复用。
- 多模型并行、referee、课程上下文与受限 DuckDuckGo 搜索增强。
- 搜索失败降级、provider 能力约束、配置 fail closed 和 HTTP Session 关闭。
- 全局答案 profile、账号继承/覆盖和任务排队配置快照。

### 前端

- Vue 3 + TypeScript + Vite + Naive UI 的全新响应式控制台。
- 概览、账号、任务、活动、设置、首次初始化和登录页面。
- 账号 CSV 导入、启停/编辑/删除、课程搜索、章节选择、多课程批量创建和任务批量控制/终态清理。
- 任务列表使用稳定 offset 分页聚合全部记录；批量课程发现限制为 4 路并发，创建请求按 100 项分块并保留部分成功结果。
- 全局答案/通知设置和账号级答案覆盖 UI。
- 活动页 SSE 自动重连和事件增量合并。
- 概览页人工接管队列和详细系统/worker 健康状态；活动页按日期归档入口。
- 浅色、深色、跟随系统三态主题，本地持久化并适配 reduced-motion/transparency。
- 生产构建固定输出到根目录 `.frontend-dist/`，由 FastAPI 提供 SPA 静态文件。

### 数据与交付

- SQLAlchemy 2 + SQLite WAL + Alembic 版本化迁移链；开发/生产启动严格要求当前 head，只有测试环境使用 `create_all()`。
- SQLite snapshot 备份和未加密 ZIP 归档；数据库与 `master.key` 密文配对验证。
- 根目录 `backups/` 与 `.cxbackup` 归档默认从 Git 和 Docker 构建上下文排除，降低未加密备份误提交风险。
- 离线强制恢复、跨卷持久复制、原子落位、旧数据 rollback 目录和 `.restore.lock` 阶段 journal。
- 多阶段 `Dockerfile`、Compose 持久卷/健康检查和 GitHub Actions 检查矩阵。
- 后端 `uv.lock`、前端 `pnpm-lock.yaml` 和 ESLint 9 flat config。
- Playwright Chromium E2E 及 GitHub Actions 自动浏览器回归。

## 验证基线

以下检查构成发布前的持续验证基线；具体运行环境与最新结果以 GitHub Actions 为准。

| 检查 | 命令或范围 |
| --- | --- |
| 后端测试 | `cd backend && uv run --locked python -m pytest -q` |
| Ruff | `cd backend && uv run --locked python -m ruff check .` |
| mypy strict | `cd backend && uv run --locked python -m mypy` |
| Alembic | 从临时空库执行 `upgrade head`、`check` 和 `downgrade base` |
| 前端类型检查 | `cd frontend && pnpm typecheck` |
| 前端 ESLint | `cd frontend && pnpm lint` |
| 前端生产构建 | `cd frontend && pnpm build` |
| 浏览器回归 | `cd frontend && pnpm e2e`，覆盖初始化、登录、主要路由、批量任务、活动记录和响应式布局 |
| 容器 | GitHub Actions 构建 `linux/amd64` 与 `linux/arm64` 镜像并验证迁移、CLI 和健康检查 |

## 剩余限制与目标环境验收

- 登录失败限流按应用连接上看到的 TCP 对端与规范化管理员名分桶；服务入口不解析 `Forwarded` 或 `X-Forwarded-*`。它是为当前单实例部署设计的进程内有界状态，同机反代还需在代理层配置公网客户端限流；服务重启会清空计数，也不能作为未来多副本部署的共享限流器。
- 只支持一个 API/调度实例；不支持多个 Uvicorn worker 或多个副本共享同一 SQLite 数据库。
- legacy importer 只迁移受支持的账号字段和少量调度设置，不迁移管理员、任务/章节历史、事件、答案缓存、账号答案配置或通知配置；旧库应保留为独立只读归档。
- 任务删除有意限制为终态记录；活动任务必须先取消并到达终态。人工接管“已处理”是审计动作，不会改变原执行结果。
- CPU、内存和温度指标按平台能力返回；无法读取的硬件指标会明确显示不可用。
- 超星非公开协议仍可能随平台页面、验证码或接口变化而失效，需要通过脱敏 fixture 和受控真实账号验收持续维护。
- 本机未进行 Docker 镜像实构建；应以 GitHub Actions 或目标部署机结果补齐环境验证。

这些限制不阻断管理员初始化、账号管理、多课程任务、worker 执行、答题、批量控制、事件、通知和备份恢复的核心路径。

## 关键运行约束

- 生产模式必须通过 HTTPS 使用；`CX_ENVIRONMENT=production` 会启用 Secure Cookie。
- 反向代理部署必须正确配置 `CX_ALLOWED_ORIGINS`，否则跨代理 Origin 的修改请求会被拒绝。
- 反向代理必须覆盖或移除客户端提供的转发头；应用不使用代理头恢复公网来源，公网客户端级登录限流应由代理层承担。
- 只支持一个 API/调度实例，不要使用多个 Uvicorn worker 或多个副本共享同一 SQLite 数据库。
- 开发和生产启动前必须执行 `python -m alembic upgrade head`；非测试环境不会调用 `create_all()`，数据库未到当前 head 时会拒绝启动。
- `import-legacy` 默认只是 dry-run；确认源/目标均离线、报告无意外并完成目标备份后，才使用 `--apply`。
- `master.key` 必须与数据库一起备份；备份 ZIP 未加密，必须按最高敏感级别保管。
- restore 只能在服务和所有 worker 完全停止时运行。残留 `.restore.lock` 需要先按 journal 和 rollback 目录人工核查。
- 根目录旧 `main.py`、`api/`、`web/`、`server.py`、`app.py` 和 `requirements.txt` 不属于新应用入口。
- 不要删除或覆盖用户原有根目录 `data/`；Compose 默认使用新应用专用的 `chaoxing-data/`。不要把任一数据目录、`.env`、数据库、Cookie、API key 或主密钥提交到 Git。

## 发布范围

当前正式应用位于 `backend/`、`frontend/`，相关设计文档位于 `docs/rewrite/`；CI、容器镜像和 Compose 配置分别由 `.github/workflows/`、`Dockerfile` 和 `docker-compose.yml` 维护。根目录旧版代码只作为协议与迁移参考，不参与新版运行。

任何发布提交都必须纳入锁文件、迁移、源码和必要文档，并排除运行时数据、真实测试数据、凭据、Cookie、日志、备份与本地主密钥。

完整启动、迁移、HTTPS、Docker、备份和恢复说明以根目录 [`README.md`](../../README.md) 为准。
