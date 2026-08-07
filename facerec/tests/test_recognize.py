# pip install requests
import base64
import requests
from pathlib import Path

BASE_URL = "http://127.0.0.1:8003"
ENDPOINT = f"{BASE_URL}/recognize2"

TEST_IMG = Path(__file__).resolve().parent / "data" / "常泽宇.png"

def run_base64_smoke():
    """
    通过 base64 方式测试 /recognize2
    """
    b64_str = base64.b64encode(TEST_IMG.read_bytes()).decode()
    b64_url = f"data:image/jpeg;base64,{b64_str}"

    payload = {
        "raw": b64_url,
        "persons": [
            {"name": "张三", "number": "10001"},
            {"name": "李四", "number": "10002"}
        ]
    }

    resp = requests.post(ENDPOINT, json=payload, timeout=30)
    print("status:", resp.status_code)
    print("response:", resp.json())

if __name__ == "__main__":
    run_base64_smoke()
