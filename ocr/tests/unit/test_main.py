from app import main
from app.core.settings import load_settings


def test_run_uses_server_settings(monkeypatch, settings_file):
    settings = load_settings(settings_file)
    calls = []

    monkeypatch.setattr(main, "load_settings", lambda: settings)
    monkeypatch.setattr(main.uvicorn, "run", lambda *args, **kwargs: calls.append((args, kwargs)))

    main.run()

    assert calls == [
        (
            ("app.main:app",),
            {
                "host": "127.0.0.1",
                "port": 8866,
                "workers": 1,
            },
        )
    ]


def test_module_exports_application():
    assert main.app is not None


def test_main_uses_the_same_startup_path(monkeypatch):
    calls = []
    monkeypatch.setattr(main, "run", lambda: calls.append(True))

    main.main()

    assert calls == [True]
