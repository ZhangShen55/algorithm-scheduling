import importlib
import sys
import types


def _install_lightweight_tias_settings():
    sys.modules["app.core.settings"] = types.SimpleNamespace(
        operator_deployment=types.SimpleNamespace(
            platform=types.SimpleNamespace(
                registration_enabled=False,
                control_service_url="",
                heartbeat_interval_seconds=5,
                max_concurrent_requests=128,
            ),
            runtime=types.SimpleNamespace(require_gpu=False),
        ),
        settings=types.SimpleNamespace(
            TiasExposeLegacySyncTasks=False,
            InstanceId="tias-test",
            BaseUrl="http://127.0.0.1:8981",
            AiQualityBaseUrl="",
            MaxConcurrentBatches=1,
            MaxQueueSize=0,
            HeartbeatIntervalSeconds=5,
            HeartbeatTimeoutSeconds=15,
            RegisterRetryIntervalSeconds=1,
            TIAS={
                "TiasExposeLegacySyncTasks": False,
            },
        ),
        APP_VER="test",
        ADP_VER="test",
        ALG_VER="test",
        Total_HaveProcess_Tasks={"val": 0},
        use_half=False,
        yolo_person_model=object(),
        yolo_face_model=object(),
        yolo_student_model=object(),
        yolo_teacher_behavior_model=object(),
    )


def test_tias_default_api_surface_excludes_removed_routes(monkeypatch):
    for module_name in list(sys.modules):
        if module_name.startswith("app.") and module_name != "tias":
            sys.modules.pop(module_name, None)

    _install_lightweight_tias_settings()

    async def analyze_student_behavior_parallel(request):
        return "student"

    async def analyze_teacher_behavior_by_model(request):
        return "teacher"

    sys.modules["app.services.student_behavior_service"] = types.SimpleNamespace(
        analyze_student_behavior_parallel=analyze_student_behavior_parallel,
    )
    sys.modules["app.services.teacher_behavior_service"] = types.SimpleNamespace(
        analyze_teacher_behavior_by_model=analyze_teacher_behavior_by_model,
    )

    module = importlib.import_module("app.main")
    paths = {route.path for route in module.app.routes}

    assert "/ImageDetect/student/v1.0.0" in paths
    assert "/AE/WorkerStatus" in paths
    assert "/AE/Health" in paths
    assert "/AE/Drain" in paths
    assert "/ImageDetect/student/v1.0.1" not in paths
    assert "/ImageDetect/teacher/v1.0.0" in paths
    assert "/AE/Capacity" not in paths
    assert "/AE/Capacity_v2" not in paths
    assert "/AE/Version" not in paths
    assert "/AE/LogLevel" not in paths
    assert "/AE/SyncTasks" not in paths
    assert "/AE/SyncTasks2" not in paths
