import subprocess
import time
import uuid

import pytest


def _docker_available() -> bool:
    return subprocess.run(
        ["docker", "info"],
        capture_output=True,
        check=False,
    ).returncode == 0


@pytest.mark.integration
def test_real_mongodb_accepts_configured_credentials_and_rejects_wrong_password() -> None:
    if not _docker_available():
        pytest.skip("Docker daemon 不可用")

    container = f"algorithm-test-mongodb-{uuid.uuid4().hex}"
    try:
        subprocess.run(
            [
                "docker",
                "run",
                "--detach",
                "--name",
                container,
                "--tmpfs",
                "/data/db",
                "--publish",
                "127.0.0.1::27017",
                "--env",
                "MONGO_INITDB_ROOT_USERNAME=face@operator",
                "--env",
                "MONGO_INITDB_ROOT_PASSWORD=p:a/ss?word",
                "mongo:7.0",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        deadline = time.monotonic() + 30
        while True:
            correct = subprocess.run(
                [
                    "docker",
                    "exec",
                    container,
                    "mongosh",
                    "--quiet",
                    "--username",
                    "face@operator",
                    "--password",
                    "p:a/ss?word",
                    "--authenticationDatabase",
                    "admin",
                    "--eval",
                    "quit(db.adminCommand({ping:1}).ok === 1 ? 0 : 2)",
                ],
                capture_output=True,
                check=False,
                text=True,
            )
            if correct.returncode == 0:
                break
            if time.monotonic() >= deadline:
                pytest.fail(f"MongoDB 未在期限内就绪: {correct.stderr}")
            time.sleep(0.5)

        wrong = subprocess.run(
            [
                "docker",
                "exec",
                container,
                "mongosh",
                "--quiet",
                "--username",
                "face@operator",
                "--password",
                "wrong-password",
                "--authenticationDatabase",
                "admin",
                "--eval",
                "quit(db.adminCommand({ping:1}).ok === 1 ? 0 : 2)",
            ],
            capture_output=True,
            check=False,
            text=True,
        )
        assert wrong.returncode != 0
    finally:
        subprocess.run(
            ["docker", "rm", "--force", container],
            capture_output=True,
            check=False,
        )
