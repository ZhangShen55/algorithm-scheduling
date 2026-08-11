from __future__ import annotations

import email
import subprocess
import sys
import zipfile
from pathlib import Path

PACKAGE_ROOT = (
    Path(__file__).resolve().parents[1] / "packages" / "operator_registry_client"
)


def test_registry_client_builds_as_lightweight_isolated_wheel(tmp_path: Path) -> None:
    pyproject = PACKAGE_ROOT / "pyproject.toml"
    assert pyproject.is_file()

    wheel_dir = tmp_path / "wheelhouse"
    wheel_dir.mkdir()
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            "--no-build-isolation",
            "--wheel-dir",
            str(wheel_dir),
            str(PACKAGE_ROOT),
        ],
        check=True,
        cwd=tmp_path,
    )

    wheels = list(wheel_dir.glob("algorithm_operator_registry_client-*.whl"))
    assert len(wheels) == 1
    wheel = wheels[0]

    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        assert "packages/operator_registry_client/__init__.py" in names
        assert not any(name.startswith("packages/platform_common/") for name in names)
        metadata_name = next(name for name in names if name.endswith(".dist-info/METADATA"))
        metadata = email.message_from_bytes(archive.read(metadata_name))

    assert metadata["Requires-Python"] == ">=3.10"
    requirements = set(metadata.get_all("Requires-Dist", []))
    assert requirements == {
        "fastapi<1,>=0.109",
        "httpx<1,>=0.25",
        "pydantic<3,>=2.5",
    }
