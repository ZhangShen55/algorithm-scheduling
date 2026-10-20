import argparse
import asyncio
import json
import os
import time
import wave
from pathlib import Path

import websockets
from websockets.exceptions import ConnectionClosed


DEFAULT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WAV = DEFAULT_ROOT / "test" / "教师1_16k.wav"
DEFAULT_OUTPUT = DEFAULT_ROOT / "test" / "v1_0_4_results.jsonl"
DEFAULT_URL = "ws://192.168.29.12:8084/v1.0.4/seacraft_asr_online"
CHUNK_SAMPLES = 960 * 8
CHUNK_SECONDS = CHUNK_SAMPLES / 16000


def read_pcm_chunks(
    wav_path: Path,
    max_seconds: float | None = None,
    start_seconds: float = 0.0,
):
    with wave.open(str(wav_path), "rb") as wav:
        if wav.getnchannels() != 1 or wav.getsampwidth() != 2 or wav.getframerate() != 16000:
            raise ValueError("测试音频必须是 16 kHz、单声道、16-bit WAV")
        if start_seconds < 0:
            raise ValueError("start_seconds 不能为负数")
        wav.setpos(min(wav.getnframes(), int(start_seconds * wav.getframerate())))
        remaining_samples = None
        if max_seconds is not None:
            remaining_samples = max(1, int(max_seconds * wav.getframerate()))
        while True:
            if remaining_samples is not None and remaining_samples <= 0:
                break
            frames_to_read = CHUNK_SAMPLES
            if remaining_samples is not None:
                frames_to_read = min(frames_to_read, remaining_samples)
            chunk = wav.readframes(frames_to_read)
            if not chunk:
                break
            if remaining_samples is not None:
                remaining_samples -= len(chunk) // wav.getsampwidth()
            if len(chunk) < CHUNK_SAMPLES * 2:
                chunk += b"\x00" * (CHUNK_SAMPLES * 2 - len(chunk))
            yield chunk


