import json
from pathlib import Path

import pytest
from PIL import Image

from dialogue_tool import generation, compshare
from dialogue_tool.generation import CanvasError
from dialogue_tool.service import Service
from dialogue_tool.workflow_api import WorkflowAPI


@pytest.fixture
def setup(tmp_path, monkeypatch):
    images = [tmp_path / 'a.png', tmp_path / 'b.png']
    for file in images:
        Image.new('RGB', (640, 480)).save(file)
    monkeypatch.setattr(generation, 'original_frame', lambda p, frame, *a: images[frame])
    monkeypatch.setattr('dialogue_tool.workflow_api.original_frame', lambda p, frame, *a: images[frame])
    service = Service({'data': str(tmp_path / 'db'), 'ffmpeg': 'ffmpeg', 'ffprobe': 'ffprobe'})
    source = tmp_path / 'source.mp4'; source.write_bytes(b'placeholder')
    p = service.projects.create(source)
    p['groups'] = [{'id': 'one', 'frame_a': 0, 'frame_b': 1, 'selected': True,
                    'needs_review': False, 'prompt': '@男主说话，@聆听者听。'}]
    service.projects.save(p)
    api = WorkflowAPI(service); pid = p['id']
    api.flow.select_channel(pid, 'compshare')
    api.execute(pid, 'configure_generation', dict(model='MiniMax-H3', duration=10, ratio='4:3', resolution='480P', prompt_mode='combined', common_prompt=''))
    calls = []
    def request(self, method, path, payload=None, identity=None):
        calls.append((method, path, payload, identity))
        if path == 'query/point_usage_summary':
            return {'available_points': 100.25}
        if method == 'POST':
            saved = service.projects.get(pid)['generation']['tasks']['one']
            assert saved['submit_id'] == identity
            assert saved['status'] == 'submission_unknown'
            return {'task_id': 'task-1'}
        return {'task': {'id': 'task-1', 'status': 'succeeded', 'resolution': '480P', 'content': {'url': 'https://example.com/output.mp4'}}}
    monkeypatch.setattr(compshare.H3Client, 'request', request)
    return api, pid, service, calls


def prepare(api, pid):
    assert api.execute(pid, 'generation_preflight', {'group_ids': ['one']})['ready']
    api.execute(pid, 'prepare_batch', {'group_ids': ['one']})
    api.execute(pid, 'quote_batch')
    return api.execute(pid, 'request_submission')['pending_approval']


def test_h3_approval_and_reference_contract(setup):
    api, pid, service, calls = setup
    card = prepare(api, pid)
    assert card['ceiling'] == 55 and card['available_credits'] == 100.25
    assert not any(c[0] == 'POST' for c in calls)
    api.approve_submission(pid, card['id'], 55)
    body = next(c[2] for c in calls if c[0] == 'POST')
    assert body['resolution'] == '480P' and body['mute_audio'] is False
    assert body['use_context_ir'] is True
    assert [c['role'] for c in body['content'][1:]] == ['reference_image', 'reference_image']
    assert '<Picture 1>' in body['content'][0]['text'] and '字幕' in body['content'][0]['text']
    saved = json.dumps(service.projects.get(pid), ensure_ascii=False)
    assert 'base64,' not in saved
    api.execute(pid, 'refresh_results')
    assert service.projects.get(pid)['generation']['tasks']['one']['status'] == 'completed'
    with pytest.raises(ValueError):
        api.approve_submission(pid, card['id'], 55)


def test_preserve_accepted_prompt_does_not_prepend_new_instructions(setup):
    api, pid, service, calls = setup
    prompt = '人物身份与分工。@男主是唯一说话人，@聆听者闭嘴。豪哥说出台词“<d>[Chinese] 你好 </d>”。'
    api.execute(pid, 'configure_generation', dict(model='MiniMax-H3', duration=10, ratio='4:3',
        resolution='480P', prompt_mode='shared', common_prompt=prompt,
        preserve_prompt=True, audio_first=False, use_context_ir=False))
    sheet = api.execute(pid, 'execution_sheet', {'group_id': 'one'})
    expected = prompt.replace('@男主','<Picture 1>').replace('@聆听者','<Picture 2>') + '\n画面中不要生成任何字幕、对白文字、标题或文字叠加。保留人物对白音轨。'
    assert sheet['bound_prompt'] == expected
    assert 'speech_plan' not in sheet
    card = prepare(api, pid)
    with pytest.raises(ValueError, match='新建轮次'):
        api.execute(pid, 'configure_generation', dict(model='MiniMax-H3', duration=10, ratio='4:3',
            resolution='480P', prompt_mode='shared', common_prompt=prompt, preserve_prompt=False))
    api.approve_submission(pid, card['id'], card['ceiling'])
    assert next(c[2] for c in calls if c[0]=='POST')['content'][0]['text'] == expected
    api.execute(pid,'refresh_results')
    p=service.projects.get(pid);p['generation']['tasks']['one']['status']='downloaded';service.projects.save(p)
    api.execute(pid,'new_round')
    assert service.projects.get(pid)['generation']['preserve_prompt'] is True


