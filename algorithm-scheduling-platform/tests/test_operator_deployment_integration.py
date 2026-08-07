from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]


def test_all_operator_entrypoints_install_the_shared_registry_runtime() -> None:
    expected = {
        "asr_offline": ("app/main.py", "asr_offline", ["asr_offline"]),
        "asr_online": ("app/main.py", "asr_online", ["asr_online"]),
        "ppt_slice": ("app/main.py", "ppt_slice", ["ppt_slice"]),
        "ocr": ("app/main.py", "ocr", ["ocr"]),
        "text_analysis": (
            "app/main.py",
            "text_analysis",
            ["course_overviews", "extract_keywords"],
        ),
        "vbas": ("app/main.py", "vbas", ["student_behavior", "teacher_behavior"]),
        "facerec": ("app/main.py", "facerec", ["recognize"]),
        "screen_det": ("app/application.py", "screen_det", ["detect_all"]),
    }

    for project, (relative_path, operator_code, capabilities) in expected.items():
        source = (WORKSPACE_ROOT / project / relative_path).read_text(encoding="utf-8")
        assert "install_operator_runtime" in source, project
        assert f'operator_code="{operator_code}"' in source, project
        for capability in capabilities:
            assert f'"{capability}"' in source, (project, capability)

    ppt_source = (WORKSPACE_ROOT / "ppt_slice/app/main.py").read_text(encoding="utf-8")
    assert "declared_capacity=settings.MAX_CONCURRENT_TASKS" in ppt_source
    assert "inflight_provider=task_manager.get_task_count" in ppt_source


def test_asr_images_run_one_registered_uvicorn_endpoint_without_internal_nginx() -> None:
    expected_ports = {"asr_offline": 8083, "asr_online": 8084}

    for project, port in expected_ports.items():
        project_root = WORKSPACE_ROOT / project
        start_script = (project_root / "docker" / "start.sh").read_text(encoding="utf-8")
        dockerfiles = list((project_root / "docker").glob("Dockerfile*"))
        docker_text = "\n".join(path.read_text(encoding="utf-8") for path in dockerfiles)
        config = (project_root / "config.toml").read_text(encoding="utf-8")

        assert "nginx" not in start_script.lower(), project
        assert "instance_count" not in start_script, project
        assert "--workers 1" in start_script, project
        assert f'${{PORT:-{port}}}' in start_script, project
        assert "nginx" not in docker_text.lower(), project
        assert f"EXPOSE {port}" in docker_text, project
        assert "instance_count" not in config, project
        assert not (project_root / "docker" / "nginx.conf").exists(), project


def test_operator_business_routes_and_default_ports_remain_compatible() -> None:
    contracts = {
        "asr_offline": {
            "port": "8083",
            "sources": {
                "app/api/routes/asr_v17.py": ["/v1.1.7/seacraft_asr"],
                "app/api/routes/asr_v18.py": ["/v1.1.8/seacraft_asr"],
            },
        },
        "asr_online": {
            "port": "8084",
            "sources": {
                "app/api/routes/ws_online.py": ["/v1.0.1/seacraft_asr_online"],
            },
        },
        "facerec": {
            "port": "8003",
            "sources": {"app/router/faces.py": ["/recognize"]},
        },
        "ocr": {
            "port": "8866",
            "sources": {"app/api/routes/ocr.py": ["/ocr/prediction"]},
        },
        "screen_det": {
            "port": "8880",
            "sources": {"app/api/v1/aggregate.py": ["/detect_all"]},
        },
        "ppt_slice": {
            "port": "9001",
            "sources": {
                "app/api/v1/video.py": ["/LocalVideoPPTSliceTasks/v1.0.0"]
            },
        },
        "vbas": {
            "port": "8981",
            "sources": {
                "app/api/stu_tea_behavior.py": [
                    "/ImageDetect/student/v1.0.0",
                    "/ImageDetect/teacher/v1.0.0",
                ]
            },
        },
        "text_analysis": {
            "port": "8000",
            "sources": {
                "app/api/v1/routes/course_overviews.py": ["/v1/course_overviews"],
                "app/api/v1/routes/extract_keywords_text.py": [
                    "/v1/extract_keywords"
                ],
            },
        },
    }

    for project, contract in contracts.items():
        project_root = WORKSPACE_ROOT / project
        searchable = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (
                project_root / "app" / "main.py",
                project_root / "app" / "application.py",
                project_root / "config.toml",
                project_root / "AGENTS.md",
            )
            if path.exists()
        )
        assert contract["port"] in searchable, project
        for relative_path, paths in contract["sources"].items():
            source = (project_root / relative_path).read_text(encoding="utf-8")
            for route_path in paths:
                assert route_path in source, (project, route_path)


def test_operator_compose_declares_restart_health_mounts_and_instance_identity() -> None:
    compose_path = (
        Path(__file__).resolve().parents[1]
        / "deploy"
        / "docker-compose.operators.yml"
    )
    compose = compose_path.read_text(encoding="utf-8")

    for service in (
        "asr-offline-gpu0",
        "asr-online-gpu0",
        "asr-offline-gpu1",
        "asr-online-gpu1",
        "ppt-slice",
        "ocr",
        "text-analysis",
        "vbas",
        "facerec",
        "screen-det",
    ):
        assert f"  {service}:" in compose
        assert f"PLATFORM_INSTANCE_ID: {service}" in compose
    assert "restart: unless-stopped" in compose
    assert "PLATFORM_CONTROL_SERVICE_URL: http://control-service:18100" in compose
    assert "PLATFORM_REGISTRATION_ENABLED: \"true\"" in compose
    assert "PLATFORM_DECLARED_CAPACITY: ${PPT_SLICE_CAPACITY:-15}" in compose
    assert "${COURSE_ROOT:-/data/course}:/data/course" in compose
    assert "${RESULT_ROOT:-/data/result}:/data/result" in compose
    assert "/ops/health" in compose
    assert "PLATFORM_GPU_ID: \"0\"" in compose
    assert "PLATFORM_GPU_ID: \"1\"" in compose
