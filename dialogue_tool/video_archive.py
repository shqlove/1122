"""Copy completed videos to the desktop library, preserving project originals."""
import hashlib
import re
import shutil
import uuid
from datetime import datetime
from pathlib import Path


def clean_name(value):
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', str(value)).strip(' .')[:60] or '未命名'


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def archive_videos(project, cfg, destination, *, group_ids=None):
    if not destination:
        return
    tasks = [t for t in cfg.get('tasks', {}).values() if t.get('status') == 'downloaded' and t.get('output')
             and (group_ids is None or t.get('group_id') in group_ids)]
    if not tasks:
        return
    root = Path(destination).resolve()
    # Old batches have no submission timestamp; use the earliest original file time.
    stamp = cfg.get('submitted_at') or min(Path(t['output']).stat().st_mtime for t in tasks)
    channel = '优云智算H3' if cfg.get('model') in ('MiniMax-H3', 'minimax-h3-lite') else '即梦'
    identity = '|'.join(sorted(str(t.get('submit_id') or t.get('group_id')) for t in cfg.get('tasks', {}).values()))
    batch = hashlib.sha256(identity.encode()).hexdigest()[:8]
    folder = root / (datetime.fromtimestamp(stamp).strftime('%Y-%m-%d_%H-%M-%S') + '_' + clean_name(project['name']) + '_' + channel + '_' + batch)
    # Persist grouping in the config so subsequent downloads join the same batch.
    saved = cfg.get('archive_directory')
    if saved and Path(saved).resolve().is_relative_to(root):
        folder = Path(saved).resolve()
    cfg['archive_directory'] = str(folder)
    folder.mkdir(parents=True, exist_ok=True)
    numbers = {g['id']: i for i, g in enumerate(project['groups'], 1)}
    for task in tasks:
        source = Path(task['output'])
        checksum = digest(source)
        if task.get('sha256') and checksum != task['sha256'].lower():
            raise RuntimeError('项目成片与下载校验值不符，未归档或覆盖，请检查原文件')
        identity = clean_name(task.get('submit_id') or task['group_id'])
        number = numbers.get(task.get('source_group_id') or task['group_id'], 0)
        segment = task.get('segment') or {}
        part = f"_第{segment['index']}段" if segment.get('count', 1) > 1 else ''
        target = folder / f"第{number:02d}组{part}_{channel}_{identity}{source.suffix}"
        if target.exists() and digest(target) != checksum:
            # Preserve files the user has edited in the desktop library.
            target = target.with_stem(target.stem + '_' + checksum[:12])
        if not target.exists():
            partial = target.with_name(target.name + '.' + uuid.uuid4().hex + '.partial')
            try:
                shutil.copy2(source, partial)
                if digest(partial) != checksum:
                    raise RuntimeError('桌面视频归档校验失败，项目原片已保留')
                partial.replace(target)
            finally:
                partial.unlink(missing_ok=True)
        elif digest(target) != checksum:
            raise RuntimeError('桌面归档存在同名不同内容文件，请检查，未覆盖')
        task['archive_output'] = str(target)
        task['archive_sha256'] = checksum
