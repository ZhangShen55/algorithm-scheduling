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
DEFAULT_OUTPUT = DEFAULT_ROOT / "test" / "v1_0_2_results.jsonl"
DEFAULT_URL = "ws://192.168.29.12:8084/v1.0.2/seacraft_asr_online"
CHUNK_SAMPLES = 960 * 8


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
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    counters = {"responses": 0, "final_results": 0}
    last_response_at = time.monotonic()
    response_queue = asyncio.Queue()

    async def receive_responses(websocket, output):
        nonlocal last_response_at
        try:
            while True:
                response = json.loads(await websocket.recv())
                counters["responses"] += 1
                counters["final_results"] += int(response.get("finished") is True)
                output.write(json.dumps(response, ensure_ascii=False) + "\n")
                output.flush()
                last_response_at = time.monotonic()
                await response_queue.put(response)
        except ConnectionClosed:
            pass

    with output_path.open("w", encoding="utf-8") as output:
        async with websockets.connect(
            ws_url,
            max_size=None,
            open_timeout=open_timeout,
        ) as websocket:
            receiver = asyncio.create_task(receive_responses(websocket, output))
            for chunk in read_pcm_chunks(
                wav_path,
                max_seconds=max_seconds,
                start_seconds=start_seconds,
            ):
                await websocket.send(chunk)
                await response_queue.get()

            # 协议没有显式 EOS 帧，补充静音块让服务端有机会完成最后一个语音段。
            for _ in range(tail_chunks):
                await websocket.send(b"\x00" * (CHUNK_SAMPLES * 2))
                await response_queue.get()

            # v1.0.2 句末会在一个输入块后发送临时结果和最终结果，必须
            # 等待一段连续空闲时间才能确保这些额外响应已经写入 JSONL。
            while time.monotonic() - last_response_at < drain_seconds:
                await asyncio.sleep(0.1)
            await websocket.close()
            await receiver

    elapsed = time.perf_counter() - started
    summary = {
        "wav": str(wav_path),
        "url": ws_url,
        "responses": counters["responses"],
        "final_results": counters["final_results"],
        "elapsed_seconds": round(elapsed, 3),
        "output": str(output_path),
    }
    summary_path = output_path.with_suffix(".summary.json")
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


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
    args = parser.parse_args()
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
        )
    )


if __name__ == "__main__":
    main()
