#!/usr/bin/env python3
"""按指定 config.toml 加载在线模型并保持运行，便于 nvidia-smi / 进程观测。"""
import asyncio
import os
import sys
import time

if len(sys.argv) < 2:
    print("用法: verify_config_runtime.py <config.toml> [hold_seconds]", file=sys.stderr)
    sys.exit(1)

os.environ["CONFIG_PATH"] = os.path.abspath(sys.argv[1])
hold = int(sys.argv[2]) if len(sys.argv) > 2 else 120

from app.core.config import settings
from app.core.models import load_models_if_needed


async def main():
    print(f"[config] path={settings.config_path}")
    print(f"[config] device={settings.device} ngpu={settings.ngpu}")
    await load_models_if_needed()
    print("[ready] 模型已加载，保持进程供观测")
    for i in range(hold):
        if i % 10 == 0:
            print(f"[alive] {i}s / {hold}s")
        await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(main())
