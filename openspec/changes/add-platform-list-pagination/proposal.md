## Why

包管理、测试任务和 CI 流转仍分别一次返回全部、最新 50 条和最新 100 条记录。记录持续增长后，页面会变慢且无法浏览更早的数据；同时测试报告列表的全局定时刷新会反复显示“正在加载报告”，干扰用户查看页面。

## What Changes

- 为包管理、测试任务和 CI 流转的列表接口增加分页查询与总数元数据，并保留无分页参数时的原有数组响应，避免影响已有调用方。
- 为这三个页面增加总数、当前显示范围、页码、上一页和下一页操作。
- 测试任务分页后仍保持批量任务的状态和全量统计准确；仪表盘与 CI 状态卡使用服务端全量统计，而非当前页数据。
- 取消测试报告列表的固定 5 秒刷新；报告仅在初次进入、手动翻页或删除操作后重新获取。

## Capabilities

### New Capabilities

- `operational-list-pagination`: 包管理、测试任务与 CI 流转的分页 API 和页面导航。
- `report-list-refresh-control`: 测试报告列表的按需刷新行为。

### Modified Capabilities

无。现有报告分页变更尚未归档到主规格目录，本变更以独立的刷新行为规格补充该页面。

## Impact

- `app/routers/packages.py`、`app/routers/tasks.py`、`app/routers/ci.py`：列表查询、分页元数据和全量统计。
- `app/static/index.html`：三个列表的分页状态、导航和报告列表轮询策略。
- `app/routers/CLAUDE.md` 与 API 测试：更新接口说明并覆盖分页、兼容和页码越界行为。
- 不新增第三方依赖，不变更数据库表、报告详情 URL 或 CI 回调契约。
