import pytest
import base64
import io
from PIL import Image

from dialogue_tool.desktop import DesktopAPI
from dialogue_tool.service import Service


def make_api(tmp_path):
    service = Service({'data': str(tmp_path / 'data')})
    source = tmp_path / 'sample.mp4'
    source.write_bytes(b'sample')
    first = service.projects.create(source)
    second = service.projects.create(source)
    second['groups'] = [{'id': 'group', 'frame_a': 1, 'frame_b': 2, 'prompt': 'keep'}]
    service.projects.save(second)
    api = DesktopAPI(service)
    api.current = second['id']
    return api, service, first, second


def test_save_rejects_draft_from_another_project(tmp_path):
    api, service, first, second = make_api(tmp_path)
    with pytest.raises(ValueError, match='项目'):
        api.save_groups([], first['id'])
    assert service.projects.get(second['id'])['groups'][0]['prompt'] == 'keep'


def test_restart_restores_selected_project_and_conversation(tmp_path):
    api, service, first, second = make_api(tmp_path)
    api.select_project(first['id'])
    api._agent._message(first['id'], 'user', '之前的提示词')
    restarted = DesktopAPI(service)
    assert restarted.current == first['id']
    assert restarted.agent_state(first['id'])['messages'][0]['text'] == '之前的提示词'


def test_jimeng_authorization_publishes_link_and_waits(tmp_path, monkeypatch):
    api, _, _, _ = make_api(tmp_path)
    challenge = {'deviceCode': 'private-reference', 'userCode': 'ABCD',
                 'verificationUri': 'https://jimeng.jianying.com/authorize'}
    monkeypatch.setattr(api._generation, 'login', lambda: {'loggedIn': False, 'challenge': challenge})
    opened = []
    monkeypatch.setattr(api, 'generation_open_auth', lambda: opened.append(api.generation_auth_state()))
    def wait(code):
        assert code == 'private-reference'
        state = api.generation_auth_state()
        assert state['busy'] and state['verification_url'] == challenge['verificationUri']
        assert 'deviceCode' not in str(state)
        return {'loggedIn': True, 'message': '即梦已连接'}
    monkeypatch.setattr(api._generation, 'wait_login', wait)
    api._login_jimeng()
    assert len(opened) == 1
    assert api.generation_auth_state() == {'loggedIn': True, 'message': '即梦已连接', 'busy': False}


def test_agent_running_blocks_manual_edits_but_state_remains_readable(tmp_path):
    api, service, _, p = make_api(tmp_path)
    api._agent.active_project = p['id']
    assert api.get_state()['busy']
    with pytest.raises(ValueError, match='尚未结束'):
        api.save_groups([], p['id'])
    assert service.projects.get(p['id'])['groups']


def test_background_generation_downloads_to_existing_batch_without_resubmitting(tmp_path, monkeypatch):
    api, service, _, p = make_api(tmp_path)
    p['generation'] = {'tasks': {'group': {'submit_id': 'original', 'status': 'running'}}}
    service.projects.save(p)
    monkeypatch.setattr(api._monitor_stop, 'wait', lambda seconds: False)
    calls = []
    monkeypatch.setattr(api._generation, 'refresh', lambda pid: calls.append(('refresh', pid)))
    def download(pid):
        calls.append(('download', pid))
        result = service.projects.get(pid)
        result['generation']['tasks']['group'].update(status='downloaded', output='existing-output.mp4')
        service.projects.save(result)
        return result
    monkeypatch.setattr(api._generation, 'download', download)
    monkeypatch.setattr(api._generation, 'run_approved', lambda *args: pytest.fail('must not resubmit'))
    api._monitor_generation(p['id'], {'group': 'original'})
    assert calls == [('refresh', p['id']), ('download', p['id'])]
    assert '已下载回图片组' in api._agent.state(p['id'])['messages'][-1]['text']


def test_repair_rejects_mask_for_a_replaced_frame(tmp_path):
    api, _, _, second = make_api(tmp_path)
    with pytest.raises(ValueError, match='截图'):
        api.start_repair('group', 'a', [0, 0, 8, 8], False,
                         expected_frame=99, project_id=second['id'])
    assert api.busy is False


