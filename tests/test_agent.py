import json
import queue
import time

import pytest

from dialogue_tool.agent import AgentManager
from dialogue_tool.service import Service
from dialogue_tool.workflow_api import WorkflowAPI, dynamic_tools


@pytest.fixture
def project(tmp_path):
    service = Service({'data': str(tmp_path / 'data'), 'codex': 'fake'})
    source = tmp_path / 'movie.mp4'; source.write_bytes(b'video')
    p = service.projects.create(source)
    p['groups'] = [{'id': gid, 'frame_a': 1, 'frame_b': 2, 'prompt': 'original', 'selected': True,
                    'needs_review': False} for gid in ('first', 'second')]
    service.projects.save(p)
    return service, p['id']


def test_tools_do_not_offer_payment_approval_or_shell():
    names = {t['name'] for t in dynamic_tools()}
    assert 'workbench_request_submission' in names
    assert not any('approve' in n or 'shell' in n or 'command' in n for n in names)


def test_quote_scope_uses_original_group_for_dialogue_parts(project, monkeypatch):
    service, pid = project
    agent = AgentManager(service)
    p = service.projects.get(pid)
    p['generation'] = {'tasks': {'first__dialogue_part_1': {'source_group_id': 'first'},
                               'first__dialogue_part_2': {'source_group_id': 'first'}}}
    service.projects.save(p)
    seen = []
    monkeypatch.setattr(agent.workflow, 'execute', lambda *args: seen.append(args) or {})
    params = {'tool': 'workbench_quote_batch', 'callId': 'quote', 'arguments': {}}
    assert agent._tool(pid, params, ['first'])['success']
    with pytest.raises(ValueError, match='范围外'):
        agent._tool(pid, params, ['second'])
    assert len(seen) == 1


def test_stale_revision_and_scope_protect_other_groups(project):
    service, pid = project
    agent = AgentManager(service)
    before = agent.workflow.summary(pid)
    with pytest.raises(ValueError, match='范围'):
        agent._tool(pid, {'tool': 'workbench_update_groups', 'callId': 'one', 'arguments': {
            'expected_revision': before['revision'], 'updates': [{'group_id': 'second', 'prompt': 'wrong'}]}}, ['first'])
    agent.workflow.execute(pid, 'update_groups', {'expected_revision': before['revision'],
        'updates': [{'group_id': 'first', 'prompt': 'new'}]})
    with pytest.raises(ValueError, match='变化'):
        agent.workflow.execute(pid, 'update_groups', {'expected_revision': before['revision'],
            'updates': [{'group_id': 'second', 'prompt': 'stale'}]})
    assert service.projects.get(pid)['groups'][1]['prompt'] == 'original'


def test_visual_descriptions_update_through_agent_api_and_protect_prepared_jobs(project):
    service, pid = project
    api = WorkflowAPI(service)
    result = api.execute(pid, 'update_groups', {'expected_revision': api.summary(pid)['revision'],
        'updates': [{'group_id': 'first', 'person_a': ' 中央黑衣男子 ', 'person_b': '绿制服男子'}]})
    assert result['groups'][0]['person_a'] == '中央黑衣男子'
    assert result['groups'][1]['person_a'] is None
    saved = service.projects.get(pid)
    saved['generation'] = {'tasks': {'first': {'status': 'draft'}}}
    service.projects.save(saved)
    with pytest.raises(ValueError, match='再修改人物描述'):
        api.execute(pid, 'update_groups', {'expected_revision': api.summary(pid)['revision'],
            'updates': [{'group_id': 'first', 'person_a': '其他人'}]})
    assert service.projects.get(pid)['groups'][0]['person_a'] == '中央黑衣男子'


