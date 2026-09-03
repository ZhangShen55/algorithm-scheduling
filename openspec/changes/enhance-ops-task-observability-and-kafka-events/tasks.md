## 1. Repository 查询与数据模型

- [x] 1.1 扩展公共 Repository 的任务列表查询模型和 `list_course_jobs` 参数，支持重复任务类型 AND、课程整体状态、单个任务项状态、更新时间闭区间和安全的 `task_id` 字面模糊匹配
- [x] 1.2 重构课程活动与整体状态聚合 SQL，使筛选在计数、排序和分页前执行，并验证多任务类型 join 不会重复课程或产生错误 `total`
- [x] 1.3 为 `NodeRecord` 和节点读取补充 `ready_at`、`finished_at`，实现四种非负耗时计算及缺失端点返回 `null` 的公共映射
- [x] 1.4 增加课程摘要和任务类型摘要的白名单投影，确保不包含 OCR/ASR 长文本、完整区间、逐帧和证据数组
- [x] 1.5 增加按任务类型、节点、结果区块读取及集合分页的 Repository 能力，将 OCR 页、ASR 分段、行为区间和证据页大小限制为 `1-100`
- [x] 1.6 增加 Outbox 事件摘要、单事件详情和按课程任务时间线的只读 Repository 查询，派生 `PENDING/PUBLISHING/RETRY_PENDING/PUBLISHED` 且不返回 `claim_token`
- [x] 1.7 使用真实数据规模执行 PostgreSQL 查询计划检查；只有 `EXPLAIN` 证明确有需要时才增加迁移索引，并记录索引前后依据

## 2. Control Service 运维接口

- [x] 2.1 扩展 `GET /ops/course-jobs` 的 FastAPI 查询参数、校验和响应，覆盖组合筛选、自定义分页、筛选总数及 `updated_from <= updated_to`
- [x] 2.2 对不完整 `task_status_type/task_status`、非法任务类型/状态、无效时间范围和越界分页返回可诊断的 `422`
- [x] 2.3 新增 `GET /ops/course-jobs/{task_id}/summary` 和 `GET /ops/course-jobs/{task_id}/task-types/{task_type}`，返回轻量课程与任务类型详情
- [x] 2.4 新增 `GET /ops/course-jobs/{task_id}/task-types/{task_type}/result`，实现 `node_code`、`section`、`page`、`page_size` 按需读取及 `404/422/503` 语义
- [x] 2.5 在节点响应中返回四个原始时间和四个派生耗时，确保不使用 `updated_at` 推断缺失的完成时间
- [x] 2.6 新增 `GET /ops/kafka/events`、`GET /ops/kafka/events/{event_id}` 和 `GET /ops/course-jobs/{task_id}/events`，实现事件筛选、分页、受限错误和按需 payload
- [x] 2.7 保持 `GET /ops/course-jobs/{task_id}` 完整详情、`GET /ops/kafka` 汇总及 A 服务 `/api/course-jobs` 请求响应兼容，不修改 Outbox 写入和 Kafka envelope

## 3. 前端连接与任务检索

- [x] 3.1 扩展 TypeScript 接口类型和 API 客户端，覆盖任务筛选、轻量摘要、任务类型结果分页、节点耗时及 Outbox 事件
- [x] 3.2 根据 `window.location.hostname` 生成 `18100/18103/9400` 三个部署地址模板，并实现构建默认、部署模板和浏览器保存值的来源判定
- [x] 3.3 调整连接配置抽屉，提供三个地址模板、恢复默认且保存后生效，并对 Control Service、gateway-online 和 GPU exporter 分别执行只读测试和显示结果
- [x] 3.4 将返回 HTML、HTTP 非成功、JSON/Prometheus 格式错误和网络失败区分显示，错误地址指向 `5174` 时提示检查后端端口
- [x] 3.5 在“课程任务”增加四类可组合任务类型、状态对象、课程整体/任务项状态、更新时间范围、Task ID 模糊搜索、排序和清除筛选控件
- [x] 3.6 保留 `10/20/50/100` 快选并增加 `1-100` 自定义每页数量，筛选或每页变化时回到第一页且长列表保持独立滚动
- [x] 3.7 将“查询课程任务”的模糊输入先显示分页候选列表，点击候选或课程列表箭头后进入同一精确任务详情视图

## 4. 分层任务详情与中文展示

- [x] 4.1 重构任务详情为课程摘要、四类任务摘要和最多一层详细结果折叠，常显状态、进度、关键计数、时间、耗时和错误原因
- [x] 4.2 实现 PPT 摘要、识别/切片页数、节点耗时和疑似视频区间摘要；区间明细默认收起并以 `时:分:秒` 和中文原因展示
- [x] 4.3 实现 PPT OCR 完成/成功/空结果/失败摘要和耗时；逐页摘要及 OCR 全文默认收起并按页加载
- [x] 4.4 实现 ASR 音频时长、处理耗时、语种、分段数、文本长度和参数摘要；完整转写、分段、速度数据及原始参数默认收起并按需加载
- [x] 4.5 实现教师行为的视频时长、分析耗时、有效帧率、质量、各行为区间数和证据数摘要；扫描过程、区间及证据默认收起
- [x] 4.6 实现学生行为的视频时长、分析耗时、学生参数、稳定/识别人数、出勤率、区域占用和证据数摘要；逐帧、趋势明细及证据默认收起
- [x] 4.7 集中实现算法原因和状态中文映射，对未知代码保留原值；耗时使用累计 `分:秒`，绝对时间使用日期时刻，缺失精确耗时显示明确空态
- [x] 4.8 使用 `task_id + task_type + section` 管理可访问折叠状态，保证自动刷新不重置已展开内容、不预取未展开结果且不产生页面跳动