def test_disabled_prompt_optimization_reaches_request_and_removes_fee(setup):
    api, pid, service, calls = setup
    api.execute(pid, 'configure_generation', dict(model='MiniMax-H3', duration=10, ratio='4:3', resolution='480P', prompt_mode='combined', common_prompt='', use_context_ir=False))
    sheet = api.execute(pid, 'execution_sheet', {'group_id': 'one'})
    assert sheet['use_context_ir'] is False
    card = prepare(api, pid)
    assert card['ceiling'] == 50 and card['use_context_ir'] is False
    assert not any(c[0] == 'POST' for c in calls)
    api.approve_submission(pid, card['id'], 50)
    body = next(c[2] for c in calls if c[0] == 'POST')
    assert body['use_context_ir'] is False
    assert [c['role'] for c in body['content'][1:]] == ['reference_image', 'reference_image']


def test_prompt_optimization_cannot_change_after_quote(setup):
    api, pid, service, calls = setup
    prepare(api, pid)
    with pytest.raises(CanvasError, match='新建轮次'):
        api.execute(pid, 'configure_generation', dict(model='MiniMax-H3', duration=10, ratio='4:3', resolution='480P', prompt_mode='combined', common_prompt='', use_context_ir=False))
    assert not any(c[0] == 'POST' for c in calls)


def test_new_round_enables_context_ir_and_preserves_history(setup):
    api, pid, service, calls = setup
    api.execute(pid, 'configure_generation', dict(model='MiniMax-H3', duration=10, ratio='4:3', resolution='480P', prompt_mode='combined', common_prompt='', use_context_ir=False))
    assert prepare(api, pid)['ceiling'] == 50
    api.execute(pid, 'new_round')
    project = service.projects.get(pid)
    assert project['generation']['use_context_ir'] is True
    assert project['generation_history'][-1]['use_context_ir'] is False
    api.flow.select_channel(pid, 'compshare')
    api.execute(pid, 'configure_generation', dict(model='MiniMax-H3', duration=10, ratio='4:3', resolution='480P', prompt_mode='combined', common_prompt=''))
    card = prepare(api, pid)
    assert card['ceiling'] == 55
    api.approve_submission(pid, card['id'], 55)
    assert next(c[2] for c in calls if c[0] == 'POST')['use_context_ir'] is True


def test_selecting_h3_enables_context_ir(setup):
    api, pid, service, _ = setup
    api.execute(pid, 'configure_generation', dict(model='MiniMax-H3', duration=10, ratio='4:3', resolution='480P', prompt_mode='combined', common_prompt='', use_context_ir=False))
    api.flow.select_channel(pid, 'dreamina')
    api.flow.select_channel(pid, 'compshare')
    assert service.projects.get(pid)['generation']['use_context_ir'] is True


def test_changed_material_blocks_payment(setup):
    api, pid, service, calls = setup
    card = prepare(api, pid)
    p = service.projects.get(pid); p['groups'][0]['prompt'] += '新台词'; service.projects.save(p)
    with pytest.raises(CanvasError, match='草稿已改变'):
        api.approve_submission(pid, card['id'], 55)
    assert not any(c[0] == 'POST' for c in calls)


def test_unknown_submission_keeps_identity_and_blocks_new_round(setup, monkeypatch):
    api, pid, service, calls = setup
    card = prepare(api, pid)
    original = compshare.H3Client.request
    def timeout(self, method, *args, **kwargs):
        if method == 'POST':
            raise CanvasError('网络超时')
        return original(self, method, *args, **kwargs)
    monkeypatch.setattr(compshare.H3Client, 'request', timeout)
    with pytest.raises(CanvasError):
        api.approve_submission(pid, card['id'], 55)
    task = service.projects.get(pid)['generation']['tasks']['one']
    assert task['submit_id'] and task['status'] == 'submission_unknown'
    with pytest.raises(CanvasError):
        api.execute(pid, 'new_round')
    with pytest.raises(CanvasError):
        api.approve_submission(pid, card['id'], 55)


