import copy

import pytest

from dialogue_tool.projects import normalize_character_groups
from dialogue_tool.generation import CanvasError, CanvasWorkflow
from dialogue_tool.service import Service


def test_target_swap_preserves_repairs_voice_and_is_idempotent():
    group = dict(id='g', frame_a=1, frame_b=2, person_a='听众', person_b='豪哥',
                 repaired_a='a.png', repaired_b='b.png', mask_a='ma', mask_b='mb',
                 required_character_status='matched', required_character_side='b',
                 generation_bindings={'male_side': 'a', 'voice_path': 'voice.mp3'})
    project = {'analysis_filter': {'required_character': '张世豪'}, 'groups': [group]}
    assert normalize_character_groups(project) == ['g']
    assert (group['frame_a'], group['repaired_a'], group['person_a'], group['mask_a']) == (2, 'b.png', '豪哥', 'mb')
    assert group['generation_bindings'] == {'male_side': 'a', 'voice_path': 'voice.mp3'}
    assert normalize_character_groups(project) == []
    group['required_character_side'] = 'b'
    before = copy.deepcopy(group)
    project['generation'] = {'tasks': {'g': {'status': 'draft'}}}
    with pytest.raises(ValueError, match='草稿'):
        normalize_character_groups(project)
    assert group == before


def test_target_identity_and_position_checked_before_extracting_assets(tmp_path):
    flow = CanvasWorkflow(Service({'data': str(tmp_path)}))
    project = {'analysis_filter': {'required_character': '张世豪'}, 'generation': {}}
    group = {'required_character_status': 'uncertain'}
    with pytest.raises(CanvasError, match='身份尚未确认'):
        flow.inputs(project, group)
    group.update(required_character_status='matched', required_character_side='b')
    with pytest.raises(CanvasError, match='人物A'):
        flow.inputs(project, group)


def test_character_batch_requires_shared_prompt_and_uses_group_references(tmp_path, monkeypatch):
    service = Service({'data': str(tmp_path / 'data')})
    source = tmp_path / 'source.mp4'
    source.write_bytes(b'video')
    project = service.projects.create(source)
    project['analysis_filter'] = {'required_character': '张世豪'}
    project['groups'] = [{'id': name, 'selected': True} for name in ('one', 'two')]
    project['generation'] = {'model': 'model', 'duration': 15, 'prompt_mode': 'combined'}
    service.projects.save(project)
    flow = CanvasWorkflow(service)
    monkeypatch.setattr(flow, 'inputs', lambda p, g: ({'model': 'model', 'duration': 15,
                        'prompt': '@男主说话，@聆听者聆听，参考@音色'}, g['id']))
    assert not flow.preflight(project['id'], ['one', 'two'])['ready']
    project['generation']['prompt_mode'] = 'shared'
    service.projects.save(project)
    assert flow.preflight(project['id'], ['one', 'two'])['ready']
