[根目录](../CLAUDE.md) > **app**

# app 模块 - 核心应用

> 职责: FastAPI 应用入口、数据模型、数据库连接、Agent 管理

---

## 模块职责

`app` 是自动化测试平台的核心 Python 包，包含:
- FastAPI 应用实例创建和配置
- 数据库模型定义 (SQLAlchemy)
- 数据库连接和会话管理
- 全局配置
- Agent 连接管理器 (WebSocket)

---

## 入口与启动

**主入口**: `main.py`

```python
# 启动方式
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

**启动流程**:
1. 创建 FastAPI 实例
2. 注册 CORS 中间件
3. 注册路由 (packages, devices, tasks, reports)
4. 挂载静态文件
5. 启动时初始化数据库 (`init_db()`)
6. 启动 Agent 清理后台任务

---

## 对外接口

### WebSocket 端点

| 路径 | 功能 | 消息类型 |
|------|------|---------|
| `/ws/agent` | Agent 连接 | register, heartbeat, device_update, task_log, task_result |

### REST API (由 routers 提供)

| 前缀 | 功能 |
|------|------|
| `/api/packages` | 包管理 |
| `/api/devices` | 设备管理 |
| `/api/tasks` | 测试任务 |
| `/api/reports` | 测试报告 |
| `/static` | 静态文件 |
| `/` | 首页 (index.html) |

---

## 关键依赖与配置

**配置项** (`config.py`):
```python
BASE_DIR              # 项目根目录
UPLOAD_DIR            # 上传文件存储路径 (uploads/)
REPORT_DIR            # 报告输出路径 (reports/)
DATABASE_URL          # SQLite 数据库连接
API_KEY               # CI 推送用的 API Key
testcase_project_dir  # 外部 testcase 项目路径
```

**核心依赖**:
- FastAPI - Web 框架
- SQLAlchemy - ORM
- Uvicorn - ASGI 服务器
- python-multipart - 文件上传

---

## 数据模型

### Package (包表)
| 字段 | 类型 | 说明 |
|------|------|------|
| id | Integer | 主键 |
| filename | String(255) | 文件名 |
| package_name | String(255) | 解析的包名 |
| file_type | String(10) | apk/rpk |
| file_size | Integer | 文件大小 |
| file_path | String(500) | 存储路径 |
| source | String(20) | upload/ci |
| created_at | DateTime | 创建时间 |

### Task (任务表)
| 字段 | 类型 | 说明 |
|------|------|------|
| id | Integer | 主键 |
| package_id | Integer | 关联包ID |
| device_serial | String(100) | 目标设备 |
| batch_id | String(64) | 批量任务组ID |
| status | String(20) | pending/running/done/failed/cancelled |
| logs | Text | JSON 格式日志 |
| report_path | String(500) | 报告文件路径 |
| error | Text | 错误信息 |
| created_at/started_at/finished_at | DateTime | 时间戳 |

### Report (报告表)
| 字段 | 类型 | 说明 |
|------|------|------|
| id | Integer | 主键 |
| task_id | Integer | 单任务ID (可选) |
| batch_id | String(64) | 批量任务ID (可选) |
| package_name | String(255) | 包名 |
| status | String(20) | 状态 |
| html_path | String(500) | HTML 报告路径 |
| summary | Text | JSON 摘要 |
| created_at | DateTime | 创建时间 |

---

## Agent 管理器

**文件**: `agent_manager.py`

**功能**: 管理分布式 Agent 的 WebSocket 连接

**核心方法**:
- `register(agent_id, hostname, websocket)` - 注册 Agent
- `unregister(agent_id)` - 注销 Agent
- `update_devices(agent_id, devices)` - 更新设备列表
- `heartbeat(agent_id)` - 心跳更新
- `get_all_devices()` - 获取所有设备
- `get_online_agents()` - 获取在线 Agent 列表
- `cleanup_stale_agents(timeout)` - 清理离线 Agent

---

## 相关文件清单

```
app/
├── __init__.py          # 包初始化
├── main.py              # FastAPI 入口
├── models.py            # 数据模型定义
├── database.py          # 数据库连接
├── config.py            # 全局配置
├── agent_manager.py     # Agent 连接管理
├── routers/             # 路由层
│   ├── __init__.py
│   ├── packages.py      # 包管理路由
│   ├── devices.py       # 设备管理路由
│   ├── tasks.py         # 测试任务路由
│   └── reports.py       # 报告管理路由
└── services/            # 服务层
    ├── __init__.py
    ├── package_service.py
    ├── device_service.py
    ├── task_runner.py
    ├── load_balancer.py
    └── report_service.py
```

---

## 变更记录 (Changelog)

| 时间 | 变更内容 |
|------|---------|
| 2026-03-25 | 初始化模块文档 |