@pytest.mark.parametrize('state, expected', [('moderating','running'), ('queued','running'), ('running','running'), ('blocked','failed'), ('cancelled','canceled')])
def test_terminal_states(setup, monkeypatch, state, expected):
    api, pid, service, _ = setup
    card = prepare(api, pid); api.approve_submission(pid, card['id'],55)
    monkeypatch.setattr(compshare.H3Client, 'request', lambda *a, **kw: {'task': {'id':'task-1','status':state}})
    api.execute(pid, 'refresh_results')
    assert service.projects.get(pid)['generation']['tasks']['one']['status'] == expected


def test_rejects_720p_and_out_of_range(setup):
    api, pid, _, _ = setup
    for duration, resolution in [(31,'480P'), (10,'720p')]:
        with pytest.raises(CanvasError):
            api.flow.configure(pid, 'MiniMax-H3', duration, '', '4:3', resolution)


def test_voice_is_bound_and_never_becomes_first_frame(setup, monkeypatch):
    api, pid, service, calls = setup
    p = service.projects.get(pid)
    voice = Path(p['directory']) / 'voice.wav'; voice.write_bytes(b'audio')
    p['groups'][0]['generation_bindings'] = {'male_side': 'b', 'voice_path': str(voice)}
    p['groups'][0]['prompt'] = '@男主参考@音色说话，@聆听者倾听。'
    service.projects.save(p)
    monkeypatch.setattr(compshare, 'probe', lambda *a: {'format': {'duration': 5}})
    card = prepare(api,pid); api.approve_submission(pid,card['id'],55)
    body = next(c[2] for c in calls if c[0]=='POST')
    assert '<Picture 2>参考<Audio 1>说话，<Picture 1>倾听' in body['content'][0]['text']
    assert body['content'][3]['role']=='reference_audio'
    assert body['content'][3]['audio_url']['url'].startswith('data:audio/')


def test_client_missing_key_does_not_connect(monkeypatch, tmp_path):
    monkeypatch.delenv('COMPSHARE_H3_API_KEY', raising=False)
    monkeypatch.setattr(compshare, 'build_opener', lambda *a: pytest.fail('must not connect'))
    with pytest.raises(CanvasError, match='sk-ml-'):
        compshare.H3Client({'compshare_h3_key_file': str(tmp_path/'absent')}).request('GET','query/point_usage_summary')


def test_channel_choice_required_for_each_round(setup):
    api, pid, service, _ = setup
    api.execute(pid, 'new_round')
    request = api.execute(pid, 'request_generation_channel')
    assert set(request['options']) == {'dreamina','compshare'}
    assert request['status'] == 'awaiting_channel_selection'
    with pytest.raises(CanvasError, match='尚未选择'):
        api.execute(pid, 'prepare_batch', {'group_ids':['one']})
    api.flow.select_channel(pid,'compshare')
    cfg=service.projects.get(pid)['generation']
    assert cfg['duration']==15 and cfg['resolution']=='768P'
    assert 'channel_request' not in cfg
    with pytest.raises(CanvasError, match='渠道不符'):
        api.execute(pid, 'configure_generation', dict(model='seedance_2.0_fast_vip',duration=15,ratio='4:3',resolution='720p',prompt_mode='combined',common_prompt=''))
    api.execute(pid,'prepare_batch',{'group_ids':['one']})
    with pytest.raises(CanvasError, match='本轮已有'):
        api.flow.select_channel(pid,'dreamina')


def test_h3_card_labels_and_balance_refresh_blocks_spend(setup, monkeypatch):
    api, pid, service, calls=setup
    card=prepare(api,pid)
    assert card['provider']=='compshare' and card['provider_label']=='优云智算 H3'
    assert card['quote_kind']=='estimate' and card['resolution']=='480P'
    monkeypatch.setattr(compshare.H3Client,'request',lambda *a, **kw:{'available_points':1.25})
    with pytest.raises(CanvasError, match='积分不足'):
        api.approve_submission(pid,card['id'],55)
    pending=service.projects.get(pid)['generation']['pending_approval']
    assert pending['sufficient'] is False and pending['available_credits']==1.25
    assert not any(c[0]=='POST' for c in calls)