def test_pending_confirmation_cannot_spend_after_batch_changed(project, monkeypatch):
    service, pid = project
    p = service.projects.get(pid)
    p['generation'] = {'model': 'model', 'duration': 15, 'tasks': {'first': {'status': 'draft'}},
                       'quote': {'ceiling': 90, 'group_ids': ['first'], 'node_ids': ['node1']}}
    service.projects.save(p)
    flow = WorkflowAPI(service)
    monkeypatch.setattr(flow.flow, 'preflight', lambda *args: {'errors': [], 'warnings': []})
    card = flow.execute(pid, 'request_submission')['pending_approval']
    p = service.projects.get(pid);p['generation']['quote']['node_ids'] = ['different'];service.projects.save(p)
    monkeypatch.setattr(flow.flow, 'run_approved', lambda *a: pytest.fail('must not spend'))
    with pytest.raises(ValueError, match='批次'):
        flow.approve_submission(pid, card['id'], 90)


class FakeClient:
    def __init__(self, *args):
        self.closed = False;self.events = queue.Queue();self.calls = [];self.responses = []

    def request(self, method, args=None):
        self.calls.append((method, args))
        if method == 'model/list':
            return {'data': [{'model': 'test-model', 'isDefault': True, 'defaultReasoningEffort': 'low',
                'supportedReasoningEfforts': [{'reasoningEffort': 'low'}, {'reasoningEffort': 'high'}]}]}
        if method in ('thread/start', 'thread/resume'):
            return {'thread': {'id': 'thread1'}}
        if method == 'turn/start':
            self.events.put({'id': 4, 'method': 'item/tool/call', 'params': {'threadId': 'thread1',
                'tool': 'workbench_read_project', 'arguments': {}, 'callId': 'read1', 'turnId': 'turn1'}})
            return {'turn': {'id': 'turn1'}}

    def respond(self, identifier, result):
        self.responses.append(result)
        for event in [
            {'method': 'item/agentMessage/delta', 'params': {'threadId': 'thread1', 'itemId': 'msg1', 'delta': '已读取'}},
            {'method': 'item/completed', 'params': {'threadId': 'thread1', 'item': {'id': 'msg1', 'type': 'agentMessage', 'text': '已读取两组。'}}},
            {'method': 'turn/completed', 'params': {'threadId': 'thread1', 'turn': {'id': 'turn1', 'status': 'completed'}}}]:
            self.events.put(event)

    def close(self): self.closed = True


def wait(agent):
    for _ in range(300):
        if not agent.active_project: return
        time.sleep(.01)
    pytest.fail('agent did not finish')


def test_live_contract_streams_persists_and_resumes_without_environment_tools(project):
    service, pid = project
    agent = AgentManager(service, FakeClient);agent.connect()
    with pytest.raises(ValueError, match='强度'):
        agent.send(pid, 'read', 'test-model', 'ultra', ['first'])
    agent.send(pid, 'read', 'test-model', 'high', ['first']);wait(agent)
    start = next(args for method, args in agent.client.calls if method == 'thread/start')
    assert start['environments'] == []
    assert start['approvalPolicy'] == 'never'
    assert start['dynamicTools'] == dynamic_tools()
    turn = next(args for method, args in agent.client.calls if method == 'turn/start')
    assert turn['effort'] == 'high'
    assert json.loads(agent.client.responses[0]['contentItems'][0]['text'])['project_id'] == pid
    assert agent.state(pid)['messages'][-1]['text'] == '已读取两组。'
    restored = AgentManager(service, FakeClient);restored.connect()
    restored.send(pid, 'continue', 'test-model', 'low', ['first']);wait(restored)
    assert any(method == 'thread/resume' for method, _ in restored.client.calls)
    assert restored.state(pid)['busy'] is False


def test_disconnect_is_not_replayed(project):
    class Disconnected(FakeClient):
        def request(self, method, args=None):
            result = super().request(method, args)
            if method == 'turn/start':
                self.events = queue.Queue()
                self.events.put({'method': 'connection/closed'})
            return result
    service, pid = project
    agent = AgentManager(service, Disconnected);agent.connect()
    agent.send(pid, 'read', 'test-model', 'low', ['first']);wait(agent)
    assert '断开' in agent.state(pid)['error']
    assert len([m for m, _ in agent.client.calls if m == 'turn/start']) == 1


