from __future__ import annotations

import json
import os
import secrets
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .aggregation import CampaignAggregate, CampaignClassification
from .metrics import MetricSummary

_SENSITIVE_EXACT_KEYS = frozenset(
    {
        "base64",
        "image",
        "photo",
        "audio",
        "embedding",
        "password",
        "token",
        "secret",
        "authorization",
        "credential",
        "private_key",
        "pcm",
        "media_bytes",
        "request_body",
        "response_body",
        "asr_text",
        "ocr_text",
        "full_text",
    }
)
_SENSITIVE_SUFFIXES = (
    "_base64",
    "_image",
    "_photo",
    "_audio",
    "_embedding",
    "_password",
    "_token",
    "_secret",
    "_credential",
    "_private_key",
    "_media_bytes",
    "_request_body",
    "_response_body",
    "_asr_text",
    "_ocr_text",
    "_full_text",
)


def validate_public_payload(payload: object, *, path: str = "$") -> None:
    if isinstance(payload, Mapping):
        for raw_key, value in payload.items():
            key = str(raw_key).lower()
            if key in _SENSITIVE_EXACT_KEYS or key.endswith(_SENSITIVE_SUFFIXES):
                raise ValueError(f"普通报告包含敏感字段: {path}.{raw_key}")
            validate_public_payload(value, path=f"{path}.{raw_key}")
    elif isinstance(payload, Sequence) and not isinstance(payload, (str, bytes, bytearray)):
        for index, value in enumerate(payload):
            validate_public_payload(value, path=f"{path}[{index}]")
    elif isinstance(payload, (bytes, bytearray)):
        raise ValueError(f"普通报告不能包含媒体字节: {path}")


def _metric_document(metrics: MetricSummary) -> dict[str, Any]:
    document = asdict(metrics)
    document["gateway_delta"] = asdict(metrics.gateway_delta)
    return document


def build_report_document(
    *,
    campaign_id: str,
    git_sha: str,
    summary: CampaignAggregate,
    metrics: MetricSummary,
) -> dict[str, Any]:
    if not campaign_id:
        raise ValueError("campaign_id 不能为空")
    if len(git_sha) != 40 or any(character not in "0123456789abcdef" for character in git_sha):
        raise ValueError("git_sha 必须是 40 位小写十六进制")
    document = {
        "schema_version": 1,
        "campaign_id": campaign_id,
        "git_sha": git_sha,
        **summary.to_dict(),
        "metrics": _metric_document(metrics),
        "classification_legend": [item.value for item in CampaignClassification],
    }
    validate_public_payload(document)
    return document


def _format_mapping(values: Mapping[str, object]) -> str:
    if not values:
        return "无"
    return "、".join(f"{key}={value}" for key, value in sorted(values.items()))