def test_download_checks_media_before_completion(setup, monkeypatch):
    import io
    api, pid, service, _ = setup
    card = prepare(api,pid); api.approve_submission(pid,card['id'],55)
    api.execute(pid,'refresh_results')
    monkeypatch.setattr(compshare, 'urlopen', lambda *a, **kw: io.BytesIO(b'video'))
    monkeypatch.setattr(compshare, 'probe', lambda *a: {'format':{'duration':10},'streams':[{'codec_type':'video','width':640,'height':480}]})
    with pytest.raises(CanvasError, match='音轨'):
        api.execute(pid, 'download_results')
    assert service.projects.get(pid)['generation']['tasks']['one']['status'] == 'completed'
    monkeypatch.setattr(compshare, 'probe', lambda *a: {'format':{'duration':10},'streams':[{'codec_type':'video','width':640,'height':480},{'codec_type':'audio'}]})
    api.execute(pid, 'download_results')
    task=service.projects.get(pid)['generation']['tasks']['one']
    assert task['status']=='downloaded' and Path(task['output']).is_file()


def test_one_query_failure_preserves_other_tasks_progress(setup, monkeypatch):
    from dialogue_tool.h3_errors import H3Error
    api, pid, service, _ = setup
    p = service.projects.get(pid)
    p['generation']['tasks'] = {
        'one': {'submit_id': 'first', 'task_id': 'task-1', 'status': 'running'},
        'two': {'submit_id': 'second', 'task_id': 'task-2', 'status': 'running'},
    }
    service.projects.save(p)

    def request(client, method, path, *args):
        if path.endswith('task-1'):
            raise H3Error('timeout', '等待平台回复超时', retryable=True)
        return {'task': {'id': 'task-2', 'status': 'succeeded', 'resolution': '768P'}}

    monkeypatch.setattr(compshare.H3Client, 'request', request)
    with pytest.raises(H3Error):
        api.execute(pid, 'refresh_results')
    tasks = service.projects.get(pid)['generation']['tasks']
    assert tasks['one']['status'] == 'running'
    assert tasks['one']['query_error']['code'] == 'timeout'
    assert tasks['two']['status'] == 'completed'


@pytest.mark.parametrize('male_side,speaker_token,listener_token', [
    ('a', '<Picture 1>', '<Picture 2>'),
    ('b', '<Picture 2>', '<Picture 1>'),
])
def test_character_identity_survives_preview_approval_and_submission(setup, monkeypatch, male_side, speaker_token, listener_token):
    api, pid, service, calls = setup
    p = service.projects.get(pid); p['generation']['preserve_prompt'] = False; service.projects.save(p)
    original_prompt = '@男主说出台词：“如果没有天赋，那就不断的重复。”\n@聆听者安静听，最后2秒切换。'
    update = {'group_id': 'one', 'male_side': male_side, 'prompt': original_prompt,
              'person_' + male_side: '画面中央穿黑色外套、戴细项链的男子',
              'person_' + ('b' if male_side == 'a' else 'a'): '绿色制服、肩章带星的男子'}
    api.execute(pid, 'update_groups', {'expected_revision': api.summary(pid)['revision'], 'updates': [update]})
    project = service.projects.get(pid)
    voice = Path(project['directory']) / 'voice.wav'
    voice.write_bytes(b'audio')
    project['groups'][0]['generation_bindings']['voice_path'] = str(voice)
    service.projects.save(project)
    monkeypatch.setattr(compshare, 'probe', lambda *args: {'format': {'duration': 5.2}})

    sheet = api.execute(pid, 'execution_sheet', {'group_id': 'one'})
    assert original_prompt in sheet['prompt']
    assert f'{speaker_token}参考图中的【画面中央穿黑色外套、戴细项链的男子】是本片主角和唯一说话人' in sheet['bound_prompt']
    assert f'{listener_token}参考图中的【绿色制服、肩章带星的男子】是聆听者' in sheet['bound_prompt']
    assert f'<Audio 1>仅对应{speaker_token}的声音特征' in sheet['bound_prompt']
    assert '聆听者全程嘴唇闭合' in sheet['bound_prompt']
    card = prepare(api, pid)
    assert card['character_previews'][0]['bound_prompt'] == sheet['bound_prompt']
    api.approve_submission(pid, card['id'], card['ceiling'])
    sent = next(call[2] for call in calls if call[0] == 'POST')
    snapshot = service.projects.get(pid)['generation']['tasks']['one']['request_snapshot']
    assert sent['content'][0]['text'] == sheet['bound_prompt'] == snapshot['bound_prompt']
    assert snapshot['role_bindings'] == sheet['role_bindings']
    assert service.projects.get(pid)['groups'][0]['prompt'] == original_prompt


