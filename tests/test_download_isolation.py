"""One failed download/archive must not block other original task results."""
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from dialogue_tool.generation import CanvasError, CanvasWorkflow


def test_partial_download_keeps_successful_archives_and_retries_only_failed_item(tmp_path, monkeypatch):
    project = {'name': '独立下载测试', 'groups': [{'id': 'bad'}, {'id': 'good'}],
               'generation': {'model': 'MiniMax-H3', 'tasks': {
                   k: {'group_id': k, 'submit_id': k, 'status': 'completed'} for k in ('bad', 'good')}}}
    flow = CanvasWorkflow(SimpleNamespace(config={'video_archive': str(tmp_path / 'desktop')}))
    monkeypatch.setattr(flow, '_load', lambda _: project)
    saved = []
    monkeypatch.setattr(flow, '_save', lambda p: saved.append(p['generation']['tasks']['good']['status']))
    calls = []
    def download(p, cfg, task):
        calls.append(task['group_id'])
        if task['group_id'] == 'bad' and calls.count('bad') == 1:
            raise CanvasError('模拟连接中断')
        output = tmp_path / (task['group_id'] + '.mp4')
        output.write_bytes(task['group_id'].encode())
        task.update(status='downloaded', output=str(output), sha256=hashlib.sha256(output.read_bytes()).hexdigest())
    monkeypatch.setattr(flow, '_download_task', download)
    with pytest.raises(CanvasError, match='成功项已保存'):
        flow.download('project')
    good = project['generation']['tasks']['good']
    assert Path(good['archive_output']).read_bytes() == b'good'
    assert project['generation']['tasks']['bad']['status'] == 'completed'
    flow.download('project')
    assert calls == ['bad', 'good', 'bad']
    assert not project['generation']['download_errors']
    assert 'download_error' not in project['generation']['tasks']['bad']


def test_archive_failure_can_resume_without_redownloading(tmp_path, monkeypatch):
    import dialogue_tool.generation as module
    source = tmp_path / 'original.mp4'; source.write_bytes(b'original')
    project = {'name': '归档测试', 'groups': [{'id': 'one'}], 'generation': {'tasks': {
        'one': {'group_id': 'one', 'submit_id': 'same-id', 'status': 'downloaded', 'output': str(source)}}}}
    flow = CanvasWorkflow(SimpleNamespace(config={'video_archive': str(tmp_path / 'desktop')}))
    monkeypatch.setattr(flow, '_load', lambda _: project)
    monkeypatch.setattr(flow, '_save', lambda _: None)
    monkeypatch.setattr(flow, '_download_task', lambda *a: pytest.fail('must not download again'))
    original = module.archive_videos
    monkeypatch.setattr(module, 'archive_videos', lambda *a, **k: (_ for _ in ()).throw(OSError('磁盘暂不可用')))
    with pytest.raises(CanvasError, match='磁盘'):
        flow.download('project')
    assert project['generation']['tasks']['one']['status'] == 'downloaded'
    monkeypatch.setattr(module, 'archive_videos', original)
    flow.download('project')
    assert Path(project['generation']['tasks']['one']['archive_output']).read_bytes() == b'original'
