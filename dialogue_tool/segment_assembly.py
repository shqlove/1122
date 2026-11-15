"""Join an explicitly ordered, completed dialogue batch without cutting speech."""
import hashlib
import json
import subprocess
from pathlib import Path

from .generation import CanvasError, _sha
from .media import probe
from .projects import project_asset


def assemble_segments(service, project):
    cfg = project.get('generation', {})
    from .dialogue_segments import assembly_plans
    for plan in assembly_plans(cfg):
        _assemble_plan(service, project, cfg, plan)


def _assemble_plan(service, project, cfg, plan):
    order = plan['assembly']['order']
    if not order or len(set(order)) != len(order) or set(order) != {p['group_id'] for p in plan['parts']}:
        raise CanvasError('分段拼接顺序与任务范围不一致')
    tasks = [cfg.get('tasks', {}).get(gid, {}) for gid in order]
    if any(t.get('status') != 'downloaded' for t in tasks):
        return
    files = [project_asset(project, t['output']) for t in tasks]
    hashes = [_sha(f) for f in files]
    if any(value != task['sha256'] for value, task in zip(hashes, tasks)):
        raise CanvasError('分段视频文件已变化，停止拼接；不会重新提交生成')
    signature = hashlib.sha256(json.dumps(hashes).encode()).hexdigest()[:16]
    existing = plan['assembly']
    if existing.get('signature') == signature and existing.get('output'):
        old = Path(existing['output'])
        if old.is_file() and _sha(old) == existing.get('sha256'):
            return
    info = [probe(f, service.config['ffprobe']) for f in files]
    videos = [next((s for s in i['streams'] if s.get('codec_type') == 'video'), None) for i in info]
    if any(v is None for v in videos) or any(not any(s.get('codec_type') == 'audio' for s in i['streams']) for i in info):
        raise CanvasError('分段视频缺少画面或对白音轨，不能拼接')
    width, height = videos[0]['width'], videos[0]['height']
    folder = Path(project['directory']) / 'assembled-videos'
    folder.mkdir(exist_ok=True)
    output = folder / f'完整拼接_{len(files)}段_{signature}.mp4'
    temporary = output.with_suffix('.partial.mp4')
    command = [service.config['ffmpeg'], '-hide_banner', '-loglevel', 'error', '-nostdin', '-y']
    filters = []
    for i, f in enumerate(files):
        command.extend(['-i', str(f)])
        filters.extend([
            f'[{i}:v]setpts=PTS-STARTPTS,scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=24[v{i}]',
            f'[{i}:a]asetpts=PTS-STARTPTS,aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[a{i}]'])
    filters.append(''.join(f'[v{i}][a{i}]' for i in range(len(files))) + f'concat=n={len(files)}:v=1:a=1[v][a]')
    command.extend(['-filter_complex', ';'.join(filters), '-map', '[v]', '-map', '[a]',
                    '-c:v', 'libx264', '-preset', 'fast', '-crf', '18', '-pix_fmt', 'yuv420p',
                    '-c:a', 'aac', '-b:a', '192k', '-movflags', '+faststart', str(temporary)])
    result = subprocess.run(command, capture_output=True, timeout=600,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise CanvasError('本地拼接失败，原分段视频已保留：' + result.stderr.decode('utf-8', errors='replace')[-600:])
    joined = probe(temporary, service.config['ffprobe'])
    duration = float(joined['format']['duration'])
    expected = sum(float(i['format']['duration']) for i in info)
    if abs(duration - expected) > max(.75, len(files) * .3):
        raise CanvasError('拼接时长校验失败，保留分段原片供检查')
    temporary.replace(output)
    existing.update(status='assembled', signature=signature, output=str(output),
                    sha256=_sha(output), duration=duration, speech_review='pending',
                    note='按顺序完整拼接，未按提示词时间裁剪对白；需核对实际发声与衔接')
    service.projects.save(project)
