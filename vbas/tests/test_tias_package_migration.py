import importlib


def test_tias_main_is_service_entrypoint():
    module = importlib.import_module("app.main")

    assert hasattr(module, "app")
