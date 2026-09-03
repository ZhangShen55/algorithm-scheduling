# 算法调度运维控制台页面调整验证

## 范围

本记录对应 OpenSpec 变更 `standardize-ops-console-deployment-and-observability` 的页面调整和正式替换。目标机为 `192.168.29.11`。本次只更新 `algorithm-scheduling-ops-console` 控制台容器和相关运维说明，没有修改或重启 Control Service、online-gateway-service、A 服务、七类算子、PostgreSQL、Redis、Kafka 或 GPU exporter。

## 页面调整

- 刷新期间不再插入会改变页面高度的实时读取提示行，保留顶部刷新图标作为状态反馈；
- 左侧平台工作区支持桌面收缩和展开，移动导航保持独立抽屉行为；
- 风格选择仅保留“日间模式”和“深色模式”，移除英文页眉和只读模式装饰文案；
- 总览移除 Task ID 查询，任务追踪增加“课程任务”和“查询课程任务”二级分区；
- Kafka 流程、网关请求分布和平台就绪检查补齐稳定布局；平台就绪检查额外展示 Kafka 聚合状态；
- 三个刷新周期的最小配置值统一为 1 秒，服务地址仍支持页面内修改并保存在当前浏览器。

## 本地验证

- `npm ci` 通过，依赖审计无漏洞；
- `npm run build` 通过，TypeScript 和 Vite 构建无错误；
- `git diff --check` 通过；
- 静态构建资源中已确认不存在旧英文页眉和“正在读取实时观测数据”提示行，存在“日间模式”“深色模式”“任务发布 / Kafka”和“查询课程任务”。

## 正式发布

目标机按 x86_64 原生 Docker 构建并替换控制台镜像：

```text
镜像：algorithm-scheduling/ops-console:v0.1_260903_996c09ba
镜像摘要：sha256:4587ca1cd6efdbe5e364e601d126a4b0c7ca512b6e94bc8113f3adceb52aaae2
容器：beb38f6c53afd87c74f8f30a37c2fe065a249ae8575e970ff97f2bf328a3f97e
入口：http://192.168.29.11:5174/
状态：healthy
重启次数：0
```

GPU exporter 保持原容器和镜像不变：`algorithm-scheduling/gpu-metrics-exporter:v0.1_260903_2ace5aa2`，状态 `healthy`，重启次数 `0`。

## 真实数据复核

- Control Service 实例接口返回 21 个实例，覆盖 7 类算子；
- 任务列表使用 `page=1&page_size=10&sort_by=updated_at&order=desc`，本次复核总数为 13,441，首条为 `test_all_0903_15`；
- Control `/ops/readiness` 返回 PostgreSQL、Redis 和 schema ready；
- Gateway `/ready` 返回 ready；
- GPU `/gpu` 返回 3 张 GPU，型号为两张 RTX 4090 D 和一张 RTX 3090；
- 控制台首页、CSS/JS 静态资源和三个服务地址均可访问；
- 控制台和 GPU exporter 容器均保持健康，最近日志未发现 `Traceback`、`ERROR` 或 `CRITICAL`。

## 多服务器部署判断

多服务器部署说明见 `docs/算法调度多服务器部署说明.md`。当前控制台支持分别配置 Control Service、Gateway 和一个 GPU exporter 地址；多 GPU 主机需要每台主机部署 exporter，后续可将 GPU 地址扩展为列表或增加 GPU 聚合器。PostgreSQL、Redis、Kafka 由平台服务通过环境变量连接，前端不直连基础设施。当前 canonical Compose 默认仍使用同一 Docker 网络中的 `postgres`、`redis` 和 `kafka` 服务名，分机部署时需要使用 Compose override 或部署环境覆盖连接地址，并同步处理 `depends_on` 和 Kafka `advertised.listeners`。
