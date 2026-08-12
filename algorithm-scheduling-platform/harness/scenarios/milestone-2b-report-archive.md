# 里程碑 2B 报告与受限模型清单归档

## 范围

本场景验证 Task 7D 的目录、权限和 Git 边界，不执行 Docker 构建、GPU 验证、真实推理或远端部署。
它也不重复 Task 7C 的模型 manifest 生成与内容校验。

## 固定合同

- 常规运行证据位于
  `deploy/reports/milestone-2b/releases/{release_tag}/{git_sha}/{category}`。
- 分类固定为 `preflight`、`container-maintenance`、`image-build`、`gpu-instances`、
  `registration`、`smoke`、`negative`、`load`、`recovery` 和 `summary`。
- 报告目录和受限归档目录使用 `0700`；外部 manifest 的受控副本使用 `0600`。
- 外部逐文件模型 manifest 必须保存在 Git 工作树外；仓库只提交不含哈希的
  `deploy/model-assets.json` 和目录说明。
- 外部 manifest 的直接父目录必须由当前用户拥有且为 `0700`，文件必须是同一用户拥有的普通
  `0600` 文件；源父链不得包含软链接。生成器原子建立该文件，归档器在写目标前重新验证。
- 初始化重复执行不得删除、截断或覆盖既有证据；同一 release/SHA 已归档不同 manifest 时失败。
- 同一 release/SHA 使用 release 级 `flock` 串行化；临时文件写满并 `fsync` 后原子发布，再对目录
  `fsync`。中断恢复只清理由当前用户拥有且匹配严格随机命名的归档临时文件。
- 路径穿越、软链接、非目录、重叠根和位于 Git 工作树内的受限根必须失败。

## 本地验证

从 `algorithm-scheduling-platform` 执行：

```bash
.venv/bin/python -m pytest -q tests/test_milestone_2b_report_archive.py
.venv/bin/ruff check tests/test_milestone_2b_report_archive.py \
  deploy/scripts/prepare-report-directory
.venv/bin/python -m mypy --strict deploy/scripts/prepare-report-directory \
  tests/test_milestone_2b_report_archive.py
.venv/bin/python -m py_compile deploy/scripts/prepare-report-directory \
  tests/test_milestone_2b_report_archive.py

git check-ignore -q \
  deploy/reports/milestone-2b/releases/v1.0_260812/0000000000000000000000000000000000000000/smoke/result.json
test ! -n "$(git check-ignore deploy/reports/.gitkeep deploy/reports/README.md \
  deploy/reports/milestone-2b/.gitkeep deploy/model-assets.json)"
```

## 证据与结论

- RED：新增门禁首次运行 `25` 项，其中 `20` 项按预期失败，缺口为目录骨架、忽略规则和初始化器。
- GREEN：规格复审补强后，7C/7D 聚焦测试共 `78` 项通过；覆盖 ignore 正反合同、`0700/0600`、
  幂等、既有证据保留、manifest 不同内容拒绝、源或目标位于 worktree、源父链软链接、非目录、
  路径穿越和工作树内受限根拒绝。
- 质量复审补强：真实子进程验证归档写满临时文件后被 `SIGKILL`，重跑可清理严格命名临时文件并
  发布完整终态；相同 manifest 两进程并发均成功，不同 manifest 并发恰好一方成功、一方内容冲突。
- 证据层级：静态、单元和本地文件系统行为符合。
- 未达到：目标服务器权限、真实报告生成、八镜像、24 实例、GPU、注册、smoke、反例、压力和恢复。
