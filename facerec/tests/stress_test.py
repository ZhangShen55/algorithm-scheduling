import os
import time
import threading
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

# ========= 配置区 =========
IMAGE_PATH   = "test.png"  # 本地待上传图片
URL          = "http://localhost:8003/recognize"
MAX_WORKERS  = 32                 # 并发数
TIMEOUT      = 100                     # 超时时长（秒）
# ==========================

# 全局计数器（加锁防止并发丢失）
stats = {"ok": 0, "fail": 0, "timeout": 0, "other": 0}
lock = threading.Lock()
# 收集少量错误样本，用于排查
errors = []
MAX_ERRORS = 10

def single_request():
    try:
        with open(IMAGE_PATH, "rb") as f:
            resp = requests.post(
                URL,
                files={"photo": ("test.png", f, "image/png")},
                timeout=TIMEOUT,
            )
        if resp.status_code == 200:
            with lock:
                stats["ok"] += 1
            return "OK"
        else:
            with lock:
                stats["fail"] += 1
                if len(errors) < MAX_ERRORS:
                    errors.append(f"HTTP_{resp.status_code}:{resp.text}")
            return f"HTTP_{resp.status_code}"
    except requests.exceptions.Timeout:
        with lock:
            stats["timeout"] += 1
        return "TIMEOUT"
    except Exception as e:
        with lock:
            stats["other"] += 1
            if len(errors) < MAX_ERRORS:
                errors.append(repr(e))
        return f"ERROR:{e}"

def worker_loop():
    """单个线程的死循环任务"""
    while True:
        single_request()

def print_stats():
    """每 2 秒打印一次汇总"""
    while True:
        time.sleep(0.5)
        with lock:
            snapshot = stats.copy()
            err_snapshot = list(errors)
        total = sum(snapshot.values())
        err_tail = "; ".join(err_snapshot[-3:]) if err_snapshot else "-"
        print(f"[{time.strftime('%H:%M:%S')}]  total={total}  "
              f"ok={snapshot['ok']}  fail={snapshot['fail']}  "
              f"timeout={snapshot['timeout']}  other={snapshot['other']}  "
              f"errs={err_tail}")

def main():
    # 检查图片是否存在
    if not os.path.isfile(IMAGE_PATH):
        raise SystemExit(f"图片不存在 -> {IMAGE_PATH}")

    # 启动统计线程
    ThreadPoolExecutor(max_workers=1).submit(print_stats)

    # 启动 16 个长寿命工作线程
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        # 一次性 submit 16 个死循环任务
        futures = [pool.submit(worker_loop) for _ in range(MAX_WORKERS)]
        # 阻塞主线程（永不退出）
        for f in as_completed(futures):
            # 理论上不会完成，除非线程异常退出
            print("线程异常退出:", f.exception())

if __name__ == "__main__":
    main()
