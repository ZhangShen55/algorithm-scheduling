## ADDED Requirements

### Requirement: 网关必须提供三个独立在线 VBas 路由

`online-gateway-service` MUST 提供以下三个 POST 路由，并且路由路径不得包含 `/api` 前缀：

- `/online/vbas/teacher`
- `/online/vbas/student`
- `/online/vbas/person-count`

#### Scenario: 教师行为请求
- **WHEN** A 服务向 `/online/vbas/teacher` 发送合法的教师行为请求
- **THEN** 网关必须申请在线 VBas 租约并调用 `/ImageDetect/teacher/v1.0.0`

#### Scenario: 学生行为请求
- **WHEN** A 服务向 `/online/vbas/student` 发送合法的学生行为请求
- **THEN** 网关必须申请在线 VBas 租约并调用 `/ImageDetect/student/v1.0.0`

#### Scenario: 人数检测请求
- **WHEN** A 服务向 `/online/vbas/person-count` 发送合法的 Base64 人数检测请求
- **THEN** 网关必须申请在线 VBas 租约并调用 `/AE/SyncTasks2`

### Requirement: 网关必须保持 VBas 请求和响应结构

三个网关路由 MUST 使用对应 VBas 接口的请求字段和成功响应字段，不得新增统一包装层或删除原始结果字段。

#### Scenario: 人数检测坐标区域
- **WHEN** 人数检测请求包含 `AnalysisRule.AlgParams.PolygonList` 和每个多边形的 `Points`
- **THEN** 网关必须原样转发坐标，响应必须保留每个区域的 `PersonInfo`、`FaceInfo` 和 `TaskResult`

#### Scenario: 在线请求单图
- **WHEN** 请求的 `ImageList` 只包含一张 Base64 图片
- **THEN** 网关必须将该请求作为一个在线请求槽位转发，并原样返回 VBas 响应

### Requirement: 网关必须删除旧统一路径

完成迁移后，网关 MUST 移除 `/api/online/vbas/analyze`，A 服务 MUST 使用三个新路径。

#### Scenario: 旧路径访问
- **WHEN** 客户端访问 `/api/online/vbas/analyze`
- **THEN** 网关必须返回明确的路径不存在或迁移提示，不得继续执行旧的 `stream_type` 分流逻辑