def test_shared_prompt_uses_each_groups_visual_descriptions(setup):
    api, pid, service, _ = setup
    project = service.projects.get(pid)
    project['groups'][0].update(person_a='黑外套男子', person_b='绿制服男子')
    project['groups'].append({**project['groups'][0], 'id': 'two',
                              'person_a': '棕西装男子', 'person_b': '紫衣短发女子'})
    service.projects.save(project)
    api.execute(pid, 'configure_generation', dict(model='MiniMax-H3', duration=10,
        ratio='4:3', resolution='480P', prompt_mode='shared', common_prompt='@男主讲述，@聆听者安静听。', preserve_prompt=False))
    result = api.execute(pid, 'generation_preflight', {'group_ids': ['one', 'two']})
    assert result['ready']
    first, second = result['execution_sheets']
    assert '黑外套男子' in first['bound_prompt'] and '紫衣短发女子' not in first['bound_prompt']
    assert '棕西装男子' in second['bound_prompt'] and '绿制服男子' not in second['bound_prompt']


def test_default_prompt_does_not_add_unrequested_role_constraints(setup):
    api, pid, _, _ = setup
    check = api.execute(pid, 'generation_preflight', {'group_ids': ['one']})
    assert check['ready']
    assert '唯一说话人' not in check['execution_sheets'][0]['bound_prompt']
    assert any('外貌与位置' in warning['message'] for warning in check['warnings'])


def test_invalid_final_binding_or_changed_file_is_rejected_before_submit(setup):
    api, pid, _, calls = setup
    sheet = api.execute(pid, 'execution_sheet', {'group_id': 'one'})
    with pytest.raises(CanvasError, match='最终提示词'):
        api.flow.h3.body({**sheet, 'bound_prompt': 'wrong'})
    with pytest.raises(CanvasError, match='引用重复'):
        compshare.h3_reference_bindings([sheet['references'][0], sheet['references'][0]])
    Path(sheet['references'][0]['path']).write_bytes(b'changed')
    with pytest.raises(CanvasError, match='素材在核对后发生变化'):
        api.flow.h3.body(sheet)
    assert not any(call[0] == 'POST' for call in calls)


def test_literal_image_numbers_cannot_bypass_group_bindings(setup):
    api, pid, service, _ = setup
    project = service.projects.get(pid)
    project['groups'][0]['prompt'] = '<Picture 2>说话，@聆听者安静听。'
    service.projects.save(project)
    check = api.execute(pid, 'generation_preflight', {'group_ids': ['one']})
    assert not check['ready']
    assert '编号由本组绑定自动生成' in check['errors'][0]['message']


def use_first_baseline(setup, monkeypatch):
    api, pid, service, calls = setup
    monkeypatch.setattr(compshare, 'probe', lambda *a: {'format': {'duration': 8.829375}})
    monkeypatch.setattr(api.flow.h3, 'credit_balance', lambda ceiling:
        {'available_credits': 1000, 'sufficient': True, 'shortfall': 0, 'checked_at': 1})
    api.execute(pid, 'apply_h3_baseline')
    return api, pid, service, calls


def review_first_inputs(api, pid):
    sheet = api.execute(pid, 'execution_sheet', {'group_id': 'one'})
    for ref in sheet['references']:
        if ref['type'] == 'image':
            api.execute(pid, 'record_reference_review', dict(group_id='one', side=ref['side'], sha256=ref['sha256'], clean=True))
    return api.execute(pid, 'execution_sheet', {'group_id': 'one'})


