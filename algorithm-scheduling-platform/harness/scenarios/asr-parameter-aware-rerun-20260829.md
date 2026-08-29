# ASR 参数版本重跑验证

## 范围

本记录对应 OpenSpec 变更 `asr-parameter-aware-rerun`。平台为同一课程的离线 ASR 请求保存
完整 `effective_params`，按 SHA-256 `params_fingerprint` 区分参数执行版本，并将 `run_id`
传播到 Outbox、DAG 节点、算子调用、结果回写和查询响应。

## 已完成实现

- ASR 默认 `showSpk=false`、`showEmotion=false`、`showRoleIdentify=false`、
  `wordTimestamps=false`，`language=auto`、`hotWords=[]`。
- `task_type_runs` 保存版本状态、中文原因、完整结果和时间；活动指纹部分唯一，失败/取消版本
  可重新创建。
- 同一课程相同参数提交复用已完成或活动版本；参数变化创建新 `run_id`，历史结果不覆盖。
- 节点按 `run_id` 隔离；迁移前事件不带 `run_id` 时按兼容逻辑绑定当前版本。
- 控制面查询返回当前版本和 ASR `runs` 历史摘要。

## 验证命令与结果

从工作区根目录执行：

```bash
PYTHONPATH="$PWD/control_service:$PWD/algorithm-scheduling-platform:$PWD" \
  algorithm-scheduling-platform/.venv/bin/python -m pytest -q \
  control_service/tests

PYTHONPATH="$PWD/orchestrator_service:$PWD/algorithm-scheduling-platform:$PWD" \
  algorithm-scheduling-platform/.venv/bin/python -m pytest -q \
  orchestrator_service/tests

PYTHONPATH="$PWD/algorithm-scheduling-platform:$PWD" \
  algorithm-scheduling-platform/.venv/bin/python -m pytest -q \
  algorithm-scheduling-platform/tests/test_control_api_submission.py \
  algorithm-scheduling-platform/tests/test_contract_stub.py \
  algorithm-scheduling-platform/tests/test_offline_asr_adapter.py

PYTHONPATH="$PWD/algorithm-scheduling-platform:$PWD" \
  algorithm-scheduling-platform/.venv/bin/python -m pytest -q \
  algorithm-scheduling-platform/tests/integration/test_course_repository.py \
  algorithm-scheduling-platform/tests/integration/test_asr_parameter_runs.py
```

本次复核结果：control `25 passed`，orchestrator `95 passed`，契约/适配器 `24 passed`，Repository
与 ASR 参数版本集成合计 `43 passed`。真实 PostgreSQL 测试创建隔离数据库并执行
`0001`--`0009` 迁移，覆盖默认值、参数切换、同参数并发提交、失败版本重跑和历史结果保留。

## 真实算子验证

本机 `asr` 环境为 Python 3.11、Torch 2.6，使用 `test_wav/chinEng-16k.wav` 启动单 worker
`asr_offline`（端口 8083），按平台适配器的 multipart 字段调用：

```bash
conda run -n asr python -m uvicorn app.main:app --host 127.0.0.1 --port 8083 --workers 1
curl -X POST http://127.0.0.1:8083/v1.1.8/seacraft_asr \
  -F 'audioFile=@test_wav/chinEng-16k.wav' \
  -F 'language=auto' -F 'showSpk=false' -F 'showEmotion=false' \
  -F 'showRoleIdentify=false' -F 'wordTimestamps=false' -F 'hotWords='
```

结果：HTTP 200，返回 `language`、非空 `segments`（182 段）、`text`、`speed_info`、
`load_audio_time_ms` 和 `gpu_time_ms`，请求字段与 v1.1.8 原有合同兼容。该验证达到本机真实
模型推理层级；目标机三卡部署仍需在后续发布验证中单独执行。
