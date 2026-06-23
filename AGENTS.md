# AI Context Bootstrap - 自动化测试平台

> 项目路径: `C:\sweetstar\test-platform`
> 技术栈: Python + FastAPI + SQLAlchemy + SQLite + Alpine.js + Tailwind
> 最近整理: 2026-05-11

这个文档用于在新对话或上下文丢失后快速恢复项目理解。接手本项目时先读这里，再按需查看 `app/CLAUDE.md`、`app/routers/CLAUDE.md`、`app/services/CLAUDE.md`。

---

## 一句话概览

这是一个移动端应用自动化测试平台，支持 APK/RPK 包上传、ADB 设备管理、单任务/批量任务执行、负载均衡分配、实时日志和 HTML 报告。RPK 测试会调用外部测试框架 `C:\sweetstar\UI-Automation`，APK 测试使用平台内置 ADB 基础流程。

---

## 当前真实项目状态

- 当前仓库目录是 `C:\sweetstar\test-platform`。
- 主应用入口是 `app/main.py`。
- 前端是纯静态 Alpine.js 单页，文件是 `app/static/index.html`，无需构建步骤。
- SQLite 数据库文件是 `test_platform.db`，运行时数据目录是 `uploads/` 和 `reports/`。
- 当前数据库里曾观察到已有运行数据，修改表结构时要谨慎。
- `tasks.new_package` 字段已经在模型中声明；旧数据库启动时由 `app/database.py:init_db()` 自动迁移补列。
- 系统 Python 和当前 `.venv` 可能没有安装 FastAPI；如果导入应用失败，先执行依赖安装或确认解释器。

### 当前未提交改动线索

最近工作区存在这些未提交改动，接手时不要随意回滚:

- `app/config.py`: 新增 `CI_WEBHOOK_URL`、`CI_WEBHOOK_TIMEOUT` 配置。
- `app/main.py`: 新增挂载 `app.routers.ci`，前缀 `/api/ci`。
- `app/services/task_runner.py`: 批量任务完成后调用 Webhook 推送结果。
- `app/routers/ci.py`: 新增 CI 查询接口。
- `AGENTS.md`: 本上下文初始化文档。

这些改动的方向是: 让外部 CI/打包流程查询批量测试结果，并可在批量测试完成后收到回调。

---

## 目录地图

```text
.
├── app/
│   ├── main.py                 # FastAPI 入口，挂载路由、静态文件、Agent WebSocket
│   ├── config.py               # 全局配置、目录、CI Webhook、外部 testcase 路径
│   ├── database.py             # SQLAlchemy engine/session/init_db
│   ├── models.py               # Package / Task / Report
│   ├── agent_manager.py        # 分布式 Agent 连接和设备映射
│   ├── routers/
│   │   ├── packages.py         # 包上传、CI 推包、删除、推送到设备
│   │   ├── devices.py          # ADB 设备、WiFi ADB、Agent 列表
│   │   ├── tasks.py            # 创建/批量/取消/删除任务、日志、负载均衡统计
│   │   ├── reports.py          # 报告列表、查看、删除
│   │   └── ci.py               # CI 查询批量任务结果
│   ├── services/
│   │   ├── package_service.py  # APK/RPK 包名解析
│   │   ├── device_service.py   # adb devices/connect/disconnect/getprop/install
│   │   ├── load_balancer.py    # least_tasks / round_robin / weighted
│   │   ├── task_runner.py      # 后台测试执行引擎
│   │   └── report_service.py   # HTML 报告生成
│   └── static/
│       ├── index.html          # 主 SPA
│       ├── projects-showcase.html
│       ├── alpine.js
│       └── tailwind.js
├── agent.py                    # 独立分布式 Agent 客户端
├── refresh_reports.py          # 批量刷新历史报告样式
├── requirements.txt
├── start.bat                   # 启动平台 + Cloudflare Tunnel，端口 8080
├── start-with-tunnel.bat       # 使用用户目录 cloudflared config 启动
├── uploads/                    # 上传包文件，运行数据
├── reports/                    # HTML 报告，运行数据
└── test_platform.db            # SQLite 运行数据库
```

---

## 运行方式

### 安装依赖

```powershell
pip install -r requirements.txt
```

如果项目里 `.venv` 不可用或缺依赖，优先修复虚拟环境:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### 启动服务

常规开发启动:

```powershell
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

现有批处理脚本偏向公网隧道场景，端口通常是 `8080`:

```powershell
.\start.bat
.\start-with-tunnel.bat
```

### 外部依赖

- `adb`: 必须在 PATH 中，设备列表、安装、推送、截图都依赖它。
- `aapt`: 用于解析 APK 包名；没有时会走弱兜底解析。
- `C:\sweetstar\UI-Automation`: RPK 测试框架默认路径，由 `TESTCASE_PROJECT_DIR` 环境变量可覆盖。

---

## 数据模型

### Package

- `id`
- `filename`
- `package_name`
- `file_type`: `apk` / `rpk`
- `file_size`
- `file_path`
- `source`: `upload` / `ci`
- `created_at`

### Task

- `id`
- `package_id`
- `device_serial`
- `batch_id`
- `status`: `pending` / `running` / `done` / `failed` / `cancelled`
- `logs`: JSON 字符串
- `report_path`
- `error`
- `created_at` / `started_at` / `finished_at`
- `new_package`: 是否给外部测试框架传 `--new-package`

### Report

- `id`
- `task_id`: 单任务报告
- `batch_id`: 批量汇总报告
- `package_name`
- `status`
- `html_path`
- `summary`
- `created_at`

---

## API 速查

### 包管理 `/api/packages`

- `POST /upload`: Web 上传 APK/RPK。
- `POST /push`: CI 推包，需要 `X-API-Key`。
- `GET /`: 包列表。
- `POST /batch-delete`: 批量删除包。
- `DELETE /all`: 删除所有包。
- `DELETE /{pkg_id}`: 删除单包。
- `POST /{pkg_id}/push`: 手动推送包到指定或全部设备。

### 设备 `/api/devices`

- `GET /`: 设备列表。若有在线 Agent，优先返回 Agent 上报设备；否则返回本机 ADB 设备。
- `GET /agents`: 在线 Agent。
- `POST /connect`: WiFi ADB 连接。
- `POST /disconnect`: WiFi ADB 断开。
- `GET /{serial}/info`: 设备详情。

### 任务 `/api/tasks`

- `POST /`: 创建单任务。
- `GET /`: 最近 50 个任务。
- `GET /{task_id}`: 任务详情和日志。
- `GET /{task_id}/logs`: SSE 日志流，前端当前主要使用轮询详情接口。
- `POST /batch`: 批量创建任务。
- `POST /batch/distribute`: 指定负载均衡策略创建批量任务。
- `POST /{task_id}/cancel`: 取消任务。
- `DELETE /{task_id}`: 删除单任务。
- `POST /batch/{batch_id}/cancel`: 取消批量任务里未完成的任务。
- `DELETE /batch/{batch_id}`: 删除某批次任务。
- `DELETE /all/clear`: 清空任务。
- `GET /stats/executors`: 设备执行器统计。
- `GET /stats/loadbalancer`: 负载均衡统计。
- `POST /strategy?strategy=least_tasks|round_robin|weighted`: 设置策略。

### 报告 `/api/reports`

- `GET /`: 报告列表。
- `GET /{report_id}`: 查看 HTML 报告。
- `DELETE /{report_id}`: 删除报告。
- `DELETE /all/clear`: 删除全部报告和报告文件。

### CI `/api/ci`

- `GET /batch/{batch_id}`: 查询批量任务总体结果。
- `GET /batch/{batch_id}/failed`: 只查询失败包列表。

---

## 核心执行流程

### 上传包

1. 前端上传文件到 `/api/packages/upload`。
2. `packages.py` 保存到 `uploads/`。
3. 先用文件名作为临时包名，快速入库。
4. 后台线程调用 `package_service.parse_package_name()` 解析真实包名并更新数据库。
5. 若 `AUTO_PUSH_TO_DEVICE=true`，上传完成后自动推送到设备指定目录。

### 创建任务

1. 前端在 `app/static/index.html` 选择包和设备。
2. 单包调用 `POST /api/tasks`，多包调用 `POST /api/tasks/batch`。
3. 未指定设备时使用 `load_balancer` 自动选择。
4. 写入 `Task(status="pending")`。
5. `task_runner.submit_task(task.id)` 提交后台执行。

### 执行任务

`task_runner.py` 的关键设计:

- 每个设备一个 `ThreadPoolExecutor(max_workers=1)`。
- 同一设备串行执行，避免并发抢设备。
- 不同设备之间可并行。
- 日志先存内存 `_task_logs`，完成后写回 `Task.logs`。
- 取消任务会标记 `_cancelled_task_ids` 并终止对应子进程。

#### RPK

1. 推送 RPK 到设备 `/sdcard/快应用/`。
2. 创建临时包列表 `_platform_task_{task_id}.txt`。
3. 用当前 Python 解释器启动 `C:\sweetstar\UI-Automation\main.py`。
4. 参数包含 `--packages`、`--device`、`--report`。
5. 若任务 `new_package=True`，追加 `--new-package`。
6. 实时读取 stdout，解析 `功能名 -> 模块名 : success/failed/skipped`。
7. 生成单任务或批量报告。

#### APK

1. `adb install -r`。
2. `adb shell monkey -p package_name ...` 启动。
3. `adb exec-out screencap -p` 截图。
4. `dumpsys activity activities` 获取当前 Activity。
5. 生成报告。

---

## 前端说明

主文件: `app/static/index.html`

特点:

- Alpine.js 单页应用。
- Tailwind CDN + 页面内自定义 CSS。
- 页面 tab: 仪表盘、包管理、测试任务、测试报告、设备管理、项目介绍。
- 每 5 秒轮询包、设备、任务、报告、Agent、负载信息。
- 日志弹窗当前通过轮询 `/api/tasks/{task_id}` 更新，虽然后端也提供 SSE。
- 项目介绍通过 iframe 加载 `/static/projects-showcase.html`。

修改前端时只需要编辑 `app/static/index.html`，通常不需要构建。

---

## 分布式 Agent 状态

`agent.py` 是一个独立客户端:

- 连接 `ws://localhost:8000/ws/agent`。
- 注册自身 `agent_id` 和 hostname。
- 定期上报本机 `adb devices -l`。
- 支持接收 `execute_task`、`ping`、`disconnect` 指令。

