"""Single short real-time smoke; owns and always stops its backend process group.

Run from the project root with:
python -c 'import runpy; runpy.run_path("test/validate_cumulative_v4.py", run_name="__main__")' --help
"""
import argparse
import asyncio
import json
import math
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
import urllib.request

import numpy as np
import soundfile as sf
import websockets


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


async def client(args, output):
    audio, rate = sf.read(args.wav, dtype='int16', start=round(args.start*16000),
                          frames=round(args.duration*16000))
    if rate != 16000 or audio.ndim != 1 or not len(audio):
        raise ValueError('expected nonempty mono 16 kHz audio')
    sf.write(output / 'clip.wav', audio, rate, subtype='PCM_16')
    expected = math.ceil(len(audio)/7680) + 5
    rows = []
    last_at = time.monotonic()
    started = last_at
    async with websockets.connect('ws://127.0.0.1:8084/v1.0.4/seacraft_asr_online',
                                  max_size=None, open_timeout=10) as ws:
        with (output / 'responses.jsonl').open('w', encoding='utf-8') as stream:
            async def reader():
                nonlocal last_at
                async for payload in ws:
                    row = json.loads(payload)
                    rows.append(row)
                    last_at = time.monotonic()
                    stream.write(json.dumps(row, ensure_ascii=False)+'\n')
                    stream.flush()
            receive = asyncio.create_task(reader())
            try:
                for index in range(expected):
                    frame = audio[index*7680:(index+1)*7680]
                    frame = np.pad(frame, (0,7680-len(frame)))
                    await ws.send(frame.astype('<i2', copy=False).tobytes())
                    await asyncio.sleep(max(0, started+(index+1)*.48-time.monotonic()))
                    if time.monotonic()-last_at > args.response_timeout:
                        raise TimeoutError('no response within bounded smoke deadline')
                until = time.monotonic()+args.response_timeout
                while time.monotonic()<until:
                    if rows and rows[-1]['ed']>=round(expected*.48,2) and time.monotonic()-last_at>1:
                        break
                    if receive.done():
                        await receive
                        break
                    await asyncio.sleep(.1)
                if not rows or rows[-1]['ed'] < round(expected*.48,2):
                    raise AssertionError('incomplete source processing')
            finally:
                await ws.close()
                await asyncio.gather(receive, return_exceptions=True)
    ends = set()
    previous = 0
    previous_final = 0
    for row in rows:
        assert set(row)=={'key','text','finished','bg','ed'}, row.keys()
        assert previous <= row['ed'] and row['bg'] <= row['ed']
        assert row['bg'] >= previous_final
        assert row['key'] not in ends, 'update after finished'
        previous = row['ed']
        if row['finished']:
            ends.add(row['key'])
            previous_final = row['ed']
    assert any(r['text'] for r in rows), 'empty transcript'
    summary = {'source':str(Path(args.wav).resolve()), 'start_seconds':args.start,
               'source_samples':len(audio),'source_duration':len(audio)/16000,
               'chunks_with_tail':expected,'responses':len(rows),'finals':len(ends),
               'last_ed':rows[-1]['ed'],'elapsed_seconds':time.monotonic()-started,
               'protocol_pass':True,'identification':'accuracy awaiting user review'}
    write_json(output/'summary.json',summary)
    return rows, summary


def collect_positions(output, rows, summary):
    events = []
    for line in (output/'service.log').open(errors='replace'):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        try:
            row.update(json.loads(row.get('event', '')))
        except (ValueError, TypeError):
            pass
        if row.get('name',row.get('event'))=='v4_correction_visible':
            events.append(row)
    positions=[]
    for event in events:
        index=event['response_sequence']-1
        if index>=len(rows):
            continue
        row=rows[index]
        before=next((r['text'] for r in reversed(rows[:index]) if r['key']==row['key']), '')
        positions.append({'response_line':index+1,'key':row['key'],
            'kind':event['correction_kind'],'finished':row['finished'],
            'source_bg':summary['start_seconds']+event['start_sample']/16000,
            'source_ed':summary['start_seconds']+event['end_sample']/16000,
            'before':before,'after':row['text']})
    write_json(output/'correction-positions.json',positions)
    summary['intermediate_visible']=sum(not p['finished'] for p in positions)
    summary['final_visible']=sum(p['finished'] for p in positions)
    return summary


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--config',default='test/config.server12-v4.toml')
    p.add_argument('--wav',default='test/教师1_16k.wav')
    p.add_argument('--start',type=float,default=98)
    p.add_argument('--duration',type=float,default=30)
    p.add_argument('--output',required=True)
    p.add_argument('--response-timeout',type=float,default=15)
    p.add_argument('--require-intermediate',action='store_true')
    args=p.parse_args()
    output=Path(args.output).resolve()
    output.mkdir(parents=True,exist_ok=False)
    with socket.socket() as probe:
        probe.bind(('0.0.0.0',8084))
    env=dict(os.environ,CONFIG_PATH=str(Path(args.config).resolve()))
    rows=[]
    summary={}
    process=None
    with (output/'service.log').open('w') as log:
        try:
            process=subprocess.Popen([sys.executable,'-m','uvicorn','app.main:app',
                '--host','0.0.0.0','--port','8084','--workers','1',
                '--ws-max-queue','16','--ws-max-size','640000'],
                env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
            write_json(output/'process.json',{'pid':process.pid,'config':env['CONFIG_PATH'],
                                              'cwd':os.getcwd()})
            ready=False
            deadline=time.monotonic()+300
            while time.monotonic()<deadline:
                if process.poll() is not None:
                    raise RuntimeError('backend exited during startup')
                try:
                    with urllib.request.urlopen('http://127.0.0.1:8084/get_status',timeout=1) as r:
                        ready=json.load(r).get('status')=='living'
                    if ready:
                        break
                except (OSError,ValueError):
                    pass
                time.sleep(.5)
            if not ready:
                raise TimeoutError('startup exceeded five minutes')
            print('backend ready; starting short real-time clip',flush=True)
            rows,summary=asyncio.run(client(args,output))
        except Exception as exc:
            write_json(output/'failure.json',{'type':type(exc).__name__,'message':str(exc)})
            raise
        finally:
            if process is not None and process.poll() is None:
                os.killpg(process.pid,signal.SIGTERM)
                try:
                    process.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid,signal.SIGKILL)
                    process.wait(timeout=10)
            summary['backend_stopped']=process is None or process.poll() is not None
            with socket.socket() as check:
                summary['port_closed']=check.connect_ex(('127.0.0.1',8084))!=0
            write_json(output/'cleanup.json',{k:summary[k] for k in ['backend_stopped','port_closed']})
    summary=collect_positions(output,rows,summary)
    write_json(output/'summary.json',summary)
    print(json.dumps(summary,ensure_ascii=False),flush=True)
    if args.require_intermediate and not summary['intermediate_visible']:
        raise AssertionError('no intermediate correction was displayed')


if __name__=='__main__':
    main()
