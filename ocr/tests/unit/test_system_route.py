from app.api.routes import system


def test_gpu_usage_accepts_cuda_device(monkeypatch):
    monkeypatch.setattr(
        system.subprocess,
        "check_output",
        lambda *args, **kwargs: "12, 34, 567\n",
    )

    usage = system._gpu_usage("cuda:1")

    assert usage == {
        "gpu_compute_used_percent": 12,
        "gpu_mem_used_percent": 34,
        "gpu_mem_used_size(MB)": 567,
    }
