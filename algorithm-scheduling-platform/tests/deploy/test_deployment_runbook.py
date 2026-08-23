from __future__ import annotations

import re
from pathlib import Path

from deploy.scripts import production_stack

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUNBOOK = PROJECT_ROOT / "deploy/算法功能调度平台部署手册.md"


def _document() -> str:
    return RUNBOOK.read_text(encoding="utf-8")


def _compose_services(path: Path) -> set[str]:
    services: set[str] = set()
    inside_services = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line == "services:":
            inside_services = True
            continue
        if inside_services and line and not line.startswith((" ", "#")):
            break
        match = re.fullmatch(r"  ([a-z0-9-]+):", line)
        if inside_services and match is not None:
            services.add(match.group(1))
    return services


def test_runbook_covers_current_topology_and_lifecycle() -> None:
    document = _document()
    required = (
        "七类算子",
        "21 个算子实例",
        "18 个 GPU 实例",
        "3 个 CPU PPT Slice 实例",
        "四个平台服务",
        "PostgreSQL",
        "Kafka",
        "Redis",
        "MongoDB",
        "start-production-stack",
        "status-production-stack",
        "stop-production-stack",
        "/data/course/{task_id}",
        "/data/result/{task_id}",
        "max_connections=2048",
        "max_keepalive_connections=512",
        "pool_timeout_seconds",
        "1/3/10/30",
        "save_person_photo=false",
    )
    for value in required:
        assert value in document
    assert "八算子" not in document
    assert "24 个算子实例" not in document


def test_runbook_exports_release_environment_and_scopes_production_ledger() -> None:
    document = _document()
    required_exports = (
        'export OPERATOR_REGISTRY_TOKEN="${OPERATOR_REGISTRY_TOKEN:?required}"',
        'export EXPECTED_GIT_SHA="$DEPLOY_GIT_SHA"',
        'export RELEASE_ROOT="$REPORT_ROOT/milestone-2b/releases/$RELEASE_TAG/$EXPECTED_GIT_SHA"',
        'export PRODUCTION_ROOT="$RELEASE_ROOT/production"',
        'export PRODUCTION_LEDGER="$PRODUCTION_ROOT/production-stack.json"',
    )
    for statement in required_exports:
        assert statement in document

    assert "默认解析的 `0600` 权威账本" in document
    bash_blocks = re.findall(r"```bash\n(.*?)\n```", document, flags=re.DOTALL)
    for entrypoint in (
        "start-production-stack",
        "status-production-stack",
        "stop-production-stack",
    ):
        matching_blocks = [
            block
            for block in bash_blocks
            if f"deploy/scripts/{entrypoint}" in block
        ]
        assert len(matching_blocks) == 1, entrypoint
        assert '--reports-root "$REPORT_ROOT"' in matching_blocks[0], entrypoint


def test_runbook_prepares_git_checkout_with_explicit_deploy_key() -> None:
    document = _document()

    required = (
        "git@github.com:ZhangShen55/algorithm-scheduling.git",
        "git clone --branch \"$DEPLOY_BRANCH\"",
        "set -euo pipefail",
        "export GIT_TERMINAL_PROMPT=0",
        "test ! -L /root/.ssh/algorithm-scheduling-github-deploy",
        'stat -c %a /root/.ssh/algorithm-scheduling-github-deploy)" = 600',
        'stat -c %u /root/.ssh/algorithm-scheduling-github-deploy)" = "$(id -u)"',
        'stat -c %h /root/.ssh/algorithm-scheduling-github-deploy)" = 1',
        'test "$(git -C algorithm-scheduling remote get-url origin)" = '
        '"$DEPLOY_REPOSITORY"',
        "git -C algorithm-scheduling fetch --depth=1 origin \"$DEPLOY_GIT_SHA\"",
        "git -C algorithm-scheduling checkout --detach FETCH_HEAD",
        'test "$(git -C algorithm-scheduling rev-parse HEAD)" = "$DEPLOY_GIT_SHA"',
        'git_status_before="$(git -C algorithm-scheduling status '
        '--porcelain --untracked-files=all)"',
        'test -z "$git_status_before"',
        'git_status_after="$(git -C algorithm-scheduling status '
        '--porcelain --untracked-files=all)"',
        'test -z "$git_status_after"',
        'export EXPECTED_GIT_SHA="$DEPLOY_GIT_SHA"',
        "deploy/scripts/checkout-release",
        "DEP-020",
    )
    for value in required:
        assert value in document

    assert (
        "export GIT_SSH_COMMAND='ssh -i "
        "/root/.ssh/algorithm-scheduling-github-deploy "
        "-o IdentitiesOnly=yes -o StrictHostKeyChecking=yes'"
    ) in document
    assert 'test -z "$(git -C algorithm-scheduling status' not in document
    assert "reset --hard" not in "\n".join(
        re.findall(r"```bash\n(.*?)\n```", document, flags=re.DOTALL)
    )
    assert "clean -fd" not in "\n".join(
        re.findall(r"```bash\n(.*?)\n```", document, flags=re.DOTALL)
    )