def test_tool_update_restores_local_history_and_new_chat_starts_clean(project):
    service, pid = project
    previous = AgentManager(service, FakeClient)
    previous._message(pid, 'user', '共用提示词：如果没有天赋，那就不断地重复。')
    previous._session(pid).update(thread_id='old-thread', tool_signature='old-tools')
    previous._save(pid)
    restored = AgentManager(service, FakeClient)
    restored.connect()
    restored.send(pid, '继续用之前的提示词', 'test-model', 'low', ['first'])
    wait(restored)
    turn = next(args for method, args in restored.client.calls if method == 'turn/start')
    assert '如果没有天赋' in turn['input'][0]['text']
    history = restored.workflow.execute(pid, 'read_chat_history', {'limit': 1})
    assert history['messages'][0]['role'] == 'user'
    assert history['next_offset'] == 1
    restored.new_chat(pid)
    assert restored.workflow.execute(pid, 'read_chat_history')['messages'] == []
    restored.client.calls.clear()
    restored.send(pid, '新任务', 'test-model', 'low', ['first'])
    wait(restored)
    turn = next(args for method, args in restored.client.calls if method == 'turn/start')
    assert '如果没有天赋' not in turn['input'][0]['text']


def test_connection_state_tracks_transport(project):
    service, pid = project
    agent = AgentManager(service, FakeClient)
    assert not agent.state(pid)['connected']
    agent.connect()
    assert agent.state(pid)['connected']
    agent.client.close()
    assert not agent.state(pid)['connected']
    agent.connect()
    assert agent.state(pid)['connected']


def test_codex_path_recovers_after_desktop_update(tmp_path, monkeypatch):
    from dialogue_tool.settings import resolve_codex
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    monkeypatch.setattr('dialogue_tool.settings.shutil.which', lambda _: None)
    current = tmp_path / 'OpenAI' / 'Codex' / 'bin' / 'new' / 'codex.exe'
    current.parent.mkdir(parents=True)
    current.write_bytes(b'new')
    stale = current.parent.parent / 'old' / 'codex.exe'
    assert resolve_codex(str(stale)) == str(current)
    assert resolve_codex(str(current)) == str(current)


def test_chat_save_merges_other_manager_messages_without_reverting_updates(project):
    service, pid = project
    first = AgentManager(service)
    first._message(pid, 'assistant', 'partial', 'stream')
    second = AgentManager(service)
    second.state(pid)
    first._message(pid, 'assistant', 'completed', 'stream')
    second._message(pid, 'user', 'another window')
    first._message(pid, 'user', 'first window')
    saved = json.loads(first._path(pid).read_text(encoding='utf-8'))
    assert [m['text'] for m in saved['messages']] == ['completed', 'another window', 'first window']


def test_chat_save_retries_windows_replace_permission_error(project, monkeypatch):
    from pathlib import Path
    service, pid = project
    manager = AgentManager(service)
    replace = Path.replace
    temporary_paths = []

    def transient_replace(path, target):
        temporary_paths.append(path)
        if len(temporary_paths) < 3:
            raise PermissionError('sharing violation')
        return replace(path, target)

    monkeypatch.setattr(Path, 'replace', transient_replace)
    monkeypatch.setattr('dialogue_tool.agent.time.sleep', lambda _: None)
    manager._message(pid, 'user', 'saved')
    assert len(set(temporary_paths)) == 3
    assert all(not path.exists() for path in temporary_paths)
    assert json.loads(manager._path(pid).read_text(encoding='utf-8'))['messages'][0]['text'] == 'saved'


def test_stale_manager_cannot_restore_previous_chat_after_new_chat(project):
    service, pid = project
    first = AgentManager(service)
    first._message(pid, 'user', 'old')
    second = AgentManager(service)
    second.state(pid)
    first.new_chat(pid)
    with pytest.raises(ValueError, match='其他窗口重建'):
        second._message(pid, 'user', 'stale')
    saved = json.loads(first._path(pid).read_text(encoding='utf-8'))
    assert saved['messages'] == []
    assert len(list(first._path(pid).parent.glob('agent-chat-*.json'))) == 1


