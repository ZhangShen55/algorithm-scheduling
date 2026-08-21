from __future__ import annotations

from types import SimpleNamespace

from app.infrastructure import retired_node_preflight


class FakeEngine:
    def __init__(self) -> None:
        self.disposed = False

    def dispose(self) -> None:
        self.disposed = True


def test_cli_returns_zero_when_no_active_retired_nodes(monkeypatch, capsys) -> None:
    engine = FakeEngine()
    monkeypatch.setattr(retired_node_preflight, "create_engine", lambda dsn: engine)
    monkeypatch.setattr(
        retired_node_preflight,
        "find_active_retired_nodes",
        lambda current: (),
    )

    result = retired_node_preflight.main(
        ["--postgres-dsn", "postgresql+psycopg://unused/test"]
    )

    assert result == retired_node_preflight.EXIT_OK
    assert "退役节点切换预检通过" in capsys.readouterr().out
    assert engine.disposed


def test_cli_returns_blocked_code_and_chinese_diagnostics(monkeypatch, capsys) -> None:
    engine = FakeEngine()
    active = retired_node_preflight.ActiveRetiredNode(
        task_id="course-active",
        task_type="PPT",
        task_type_status=30,
        node_id=42,
        node_code="PPT_KEYWORDS",
        node_status=20,
    )
    monkeypatch.setattr(retired_node_preflight, "create_engine", lambda dsn: engine)
    monkeypatch.setattr(
        retired_node_preflight,
        "find_active_retired_nodes",
        lambda current: (active,),
    )

    result = retired_node_preflight.main(
        ["--postgres-dsn", "postgresql+psycopg://unused/test"]
    )

    captured = capsys.readouterr()
    assert result == retired_node_preflight.EXIT_ACTIVE_RETIRED_NODES
    assert "发现活动退役节点" in captured.err
    assert "course-active" in captured.err
    assert "PPT_KEYWORDS" in captured.err
    assert engine.disposed


def test_cli_returns_runtime_error_without_printing_dsn(monkeypatch, capsys) -> None:
    engine = FakeEngine()
    dsn = "postgresql+psycopg://algorithm:secret@database/test"
    monkeypatch.setattr(retired_node_preflight, "create_engine", lambda value: engine)

    def fail(current):
        raise RuntimeError("database failed at " + dsn)

    monkeypatch.setattr(retired_node_preflight, "find_active_retired_nodes", fail)

    result = retired_node_preflight.main(["--postgres-dsn", dsn])

    captured = capsys.readouterr()
    assert result == retired_node_preflight.EXIT_RUNTIME_ERROR
    assert "退役节点切换预检执行失败" in captured.err
    assert "secret" not in captured.err
    assert engine.disposed


def test_cli_uses_control_config_when_dsn_is_not_explicit(monkeypatch) -> None:
    engine = FakeEngine()
    observed: list[str] = []
    monkeypatch.setattr(
        retired_node_preflight.ControlSettings,
        "load",
        lambda config_path=None: SimpleNamespace(
            postgres=SimpleNamespace(dsn="postgresql+psycopg://from-config/test")
        ),
    )
    monkeypatch.setattr(
        retired_node_preflight,
        "create_engine",
        lambda dsn: observed.append(dsn) or engine,
    )
    monkeypatch.setattr(
        retired_node_preflight,
        "find_active_retired_nodes",
        lambda current: (),
    )

    result = retired_node_preflight.main(["--config-path", "/tmp/control.toml"])

    assert result == retired_node_preflight.EXIT_OK
    assert observed == ["postgresql+psycopg://from-config/test"]
    assert engine.disposed