def test_first_baseline_golden_prompt_audio_and_exact_paid_snapshot(setup, monkeypatch):
    api, pid, service, calls = use_first_baseline(setup, monkeypatch)
    # Archived single-task rounds retain their original full-prompt behavior.
    p = service.projects.get(pid)
    p['generation'].pop('dialogue_policy')
    service.projects.save(p)
    check = api.execute(pid, 'generation_preflight', {'group_ids': ['one']})
    assert not check['ready'] and len(check['errors']) == 2
    sheet = review_first_inputs(api, pid)
    expected = (Path(__file__).parent / 'fixtures/h3-first-submitted.txt').read_text(encoding='utf-8').strip()
    assert sheet['bound_prompt'] == expected
    assert [r['token'] for r in sheet['role_bindings']] == ['<Picture 1>', '<Picture 2>', '<Audio 1>']
    assert sheet['references'][-1]['sha256'] == '5c040917a15eeaef8c433c858414da4297ae419656f66d74d9b5836ad9fc9bc5'
    assert (sheet['duration'], sheet['resolution'], sheet['ratio'], sheet['use_context_ir']) == (15, '768P', '16:9', True)
    assert 'speech_plan' not in sheet and '身份为张世豪' not in sheet['bound_prompt']
    card = prepare(api, pid)
    assert card['ceiling'] == 155
    assert not any(c[0] == 'POST' for c in calls)
    api.approve_submission(pid, card['id'], card['ceiling'])
    body = next(c[2] for c in calls if c[0] == 'POST')
    snapshot = service.projects.get(pid)['generation']['tasks']['one']['request_snapshot']
    assert snapshot['baseline_id'] == 'h3-first-v1'
    assert body['content'][0]['text'] == card['character_previews'][0]['bound_prompt'] == snapshot['bound_prompt'] == expected
    assert snapshot['role_bindings'] == sheet['role_bindings']
    assert not service.projects.get(pid)['groups'][0].get('generation_bindings', {}).get('voice_path')


@pytest.mark.parametrize('change', [dict(common_prompt='改掉台词'), dict(duration=30),
    dict(use_context_ir=False), dict(preserve_prompt=False), dict(audio_first=True), dict(prompt_mode='combined')])
def test_baseline_rejects_drift_before_saving(setup, monkeypatch, change):
    api, pid, service, calls = use_first_baseline(setup, monkeypatch)
    before = service.projects.get(pid)
    cfg = before['generation']
    args = {k: cfg[k] for k in ('model','duration','ratio','resolution','prompt_mode')}
    args.update(common_prompt=cfg['global_prompt'])
    args.update(change)
    with pytest.raises(CanvasError, match='基准已锁定'):
        api.execute(pid, 'configure_generation', args)
    assert service.projects.get(pid) == before
    assert not calls


def test_baseline_rejects_changed_reference_and_old_review(setup, monkeypatch):
    api, pid, service, calls = use_first_baseline(setup, monkeypatch)
    sheet = review_first_inputs(api, pid)
    first = sheet['references'][0]
    Image.new('RGB', (640, 480), 'red').save(first['path'])
    with pytest.raises(ValueError, match='图片已变化'):
        api.execute(pid, 'record_reference_review', dict(group_id='one', side='a', sha256=first['sha256'], clean=True))
    check = api.execute(pid, 'generation_preflight', {'group_ids': ['one']})
    assert not check['ready'] and any('人物A' in e['message'] for e in check['errors'])
    cfg = service.projects.get(pid)['generation']
    Path(cfg['baseline_voice_path']).write_bytes(b'different voice')
    with pytest.raises(CanvasError, match='参考音频偏离'):
        api.execute(pid, 'execution_sheet', {'group_id': 'one'})
    assert not calls


def test_baseline_preserves_existing_tasks_and_restores_on_next_round(setup, monkeypatch):
    import copy
    api, pid, service, _ = use_first_baseline(setup, monkeypatch)
    review_first_inputs(api, pid)
    prepare(api, pid)
    before = copy.deepcopy(service.projects.get(pid))
    with pytest.raises(CanvasError, match='已有草稿或任务'):
        api.execute(pid, 'apply_h3_baseline')
    assert service.projects.get(pid) == before
    api.execute(pid, 'new_round')
    after = service.projects.get(pid)
    assert after['generation_history'][-1] == before['generation']
    assert after['generation']['baseline_id'] == 'h3-first-v1'
    assert after['generation']['preserve_prompt'] and after['generation']['use_context_ir']
    assert api.execute(pid, 'generation_preflight', {'group_ids': ['one']})['ready']