@pytest.mark.parametrize('status', ['submission_unknown', 'submitted', 'failed', 'downloaded'])
def test_old_confirmation_is_hidden_after_submission(project, status):
    service, pid = project
    p = service.projects.get(pid)
    p['generation'] = {'pending_approval': {'group_ids': ['first']},
                       'tasks': {'first': {'status': status, 'submit_id': 'original-id'}}}
    service.projects.save(p)
    assert AgentManager(service).state(pid)['pending_approval'] is None


def test_shared_chat_resumes_across_projects_and_keeps_tool_scope(project):
    service, pid = project
    other = service.projects.create(service.projects.root.parent / 'movie.mp4')
    agent = AgentManager(service, FakeClient)
    agent.connect()
    agent.send(pid, '已确认提示词与音色', 'test-model', 'low', ['first'])
    wait(agent)
    agent.send(other['id'], '沿用约定，读取新项目', 'test-model', 'low', [])
    wait(agent)
    assert sum(method == 'thread/start' for method, _ in agent.client.calls) == 1
    assert sum(method == 'thread/resume' for method, _ in agent.client.calls) == 1
    response = json.loads(agent.client.responses[-1]['contentItems'][0]['text'])
    assert response['project_id'] == other['id']
    assert '已确认提示词与音色' in str(agent.state(other['id'])['messages'])
    history = agent.workflow.execute(other['id'], 'read_chat_history')['messages']
    assert history[0]['project_id'] == pid
    assert next(m for m in history if m['text'] == '沿用约定，读取新项目')['project_id'] == other['id']
    with pytest.raises(ValueError, match='范围'):
        agent._tool(other['id'], {'tool': 'workbench_update_groups', 'callId': 'stale',
                    'arguments': {'updates': [{'group_id': 'first', 'prompt': 'wrong'}]}}, [])
    restarted = AgentManager(service)
    assert restarted.state(pid)['messages'] == agent.state(other['id'])['messages']
    restarted.new_chat(other['id'])
    assert restarted.state(pid)['messages'] == []


def test_legacy_project_chats_migrate_without_overwriting_sources(project):
    from pathlib import Path
    service, pid = project
    other = service.projects.create(service.projects.root.parent / 'movie.mp4')
    originals = []
    for index, project_id in enumerate((pid, other['id'])):
        path = Path(service.projects.get(project_id)['directory']) / 'agent-chat.json'
        raw = json.dumps({'thread_id': 'legacy-thread', 'messages': [
            {'role': 'user', 'text': f'旧约定{index}', 'time': index, 'id': 'same'}]})
        path.write_text(raw, encoding='utf-8')
        originals.append((path, raw))
    agent = AgentManager(service)
    state = agent.state(other['id'])
    assert [m['text'] for m in state['messages']] == ['旧约定0', '旧约定1']
    assert len({m['id'] for m in state['messages']}) == 2
    assert not state.get('thread_id')
    agent._message(other['id'], 'user', '继续')
    assert all(path.read_text(encoding='utf-8') == raw for path, raw in originals)
    restarted = AgentManager(service)
    assert len(restarted.state(pid)['messages']) == 3
    restarted.new_chat(pid)
    assert AgentManager(service).state(other['id'])['messages'] == []


def test_connection_reused_until_actual_loss(project):
    service, pid = project
    agent = AgentManager(service, FakeClient)
    agent.connect()
    client = agent.client
    agent.connect()
    agent.new_chat(pid)
    agent.connect()
    assert agent.client is client
    assert sum(method == 'model/list' for method, _ in client.calls) == 1
    client.close()
    assert not agent.state(pid)['connected']
    with pytest.raises(ValueError, match='断开'):
        agent.send(pid, 'read', 'test-model', 'low', ['first'])
    assert agent.client is client
    agent.connect()
    assert agent.client is not client
    assert agent.state(pid)['connected']


