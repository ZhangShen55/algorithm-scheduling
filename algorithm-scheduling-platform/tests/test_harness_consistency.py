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
