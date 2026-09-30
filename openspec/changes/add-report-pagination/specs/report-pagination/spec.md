## ADDED Requirements

### Requirement: Paginated report-list API
系统 SHALL 通过 `GET /api/reports` 提供测试报告的分页查询。接口 MUST 接受从 1 开始的 `page` 参数与 `page_size` 参数，默认每页 20 条，且单页大小不得超过 100 条。响应 MUST 包含 `items`、`total`、`page`、`page_size` 和 `total_pages`，其中 `items` 必须按报告创建时间倒序排列。

#### Scenario: Request the first report page
- **WHEN** 客户端请求 `GET /api/reports?page=1&page_size=20`
- **THEN** 系统返回最多 20 条最新报告以及正确的总数和分页元数据

#### Scenario: Request a later report page
- **WHEN** 客户端请求一个存在的后续页码
- **THEN** 系统返回该页对应的较早报告，且不与相邻页面重复

#### Scenario: Request a page beyond the last page
- **WHEN** 客户端请求大于当前总页数的页码
- **THEN** 系统返回最后一个有效页及其实际页码，而不是返回无效空页

### Requirement: Paginated report-list user interface
测试报告页面 SHALL 显示当前页的报告、全部报告总数、当前页/总页数，并提供可用的上一页、下一页和页码跳转控件。仪表盘的测试报告数量 MUST 使用全部报告总数，而非当前页数量。

#### Scenario: Navigate to an older report page
- **WHEN** 用户点击下一页或一个可见的后续页码
- **THEN** 页面加载该页报告并更新当前页指示和控件状态

#### Scenario: Navigate at a page boundary
- **WHEN** 用户位于第一页或最后一页
- **THEN** 对应的上一页或下一页控件处于不可用状态

#### Scenario: Delete the last item on the current page
- **WHEN** 用户删除当前页最后一条报告且该页不再有效
- **THEN** 页面自动显示最后一个仍有效的报告页
