[根目录](../../CLAUDE.md) > [app](../) > **services**

# services 模块 - 业务服务层

> 职责: 实现核心业务逻辑，被路由层调用

---

## 模块职责

`services` 目录包含所有业务逻辑实现:
- `package_service.py` - 包名解析 (APK/RPK)
- `device_service.py` - ADB 设备管理
- `task_runner.py` - 测试执行引擎 (核心)
- `load_balancer.py` - 设备负载均衡
- `report_service.py` - HTML 报告生成

---

## 服务详解

### package_service.py

**功能**: 自动识别 APK/RPK 包名

| 函数 | 功能 |
|------|------|
| `parse_package_name(file_path)` | 根据扩展名调用对应解析器 |
| `_parse_apk(file_path)` | 使用 aapt 或 zipfile 解析 APK |
| `_parse_rpk(file_path)` | 读取 manifest.json 解析 RPK |
| `get_file_type(filename)` | 获取文件扩展名 |

### device_service.py

**功能**: ADB 设备管理

| 函数 | 功能 |
|------|------|
| `list_devices()` | 列出所有 ADB 设备 (自动去重) |
| `connect_wifi(address)` | WiFi ADB 连接 |
| `disconnect_wifi(address)` | 断开 WiFi ADB |
| `get_device_info(serial)` | 获取设备详细信息 |
| `install_apk(serial, apk_path)` | 安装 APK |

### task_runner.py

**功能**: 测试执行引擎 (核心模块)

**设计特点**:
- 设备级并行: 每台设备一个独立线程池
- 同一设备串行，不同设备并行
- 支持 RPK (子进程执行 testcase) 和 APK (基础测试)

| 函数 | 功能 |
|------|------|
| `submit_task(task_id)` | 提交任务到对应设备队列 |
| `cancel_task(task_id)` | 取消运行中的任务 |
| `get_logs(task_id, offset)` | 获取任务日志 |
| `get_executor_stats()` | 获取执行器统计 |

**RPK 测试流程**:
1. 推送 RPK 到设备 /sdcard/
2. 创建临时包列表文件
3. 以子进程执行 `testcase/main.py`
4. 实时捕获 stdout 作为日志
5. 解析功能模块结果 (格式: `功能名 -> 模块名 : status`)
6. 生成 HTML 报告

**APK 基础测试流程**:
1. 安装 APK
2. 启动应用 (monkey)
3. 截图验证
4. Activity 检测

### load_balancer.py

**功能**: 智能设备负载均衡

**策略**:
- `least_tasks` - 最少运行中任务优先 (默认)
- `round_robin` - 轮询
- `weighted` - 加权随机

| 函数 | 功能 |
|------|------|
| `refresh_devices()` | 刷新在线设备列表 |
| `sync_task_status()` | 从数据库同步任务状态 |
| `select_device(exclude)` | 选择最优设备 |
| `get_stats()` | 获取负载均衡统计 |
| `auto_assign_device()` | 自动分配设备便捷函数 |

### report_service.py

**功能**: HTML 测试报告生成

**设计风格**: Data-Dense Dashboard
- 主色: #1E40AF / #3B82F6 / #F59E0B
- 配色: 蓝色渐变、绿色(通过)、红色(失败)

| 函数 | 功能 |
|------|------|
| `generate_html_report(task_id, pkg, result, ...)` | 单任务报告 |
| `generate_batch_report(batch_id, results)` | 批量汇总报告 |

**报告包含**:
- 顶部横幅 (包名、设备、状态)
- KPI 卡片 (总步骤/通过/失败/通过率)
- 功能测试结果表
- 执行步骤表
- 日志输出框

---

## 相关文件清单

```
app/services/
├── __init__.py
├── package_service.py    # 包名解析
├── device_service.py     # ADB 设备管理
├── task_runner.py        # 测试执行引擎
├── load_balancer.py      # 负载均衡器
└── report_service.py     # 报告生成
```

---

## 变更记录 (Changelog)

| 时间 | 变更内容 |
|------|---------|
| 2026-03-25 | 初始化模块文档 |