def test_baseline_quote_alone_exposes_payment_card_without_submitting(setup, monkeypatch):
    api, pid, service, calls = use_first_baseline(setup, monkeypatch)
    review_first_inputs(api, pid)
    api.execute(pid, 'prepare_batch', {'group_ids': ['one']})
    api.execute(pid, 'quote_batch')
    cfg = service.projects.get(pid)['generation']
    card = cfg['pending_approval']
    assert card['ceiling'] == 310 and card['group_ids'] == ['one__dialogue_part_1', 'one__dialogue_part_2']
    assert card['character_previews'][0]['bound_prompt'] == api.execute(pid, 'execution_sheet', {'group_id': 'one'})['bound_prompt']
    assert all(t['status'] == 'draft' and not t.get('submit_id') for t in cfg['tasks'].values())
    assert not any(c[0] == 'POST' for c in calls)


def test_default_two_parts_preserve_prompt_references_and_exact_paid_requests(setup, monkeypatch):
    from dialogue_tool.dialogue_segments import dialogue_span, template
    api, pid, service, calls = use_first_baseline(setup, monkeypatch)
    sheet = review_first_inputs(api, pid)
    original = service.projects.get(pid)['generation']['global_prompt']
    parts = sheet['parts']
    assert len(parts) == 2 and parts[0]['references'] == parts[1]['references']
    assert all(template(s['prompt']) == template(original) for s in parts)
    speech = lambda s: s[slice(*dialogue_span(s))]
    assert ''.join(speech(s['prompt']) for s in parts) == speech(original)
    assert all(s['use_context_ir'] is True and 'speech_plan' not in s for s in parts)
    card = prepare(api, pid)
    cfg = service.projects.get(pid)['generation']
    assert cfg['dialogue_plans']['one']['assembly']['order'] == card['group_ids']
    assert len(service.projects.get(pid)['groups']) == 1
    assert not any(c[0] == 'POST' for c in calls)

    def request(client, method, path, payload=None, identity=None):
        assert method == 'POST'
        saved = service.projects.get(pid)['generation']['tasks']
        task = next(t for t in saved.values() if t.get('submit_id') == identity)
        assert task['status'] == 'submission_unknown'
        assert task['request_snapshot']['bound_prompt'] == payload['content'][0]['text']
        assert task['request_snapshot']['segment'] == task['segment']
        calls.append((method, path, payload, identity))
        return {'task_id': 'paid-' + identity}
    monkeypatch.setattr(compshare.H3Client, 'request', request)
    api.approve_submission(pid, card['id'], card['ceiling'])
    assert [c[2]['content'][0]['text'] for c in calls] == [s['bound_prompt'] for s in card['character_previews']]
    with pytest.raises(ValueError):
        api.approve_submission(pid, card['id'], card['ceiling'])
    assert len(calls) == 2


def test_default_short_dialogue_and_next_round_keep_user_text(setup, monkeypatch):
    from dialogue_tool.dialogue_segments import dialogue_span
    api, pid, service, calls = use_first_baseline(setup, monkeypatch)
    cfg = service.projects.get(pid)['generation']
    a, b = dialogue_span(cfg['global_prompt'])
    prompt = cfg['global_prompt'][:a] + '如果没有天赋，那就不断地重复。' + cfg['global_prompt'][b:]
    api.execute(pid, 'configure_generation', {**{k: cfg[k] for k in ('model', 'duration', 'ratio', 'resolution', 'prompt_mode')}, 'common_prompt': prompt})
    sheet = review_first_inputs(api, pid)
    assert len(sheet['parts']) == 1 and sheet['prompt'] == prompt
    card = prepare(api, pid)
    assert card['group_ids'] == ['one'] and card['ceiling'] == 155
    assert not service.projects.get(pid)['generation']['dialogue_plans']
    api.execute(pid, 'new_round')
    after = service.projects.get(pid)
    assert after['generation']['global_prompt'] == prompt
    assert after['generation']['dialogue_policy'] == 'dialogue-two-v1'
    assert after['generation_history'][-1]['pending_approval'] == card
    assert not calls


@pytest.mark.parametrize('change', ['prompt', 'selection', 'image'])
def test_default_changed_inputs_block_both_parts_before_payment(setup, monkeypatch, change):
    api, pid, service, calls = use_first_baseline(setup, monkeypatch)
    sheet = review_first_inputs(api, pid)
    card = prepare(api, pid)
    p = service.projects.get(pid)
    if change == 'prompt':
        p['generation']['global_prompt'] = p['generation']['global_prompt'].replace('如果没有天赋', '假如没有天赋')
    elif change == 'selection':
        p['groups'][0]['selected'] = False
    else:
        Image.new('RGB', (640, 480), 'red').save(sheet['references'][0]['path'])
    service.projects.save(p)
    with pytest.raises(CanvasError):
        api.approve_submission(pid, card['id'], card['ceiling'])
    assert not calls


