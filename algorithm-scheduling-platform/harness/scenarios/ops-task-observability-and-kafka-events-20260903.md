# 运维任务观测与 Kafka 发布记录验证

## 范围

本记录对应 OpenSpec 变更 `enhance-ops-task-observability-and-kafka-events`。验证 Control Service
课程任务组合筛选、分层详情、节点耗时和 Outbox 只读接口，以及运维控制台的连接模板、任务筛选、
折叠详情、中文原因和任务发布记录。目标机为 `192.168.29.11`，发布过程只替换 Control Service
和运维控制台，不修改 A 服务、Kafka envelope、online gateway、七类算子、GPU exporter 或基础设施。

## Git 与发布身份

| 对象 | Git revision | 镜像标签 | 镜像完整 ID | 容器完整 ID |
| --- | --- | --- | --- | --- |
| Control Service | `73e194a809f9c3d1d460e2b7ee6550ced79420c0` | `algorithm-scheduling/control-service:v1.1_260903_73e194a` | `sha256:25497bcf1eb2de95fd31f61fcdae2c008eaa959fe2de13f1b5087db8b3770c61` | `52bca0eb13a389cd9b02ac319f3d8db6b8312bce13f39a81de0cbcbe87ad6683` |
| 运维控制台 | `d3aa6291ce908e9b7779f37c4fecf1d79d3eb281` | `algorithm-scheduling/ops-console:v0.2_260903_d3aa629` | `sha256:d1113872062da4ba8b6ae4810f9346e788c7b39c8ecd5b3cf618f35e3e3657ac` | `4284436fb51f1095a7935e86fe2577475beb702ded4dba028b7935f747cac831` |

Control Compose 身份为项目 `algorithm-scheduling-platform`、服务 `control-service`，配置文件为
`/root/workspace/algorithm-ops-observability-release-73e194a/algorithm-scheduling-platform/deploy/docker-compose.platform.yml`。
控制台 Compose 身份为项目和服务 `algorithm-scheduling-ops-console`，配置文件为
`/root/workspace/algorithm-ops-console-release-d3aa629/docker-compose.yml`。正式容器均为 `healthy`，
重启次数均为 0；Control 暴露 `18100`，控制台暴露 `5174`。

## 本地自动化

从工作区根目录执行：

```bash
cd algorithm-scheduling-ops-console
npm ci
npm test
npm run build

cd ../algorithm-scheduling-platform
.venv/bin/python -m pytest -q \
  tests/test_control_api_submission.py \
  tests/test_operations_api.py \
  tests/test_outbox_publisher.py \
  tests/test_operator_deployment_integration.py

cd ../online_gateway_service
../algorithm-scheduling-platform/.venv/bin/python -m pytest -q tests
```

结果：控制台 `5 passed` 且 TypeScript/Vite 生产构建通过；A 服务、运维接口、Outbox Publisher 和
七算子部署合同 `41 passed`；online gateway 项目 `64 passed`。更早的本变更门禁还包括 Control
Service `25 passed`、Repository 与 Operations API `53 passed`、Operations API `12 passed`、
Outbox Publisher `2 passed`、`npm ci`、Compose 配置和 OpenSpec 严格校验通过。

`algorithm-scheduling-platform/tests/test_online_gateway.py` 的完整平台集合仍包含一个与本变更无关的
既有失败：测试期待已经移除的 `/api/online/vbas/analyze`。本变更未恢复该退役路由，也未修改对应代码。

## PostgreSQL 查询计划

目标库统计约为：`course_jobs=12777`、`course_task_types=14262`、`task_nodes=22536`、
`outbox_events=14282`。对四任务类型 AND、整体完成状态和 `task_id_like=test_all_0903` 的分页 SQL
执行 `EXPLAIN (ANALYZE, BUFFERS)`，执行时间 `20.195 ms`，任务类型子查询命中
`idx_course_task_types_task_query`，排序只使用 `25-30 kB` 内存。Outbox 模糊查询执行时间
`19.233 ms`，Top-N 排序使用 `50 kB` 内存。当前真实规模下没有索引迁移依据，因此未增加索引。

复现入口：

```bash
ssh root@192.168.29.11
docker exec -i algorithm-scheduling-platform-postgres-1 \
  psql -U algorithm -d algorithm
```

在 `psql` 中对 Repository 的 `job_activity/page_jobs` SQL 和 Outbox 列表 SQL执行
`EXPLAIN (ANALYZE, BUFFERS)`；SQL 定义位于
`algorithm-scheduling-platform/packages/platform_common/repository.py`。

## 真实任务验收

样本 `test_all_0903_15` 未在前端硬编码。正式接口和浏览器共同确认：

| 任务类型 | 摘要 | 按需详情 |
| --- | --- | --- |
| PPT | 切片 18 页，疑似视频 5 段 | 首段 `00:04:43-00:05:23`，中文原因为“持续画面变化，疑似播放视频” |
| PPT OCR | 识别 18 页，成功 18 页 | `ocr_pages` 总数 18，按页分页读取 |
| ASR | 音频约 2802.69 秒，708 段，文本长度 7775 | `segments` 总数 708，参数和全文默认收起 |
| 教师行为 | 视频约 2880.958 秒，有效帧 780，证据 9 条 | 区间、扫描和证据按需读取 |
| 学生行为 | 视频约 2879.921 秒，逐帧 288，证据 6 条 | 扫描、区间、逐帧和证据按需读取 |

