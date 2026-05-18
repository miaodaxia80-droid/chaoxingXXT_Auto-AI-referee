# 超星学习通 · 高级后台

<p align="center">
    <a href="https://github.com/Samueli924/chaoxing" target="_blank">
        <img src="https://img.shields.io/github/stars/Samueli924/chaoxing" alt="Github Stars" />
    </a>
    <a href="https://github.com/Samueli924/chaoxing" target="_blank">
        <img src="https://img.shields.io/github/forks/Samueli924/chaoxing" alt="Github Forks" />
    </a>
</p>

基于 [Samueli924/chaoxing](https://github.com/Samueli924/chaoxing) 的自动化任务点脚本，扩展出 **Web 可视化管理后台**、**多账号调度**、**独立设备 UA**、**多模型 AI 协同答题**、**联网搜索增强** 等能力。

---

## 项目亮点

### 1. Web 可视化管理后台
- Flask + SQLite，浏览器访问 `http://IP:5002` 即可管理所有账号
- 支持用户管理、学习中心、系统日志、人工接管、全局设置
- 深色/浅色主题切换，支持自动跟随系统主题

### 2. 多账号并行管理
- 每个账号独立配置：速度、章节并发、题库、AI 模型
- 每个账号独立 Session / Cookies / User-Agent，互不影响
- 支持多账号并发调度、同账号互斥、运行中任务强制结束

### 3. 多模型 AI 协同答题
- 支持 OpenAI 兼容模型、硅基流动、TikuAdapter、言溪、Like 等多种题库
- 多模型协同答题可配置 3 模型并行 + Referee 裁决
- 支持联网搜索增强与课程上下文注入

### 4. 实时监控与人工接管
- 仪表盘展示账号数、任务数、今日完成、学习进度
- 支持系统状态展示：CPU、温度、内存、队列状态
- 题库覆盖率不足、章节失败、未提交任务自动进入人工接管列表

### 5. Docker / 树莓派部署友好
- 支持 Docker Compose 启动
- 适合 NAS、树莓派等常驻设备运行
- 数据库和缓存持久化到宿主机，容器重建不丢数据

---

## 快速开始

### Web 后台模式（推荐）

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 启动服务
python server.py

# 3. 浏览器访问
http://localhost:5002
```

### CLI 命令行模式（兼容原有方式）

```bash
# 直接运行
python main.py

# 配置文件
python main.py -c config.ini

# 命令行参数
python main.py -u 手机号 -p 密码 -l 课程ID1,课程ID2 -s 1.5 -j 4
```

### Docker 部署

```bash
docker compose up -d --build
```

访问：

```text
http://localhost:5002
```

数据库和缓存默认持久化在 `./data/` 目录。

---

## 页面说明

| 页面 | 功能 |
|------|------|
| **控制台** | 总览：账号数、运行中任务、今日完成；子面板：学习进度、章节日志、人工接管 |
| **用户管理** | 添加/导入/编辑用户，配置题库、AI 模型、多模型协同参数 |
| **学习中心** | 选择用户、拉取课程、勾选课程/章节、开始学习、查看任务队列 |
| **全局设置** | 并发、时区、系统状态、题库默认配置、运行时间段、主题跟随等 |
| **系统日志** | 实时终端日志、操作日志、章节进度日志 |

---

## 配置说明

复制 `config_template.ini` 为 `config.ini`，主要配置项如下。

**[common]**
- `username` / `password`：登录凭据
- `speed`：视频倍速（1 ~ 2）
- `jobs`：同时进行的章节数
- `notopen_action`：未开放章节处理：`retry` / `continue`
- `user_agent`：自定义 User-Agent（可选）

**[tiku]**
- `provider`：题库/模型：`AI`、`SiliconFlow`、`TikuYanxi`、`TikuLike`、`TikuAdapter`
- `multi_model`：是否启用多模型协同（`true` / `false`）
- `submit`：提交模式：`true`（达到覆盖率自动提交）/ `false`（仅保存）
- `cover_rate`：最低覆盖率（0 ~ 1）

---

## 更新日志

### v2.0.2
- 新增运行时间段外自动停止任务，并按未完成章节自动创建恢复任务，下一时间段继续执行
- 修复运行时间段恢复链路中的章节 ID 记录与恢复逻辑，避免恢复任务选不中章节
- 系统实时日志补回课程总知识点、当前章节、剩余知识点和视频进度等结构化进度信息
- 优化 AI 搜题性能：复用 AI Client，支持可配置的有限并发搜题，并可控制仅在题目数较多时启用
- 全局设置新增实时日志搜题结果展示粒度、自动跟随系统深浅色开关，前端支持默认跟随系统主题
- 学习中心补充“全选”“选择所有未完成课程”等批量选择能力，并统一列表中的备注优先显示逻辑

### v2.0.1
- 修复子进程任务启动时误触发任务状态回收，导致队列中的等待任务直接变成“已停止”的问题
- 修复新增账号任务后，已在执行中的任务在前端被错误标记为“已停止”但实际仍在运行的问题
- 调整 Web 初始化逻辑，仅在真正的服务主进程启动时执行异常任务回收，避免影响运行中的学习任务

### v2.0.0
- 任务队列重构为多账号并发调度、同账号互斥，支持强制结束运行中任务
- 修复任务卡在等待、清空队列后账号占位不释放的问题
- 学习进度总览修正为真实章节总数显示，不再出现 `1/1 -> 50/50` 的假进度
- 首页新增章节日志清空、学习进度清空、首页用户显示备注开关
- 任务队列支持单任务删除、清空队列，以及更清晰的暂停/恢复控制
- SSE 日志与章节流改为数据库轮询，提升树莓派部署下的稳定性

### v1.9.0
- 新增运行时间段设置，支持按时间窗口自动暂停新任务启动
- 首页系统状态支持开关控制，并增加 CPU、温度、内存、队列状态展示
- 账号选择 `AI` 题库时可直接继承全局 AI 配置，无需重复填写 token
- 学习中心用户选择支持显示“备注（手机号）”

### v1.8.0
- 学习中心改为先加载课程列表，再异步补课程进度
- 新增课程进度抓取并发配置，优化多课程用户的加载速度
- 首页监测支持自动刷新，移动端界面补充响应式适配
- 增加人工接管单条清除与一键清除

### v1.7.0
- 引入多账号独立 Session、独立 Cookies、独立 User-Agent 隔离
- 新增用户级随机 UA 与批量随机 UA 功能
- 增加全局时区设置，统一首页、日志和任务时间显示
- 调整 SQLite 初始化与持久化逻辑，提升多任务场景稳定性

### v1.6.0
- 新增 Web 管理后台、用户管理、学习中心、系统日志等基础页面
- 支持多账号维护、课程拉取、章节日志查看和后台任务执行
- 增加 Docker Web 部署能力，便于树莓派、NAS 等设备运行

### v1.5.0
- 项目正式更名为「高级后台」，整合所有模块为统一发行版
- Web 管理后台功能完整化：用户管理、学习中心、系统日志、实时终端
- 多模型 AI 协同答题稳定版，裁判模型裁决 + 联网搜索增强
- 保留原有 CLI 工作流，兼容配置文件与命令行运行模式
- README 全面改版，补充架构说明与使用文档

### v1.4.0
- 新增 `EnsembleTiku` 多模型并行答题框架（3 模型 + Referee 裁决）
- 新增 `api/web_search.py` 联网搜索模块（DuckDuckGo 免费 / 自定义 API）
- 新增人工接管面板：缺覆盖率、答题失败、章节跳过自动标记
- Cookie 登录到期自动回退账号密码登录，刷新后更新持久化 cookie
- 课程名称注入 AI prompt 上下文，提高专业领域答题准确率

### v1.3.0
- 新增任务队列系统（`web/tasks.py`）：单线程顺序执行，支持 stop 信号安全中断
- 新增 SSE 章节进度实时推送（`/api/stream/<task_id>`）
- 新增全局终端日志直播广播（`/api/log-stream`），200 条环形缓冲区
- 章节日志持久化到 SQLite，前端轮询 + SSE 双通道保障
- 新增 `ChapterResult` 枚举与 `on_chapter_complete` 回调链路

### v1.2.0
- 新增 Flask + SQLite Web 管理后台（`web/` 模块）
- 新增用户 CRUD、CSV 批量导入、课程列表拉取 API
- 新增前端 SPA（`index.html` / `app.js` / `style.css`），深色/浅色主题
- 新增独立设备 User-Agent 随机生成（7 种生成器，覆盖 Chrome/Edge/Safari/Firefox × Win/Mac/Linux）
- 新增 `docker-compose.yml` Web 模式一键部署
- 新增 Cookie 字符串解析/序列化模块（`api/cookies.py`）

### v1.1.0
- 重构日志系统：引入 loguru，支持彩色控制台输出 + 文件轮转（10 MB）
- 新增自定义异常体系（`api/exceptions.py`）：`LoginError`、`MaxRetryExceeded`、`FontDecodeError` 等
- 课程筛选逻辑修复：明确指定课程 ID 未匹配时直接报错，不再静默回退学习全部课程
- 新增 `notopen_action` 重试计数正确递增，修复未开放章节无限重试死循环
- AI 题库请求限流调整为请求前检查，避免突发请求打到上游 API
- 新增 `CacheDAO` 缓存文件 JSON 损坏自动恢复与备份机制

### v1.0.0
- 基于 [Samueli924/chaoxing](https://github.com/Samueli924/chaoxing) v3.1.3 创建独立分支
- 保留原项目全部 CLI 功能：视频/音频倍速播放、文档阅读、章节测验答题、阅读任务
- 集成多种题库接口：言溪题库（`TikuYanxi`）、Like 知识库（`TikuLike`）、TikuAdapter、OpenAI 兼容大模型（`AI`）
- 支持 Server酱 / Qmsg酱 / Bark / Telegram 外部通知推送
- AES-CBC 登录密码加密，ddddocr 图形验证码识别
- 超星自定义字体混淆解码（fonttools + font_map_table.json）
- Docker CLI 模式部署，配置文件驱动运行
- GPL-3.0 开源协议

---

## 起源与致谢

本项目基于 [Samueli924/chaoxing](https://github.com/Samueli924/chaoxing) 开发，感谢原作者和所有贡献者。

> 原始项目的目标是：通过开源消灭付费刷课平台，让知识共享。

本高级后台版本在此基础上，围绕「多账号管理」和「AI 协同答题」两个方向做了 Web 化改造，保留了 CLI 模式的全部功能。

**CONTRIBUTORS**

<a href="https://github.com/Samueli924/chaoxing/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=Samueli924/chaoxing" />
</a>

---

## 免责声明

- 本代码遵循 [GPL-3.0 License](LICENSE)，允许开源/免费使用和引用/修改，不允许闭源商业发布和盈利
- 本代码仅用于**学习讨论**，禁止用于盈利
- 他人或组织使用本代码进行的任何违法行为与本人无关
