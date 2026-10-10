import argparse
import json
import resource
import statistics
import time
import wave
from pathlib import Path

import numpy as np
import torch
from funasr import AutoModel


CLIPS = (
    (1.44, 2.88),
    (74.88, 5.76),
    (180.48, 10.08),
)


def read_clip(path: Path, start_seconds: float, duration_seconds: float) -> np.ndarray:
    with wave.open(str(path), "rb") as wav:
        if wav.getframerate() != 16000 or wav.getnchannels() != 1 or wav.getsampwidth() != 2:
            raise ValueError("benchmark input must be 16 kHz mono signed 16-bit PCM")
        wav.setpos(int(start_seconds * wav.getframerate()))
        pcm = wav.readframes(int(duration_seconds * wav.getframerate()))
    return np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0


def peak_rss_mib() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kind", choices=("seaco", "sensevoice"), required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--wav", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=5)
    args = parser.parse_args()

    torch.set_num_threads(4)
    clips = [(start, duration, read_clip(args.wav, start, duration)) for start, duration in CLIPS]

    load_started = time.perf_counter()
    model = AutoModel(
        model=str(args.model),
        device="cpu",
        ngpu=0,
        disable_update=True,
        disable_pbar=True,
    )
    load_seconds = time.perf_counter() - load_started

    def infer(audio: np.ndarray):
        if args.kind == "sensevoice":
            return model.generate(
                input=audio,
                cache={},
                language="zh",
                use_itn=True,
                batch_size_s=60,
            )
        return model.generate(input=audio, is_final=True, spk_model=None)

    infer(clips[0][2])
    results = []
    for start, duration, audio in clips:
        samples = []
        text = ""
        for _ in range(args.repeats):
            started = time.perf_counter()
            response = infer(audio)
            samples.append(time.perf_counter() - started)
            if response and isinstance(response[0], dict):
                text = response[0].get("text", "") or ""
        median_seconds = statistics.median(samples)
        results.append(
            {
                "start_seconds": start,
                "audio_seconds": duration,
                "runs_seconds": [round(value, 6) for value in samples],
                "median_seconds": round(median_seconds, 6),
                "mean_seconds": round(statistics.mean(samples), 6),
                "rtf_median": round(median_seconds / duration, 6),
                "text": text,
            }
        )

    report = {
        "kind": args.kind,
        "model": str(args.model.resolve()),
        "device": "cpu",
        "torch_threads": torch.get_num_threads(),
        "load_seconds": round(load_seconds, 6),
        "peak_rss_mib": round(peak_rss_mib(), 2),
        "repeats": args.repeats,
        "clips": results,
    }
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