def test_external_repair_refreshes_same_groups_and_clears_stale_task_error(tmp_path):
    api, service, _, second = make_api(tmp_path)
    api.error = '先前的任务连接失败'
    before = api.get_state()
    assert api.get_state()['version'] == before['version']
    updated = service.projects.get(second['id'])
    updated['groups'][0]['repaired_a'] = 'new-repaired.png'
    updated['run_status'] = {'progress': 1, 'message': '去水印完成'}
    service.projects.save(updated)
    after = api.get_state()
    assert after['version'] != before['version']
    assert len(after['project']['groups']) == len(before['project']['groups'])
    assert after['project']['phase'] == before['project']['phase']
    assert after['error'] is None
    assert after['message'] == '去水印完成'
    assert api.get_state()['version'] == after['version']


def test_group_preview_uses_attached_repaired_image_by_default(tmp_path):
    api, service, _, second = make_api(tmp_path)
    repaired = tmp_path / 'data' / second['id'] / 'clean.png'
    Image.new('RGB', (24, 16), 'blue').save(repaired)
    second['groups'][0]['repaired_a'] = str(repaired)
    service.projects.save(second)
    response = api.get_image(1, 'group', 'a')
    with Image.open(io.BytesIO(base64.b64decode(response['url'].split(',', 1)[1]))) as image:
        red, green, blue = image.getpixel((12, 8))
        assert red < 10 and green < 10 and blue > 245


def test_partial_submit_starts_monitor_for_original_identity(tmp_path, monkeypatch):
    from dialogue_tool import desktop
    api, service, _, p = make_api(tmp_path)
    p['generation'] = {'tasks': {'group': {'submit_id': 'original-id', 'status': 'submission_unknown'}}}
    service.projects.save(p)
    def fail(*args):
        raise ValueError('partial failure')
    monkeypatch.setattr(api._agent.workflow, 'approve_submission', fail)
    monkeypatch.setattr(api, '_start', lambda fn: fn())
    started = []
    class Thread:
        def __init__(self, target, args, daemon):
            self.args = args
        def start(self):
            started.append(self.args)
    monkeypatch.setattr(desktop.threading, 'Thread', Thread)
    with pytest.raises(ValueError, match='partial failure'):
        api.agent_approve('card', 360, p['id'])
    assert started == [(p['id'], {'group': 'original-id'})]


def test_h3_monitor_recovers_from_timeout_and_keeps_original_submission(tmp_path, monkeypatch):
    from dialogue_tool.h3_errors import H3Error
    api, service, _, p = make_api(tmp_path)
    p['generation'] = {'tasks': {'group': {'submit_id': 'original', 'task_id': 'task-1', 'status': 'running'}}}
    service.projects.save(p)
    waits, calls = [], []
    monkeypatch.setattr(api._monitor_stop, 'wait', lambda seconds: waits.append(seconds) or False)

    def refresh(pid):
        calls.append(pid)
        if len(calls) == 1:
            raise H3Error('timeout', '等待优云智算回复超时', retryable=True)

    def download(pid):
        result = service.projects.get(pid)
        if len(calls) > 1:
            result['generation']['tasks']['group']['status'] = 'downloaded'
            service.projects.save(result)
        return result

    monkeypatch.setattr(api._generation, 'refresh', refresh)
    monkeypatch.setattr(api._generation, 'download', download)
    monkeypatch.setattr(api._generation, 'run_approved', lambda *args: pytest.fail('must not resubmit'))
    api._monitor_generation(p['id'], {'group': 'original'})
    saved = service.projects.get(p['id'])['generation']
    assert len(calls) == 2 and waits == [10, 10]
    assert saved['tasks']['group']['submit_id'] == 'original'
    assert saved['monitor']['status'] == 'finished'
    messages = [m['text'] for m in api._agent.state(p['id'])['messages']]
    assert any('超时' in m and '自动重试' in m for m in messages)
    assert any('连接已恢复' in m for m in messages)


