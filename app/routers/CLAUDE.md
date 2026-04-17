[根目录](../../CLAUDE.md) > [app](../) > **routers**

# routers 模块 - API 路由层

> 职责: 定义 HTTP API 端点，处理请求/响应

---

## 模块职责

`routers` 目录包含所有 FastAPI 路由定义，每个模块对应一个功能领域:
- `packages.py` - 包管理 (上传、列表、删除)
- `devices.py` - 设备管理 (列表、WiFi连接、Agent)
- `tasks.py` - 测试任务 (创建、日志、批量)
- `reports.py` - 报告管理 (列表、查看、删除)

---

## 路由清单

### packages.py (前缀: /api/packages)

| 方法 | 路径 | 功能 |
|------|------|------|
| POST | /upload | Web 上传包，自动推送到设备 |
| POST | /push | CI 推送包 (需 API Key) |
| GET | / | 列出所有包 |
| POST | /batch-delete | 批量删除包 |
| DELETE | /all | 删除所有包 |
| DELETE | /{pkg_id} | 删除单个包 |

### devices.py (前缀: /api/devices)

| 方法 | 路径 | 功能 |
|------|------|------|
| GET | / | 列出所有设备 |
| GET | /agents | 列出在线 Agent |
| POST | /connect | WiFi ADB 连接 |
| POST | /disconnect | 断开 WiFi ADB |
| GET | /{serial}/info | 设备详细信息 |

### tasks.py (前缀: /api/tasks)

| 方法 | 路径 | 功能 |
|------|------|------|
| POST | / | 创建单任务 |
| GET | / | 任务列表 |
| GET | /{task_id} | 任务详情 |
| GET | /{task_id}/logs | 日志流 (SSE) |
| POST | /{task_id}/cancel | 取消任务 |
| DELETE | /{task_id} | 删除任务 |
| DELETE | /all/clear | 清空所有任务 |
| POST | /batch | 批量创建任务 |
| POST | /batch/distribute | 高级批量任务 (负载均衡) |
| GET | /stats/executors | 执行器统计 |
| GET | /stats/loadbalancer | 负载均衡统计 |
| POST | /strategy | 设置负载均衡策略 |

### reports.py (前缀: /api/reports)

| 方法 | 路径 | 功能 |
|------|------|------|
| GET | / | 报告列表 |
| GET | /{report_id} | 查看报告 HTML |
| DELETE | /{report_id} | 删除报告 |
| DELETE | /all/clear | 清空所有报告 |

---

## 关键接口详解

### 上传包并自动推送

```python
POST /api/packages/upload
Content-Type: multipart/form-data

file: <binary>

Response:
{
  "id": 1,
  "filename": "test.rpk",
  "package_name": "com.example.app",
  "file_type": "rpk",
  "file_size": 1024000,
  "pushed_devices": [{"serial": "xxx", "success": true}]
}
```

### 创建测试任务

```python
POST /api/tasks
Content-Type: application/json

{
  "package_id": 1,
  "device_serial": "xxx",  // 可选，不传则自动分配
  "auto_assign": false
}

Response:
{
  "id": 1,
  "status": "pending",
  "device_serial": "xxx",
  "auto_assigned": true
}
```

### 实时日志流 (SSE)

```python
GET /api/tasks/{task_id}/logs

// Server-Sent Events 格式
data: [14:30:00] 开始测试: test.rpk

data: [14:30:01] 目标设备: xxx

data: [END] 任务状态: done
```

---

## 相关文件清单

```
app/routers/
├── __init__.py
├── packages.py      # 包管理路由
├── devices.py       # 设备管理路由
├── tasks.py         # 测试任务路由
└── reports.py       # 报告管理路由
```

---

## 变更记录 (Changelog)

| 时间 | 变更内容 |
|------|---------|
| 2026-03-25 | 初始化模块文档 |
