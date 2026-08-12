import ast
import importlib
import multiprocessing
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path


def _worker_module():
    return importlib.import_module("app.core.dlib_worker")


def test_dlib_worker_module_does_not_import_arcface_runtime() -> None:
    source = Path(_worker_module().__file__).read_text(encoding="utf-8")
    imports = {
        alias.name
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imports.update(
        node.module or ""
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom)
    )

    assert not any(name.startswith("fastdeploy") for name in imports)
    assert "app.core.ai_engine" not in imports


def test_spawned_dlib_workers_do_not_load_arcface_runtime() -> None:
    worker = _worker_module()
    context = multiprocessing.get_context("spawn")
    status_queue = context.Queue()
    startup_gate = context.Event()
    predictor_path = str(
        Path(__file__).resolve().parents[1]
        / "ai_models"
        / "shape_predictor_68_face_landmarks.dat"
    )
    with ProcessPoolExecutor(
        max_workers=2,
        mp_context=context,
        initializer=worker.init_worker,
        initargs=(status_queue, startup_gate, predictor_path),
    ) as pool:
        checks = [pool.submit(worker.self_check) for _ in range(2)]
        statuses = worker.collect_startup_status(
            status_queue,
            expected_workers=2,
            timeout_seconds=30.0,
        )
        startup_gate.set()
        results = [check.result(timeout=10.0) for check in checks]

    assert len({status["pid"] for status in statuses}) == 2
    assert all(status["fastdeploy_loaded"] is False for status in statuses)
    assert all(status["ai_engine_loaded"] is False for status in statuses)
    assert all(result["fastdeploy_loaded"] is False for result in results)
    assert "fastdeploy" in sys.modules
