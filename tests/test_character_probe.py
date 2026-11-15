import pytest

from dialogue_tool.character_probe import validate_probe
from dialogue_tool.providers import ProviderError


def test_probe_accepts_complete_classifications_in_any_order():
    result = {'frames': [
        {'frame': 2, 'status': 'uncertain', 'reason': '面部遮挡'},
        {'frame': 0, 'status': 'matched', 'reason': '与正例一致'},
        {'frame': 1, 'status': 'not_matched', 'reason': '不同角色'},
    ]}
    assert validate_probe(result, 3) is result
    assert validate_probe({'frames': []}, 0) == {'frames': []}


@pytest.mark.parametrize('indices', [[0], [0, 0], [0, 2], [-1, 0], [0, True], [0, 1.0]])
def test_probe_rejects_missing_duplicate_invalid_or_non_integer_indices(indices):
    result = {'frames': [{'frame': index, 'status': 'uncertain', 'reason': ''} for index in indices]}
    with pytest.raises(ProviderError):
        validate_probe(result, 2)


@pytest.mark.parametrize('result', [
    {},
    {'frames': None},
    {'frames': [{'frame': 0, 'status': 'unknown', 'reason': ''}]},
    {'frames': [{'frame': 0, 'status': 'uncertain'}]},
    {'frames': [{'frame': 0, 'status': 'uncertain', 'reason': None}]},
    {'frames': [{'frame': 0, 'status': 'uncertain', 'reason': '', 'extra': 1}]},
    {'frames': [], 'extra': 1},
])
def test_probe_rejects_malformed_response(result):
    with pytest.raises(ProviderError):
        validate_probe(result, 1)


def test_probe_without_confirmed_reference_retains_every_frame_and_caches_uncertainty(tmp_path, monkeypatch):
    from dialogue_tool import service as service_module

    source = tmp_path / '插翅难逃.mp4'
    source.write_bytes(b'video')
    service = service_module.Service({'data': str(tmp_path / 'data'), 'codex': 'fake'})
    project = service.projects.create(source)
    project['scan_directory'] = str(tmp_path)
    frames = [{'id': index, 'seconds': index, 'file': f'{index}.jpg'} for index in range(3)]
    requests = []

    def request(codex, images, folder, context, schema):
        requests.append(context)
        folder.mkdir()
        (folder / 'events.jsonl').write_text('', encoding='utf-8')
        return {'frames': [
            {'frame': index, 'status': status, 'reason': '模型判断'}
            for index, status in enumerate(['matched', 'not_matched', 'uncertain'])
        ]}

    monkeypatch.setattr(service_module, 'request_images', request)
    result = service._cached_request(project, 'probe', frames, '定位角色')
    assert [item['status'] for item in result['frames']] == ['uncertain'] * 3
    assert all('缺少用户确认正例' in item['reason'] for item in result['frames'])
    assert service._cached_request(project, 'probe', frames, '定位角色') == result
    assert len(requests) == 1
    assert '角色定位schema' in requests[0]


@pytest.mark.parametrize('hit_status', ['matched', 'uncertain', 'not_matched'])
def test_character_probe_expands_neighbours_or_completes_with_zero_groups(tmp_path, monkeypatch, hit_status):
    from dialogue_tool import service as service_module

    source = tmp_path / '插翅难逃.mp4'
    source.write_bytes(b'video')
    service = service_module.Service({'data': str(tmp_path / 'data'), 'codex': 'fake'})
    project = service.projects.create(source)
    frames = [{'id': index, 'seconds': index, 'shot_id': index // 3, 'file': f'{index}.jpg'}
              for index in range(27)]
    project.update(scan={'frames': frames}, scan_directory=str(tmp_path))
    service.projects.save(project)
    supplied = {'probe': [], 'discovery': []}

    def request(snapshot, kind, batch, context, *, persist=True):
        supplied[kind].extend(frame['id'] for frame in batch)
        if kind == 'probe':
            return {'frames': [
                {'frame': index, 'status': hit_status if frame['shot_id'] == 4 else 'not_matched',
                 'reason': '定位结果'}
                for index, frame in enumerate(batch)
            ]}
        assert kind == 'discovery'
        return {'summary': '无合格对话', 'pairs': []}

    monkeypatch.setattr(service, '_cached_request', request)
    result = service.analyze(project['id'])
    assert supplied['probe'] == list(range(1, 27, 3))
    expected = set(range(7, 21, 3)) if hit_status != 'not_matched' else set()
    assert set(supplied['discovery']) == expected
    assert result['analysis_report']['discovery_images'] == len(expected)
    assert result['analysis_report']['character_probe_images'] == 9
    assert len(result['character_probe']) == 9
    assert result['scan']['frames'] == frames
    assert result['groups'] == []
    assert result['phase'] == 'review'
    assert result['analysis_progress']['step'] == 'complete'
