## ADDED Requirements

### Requirement: Paginated operational-list APIs
系统 SHALL 为包管理、测试任务和 CI 流转列表支持可选的服务端分页。当请求包含 `page` 或 `page_size` 参数时，`GET /api/packages`、`GET /api/tasks` 和 `GET /api/ci/jobs` MUST 返回 `items`、`total`、`page`、`page_size` 和 `total_pages`。`page` 从 1 开始，默认页大小为 20，单页大小不得超过 100，项目 MUST 按创建时间和 ID 的倒序排列。

#### Scenario: Request a paginated package page
- **WHEN** 客户端请求 `GET /api/packages?page=2&page_size=20`
- **THEN** 系统返回第二页包项目、所有包总数和正确分页元数据，且项目不与第一页重复

#### Scenario: Request an out-of-range operational page
- **WHEN** 客户端请求的包、任务或 CI 页码大于最后有效页码
- **THEN** 系统返回最后一个有效页及其实际页码，而不是无效空页

#### Scenario: Use the legacy list request
- **WHEN** 客户端未提供 `page` 和 `page_size` 调用任一上述列表接口
- **THEN** 系统继续返回该接口原有的数组响应结构和原有限制，供已有调用方兼容使用

### Requirement: Full-list task and CI status summary
分页的任务与 CI 列表响应 SHALL 包含 `status_counts`，其值 MUST 反映该类型所有记录按状态的数量，而不是仅当前页项目。前端仪表盘的运行中任务数与 CI 流转状态卡 MUST 使用该全量统计。

#### Scenario: View a later task page while tasks are running
- **WHEN** 用户正在查看不是第一页的任务列表且系统存在运行中任务
- **THEN** 仪表盘仍显示全部运行中任务的正确数量，而不受当前页内容影响

#### Scenario: View CI status cards on a later page
- **WHEN** 用户导航到较早的 CI 流转页
- **THEN** 推送记录、流转中、已通过和异常/取消卡片继续显示全部 CI 记录的正确统计

### Requirement: Paginated operational-list user interface
包管理、测试任务和 CI 流转页面 SHALL 显示全量记录数、当前条目范围、当前页/总页数及上一页、下一页和紧凑页码跳转控件。用户删除当前页最后一项后，页面 MUST 重新显示最后一个有效页。

#### Scenario: Navigate an operational list
- **WHEN** 用户点击上一页、下一页或可见页码
- **THEN** 页面加载目标页项目，更新范围和页码控件，并在首尾页禁用不可用方向按钮

#### Scenario: Delete the final item of the current page
- **WHEN** 用户删除当前页最后一条包、任务或 CI 记录且该页变为无效页
- **THEN** 页面自动显示最后一个仍有效的页，而不是空白无效页