def render_chinese_markdown(document: Mapping[str, Any]) -> str:
    validate_public_payload(document)
    metrics = document.get("metrics")
    cases = document.get("cases")
    legend = document.get("classification_legend")
    if not isinstance(metrics, Mapping) or not isinstance(cases, list):
        raise ValueError("报告文档缺少 metrics 或 cases")
    if not isinstance(legend, list):
        raise ValueError("报告文档缺少分类图例")

    gateway = metrics.get("gateway_delta")
    if not isinstance(gateway, Mapping):
        raise ValueError("报告文档缺少 Gateway 累计差值")
    lines = [
        "# 极限负载 Campaign 报告",
        "",
        f"- Campaign ID：`{document.get('campaign_id')}`",
        f"- Git SHA：`{document.get('git_sha')}`",
        f"- 总体结论：**{document.get('overall_status')}**",
        f"- 分类口径：{'、'.join(str(item) for item in legend)}",
        "",
        "## 请求与性能结果",
        "",
        "| 用例 | 分类 | 结论 | 请求数 | 成功 | 成功率 | 容量拒绝 | 业务拒绝 | "
        "错误合计 | 超时 | 连接失败 | 非预期 5xx | 未定义错误 | P50 | P95 | P99 | "
        "吞吐 RPS | 排队 P95 | Kafka Lag | Inflight | 活跃租约 | 容器重启 |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | "
        "---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | "
        "---: | ---: |",
    ]
    for case in cases:
        if not isinstance(case, Mapping):
            raise ValueError("用例报告必须是对象")
        performance = case.get("performance")
        if not isinstance(performance, Mapping):
            raise ValueError("用例报告缺少 performance")
        lines.append(
            "| {case_id} | {classification} | {passed} | {total} | {success} | "
            "{success_rate:.4%} | {capacity} | {business} | {errors} | {timeouts} | "
            "{connections} | {http5xx} | {undefined} | {p50:.4f} | {p95:.4f} | "
            "{p99:.4f} | {throughput:.2f} | {queue:.4f} | {kafka} | {inflight} | "
            "{leases} | {restarts} |".format(
                case_id=case.get("case_id"),
                classification=case.get("classification"),
                passed="通过" if case.get("passed") is True else "未通过",
                total=performance.get("total_requests"),
                success=performance.get("successful_requests"),
                success_rate=float(performance.get("success_rate", 0)),
                capacity=performance.get("capacity_rejected"),
                business=performance.get("business_rejected"),
                errors=performance.get("error_requests"),
                timeouts=performance.get("timeouts"),
                connections=performance.get("connection_failures"),
                http5xx=performance.get("unexpected_5xx"),
                undefined=performance.get("undefined_errors"),
                p50=float(performance.get("p50_seconds", 0)),
                p95=float(performance.get("p95_seconds", 0)),
                p99=float(performance.get("p99_seconds", 0)),
                throughput=float(performance.get("throughput_rps", 0)),
                queue=float(performance.get("queue_wait_p95_seconds", 0)),
                kafka=performance.get("max_kafka_lag"),
                inflight=performance.get("peak_inflight"),
                leases=performance.get("peak_active_leases"),
                restarts=performance.get("container_restarts"),
            )
        )

    recovery = metrics.get("recovery_seconds")
    lines.extend(
        [
            "",
            "## 容量、队列与资源",
            "",
            f"- 采样数：{metrics.get('sample_count')}",
            f"- Gateway 请求累计差值：{gateway.get('requests_total')}",
            "- 实例请求累计差值："
            f"{_format_mapping(metrics.get('gateway_instance_request_delta', {}))}",
            "- 租约申请成功/拒绝/释放累计差值："
            f"{gateway.get('lease_acquired_total')}/"
            f"{gateway.get('lease_rejected_total')}/"
            f"{gateway.get('lease_released_total')}",
            f"- 峰值 Inflight：{_format_mapping(metrics.get('peak_inflight', {}))}",
            f"- 峰值活跃租约：{_format_mapping(metrics.get('peak_active_leases', {}))}",
            f"- Kafka Lag 峰值：{metrics.get('max_kafka_lag')}",
            f"- 任务队列峰值：{metrics.get('max_task_queue_depth')}",
            f"- GPU 利用率峰值：{_format_mapping(metrics.get('peak_gpu_utilization', {}))}",
            f"- GPU 显存峰值：{_format_mapping(metrics.get('peak_gpu_memory_bytes', {}))}",
            f"- GPU 进程名称：{_format_mapping(metrics.get('gpu_process_names', {}))}",
            f"- 容器 CPU 峰值：{_format_mapping(metrics.get('peak_container_cpu_percent', {}))}",
            f"- 容器内存峰值：{_format_mapping(metrics.get('peak_container_memory_bytes', {}))}",
            f"- 容器重启增量：{_format_mapping(metrics.get('container_restart_delta', {}))}",
            f"- 文件系统最小剩余字节：{metrics.get('minimum_filesystem_free_bytes')}",
            f"- 宿主机最小可用内存字节：{metrics.get('minimum_host_memory_available_bytes')}",
            f"- 宿主机 CPU 峰值：{metrics.get('peak_host_cpu_percent')}",
            "- 宿主机网络接收/发送累计差值字节："
            f"{metrics.get('host_network_receive_delta_bytes')}/"
            f"{metrics.get('host_network_transmit_delta_bytes')}",
            f"- 宿主机 socket 峰值：{metrics.get('peak_host_open_sockets')}",
            f"- 宿主机文件句柄峰值：{metrics.get('peak_host_open_file_handles')}",
            f"- 恢复时间：{'未测得' if recovery is None else f'{float(recovery):.3f} 秒'}",
            "",
            "## 用例原因",
            "",
        ]
    )
    for case in cases:
        reasons = case.get("reasons") if isinstance(case, Mapping) else None
        reason_text = "；".join(str(item) for item in reasons or ()) or "无"
        lines.append(f"- `{case.get('case_id')}`：{reason_text}")
    return "\n".join(lines) + "\n"


def atomic_write_report(path: Path, content: str) -> None:
    """同目录临时文件、fsync 和原子替换；既有报告不允许被覆盖。"""

    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    directory_fd = os.open(
        path.parent,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | os.O_NOFOLLOW,
    )
    descriptor = -1
    temporary_name = ""
    try:
        for _ in range(100):
            temporary_name = f".{path.name}.{secrets.token_hex(12)}.tmp"
            try:
                descriptor = os.open(
                    temporary_name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=directory_fd,
                )
                break
            except FileExistsError:
                continue
        if descriptor < 0:
            raise FileExistsError("无法分配唯一的报告临时文件")
        encoded = content.encode("utf-8")
        written = 0
        while written < len(encoded):
            count = os.write(descriptor, encoded[written:])
            if count <= 0:
                raise OSError("写入报告临时文件未取得进展")
            written += count
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        try:
            os.link(
                temporary_name,
                path.name,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
                follow_symlinks=False,
            )
        except FileExistsError as exc:
            raise FileExistsError(f"报告已经存在: {path}") from exc
        os.unlink(temporary_name, dir_fd=directory_fd)
        temporary_name = ""
        os.fsync(directory_fd)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary_name:
            try:
                os.unlink(temporary_name, dir_fd=directory_fd)
            except FileNotFoundError:
                pass
        os.close(directory_fd)


def render_json(document: Mapping[str, Any]) -> str:
    validate_public_payload(document)
    return json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