教师行为节点处理耗时 `2302306 ms`，页面显示累计 `38:22`；PPT 区间使用视频相对时间而非绝对
时刻。真实样本中的 `repeated_dynamic_cluster` 已显示为“重复动态画面聚集，疑似播放视频”。
四个课程 Outbox 事件均为 `PUBLISHED`，页面显示“Broker 已确认”；摘要不返回 payload 或
`claim_token`，展开单事件后只返回脱敏 payload。

## 远端 Smoke 与浏览器检查

主要复现命令：

```bash
curl -fsS http://192.168.29.11:18100/ops/readiness
curl -fsS 'http://192.168.29.11:18100/ops/course-jobs?task_type=PPT&task_type=ASR&task_type=TEACHER_BEHAVIOR&task_type=STUDENT_BEHAVIOR&page=1&page_size=30'
curl -fsS http://192.168.29.11:18100/ops/course-jobs/test_all_0903_15/summary
curl -fsS http://192.168.29.11:18100/ops/course-jobs/test_all_0903_15/events
curl -fsS http://192.168.29.11:18100/api/course-jobs/test_all_0903_15
curl -fsS http://192.168.29.11:18103/health
curl -fsSI http://192.168.29.11:5174/
```

组合筛选四类且整体完成的 `test_all_0903` 样本得到 `total=14`，自定义 `page_size=3` 返回 3 条、
共 5 页；字面模糊查询得到 15 条候选。Control readiness 的 PostgreSQL、Redis、schema 均就绪；
Orchestrator 的 Outbox Publisher、消费者、节点执行、Kafka 和 Control 检查均就绪；A 服务旧查询
返回四任务类型，旧 `/ops/course-jobs/{task_id}` 返回 5 个节点，Kafka envelope 未改变。

浏览器从 `http://192.168.29.11:5174` 打开后，连接模板自动生成为 `18100/18103/9400`。三个独立
测试分别显示“发现 21 个实例”“Prometheus 指标读取成功”“发现 3 张显卡”，证明浏览器 CORS
链路可用。桌面 `1212 px` 和移动 `390 px` 均无页面级横向溢出；13 个长结果区块默认收起，展开
PPT 区间后按需显示 5 项和中文原因。

## 保护集与清理证据

发布前旧资产：

- Control 容器 `5ebc018ddfeda82dbd02e9d5360b9b89863eecc72c7ef7c332f29346a0877465`，镜像
  `sha256:e2e87cf7e4e70417274d805ce76bc873c53091d9d9e7febbe355e74fd9b98135`，revision `fd016a7...`；
- 控制台容器 `beb38f6c53afd87c74f8f30a37c2fe065a249ae8575e970ff97f2bf328a3f97e`，镜像
  `sha256:4587ca1cd6efdbe5e364e601d126a4b0c7ca512b6e94bc8113f3adceb52aaae2`。

不可删除保护集包括 Orchestrator `f902f31b...`、Vision `d0aa0e65...`、Online Gateway
`4c1dac12...`、GPU exporter `538bcbd2...`、PostgreSQL `c0cd0c09...`、Redis `82785a79...`、
Kafka `84f182b8...`、MongoDB `a5796c85...`、21 个算子容器、所有卷、BuildKit 缓存、模型、
`/data/course` 和 `/data/result`。发布后这些容器 ID均未变化，健康且重启次数为 0。

清理 dry-run 先确认旧正式容器已由 Compose 替换并不存在，两个候选容器只引用候选/当前镜像，
四个待删除旧或中间镜像不被正式容器引用。随后按完整 ID正常停止并删除候选容器
`6984ed730a0745b2407015d18facc16c063f7f27ccf7f59195791821a7a8a31f`、
`b9c1ec7156c34311cea188f25ba117dbc2d349199922078544c21e05c7402131`，并在无 `--force` 情况下删除：

- 旧 Control 镜像 `sha256:e2e87cf7e4e70417274d805ce76bc873c53091d9d9e7febbe355e74fd9b98135`；
- 旧控制台镜像 `sha256:4587ca1cd6efdbe5e364e601d126a4b0c7ca512b6e94bc8113f3adceb52aaae2`；
- 中间控制台镜像 `sha256:94549e8293631cab5ed97d8fac26ea9c444ea0b00a7c263bc5b9e9f4b440efc9`；
- 错误 revision 元数据且未部署的镜像 `sha256:c6e28ad4b7051d9fe7839a3f3dcf75f842674e6e2defc5c66f1e84ab55653210`。

`docker system df` 前后：镜像 `255 -> 251`、容器 `53 -> 51`、镜像占用约 `235 GB -> 234.9 GB`。
未执行 `docker system/image/builder prune`，未删除卷或 BuildKit 缓存。清理后 Control、控制台、
21 个算子和全部保护容器再次复核健康，真实 ASR 708 段及 4 条 Outbox 事件仍可读取。

## 回滚与边界

旧镜像已按用户要求在全部门禁通过后删除，不能本机即时回滚。需要回滚时，按旧 Git revision
`fd016a7fa7876f152a0f0e4e99feaf0fda3a6a7a` 和旧控制台 revision `996c09ba...` 重新构建，先回滚
控制台、再回滚 Control；当前新增接口为只读，不涉及数据库迁移。回滚不得修改或删除任务事实、
Outbox、卷和结果目录。

本次不验证 Kafka 消费完成语义，`PUBLISHED` 只证明 Producer 收到 Broker 确认；不持久化或展示
Topic、Partition、Offset。未重新执行七类算子的真实推理，因为算子镜像、容器和合同均未修改；
通过部署合同测试、21 个实例注册事实和容器健康确认兼容性。Harness 不保存凭据、媒体、完整
OCR/ASR 文本、完整请求响应或 Base64。