def test_history_search_preserves_old_memory_without_full_context_replay(project):
    service, pid = project
    agent = AgentManager(service, FakeClient)
    agent._message(pid, 'user', '早先确认的台词原文')
    for i in range(20):
        agent._message(pid, 'assistant', f'普通进度{i}')
    agent.connect()
    agent.send(pid, '查询之前的台词', 'test-model', 'low', ['first'])
    wait(agent)
    turn = next(args for method, args in agent.client.calls if method == 'turn/start')
    assert '早先确认的台词原文' not in turn['input'][0]['text']
    history = agent.workflow.execute(pid, 'read_chat_history', {'query': '台词', 'newest_first': True, 'limit': 1})
    assert history['messages'][0]['text'] == '查询之前的台词'
    older = agent.workflow.execute(pid, 'read_chat_history', {'query': '台词', 'newest_first': True, 'offset': history['next_offset']})
    assert older['messages'][0]['text'] == '早先确认的台词原文'
    execution = agent.state(pid)['execution']
    assert execution['finished_at'] >= execution['started_at']
    assert execution['steps'][0]['tool'] == 'read_project'
    assert execution['steps'][0]['seconds'] >= 0


def test_global_agent_requires_explicit_source_and_enforces_each_scope(project, monkeypatch):
    service, pid = project
    agent = AgentManager(service)
    seen = []
    monkeypatch.setattr(agent.workflow, 'execute', lambda *args: seen.append(args) or {})
    scopes = {pid: ['first'], 'other': ['second']}
    def call(args):
        return agent._tool(pid, {'tool': 'workbench_update_groups', 'callId': 'test', 'arguments': args}, scopes)
    with pytest.raises(ValueError, match='allowed_scopes'):
        call({'updates': [{'group_id': 'first'}]})
    with pytest.raises(ValueError, match='范围'):
        call({'project_id': pid, 'updates': [{'group_id': 'second'}]})
    call({'project_id': pid, 'updates': [{'group_id': 'first'}]})
    assert seen[0][0] == pid and 'project_id' not in seen[0][2]
    with pytest.raises(ValueError, match='独立视频任务'):
        agent._tool(pid, {'tool': 'workbench_prepare_batch', 'callId': 'bad', 'arguments': {'project_id':pid,'group_ids':['first']}}, scopes)


def test_global_agent_post_scope_checks_exact_video_versions(project, monkeypatch):
    from dialogue_tool.workbench import PostWorkbench
    service, pid = project
    agent = AgentManager(service)
    items = [{'id':'one','project_id':pid,'group_id':'first'},
             {'id':'two','project_id':pid,'group_id':'second'},
             {'id':'other-version','project_id':pid,'group_id':'first'}]
    monkeypatch.setattr(PostWorkbench,'workbench_library',lambda self:{'items':items})
    calls = []
    monkeypatch.setattr(PostWorkbench,'workbench_start_post',lambda self,*a:calls.append(a) or {})
    scope = {'projects':{pid:['first','second']},'item_ids':['one','two']}
    params = {'tool':'workbench_start_postproduction','callId':'post','arguments':{'project_id':pid,'item_ids':['one','two'],'recognize':True}}
    assert agent._tool(pid,params,scope)['success']
    assert calls==[(['one','two'],True)]
    params['arguments']['item_ids']=['other-version']
    with pytest.raises(ValueError, match='视频范围'):
        agent._tool(pid,params,scope)
    assert len(calls)==1


@pytest.mark.parametrize('action', ['request_generation_channel', 'refresh_results', 'download_results', 'export_images'])
def test_global_scope_cannot_run_unscoped_project_actions(project, monkeypatch, action):
    service, pid = project
    agent = AgentManager(service)
    monkeypatch.setattr(agent.workflow, 'execute', lambda *args: pytest.fail('must not modify entire project'))
    with pytest.raises(ValueError, match='独立视频任务'):
        agent._tool(pid, {'tool': 'workbench_' + action, 'callId': 'scope-test',
                         'arguments': {'project_id': pid}}, {pid: ['first']})