主服务当前:

- 已处理 `register`、`heartbeat`、`device_update`。
- `task_log` 和 `task_result` 在 `app/main.py` 里还是 `pass`。

也就是说，Agent 现在主要用于“分布式设备发现/展示”，任务真正执行仍主要在平台服务本机的 `task_runner` 里完成。

---

## CI 集成现状

当前新增方向:

- `app/routers/ci.py` 提供批量结果查询。
- `app/config.py` 提供 `PUBLIC_BASE_URL`、`CI_WEBHOOK_URL` 和 `CI_WEBHOOK_TIMEOUT`。
- 批量任务完成后，`task_runner._check_batch_complete()` 生成汇总报告并调用 `_notify_webhook()`。
- 单任务 CI 回调 payload 包含 `externalTaskId`、`testStatus`、`testLog`、`reportId`、`reportUrl`。
- 单任务自动化测试通过时 `testLog` 固定返回 `测试通过`；失败时返回平台解析出的具体失败原因。
- `reportUrl` 使用 `PUBLIC_BASE_URL + /api/reports/{report_id}`，默认公网地址是 `https://test-platform.sweetstar.cloud`。
- 批量 Webhook 的 `report_url` 也返回真实 `report_id` 对应的完整报告 URL。

---

## 常见坑

- 不要随手删除 `uploads/`、`reports/`、`test_platform.db`，这些是运行数据。
- 表结构改动没有 Alembic，现有做法是在 `init_db()` 写轻量迁移；如果大改表结构，需要先确认是否能接受迁移/重建。
- `requirements.txt` 里有 FastAPI，但当前 `.venv` 可能未安装依赖；导入失败时先查环境。
- PowerShell 默认编码可能把中文打印成乱码；读文件时用 `Get-Content -Encoding UTF8` 并设置 console UTF-8。
- 文档里可能出现 `8000`，启动脚本里常用 `8080`，调试接口时先确认实际端口。
- RPK 测试强依赖外部 `C:\sweetstar\UI-Automation\main.py`，平台仓库内没有完整 RPK 测试逻辑。
- `aapt` 不在 PATH 时 APK 包名解析只是兜底，可能不准。
- 任务删除只删数据库记录，不一定清理历史报告文件；报告删除才会删 HTML 文件。
- 负载均衡只看平台本机 `device_service.list_devices()`，尚未深度整合 Agent 上报设备执行能力。

---

## 接手检查清单

新对话开始后建议先执行:

```powershell
git status --short --branch
rg --files
```

确认依赖和导入:

```powershell
python -m py_compile app\main.py app\models.py app\database.py app\config.py app\routers\*.py app\services\*.py agent.py refresh_reports.py
```

如果要完整导入应用:

```powershell
.\.venv\Scripts\python.exe -c "from app.main import app; print(len(app.routes))"
```

如果失败显示 `ModuleNotFoundError: No module named 'fastapi'`，先安装依赖。

查看数据库规模:

```powershell
sqlite3 test_platform.db "select 'packages', count(*) from packages union all select 'tasks', count(*) from tasks union all select 'reports', count(*) from reports;"
```

查看表结构:

```powershell
sqlite3 test_platform.db "pragma table_info(tasks);"
```

---

## 修改建议

- 路由层修改优先看 `app/routers/*.py`。
- 业务执行逻辑优先看 `app/services/task_runner.py`。
- 设备和 ADB 相关优先看 `app/services/device_service.py`。
- 负载分配相关优先看 `app/services/load_balancer.py`。
- 报告样式和内容优先看 `app/services/report_service.py`。
- 前端交互优先看 `app/static/index.html` 底部 `app()`。
- CI 对接优先看 `app/routers/ci.py` 和 `task_runner._notify_webhook()`。

---

## 后续优先级建议

1. 修正 CI Webhook `report_url` 和报告查询方式。
2. 明确服务端运行端口: 文档、脚本、Agent 默认地址要统一。
3. 补齐虚拟环境依赖，保证 `.venv\Scripts\python.exe -c "from app.main import app"` 可用。
4. 决定 Agent 是否只做设备发现，还是要真正执行远端任务；如果要执行，需要接入 `task_log` 和 `task_result`。
5. 给关键服务补最小测试，尤其是 CI 结果汇总、批量报告、任务取消、包名解析。