## 5. Kafka 发布记录页面

- [x] 5.1 在系统状态的“任务发布 / Kafka”区域增加“任务发布记录”入口，展示最近 Outbox 事件及任务、类型、状态、创建时间、Broker 确认时间和尝试次数
- [x] 5.2 为发布记录增加 Task ID、模糊 Task ID、事件类型、发布状态和时间范围筛选以及服务端分页排序
- [x] 5.3 在课程任务详情展示该任务的 Outbox 时间线，并复用统一事件详情抽屉
- [x] 5.4 实现 payload 默认收起和展开后按需读取的格式化 JSON 视图，隐藏 `claim_token`，不展示媒体字节、凭据或不存在的 Topic/Partition/Offset
- [x] 5.5 将 `PUBLISHED` 固定显示为“Broker 已确认”而非“消费完成”，并在界面说明第一版只覆盖课程任务 Outbox、不覆盖全部视觉 Kafka 消息
- [x] 5.6 Kafka 事件读取失败时只降级发布记录区域，不清空任务、实例、网关或 GPU 的成功观测数据

## 6. 自动化验证与兼容回归

- [x] 6.1 为 Repository 增加真实 PostgreSQL 集成测试，覆盖任务类型 AND、整体/任务项状态、时间边界、模糊字面转义、准确总数、耗时空值和 Outbox 状态派生
- [x] 6.2 为 Control Service 增加 API 合同测试，覆盖所有新查询参数、摘要/结果分页、事件列表/详情、`404/422/503` 和 payload/令牌边界
- [x] 6.3 建立控制台最小自动化测试入口，覆盖地址模板与配置来源、三个独立连接结果、筛选请求参数、自定义分页和模糊候选选择
- [x] 6.4 增加任务详情映射与交互测试，覆盖四类摘要、中文原因、两类时间格式、长内容默认收起、按需请求和刷新保持展开状态
- [x] 6.5 增加 Kafka 发布记录前端测试，覆盖 Broker 确认措辞、失败待重试、payload 按需加载、局部降级和禁止显示 Topic/Partition/Offset
- [x] 6.6 运行 Control Service 编译、应用导入、项目测试和健康/就绪检查，并运行平台相关非集成及 PostgreSQL 集成测试
- [x] 6.7 运行 `npm ci`、类型检查、前端测试和生产构建，使用桌面与移动视口检查筛选栏、详情折叠、长列表和 JSON 视图无重叠或溢出
- [x] 6.8 对 A 服务 `/api/course-jobs`、旧运维详情、Outbox Publisher/Kafka envelope、online-gateway-service 和七类算子合同执行兼容回归

## 7. 文档、发布与 Harness

- [x] 7.1 更新 Control Service 和控制台 README、前后端对接清单及多服务器部署说明，记录筛选参数、详情分层、耗时口径、连接模板和 Outbox 观测边界
- [x] 7.2 使用 `test_all_0903_15` 验证 PPT 18 页、疑似视频区间、OCR、ASR、教师行为和学生行为摘要及按需详情，不把样本特定数值硬编码到页面
- [x] 7.3 发布前记录 `192.168.29.11` 当前 Control Service 和运维控制台的容器完整 ID、镜像完整 ID/digest、Compose 身份、Git revision、端口、健康状态和重启次数，并建立不可删除保护集
- [x] 7.4 使用包含版本时间和 Git 短 SHA 的新标签，按“先 Control Service、后控制台”构建并替换目标机镜像与容器；不重建或重启本次未修改的平台服务、算子、GPU exporter 和基础设施
- [x] 7.5 执行远端 revision、健康、readiness、A 服务兼容和业务 Smoke，覆盖组合筛选、模糊查询、自定义分页、四类详情、节点耗时、课程 Outbox 时间线和三个独立连接测试
- [x] 7.6 全部门禁通过后生成旧资产清理 dry-run，确认候选不属于当前发布且无任何容器引用，再按完整 ID删除被替代的旧容器和旧镜像；禁止全局 prune、模糊匹配、强制删除及清理 BuildKit 缓存
- [x] 7.7 清理后复核当前镜像 revision、容器健康、重启次数、接口 Smoke、数据卷和保护集均完整，并记录删除清单及 `docker system df` 前后空间；任一门禁失败则保留旧镜像并按完整 ID回滚
- [x] 7.8 在 `algorithm-scheduling-platform/harness/scenarios/` 增加本变更场景记录，附可复现命令、脱敏响应摘要、自动化结果、新旧资产身份、清理证据、真实数据、兼容边界、回滚方式及未覆盖项
- [x] 7.9 运行 OpenSpec 严格校验和 `git diff --check`，精确暂存本变更文件并保留工作区其他已有脏改动；使用中文 Conventional Commit 提交信息提交并推送当前 `codex/` 分支，复核远端 SHA
