## ADDED Requirements

### Requirement: 控制台按部署主机生成三个连接模板
控制台 SHALL 在通过 `http/https` 打开时使用当前页面 `hostname` 生成 Control Service `18100`、gateway-online `18103` 和 GPU exporter `9400` 的完整地址模板；无法取得可用主机名时 SHALL 回退到构建默认值。模板 SHALL 包含协议、主机和端口，不得把控制台 `5174` 端口误作后端端口。

#### Scenario: 从部署 IP 打开控制台
- **WHEN** 运维人员打开 `http://192.168.29.11:5174`
- **THEN** 页面模板分别为 `http://192.168.29.11:18100`、`http://192.168.29.11:18103` 和 `http://192.168.29.11:9400`

### Requirement: 连接配置显示值来源并可恢复默认
连接配置 SHALL 标识每个当前地址来自“部署模板”“构建默认”或“浏览器保存”，并 SHALL 提供填入模板和恢复默认动作。恢复默认 SHALL 同时更新表单和浏览器保存配置，但 MUST 在用户保存应用后才切换正在使用的连接。

#### Scenario: 浏览器存在旧配置
- **WHEN** LocalStorage 保存了 `/control`、`/gateway` 或旧 IP 地址
- **THEN** 配置面板显示“浏览器保存”来源，并允许一键换成当前部署主机模板

#### Scenario: 恢复默认后取消
- **WHEN** 运维人员在表单中恢复默认但关闭面板而未保存
- **THEN** 当前页面继续使用原连接，避免未确认的配置立即生效

### Requirement: 三个数据源分别测试并分别报告
配置面板 SHALL 独立测试 Control Service、gateway-online 和 GPU exporter，并分别显示成功、HTTP 错误、响应格式错误或连接失败。任一测试失败 MUST 不覆盖另外两个数据源的成功结果；测试动作 SHALL 保持只读。

#### Scenario: Control Service 成功但网关失败
- **WHEN** Control Service 返回合法 JSON、GPU exporter 返回合法 JSON，而 gateway-online 无法连接
- **THEN** 页面分别显示两个成功和一个失败，不显示笼统的“三个服务全部失败”

#### Scenario: 地址错误返回前端 HTML
- **WHEN** 后端地址误填为控制台端口并返回 `<!doctype html>`
- **THEN** 页面将该数据源标记为“响应格式错误，返回 HTML”，并提示检查服务端口
