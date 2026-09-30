## ADDED Requirements

### Requirement: On-demand report-list refresh
测试报告列表 MUST NOT 参与全局固定间隔的自动轮询。页面 SHALL 仅在初始加载、用户导航分页，或用户删除报告后重新获取报告列表。

#### Scenario: Browse an unchanged report page
- **WHEN** 用户停留在测试报告页面且未进行分页或删除操作
- **THEN** 页面不会因固定时间间隔重新请求报告列表或显示重复的加载状态

#### Scenario: Delete a report
- **WHEN** 用户删除单条报告或清空报告列表
- **THEN** 页面重新获取有效页以更新列表、总数和页码