def test_h3_monitor_stops_on_invalid_key_and_bounds_transient_retries(tmp_path, monkeypatch):
    from dialogue_tool.h3_errors import H3Error
    api, service, _, p = make_api(tmp_path)
    p['generation'] = {'tasks': {'group': {'submit_id': 'original', 'status': 'running'}}}
    service.projects.save(p)
    waits = []
    monkeypatch.setattr(api._monitor_stop, 'wait', lambda seconds: waits.append(seconds) or False)
    monkeypatch.setattr(api._generation, 'download', lambda pid: service.projects.get(pid))

    def invalid_key(pid):
        raise H3Error('invalid_key', '密钥已失效，请更新密钥')

    monkeypatch.setattr(api._generation, 'refresh', invalid_key)
    api._monitor_generation(p['id'], {'group': 'original'})
    assert waits == [10]
    assert '密钥已失效' in service.projects.get(p['id'])['generation']['monitor']['message']

    def offline(pid):
        raise H3Error('timeout', '等待平台回复超时', retryable=True)

    waits.clear()
    monkeypatch.setattr(api._generation, 'refresh', offline)
    api._monitor_generation(p['id'], {'group': 'original'})
    assert waits == [10, 10, 20, 40, 60]
    assert service.projects.get(p['id'])['generation']['monitor']['status'] == 'paused'


def test_h3_connection_checks_candidate_before_replacing_key(tmp_path, monkeypatch):
    from dialogue_tool.compshare import H3Client
    from dialogue_tool.h3_errors import H3Error
    monkeypatch.delenv('COMPSHARE_H3_API_KEY', raising=False)
    api, service, _, _ = make_api(tmp_path)
    keyfile = tmp_path / 'existing.key'
    keyfile.write_text('sk-ml-original', encoding='utf-8')
    service.config['compshare_h3_key_file'] = str(keyfile)

    def request(client, method, path, *args):
        assert method == 'GET' and path == 'query/point_usage_summary'
        if client.key() == 'sk-ml-rejected':
            raise H3Error('invalid_key', '密钥已失效或填写错误')
        return {'available_points': 10512.74}

    monkeypatch.setattr(H3Client, 'request', request)
    failed = api.generation_h3_connect('sk-ml-rejected')
    assert failed['connected'] is False
    assert keyfile.read_text() == 'sk-ml-original'
    connected = api.generation_h3_connect('sk-ml-accepted')
    assert keyfile.read_text() == 'sk-ml-accepted'
    assert connected['connected'] and connected['available_credits'] == 10512.74
    assert '10512.74' in connected['message']
    assert 'sk-ml-accepted' not in str(connected)


@pytest.mark.parametrize('pending_assembly', [False, True])
def test_restart_resumes_existing_generation_once(tmp_path, monkeypatch, pending_assembly):
    from dialogue_tool import desktop
    api, service, _, p = make_api(tmp_path)
    p['generation'] = {'tasks': {'group': {'submit_id': 'original', 'task_id': 'known-task', 'status': 'running'}}}
    if pending_assembly:
        p['generation']['tasks']['group']['status'] = 'downloaded'
        p['generation']['dialogue_plans'] = {'original': {'parts': [{'group_id': 'group'}],
                                                        'assembly': {'order': ['group']}}}
    service.projects.save(p)
    started = []

    class Thread:
        def __init__(self, target, args, daemon):
            self.args = args

        def start(self):
            started.append(self.args)

    monkeypatch.setattr(desktop.threading, 'Thread', Thread)
    monkeypatch.setattr(api._generation, 'run_approved', lambda *args: pytest.fail('must not submit'))
    api.resume_generation_monitors()
    api.resume_generation_monitors()
    assert started == [(p['id'], {'group': 'original'})]


def test_project_navigation_keeps_active_agent_context(tmp_path, monkeypatch):
    api, service, first, second = make_api(tmp_path)
    api._agent._message(second['id'], 'user', '原项目的任务')
    api._agent.active_project = second['id']
    api.select_project(first['id'])
    state = api.agent_state(first['id'])
    assert state['busy']
    assert state['active_project_id'] == second['id']
    assert state['messages'][0]['text'] == '原项目的任务'
    assert api.get_state()['navigation_busy'] is False
    assert api._agent.active_project == second['id']
    with pytest.raises(ValueError, match='尚未结束'):
        api.agent_send('新项目任务', 'model', 'low', [], first['id'])
    stopped = []
    monkeypatch.setattr(api._agent, 'stop', lambda pid: stopped.append(pid))
    api.agent_stop(state['active_project_id'])
    assert stopped == [second['id']]
    api.busy = True
    with pytest.raises(ValueError, match='尚未结束'):
        api.select_project(second['id'])
