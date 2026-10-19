"""Capture raw streaming watermarks and real cumulative second-pass output."""
import argparse
import json
import time
from pathlib import Path

import numpy as np
import soundfile as sf
from funasr import AutoModel
from modelscope.pipelines import pipeline
from modelscope.utils.constant import Tasks

from app.core.model_assets import prepare_decrypted_model_dir, cleanup_prepared_model_dirs
from app.core.cumulative_text import CumulativeText


def alignment_evidence(data):
    state = CumulativeText()
    evidence = []
    for revision, item in enumerate(data['prefixes'], 1):
        end = item['seconds'] * 16000
        source = ''.join(r['text'] for r in data['stream'] if r['end_sample'] <= end)
        state.raw = ''.join(r['text'] for r in data['stream'] if r['end_sample'] <= end + 16000)
        before = state.visible
        reason = state.adopt(item['punctuated'], source, end, revision)
        evidence.append({'seconds':item['seconds'], 'source':source, 'before':before,
                         'candidate':item['punctuated'], 'reason':reason, 'after':state.visible})
    return evidence


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    parser.add_argument('--start', type=float, default=98)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    audio, rate = sf.read(root / 'test/教师1_16k.wav', dtype='float32',
                          start=round(args.start * 16000), frames=21 * 16000)
    assert rate == 16000 and audio.ndim == 1
    def load(name, device):
        return AutoModel(model=prepare_decrypted_model_dir(str(root / 'model' / name)),
                         device=device, ngpu=1, disable_update=True, disable_pbar=True)
    try:
        online = load('speech_paraformer_asr_nat-zh-cn-16k-common-vocab8404-online', 'cuda:0')
        offline = load('speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-pytorch', 'cuda:1')
        punct = pipeline(task=Tasks.punctuation, model=prepare_decrypted_model_dir(str(
            root / 'model/punc_ct-transformer_zh-cn-common-vad_realtime-vocab272727')),
            device='cuda:1', disable_update=True)
        cache = {}
        records = []
        for end in range(7680, len(audio) + 1, 7680):
            result = online.generate(input=audio[end-7680:end], cache=cache,
                is_final=False, chunk_size=[4,8,4], encoder_chunk_look_back=4,
                decoder_chunk_look_back=1)
            records.append({'end_sample': end, 'text': result[0].get('text','')})
        prefixes = []
        for seconds in [4,8,12,20]:
            started = time.monotonic()
            result = offline.generate(input=audio[:seconds*16000], cache={})
            text = result[0].get('text','')
            p = punct(text, cache={})
            ptext = p[0].get('text', '') if isinstance(p, list) else p.get('text', '')
            prefixes.append({'seconds': seconds, 'raw':text, 'punctuated': ptext,
                             'elapsed':time.monotonic()-started})
        data = {'start_seconds':args.start,'stream':records,'prefixes':prefixes}
        data['alignment'] = alignment_evidence(data)
        (output / 'prototype.json').write_text(json.dumps(data, ensure_ascii=False, indent=2))
        print('prototype complete', output)
    finally:
        cleanup_prepared_model_dirs()


if __name__ == '__main__':
    main()