def test_default_multiple_groups_plan_independently_and_reject_partial_prepare(setup, monkeypatch):
    api, pid, service, calls = use_first_baseline(setup, monkeypatch)
    review_first_inputs(api, pid)
    p = service.projects.get(pid)
    p['groups'].append({**p['groups'][0], 'id': 'two'})
    service.projects.save(p)
    with pytest.raises(CanvasError, match='原图片组'):
        api.execute(pid, 'prepare_batch', {'group_ids': ['one__dialogue_part_2']})
    api.execute(pid, 'prepare_batch', {'group_ids': ['one', 'two']})
    api.execute(pid, 'quote_batch')
    cfg = service.projects.get(pid)['generation']
    assert cfg['pending_approval']['ceiling'] == 620
    assert set(cfg['dialogue_plans']) == {'one', 'two'}
    for gid, plan in cfg['dialogue_plans'].items():
        assert plan['assembly']['order'] == [gid + '__dialogue_part_1', gid + '__dialogue_part_2']
    assert not calls


def test_switching_to_dreamina_clears_h3_dialogue_policy(setup, monkeypatch):
    api, pid, service, calls = use_first_baseline(setup, monkeypatch)
    api.flow.select_channel(pid, 'dreamina')
    assert 'dialogue_policy' not in service.projects.get(pid)['generation']
    api.execute(pid, 'configure_generation', dict(model='seedance_2.0_fast_vip', duration=15,
        ratio='4:3', resolution='720p', prompt_mode='shared', common_prompt='即梦提示词'))
    assert not calls


def test_segmented_batch_binds_only_each_parts_dialogue(setup):
    api, pid, service, calls = setup
    p = service.projects.get(pid)
    p['groups'].append({**p['groups'][0], 'id': 'two'})
    p['generation'].update(prompt_mode='shared', global_prompt='@男主说话，@聆听者安静听。',
                           baseline_id='custom', preserve_prompt=True)
    service.projects.save(p)
    refs = api.execute(pid, 'execution_sheet', {'group_id': 'one'})['references']
    p['generation']['segment_plan'] = {
        'source_group_id': 'original-four', 'reference_sha256': {r['role']: r['sha256'] for r in refs},
        'parts': [{'group_id': gid, 'index': i, 'prompt': '0–2秒台词：“' + words + '”',
                   'timeline': [{'start': 0, 'end': 2, 'text': words}]}
                  for i, (gid, words) in enumerate([('one', '如果没有天赋'), ('two', '那就不断的重复')], 1)]}
    service.projects.save(p)
    check = api.execute(pid, 'generation_preflight', {'group_ids': ['one','two']})
    assert check['ready']
    first, second = check['execution_sheets']
    assert '如果没有天赋' in first['bound_prompt'] and '不断的重复' not in first['bound_prompt']
    assert '那就不断的重复' in second['bound_prompt'] and '没有天赋' not in second['bound_prompt']
    assert first['references'] == second['references']
    assert first['segment']['index'] == 1 and second['segment']['index'] == 2
    # Reviewed full prompts must reach both the quote and provider unchanged,
    # apart from resolving reference tokens. Legacy rounds keep their format.
    p['generation']['segment_plan']['prompt_format'] = 'verbatim'
    approved = ['@男主说：“如果没有天赋”。@聆听者听。不要带字幕。',
                '@男主说：“那就不断的重复”。@聆听者听。不要带字幕。']
    for part, prompt in zip(p['generation']['segment_plan']['parts'], approved):
        part['prompt'] = prompt
    service.projects.save(p)
    check = api.execute(pid, 'generation_preflight', {'group_ids': ['one', 'two']})
    assert check['ready']
    for sheet, prompt in zip(check['execution_sheets'], approved):
        assert sheet['prompt'] == prompt
        bound = prompt.replace('@男主', '<Picture 1>').replace('@聆听者', '<Picture 2>').replace('@音色', '<Audio 1>')
        assert sheet['bound_prompt'] == bound
        assert api.flow.h3.body(sheet)['content'][0]['text'] == bound
    p['generation']['segment_plan']['reference_sha256']['男主'] = 'changed'
    service.projects.save(p)
    assert not api.execute(pid, 'generation_preflight', {'group_ids': ['one','two']})['ready']
    assert not calls
