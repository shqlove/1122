from pathlib import Path
import subprocess

import pytest

from dialogue_tool.generation import CanvasError, _sha
from dialogue_tool.segment_assembly import assemble_segments
from dialogue_tool.service import Service
from dialogue_tool.settings import settings


@pytest.mark.parametrize('multiple', [False, True])
def test_real_segment_concat_order_audio_and_restart_reuse(tmp_path, multiple):
    config = settings()
    if not config.get('ffmpeg') or not Path(config['ffmpeg']).is_file():
        pytest.skip('ffmpeg unavailable')
    source = tmp_path / 'source.mp4'
    source.write_bytes(b'fixture')
    service = Service({**config, 'data': str(tmp_path / 'data')})
    p = service.projects.create(source)
    tasks = {}
    for gid, color, tone in [('red', 'red', 440), ('blue', 'blue', 880)]:
        f = Path(p['directory']) / (gid + '.mp4')
        subprocess.run([config['ffmpeg'], '-v', 'error', '-nostdin', '-y',
            '-f', 'lavfi', '-i', f'color=c={color}:s=320x240:r=24:d=1',
            '-f', 'lavfi', '-i', f'sine=frequency={tone}:sample_rate=48000:duration=1',
            '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(f)], check=True, capture_output=True)
        tasks[gid] = {'status': 'downloaded', 'output': str(f), 'sha256': _sha(f)}
    p['generation'] = {'tasks': tasks, 'segment_plan': {
        'parts': [{'group_id': 'red'}, {'group_id': 'blue'}],
        'assembly': {'order': ['red','blue']}}}
    plan = p['generation']['segment_plan']
    if multiple:
        p['generation'].pop('segment_plan')
        p['generation']['dialogue_plans'] = {'one': plan, 'two': {
            'parts': [{'group_id': 'blue'}, {'group_id': 'red'}],
            'assembly': {'order': ['blue', 'red']}}}
    service.projects.save(p)
    tasks['blue']['status'] = 'running'
    assemble_segments(service, p)
    assert 'output' not in plan['assembly']
    tasks['blue']['status'] = 'downloaded'
    assemble_segments(service, p)
    assembly = plan['assembly']
    result = Path(assembly['output'])
    assert assembly['status'] == 'assembled' and 1.9 < assembly['duration'] < 2.2
    for when, index in [('0.4', 0), ('1.4', 2)]:
        raw = subprocess.run([config['ffmpeg'], '-v', 'error', '-ss', when,
            '-i', str(result), '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'],
            check=True, capture_output=True).stdout
        assert raw[index] > 200 and raw[2-index] < 40
    if multiple:
        other = p['generation']['dialogue_plans']['two']['assembly']['output']
        assert other != str(result)
        raw = subprocess.run([config['ffmpeg'], '-v', 'error', '-ss', '0.4',
            '-i', other, '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'],
            check=True, capture_output=True).stdout
        assert raw[2] > 200 and raw[0] < 40
    before = result.stat().st_mtime_ns
    assemble_segments(service, service.projects.get(p['id']))
    assert result.stat().st_mtime_ns == before
    Path(tasks['red']['output']).write_bytes(b'corrupted')
    with pytest.raises(CanvasError, match='文件已变化'):
        assemble_segments(service, p)