def test_runbook_has_one_a_service_smoke_section_and_no_fake_final_sha() -> None:
    document = _document()

    assert document.count("deploy/scripts/run-online-gateway-smoke") == 1
    final_release_label = re.search(
        r"最终 Git SHA/release：(.*?)(?=\n\n)",
        document,
        flags=re.DOTALL,
    )
    assert final_release_label is not None
    assert re.search(r"\b[0-9a-fA-F]{40}\b", final_release_label.group(0)) is None


def test_all_documented_deploy_script_paths_exist() -> None:
    document = _document()
    referenced = set(re.findall(r"`(deploy/scripts/[A-Za-z0-9_.-]+)`", document))
    assert {
        "deploy/scripts/start-production-stack",
        "deploy/scripts/status-production-stack",
        "deploy/scripts/stop-production-stack",
        "deploy/scripts/apply-database-migrations",
        "deploy/scripts/production-image-lifecycle",
    }.issubset(referenced)
    for relative in referenced:
        assert (PROJECT_ROOT / relative).is_file(), relative


def test_runbook_names_every_authoritative_compose_service_and_port() -> None:
    document = _document()
    compose_files = (
        "docker-compose.infrastructure.yml",
        "docker-compose.platform.yml",
        "docker-compose.operators.yml",
    )
    services: set[str] = set()
    for filename in compose_files:
        services.update(_compose_services(PROJECT_ROOT / "deploy" / filename))

    assert len(services) == 29
    for service in services:
        assert service in document, service
    for port in production_stack.REQUIRED_HOST_PORTS:
        assert str(port) in document, port


def test_runbook_lists_eleven_configuration_authorities() -> None:
    document = _document()
    config_paths = {
        value
        for value in re.findall(
            r"`((?:\.\./)?[A-Za-z0-9_./-]+\.toml)`",
            document,
        )
        if "/" in value
    }
    expected = {
        "../control_service/config.toml",
        "../orchestrator_service/config.toml",
        "../vision_orchestrator_service/config.toml",
        "../online_gateway_service/config.toml",
        "deploy/config/operators/asr_offline.gpu.toml",
        "deploy/config/operators/asr_online.gpu.toml",
        "deploy/config/operators/facerec.gpu.toml",
        "deploy/config/operators/ocr.gpu.toml",
        "deploy/config/operators/ppt_slice.cpu.toml",
        "deploy/config/operators/screen_det.gpu.toml",
        "deploy/config/operators/vbas.gpu.toml",
    }
    assert config_paths == expected


def test_runbook_bash_blocks_do_not_execute_destructive_shortcuts() -> None:
    document = _document()
    bash_blocks = re.findall(r"```bash\n(.*?)\n```", document, flags=re.DOTALL)
    commands = "\n".join(bash_blocks)

    assert "docker system prune" not in commands
    assert "docker compose down" not in commands
    assert "docker volume rm" not in commands
    assert not re.search(r"\brm\b[^\n]*/data/result", commands)
    assert "production-image-lifecycle execute" in commands
    assert "docker container rm" not in commands
    assert "docker image rm" not in commands
    assert "text_analysis" not in commands


def test_lifecycle_entrypoints_are_executable_and_runbook_does_not_embed_secrets() -> None:
    for entrypoint in (
        "start-production-stack",
        "status-production-stack",
        "stop-production-stack",
        "apply-database-migrations",
        "production-image-lifecycle",
    ):
        assert (PROJECT_ROOT / "deploy/scripts" / entrypoint).stat().st_mode & 0o111

    document = _document()
    assert "BEGIN OPENSSH PRIVATE KEY" not in document
    assert "BEGIN PRIVATE KEY" not in document
    assert "model-assets.manifest.json\n```json" not in document


def test_deploy_readme_points_to_unique_runbook() -> None:
    document = (PROJECT_ROOT / "deploy/README.md").read_text(encoding="utf-8")

    assert "[算法功能调度平台部署手册](./算法功能调度平台部署手册.md)" in document
    assert 'export OPERATOR_REGISTRY_TOKEN="${OPERATOR_REGISTRY_TOKEN:?required}"' in document
    assert 'export EXPECTED_GIT_SHA="$(git -C .. rev-parse HEAD)"' in document
    assert 'export PRODUCTION_LEDGER="$PRODUCTION_ROOT/production-stack.json"' in document
    assert "release 级默认 `0600` 权威账本" in document