async def run(
    wav_path: Path,
    ws_url: str,
    output_path: Path,
    tail_chunks: int,
    drain_seconds: float = 10.0,
    max_seconds: float | None = None,
    start_seconds: float = 0.0,
    open_timeout: float = 60.0,
    response_timeout: float = 30.0,
    send_mode: str = "fast",
    window_seconds: float = 2.88,
) -> None:
    if send_mode not in {"fast", "realtime"}:
        raise ValueError("send_mode 必须为 fast 或 realtime")
    if not CHUNK_SECONDS <= window_seconds <= 9.6:
        raise ValueError("window_seconds 必须在 0.48 到 9.6 秒之间")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    counters = {"responses": 0, "final_results": 0, "source_chunks": 0, "tail_chunks": 0}
    last_response_at = time.monotonic()
    last_progress_at = last_response_at
    last_ed = 0.0
    sent_seconds = 0.0
    progress = asyncio.Event()
    status, error, close_code, close_reason = "failed", None, None, None

    async def receive_responses(websocket, output):
        nonlocal last_response_at, last_progress_at, last_ed
        while True:
            response = json.loads(await websocket.recv())
            counters["responses"] += 1
            counters["final_results"] += int(response.get("finished") is True)
            output.write(json.dumps(response, ensure_ascii=False) + "\n")
            output.flush()
            last_response_at = time.monotonic()
            ed = float(response["ed"])
            if ed > last_ed:
                last_ed = ed
                last_progress_at = last_response_at
                progress.set()

    async def watch_progress():
        while True:
            await asyncio.sleep(min(0.1, response_timeout / 4))
            # 重复 ed 的纠正响应不能掩盖音频处理进度停滞。
            if sent_seconds > last_ed + 0.011 and time.monotonic() - last_progress_at >= response_timeout:
                raise TimeoutError(f"音频处理进度超过 {response_timeout}s 未推进，最后 ed={last_ed}")

    async def send_audio(websocket):
        nonlocal sent_seconds, last_progress_at
        next_send_at = time.monotonic()

        async def send(chunk, counter):
            nonlocal next_send_at, sent_seconds, last_progress_at
            if send_mode == "realtime":
                await asyncio.sleep(max(0, next_send_at - time.monotonic()))
            # 以音频时间戳释放窗口，重复 ed 的纠正响应不释放额度。
            while sent_seconds + CHUNK_SECONDS - last_ed > window_seconds + 1e-6:
                progress.clear()
                await progress.wait()
            if sent_seconds <= last_ed + 0.011:
                last_progress_at = time.monotonic()
            await websocket.send(chunk)
            counters[counter] += 1
            sent_seconds = (counters["source_chunks"] + counters["tail_chunks"]) * CHUNK_SECONDS
            # 调度延迟后不突发补发；纠正响应不会增加发送额度。
            next_send_at = time.monotonic() + CHUNK_SECONDS

        for chunk in read_pcm_chunks(
            wav_path, max_seconds=max_seconds, start_seconds=start_seconds,
        ):
            await send(chunk, "source_chunks")
        for _ in range(tail_chunks):
            await send(b"\x00" * (CHUNK_SAMPLES * 2), "tail_chunks")

        while last_ed + 0.011 < sent_seconds:
            await asyncio.sleep(0.05)
        drain_started = time.monotonic()
        while time.monotonic() - last_response_at < drain_seconds:
            if time.monotonic() - drain_started > response_timeout + drain_seconds:
                raise TimeoutError("音频已发送完毕，但额外响应未在限定时间内结束")
            await asyncio.sleep(0.05)

    try:
        with output_path.open("w", encoding="utf-8") as output:
            async with websockets.connect(
                ws_url, max_size=None, open_timeout=open_timeout, close_timeout=5,
            ) as websocket:
                tasks = [asyncio.create_task(receive_responses(websocket, output)),
                         asyncio.create_task(send_audio(websocket)),
                         asyncio.create_task(watch_progress())]
                try:
                    done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                    # 优先传播接收/看门狗错误，不能把断连误报为成功。
                    for task in tasks:
                        if task in done:
                            task.result()
                    status = "completed"
                finally:
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
    except ConnectionClosed as exc:
        close_code = exc.rcvd.code if exc.rcvd else 1006
        close_reason = exc.rcvd.reason if exc.rcvd else str(exc)
        error = f"WebSocket 已关闭: code={close_code}, reason={close_reason}"
        raise
    except BaseException as exc:
        status = "interrupted" if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt)) else "failed"
        error = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        summary = {
            "wav": str(wav_path), "url": ws_url, **counters,
            "status": status, "error": error,
            "close_code": close_code, "close_reason": close_reason,
            "start_seconds": start_seconds,
            "send_mode": send_mode, "window_seconds": window_seconds,
            "sent_audio_seconds": round(sent_seconds, 3),
            "source_padded_seconds": round(counters["source_chunks"] * CHUNK_SECONDS, 3),
            "last_response_ed": last_ed,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "output": str(output_path),
        }
        output_path.with_suffix(".summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wav", type=Path, default=Path(os.getenv("ASR_TEST_WAV", DEFAULT_WAV)))
    parser.add_argument("--url", default=os.getenv("ASR_TEST_URL", DEFAULT_URL))
    parser.add_argument(
        "--output", type=Path, default=Path(os.getenv("ASR_TEST_OUTPUT", DEFAULT_OUTPUT))
    )
    parser.add_argument("--tail-chunks", type=int, default=3)
    parser.add_argument("--drain-seconds", type=float, default=10.0)
    parser.add_argument("--max-seconds", type=float, default=None)
    parser.add_argument("--start-seconds", type=float, default=0.0)
    parser.add_argument("--open-timeout", type=float, default=60.0)
    parser.add_argument("--response-timeout", type=float, default=30.0,
                        help="已发送音频的处理进度无推进超时秒数，默认 30")
    parser.add_argument("--send-mode", choices=("fast", "realtime"), default="fast",
                        help="默认 fast 按处理进度加速；realtime 每 0.48 秒发送一块")
    parser.add_argument("--window-seconds", type=float, default=2.88,
                        help="允许领先服务端 ed 的音频秒数，范围 0.48~9.6，默认 2.88")
    args = parser.parse_args()
    if not CHUNK_SECONDS <= args.window_seconds <= 9.6:
        parser.error("--window-seconds 必须在 0.48 到 9.6 秒之间")
    asyncio.run(
        run(
            args.wav,
            args.url,
            args.output,
            max(1, args.tail_chunks),
            drain_seconds=max(0.1, args.drain_seconds),
            max_seconds=args.max_seconds,
            start_seconds=args.start_seconds,
            open_timeout=max(1.0, args.open_timeout),
            response_timeout=max(1.0, args.response_timeout),
            send_mode=args.send_mode,
            window_seconds=args.window_seconds,
        )
    )


if __name__ == "__main__":
    main()
