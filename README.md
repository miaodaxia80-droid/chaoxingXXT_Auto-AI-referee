# 超星学习通 · 高级后台 v2.0

> 本项目是 [Samueli924/chaoxing](https://github.com/Samueli924/chaoxing) (GPL-3.0) 的衍生作品，
> 新增了 Web 管理后台、多账号管理、多模型 AI 协同答题、联网搜索等功能。

<p align="center">
    <a href="https://github.com/Samueli924/chaoxing" target="_blank">
        <img src="https://img.shields.io/github/stars/Samueli924/chaoxing" alt="Github Stars" />
    </a>
    <a href="https://github.com/Samueli924/chaoxing" target="_blank">
        <img src="https://img.shields.io/github/forks/Samueli924/chaoxing" alt="Github Forks" />
    </a>
</p>

基于 [Samueli924/chaoxing](https://github.com/Samueli924/chaoxing) 的自动化任务点脚本，新增 **Web 可视化管理后台**、**多账号管理**、**独立设备 UA**、**多模型 AI 协同答题**、**联网搜索增强** 等功能。

---

## 在原有基础上的创新

### 1. Web 可视化管理后台
- Flask + SQLite，浏览器访问 `http://IP:5002` 即可管理所有账号
- 实时 SSE 推送：章节进度、终端日志秒级同步到页面
- 深色/浅色主题切换

### 2. 多账号并行管理
- 每个账号独立配置：速度、并行数、题库、AI 模型
- 每个账号独立随机设备 User-Agent（7 种生成器，覆盖 Chrome/Edge/Safari/Firefox × Win/Mac/Linux），支持批量随机分配
- 学习任务顺序队列执行（单线程 worker），stop 信号安全中断
- 账号间 Session 隔离，互不影响

### 3. 多模型 AI 协同答题（3 模型并行 + Referee 裁决）
- 3 个 AI 模型并行作答同一道题，交叉验证
- Referee 裁决机制：模型意见不一致时，由裁判模型综合判断
- 课程名称作为上下文注入 prompt，提高专业领域准确率
- 可选联网搜索增强（DuckDuckGo 免费免 Key / 自定义搜索 API）

### 4. 实时监控与人工接管
- 仪表盘：账号数、任务数、进度一目了然
- 题库覆盖率不足、答题失败、章节跳过 → 自动标记「需人工接管」
- 实时终端日志面板，与命令行输出完全同步

### 5. Docker 一键部署
- 支持 x86/arm64 NAS（飞牛、群晖等）Docker Compose 一键启动
- 数据库和答题缓存持久化到宿主机 `./data/`，容器重建不丢数据

---

## 使用方法

### Web 后台模式（推荐）

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 启动服务
python server.py

# 3. 浏览器访问
http://localhost:5002
```

#### 界面操作指南

| 页面 | 功能 |
|------|------|
| **控制台** | 总览：账号数、运行中任务、今日完成。子面板：近期任务 / 需人工接管的章节 |
| **用户管理** | 添加/导入/编辑用户，配置题库、AI 模型、多模型协同参数 |
| **学习** | 选择用户 → 拉取课程列表 → 勾选课程/章节 → 开始学习。实时日志同步展示 |
| **日志** | 实时终端日志 + 历史操作日志 |

#### 添加用户并配置多模型协同

1. 用户管理 → 添加用户（手机号 + 密码）
2. 点击用户行的配置按钮 → 打开配置抽屉
3. 题库提供商选择 `AI 大模型` 或 `硅基流动`，填写 API Key
4. 启用「多模型协同」→ 配置 3 个模型 → 选择裁判模型
5. 可选开启联网搜索（DuckDuckGo 免费免 Key）
6. 保存 → 到学习页面选择该用户开始刷课

### CLI 命令行模式（兼容原有方式）

```bash
# 直接运行（交互式）
python main.py

# 配置文件
python main.py -c config.ini

# 命令行参数
python main.py -u 手机号 -p 密码 -l 课程ID1,课程ID2 -s 1.5 -j 4
```

### Docker 部署（飞牛 NAS / 群晖）

```bash
# 1. 将项目拷贝到 NAS

# 2. 构建并启动（Web 模式）
docker compose up -d --build

# 3. CLI 模式
docker build -t chaoxing .
docker run -it -v /path/to/config.ini:/config/config.ini chaoxing

# 4. 访问
http://NAS_IP:5002
```

数据库和缓存持久化在 `./data/` 目录，容器删除后数据保留。

---

## 配置文件

复制 `config_template.ini` → `config.ini`，主要配置项：

**[common]**
- `username` / `password` — 登录凭据
- `course_list` — 要学习的课程 ID 列表，逗号分隔
- `speed` — 视频倍速（1 ~ 2）
- `jobs` — 同时进行的章节数
- `notopen_action` — 未开放章节处理：`retry`（重试）/ `continue`（跳过）
- `user_agent` — 自定义 User-Agent（可选，留空使用默认）

**[tiku]**
- `provider` — 题库/模型：`TikuYanxi`、`TikuLike`、`TikuAdapter`、`AI`、`SiliconFlow`
- `multi_model` — 是否启用多模型协同（`true`/`false`）
- `models` — 多模型配置 JSON 数组，每个元素含 `provider`/`endpoint`/`key`/`model`
- `voting_strategy` — 裁决策略（`referee`）
- `referee_model_index` — 裁判模型在 models 数组中的索引
- `search_enabled` — 是否启用联网搜索（`true`/`false`）
- `search_provider` — 搜索引擎（`duckduckgo` / `custom`）
- `submit` — 提交模式：`true`（达到覆盖率自动提交）/ `false`（仅保存）
- `cover_rate` — 最低覆盖率（0 ~ 1）
- `delay` — 搜索间隔秒数
- `tokens` — 言溪题库 / LIKE 知识库的 token，逗号分隔多个
- `true_list` / `false_list` — 判断题选项映射
- 各 AI 模型的专属配置：`endpoint`、`key`、`model`、`siliconflow_key`、`siliconflow_model` 等

**[notification]**
- `provider` — 推送服务：`ServerChan`、`Qmsg`、`Bark`、`Telegram`
- `url` — 推送服务的 webhook URL
- `tg_chat_id` — Telegram 推送时的 chat_id

**Cookie 登录**：在 `[common]` 中设置 `use_cookies=true` 并在项目根目录放置 `cookies.txt`。

---

## 测试

```bash
python -m pytest tests/ -v
python -m unittest tests/test_regressions.py
```

---

## 项目结构

```
api/              核心功能模块
├── base.py        超星 API 封装（登录、视频、文档、测验、阅读）
├── answer.py      题库系统（Tiku 基类 + 各 provider + EnsembleTiku 多模型协同 + CacheDAO）
├── decode.py      平台 JSON 解析
├── font_decoder.py / cxsecret_font.py   字体混淆解码
├── cipher.py      AES 登录加密
├── captcha.py     图形验证码识别
├── notification.py  外部推送通知（Server酱/Qmsg/Bark/Telegram）
├── web_search.py  联网搜索（DuckDuckGo + 自定义 API）
├── cookies.py     Cookie 解析/序列化
├── config.py      全局常量
├── exceptions.py  自定义异常
├── logger.py      日志配置
└── process.py     进度条工具
web/              Web 后台
├── __init__.py    Flask 工厂
├── models.py      SQLite 数据层（用户/任务/章节记录/设置/操作日志）
├── tasks.py       任务队列 + SSE 实时推送 + 全局日志广播
├── routes/api.py  RESTful API
├── routes/sse.py  Server-Sent Events 端点
└── static/        前端 SPA（index.html / app.js / style.css）
main.py            CLI 入口（ChapterResult 枚举 / JobProcessor 并发调度）
server.py          Web 模式入口
app.py             Celery 集成（预留）
```

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
