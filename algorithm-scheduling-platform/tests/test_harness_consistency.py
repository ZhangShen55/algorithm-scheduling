from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
HARNESS_ROOT = PROJECT_ROOT / "harness"


def _decision_rows() -> list[list[str]]:
    lines = (HARNESS_ROOT / "architecture-review.md").read_text(encoding="utf-8").splitlines()
    rows: list[list[str]] = []
    for line in lines:
        if not line.startswith("| DEC-"):
            continue
        rows.append([cell.strip() for cell in line.strip("|").split("|")])
    return rows


def test_every_architecture_decision_has_reproducible_evidence() -> None:
    rows = _decision_rows()

    assert rows, "architecture-review.md 必须至少记录一项架构决策"
    for row in rows:
        assert len(row) == 6
        decision_id, decision, owner, command, verdict, scenario = row
        assert all((decision_id, decision, owner, command, verdict, scenario))
        assert verdict in {"符合", "部分符合", "不符合", "待验证"}
        assert command.startswith("`") and command.endswith("`")
        scenario_path = PROJECT_ROOT / scenario.strip("`")
        assert scenario_path.is_file(), f"{decision_id} 缺少场景文件: {scenario_path}"


def test_harness_contains_required_governance_files() -> None:
    required = {
        "README.md",
        "architecture-review.md",
        "change-ledger.md",
        "verification.md",
        "scenarios/runtime-closure.md",
        "scenarios/ppt-shared-result.md",
        "scenarios/milestone-2b-deploy.md",
    }

    assert required <= {
        str(path.relative_to(HARNESS_ROOT))
        for path in HARNESS_ROOT.rglob("*.md")
    }


def test_milestone_2b_bootstraps_clean_clone_harness_python_before_commands() -> None:
    scenario = (HARNESS_ROOT / "scenarios/milestone-2b-deploy.md").read_text(
        encoding="utf-8"
    )
    required_fragments = (
        'python3 -m venv "$PWD/.venv"',
        '"$PWD/.venv/bin/python" -m pip install .',
        "import httpx",
        "import websockets",
        "import yaml",
        'metadata.version("httpx")',
        'metadata.version("PyYAML")',
        'metadata.version("websockets")',
        'HARNESS_RUNTIME_EVIDENCE="$RELEASE_ROOT/preflight/harness-python-runtime.json"',
        'mktemp "$RELEASE_ROOT/preflight/.harness-python-runtime.XXXXXX"',
        'mv -f -- "$HARNESS_RUNTIME_TMP" "$HARNESS_RUNTIME_EVIDENCE"',
        'export DEPLOY_PYTHON="$PWD/.venv/bin/python"',
    )
    for fragment in required_fragments:
        assert fragment in scenario, f"canonical 2B 场景缺少 Harness Python 合同: {fragment}"

    bootstrap_finished = scenario.index(
        'export DEPLOY_PYTHON="$PWD/.venv/bin/python"'
    )
    first_preflight = scenario.index("deploy/scripts/preflight host")
    first_registration = scenario.index("deploy/scripts/verify-operator-registration")
    first_smoke = scenario.index("deploy/scripts/run-operator-smoke")
    assert bootstrap_finished < first_preflight
    assert bootstrap_finished < first_registration
    assert bootstrap_finished < first_smoke


def test_clean_clone_harness_python_contract_is_documented() -> None:
    readme = (PROJECT_ROOT / "deploy/README.md").read_text(encoding="utf-8")
    agents = (PROJECT_ROOT / "AGENTS.md").read_text(encoding="utf-8")

    assert "`DEPLOY_PYTHON` -> 项目 `.venv/bin/python` -> `python3`" in readme
    assert "回退解释器仍必须自身具备 Harness 依赖" in readme
    assert "canonical 里程碑 2B 始终准备并使用项目 `.venv`" in readme
    assert "`.env` 与 `.venv` 含义不同" in agents
    assert "clean clone 必须先准备项目 `.venv`" in agents


def test_arch_003_preserves_registration_lease_and_invocation_directions() -> None:
    design = (PROJECT_ROOT.parent / "docs/算法功能调度平台总体设计-v2.md").read_text(
        encoding="utf-8"
    )
    arch_003 = design.split("### 6.5 ARCH-003", 1)[1].split("## 7.", 1)[0]

    for edge in (
        'OFF -->|"注册 / 心跳"| C',
        'VB -->|"注册 / 心跳"| C',
        'ON -->|"注册 / 心跳"| C',
        'CPU -->|"注册 / 心跳"| C',
        'O -->|"租约申请"| C',
        'V -->|"租约申请"| C',
        'G -->|"租约申请"| C',
        'O -->|"离线 ASR / OCR"| OFF',
        'O -->|"PPT / Text Analysis"| CPU',
        'V -->|"VBas 帧推理"| VB',
        'G -->|"在线 ASR / FaceRec / ScreenDet"| ON',
        'G -->|"弱实时 VBas"| VB',
    ):
        assert edge in arch_003

    assert "C --> OFF" not in arch_003
    assert "C --> VB" not in arch_003
    assert "C --> ON" not in arch_003
    assert "C --> CPU" not in arch_003
