# 里程碑 2B 七算子当前发布场景

本文件是 `run-milestone-2b-8a7` 的当前执行合同。旧
`milestone-2b-deploy.md` 继续作为八算子 release 的不可变历史事实保存；当前控制器只复用其中
已经审计过的维护锁、容器账本、精确恢复和镜像清理脚手架，并在生成运行脚本时从
`deploy/operator-topology.json` 取得当前数量门禁。

```json
{
  "schema_version": 1,
  "topology_path": "deploy/operator-topology.json",
  "lifecycle_scaffold": "harness/scenarios/milestone-2b-deploy.md",
  "forbidden_runtime_markers": [
    "text_analysis",
    "text-analysis",
    "extract_keywords",
    "course_overviews",
    "PPT_KEYWORDS",
    "COURSE_OVERVIEW",
    "!= 24",
    "= 24",
    "24 个 service"
  ]
}
```

当前运行时必须由拓扑权威得到 7 类算子、21 个实例、18 个 GPU 实例、3 个 CPU 实例、
14 个配置解析进程和 7 类 Smoke；不得从本文或历史场景复制另一套可漂移的数量常量。
