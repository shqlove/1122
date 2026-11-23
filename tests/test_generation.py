"""Offline contract checks for official Dreamina Canvas integration.
No login, network calls or paid generation.
"""
import hashlib
import json
from pathlib import Path

import pytest

from dialogue_tool import generation
from dialogue_tool.generation import CanvasError, CanvasWorkflow
from dialogue_tool.service import Service


@pytest.mark.parametrize('action', [['node', 'run'], ['node', 'confirm'], ['node', 'create', 'video']])
def test_write_timeout_never_gets_automatic_retry_permission(monkeypatch, action):
    import subprocess
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired('test-cli', 120)
    monkeypatch.setattr(generation.subprocess, 'run', timeout)
    with pytest.raises(CanvasError) as caught:
        generation.invoke('test-cli', action)
    assert caught.value.retryable is False


def test_model_auth_failure_refreshes_once_then_rechecks(tmp_path, monkeypatch):
    calls = []
    def fake(cli, args, timeout=120):
        calls.append(args)
        if len(calls) == 1:
            raise CanvasError('cli.authentication_required')
        return {'items': []}
    monkeypatch.setattr(generation, 'invoke', fake)
    flow = CanvasWorkflow(Service({'data': str(tmp_path)}))
    assert flow.models() == {'items': []}
    assert calls == [['model', 'list', '--type', 'video'], ['auth', 'refresh'],
                     ['model', 'list', '--type', 'video']]


def test_login_verifies_server_access(tmp_path, monkeypatch):
    calls = []
    def fake(cli, args, timeout=120):
        calls.append(args)
        if args == ['auth', 'status']:
            return {'loggedIn': True}
        if args[0] == 'model':
            raise CanvasError('server unavailable')
        return {'status': 'reused'}
    monkeypatch.setattr(generation, 'invoke', fake)
    flow = CanvasWorkflow(Service({'data': str(tmp_path)}))
    with pytest.raises(CanvasError, match='server unavailable'):
        flow.login()
    assert calls[-1] == ['model', 'list', '--type', 'video']


@pytest.fixture
def context(tmp_path, monkeypatch):
    file_a, file_b = tmp_path / "a.png", tmp_path / "b.png"
    file_a.write_bytes(b"PNG A")
    file_b.write_bytes(b"PNG B")
    service = Service({"data": str(tmp_path / "db"), "dreamina_canvas": "fake-canvas",
                       "ffmpeg": "unused", "ffprobe": "unused"})
    source = tmp_path / "source.mp4"
    source.write_bytes(b"test")
    project = service.projects.create(source)
    project["groups"] = [{"id": "group-1", "frame_a": 1, "frame_b": 2, "selected": True,
                          "needs_review": False, "prompt": "人物自然交流"}]
    service.projects.save(project)
    monkeypatch.setattr(generation, "original_frame",
                        lambda project, frame_id, *unused: file_a if frame_id == 1 else file_b)
    calls = []
    output_rid = "c61a296f-68cd-48f7-9bb8-0a89c83d0011"

    def fake(cli, args, timeout=120):
        calls.append(list(args))
        if args[:2] == ["canvas", "create"]:
            return {"projectId": args[args.index("--project-id") + 1]}
        if args[:2] == ["resource", "upload"]:
            return {"resourceId": args[args.index("--resource-id") + 1]}
        if args[:3] == ["node", "create", "video"]:
            assert args.count("--ref") == 2
            assert args[args.index("--mode") + 1] == "m2v"  # A/B are NOT first and last frames.
            return {"nodeId": "node_localtest"}
        if args[:2] == ["node", "quote"]:
            return {"confirmable": True, "totalMaxCredits": 12}
        if args == ['auth', 'account']:
            return {'credits': {'available': '100'}}
        if args[:2] == ["node", "confirm"]:
            assert args[args.index("--credit-ceiling") + 1] == 12
            return {"creditConfirmationToken": "do-not-persist"}
        if args[:2] == ["node", "run"]:
            assert "--credit-token" in args
            assert args.count("--submit-id") == args.count("--node-id")
            return {"items": [{"state": "ACCEPTED"}]}
        if args[:2] == ["operation", "status"]:
            return {"submission": {"state": "completed"}, "resources": [{"resourceId": output_rid}]}
        if args[:2] == ["resource", "get"]:
            return {"resourceId": output_rid}
        if args[:2] == ["resource", "download"]:
            folder = Path(args[args.index("--output") + 1])
            target = folder / "test.mp4"
            target.write_bytes(b"fake mp4 result")
            return {"path": str(target), "byteCount": target.stat().st_size,
                    "sha256": hashlib.sha256(target.read_bytes()).hexdigest()}
        raise AssertionError(args)

    monkeypatch.setattr(generation, "invoke", fake)
    monkeypatch.setattr(generation, 'probe', lambda file, executable: {
        'format': {'duration': service.projects.get(project['id'])['generation']['duration']},
        'streams': [{'codec_type': 'video', 'codec_name': 'h264', 'width': 1408, 'height': 1080},
                    {'codec_type': 'audio', 'codec_name': 'aac'}]})
    return CanvasWorkflow(service), project["id"], calls


def test_prepare_quote_approve_status_download(context):
    flow, project_id, calls = context
    flow.configure(project_id, "video-model-returned-by-discovery", 5, "保留人物一致性")
    p = flow.prepare(project_id)
    assert p["generation"]["tasks"]["group-1"]["status"] == "draft"
    assert not any(args[:2] == ["node", "run"] for args in calls)
    q = flow.quote(project_id)
    assert q["generation"]["quote"]["ceiling"] == 12
    with pytest.raises(CanvasError, match="报价不匹配"):
        flow.run_approved(project_id, 999)  # Cannot spend beyond explicit UI consent.
    p = flow.run_approved(project_id, 12)
    task = p["generation"]["tasks"]["group-1"]
    submit_id = task["submit_id"]
    assert submit_id
    assert "do-not-persist" not in json.dumps(p)
    with pytest.raises(CanvasError, match="授权报价不匹配"):
        flow.run_approved(project_id, 12)
    p = flow.refresh(project_id)
    assert p["generation"]["tasks"]["group-1"]["status"] == "completed"
    assert p["generation"]["tasks"]["group-1"]["submit_id"] == submit_id
    p = flow.download(project_id)
    assert p["generation"]["tasks"]["group-1"]["status"] == "downloaded"
    assert Path(p["generation"]["tasks"]["group-1"]["output"]).is_file()
    assert len([x for x in calls if x[:2] == ["node", "run"]]) == 1


def test_shared_prompt_uses_each_groups_own_role_bindings_and_exact_scope(context):
    flow, pid, calls = context
    p = flow._load(pid)
    p['groups'].append({**p['groups'][0], 'id': 'group-2', 'prompt': 'should not be appended',
                        'generation_bindings': {'male_side': 'b'}})
    flow._save(p)
    flow.configure(pid, 'test-model', 15, '@男主说话，@聆听者静听', prompt_mode='shared')
    p = flow.prepare(pid, group_ids=['group-2'])
    assert set(p['generation']['tasks']) == {'group-2'}
    task = p['generation']['tasks']['group-2']
    assert 'should not be appended' not in task['prompt']
    inputs, _ = flow.inputs(p, p['groups'][1])
    assert inputs['references'][1]['role'] == '男主'
    assert not any(args[:2] == ['node', 'run'] for args in calls)


def test_invalid_batch_fails_before_upload(context):
    flow, pid, calls = context
    flow.configure(pid, 'test-model', 15, 'prompt', prompt_mode='shared')
    with pytest.raises(CanvasError, match='范围'):
        flow.prepare(pid, group_ids=['not-a-group'])
    assert not calls


def test_changed_prompt_rejects_paid_submission(context):
    flow, project_id, calls = context
    flow.configure(project_id, "model", 5, "全局")
    flow.prepare(project_id)
    flow.quote(project_id)
    p = flow.service.projects.get(project_id)
    p["groups"][0]["prompt"] = "已修改的新提示词"
    flow.service.projects.save(p)
    with pytest.raises(CanvasError, match="提示词已变化"):
        flow.run_approved(project_id, 12)
    assert not any(x[:2] == ["node", "run"] for x in calls)


def test_node_create_ambiguous_never_repeats(context, monkeypatch):
    flow, project_id, calls = context
    flow.configure(project_id, "model", 5, "全局")
    original = generation.invoke

    def lost(cli, args, timeout=120):
        if args[:3] == ["node", "create", "video"]:
            calls.append(list(args))
            raise CanvasError("模拟创建成功但响应丢失")
        return original(cli, args, timeout)

    monkeypatch.setattr(generation, "invoke", lost)
    with pytest.raises(CanvasError, match="响应丢失"):
        flow.prepare(project_id)
    with pytest.raises(CanvasError, match="不确定"):
        flow.prepare(project_id)
    assert len([x for x in calls if x[:3] == ["node", "create", "video"]]) == 1


def test_cli_argv_json_no_shell(monkeypatch):
    class Done:
        returncode = 0
        stdout = '{"data":{"nodeId":"node_x"}}'
    received = {}
    def fake_run(args, **kwargs):
        received["args"] = args
        received["kwargs"] = kwargs
        return Done()
    monkeypatch.setattr(generation.subprocess, "run", fake_run)
    assert generation.invoke("/tmp/dreamina-canvas", ["node", "show", "quotes; rm -rf /"]) == {"nodeId": "node_x"}
    assert received["kwargs"]["capture_output"] is True
    assert received["args"][-1] == "quotes; rm -rf /"
    assert "shell" not in received["kwargs"]


def test_download_checksum_mismatch_is_not_marked_success(context, monkeypatch):
    flow, project_id, calls = context
    flow.configure(project_id, "model", 5, "全局")
    flow.prepare(project_id)
    flow.quote(project_id)
    flow.run_approved(project_id, 12)
    flow.refresh(project_id)
    original = generation.invoke
    def wrong(cli, args, timeout=120):
        result = original(cli, args, timeout)
        if args[:2] == ["resource", "download"]:
            result["sha256"] = "0" * 64
        return result
    monkeypatch.setattr(generation, "invoke", wrong)
    with pytest.raises(CanvasError, match="校验失败"):
        flow.download(project_id)
    assert flow.service.projects.get(project_id)["generation"]["tasks"]["group-1"]["status"] == "completed"


def test_three_real_bindings_and_wrapped_create_response(context, monkeypatch):
    flow, pid, calls = context
    p = flow.service.projects.get(pid)
    voice = Path(p['directory']) / 'voice.mp3'
    voice.write_bytes(b'voice reference')
    p['groups'][0].update(prompt='男主@发言。聆听者@听。要求音色参考@。',
                          generation_bindings={'male_side':'b','voice_path':str(voice)})
    flow.service.projects.save(p)
    flow.configure(pid,'seedance_2.0_fast_vip',15,'')
    original = generation.invoke
    def fake(cli,args,timeout=120):
        if args[:3] == ['node','create','video']:
            calls.append(args)
            assert args.count('--ref') == 3
            return {'node':{'nodeId':'node_wrapped'}}
        return original(cli,args,timeout)
    monkeypatch.setattr(generation,'invoke',fake)
    p = flow.prepare(pid,'group-1')
    t = p['generation']['tasks']['group-1']
    assert t['node_id'] == 'node_wrapped'
    assert '{{res:'+t['resource_ids'][1]+'}}发言' in t['prompt']
    assert '{{res:'+t['resource_ids'][0]+'}}听' in t['prompt']
    assert '{{res:'+t['resource_ids'][2]+'}}' in t['prompt']
    assert len([a for a in calls if a[:2] == ['resource','upload'] and 'audio' in a]) == 1


def test_unbound_voice_fails_before_any_remote_write(context):
    flow,pid,calls = context
    p = flow.service.projects.get(pid)
    p['groups'][0]['prompt']='@男主发言，音色参考@音色。'
    flow.service.projects.save(p)
    flow.configure(pid,'model',15,'')
    with pytest.raises(CanvasError,match='尚未绑定'):
        flow.prepare(pid)
    assert not calls


def test_accepted_submission_is_not_finished_generation(context,monkeypatch):
    flow,pid,calls=context
    flow.configure(pid,'model',15,'')
    flow.prepare(pid)
    p=flow.service.projects.get(pid)
    t=p['generation']['tasks']['group-1']
    t.update(submit_id='original-submit',status='submitted')
    flow.service.projects.save(p)
    monkeypatch.setattr(generation,'invoke',lambda *a,**k:{'state':'running','submission':{'state':'completed'},'resources':[{'resourceId':'output','state':'running'}]})
    p=flow.refresh(pid)
    assert p['generation']['tasks']['group-1']['status']=='running'
    with pytest.raises(CanvasError,match='不能归档'):
        flow.new_round(pid)


def test_role_change_after_quote_cannot_spend_credits(context):
    flow,pid,calls=context
    flow.configure(pid,'model',15,'')
    flow.prepare(pid)
    flow.quote(pid)
    p=flow.service.projects.get(pid)
    p['groups'][0]['generation_bindings']={'male_side':'b'}
    flow.service.projects.save(p)
    with pytest.raises(CanvasError,match='绑定'):
        flow.run_approved(pid,12)
    assert not any(a[:2]==['node','confirm'] for a in calls)


def test_new_round_preserves_old_output_and_submit_identity(context):
    flow,pid,calls=context
    flow.configure(pid,'model',15,'')
    flow.prepare(pid)
    p=flow.service.projects.get(pid)
    p['generation']['tasks']['group-1'].update(status='downloaded',submit_id='original',output='existing.mp4')
    flow.service.projects.save(p)
    p=flow.new_round(pid)
    assert not p['generation'].get('tasks')
    assert p['generation_history'][0]['tasks']['group-1']['submit_id']=='original'
    assert p['generation_history'][0]['tasks']['group-1']['output']=='existing.mp4'


def test_live_download_size_field_and_current_group_scope(context,monkeypatch):
    flow,pid,calls=context
    p=flow.service.projects.get(pid)
    p['groups'].append(dict(p['groups'][0],id='group-2'))
    flow.service.projects.save(p)
    flow.configure(pid,'model',15,'')
    p=flow.prepare(pid,'group-1')
    assert set(p['generation']['tasks'])=={'group-1'}
    flow.quote(pid)
    flow.run_approved(pid,12)
    flow.refresh(pid)
    original=generation.invoke
    def size_response(cli,args,timeout=120):
        result=original(cli,args,timeout)
        if args[:2]==['resource','download']:
            result['size']=result.pop('byteCount')
        return result
    monkeypatch.setattr(generation,'invoke',size_response)
    p=flow.download(pid)
    assert p['generation']['tasks']['group-1']['status']=='downloaded'


def test_cli_error_json_from_stderr_preserves_diagnostic_and_does_not_retry(monkeypatch):
    from types import SimpleNamespace
    calls = []
    def fake_run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=11, stdout='', stderr=json.dumps({
            'ok': False, 'error': {'code': 'auth.expired', 'requiredAction': 'login'}}))
    monkeypatch.setattr(generation.subprocess, 'run', fake_run)
    with pytest.raises(CanvasError, match='auth.expired.*login'):
        generation.invoke('canvas', ['model', 'list', '--type', 'video'])
    assert len(calls) == 1


@pytest.mark.parametrize('media,error', [
    ({'format': {'duration': '15'}, 'streams': []}, '有效视频'),
    ({'format': {'duration': '2'}, 'streams': [{'codec_type': 'video', 'width': 1280, 'height': 720}]}, '时长'),
    ({'format': {'duration': '15'}, 'streams': [{'codec_type': 'video', 'width': 1280, 'height': 720}]}, '音轨'),
])
def test_download_media_failure_keeps_original_submission(context, monkeypatch, media, error):
    flow, pid, calls = context
    flow.configure(pid, 'model', 15, '')
    flow.prepare(pid)
    flow.quote(pid)
    flow.run_approved(pid, 12)
    p = flow.refresh(pid)
    task = p['generation']['tasks']['group-1']
    submit_id = task['submit_id']
    task['references'].append({'role': '音色', 'type': 'audio'})
    flow._save(p)
    monkeypatch.setattr(generation, 'probe', lambda *args: media)
    with pytest.raises(CanvasError, match=error):
        flow.download(pid)
    saved = flow._load(pid)['generation']['tasks']['group-1']
    assert saved['status'] == 'completed'
    assert saved['submit_id'] == submit_id
    assert len([call for call in calls if call[:2] == ['node', 'run']]) == 1


def test_balance_drop_blocks_confirmation_and_paid_run(context, monkeypatch):
    flow, pid, calls = context
    flow.configure(pid, 'model', 15, '')
    flow.prepare(pid)
    flow.quote(pid)
    original = generation.invoke
    def lower_balance(cli, args, timeout=120):
        if args == ['auth', 'account']:
            return {'credits': {'available': '5'}}
        return original(cli, args, timeout)
    monkeypatch.setattr(generation, 'invoke', lower_balance)
    with pytest.raises(CanvasError, match='积分不足'):
        flow.run_approved(pid, 12)
    quote = flow._load(pid)['generation']['quote']
    assert quote['available_credits'] == 5 and quote['shortfall'] == 7
    assert quote['sufficient'] is False
    assert not any(c[:2] in (['node', 'confirm'], ['node', 'run']) for c in calls)
    assert not flow._load(pid)['generation']['tasks']['group-1'].get('submit_id')


@pytest.mark.parametrize('balance', [None, '', 'NaN', 'Infinity', -1, True])
def test_invalid_balance_blocks_submission(context, monkeypatch, balance):
    flow, pid, calls = context
    monkeypatch.setattr(generation, 'invoke', lambda *args, **kwargs: {'availableCredits': balance})
    with pytest.raises(CanvasError, match='积分'):
        flow.credit_balance(12)


def test_partial_submit_consumes_card_but_retains_original_ids(context, monkeypatch):
    flow, pid, _ = context
    flow.configure(pid, 'video-model', 5, '对话')
    flow.prepare(pid)
    flow.quote(pid)
    p = flow._load(pid)
    p['generation']['pending_approval'] = {'sufficient': True}
    flow._save(p)
    original = generation.invoke
    def partial(cli, args, **kwargs):
        if args[:2] == ['node', 'run']:
            persisted = flow._load(pid)['generation']
            assert 'pending_approval' not in persisted
            assert persisted['tasks']['group-1']['submit_id']
            raise CanvasError('partial submission')
        return original(cli, args, **kwargs)
    monkeypatch.setattr(generation, 'invoke', partial)
    with pytest.raises(CanvasError, match='partial'):
        flow.run_approved(pid, 12)
    assert flow._load(pid)['generation']['tasks']['group-1']['status'] == 'submission_unknown'


def test_refresh_persists_credit_failure_reason(context, monkeypatch):
    flow, pid, _ = context
    flow.configure(pid, 'video-model', 5, '对话')
    flow.prepare(pid)
    flow.quote(pid)
    flow.run_approved(pid, 12)
    monkeypatch.setattr(generation, 'invoke', lambda *a, **k: {
        'state': 'failed', 'submissionError': {'serviceCode': 20005, 'message': 'insufficient credits'}})
    task = flow.refresh(pid)['generation']['tasks']['group-1']
    assert task['status'] == 'failed'
    assert '积分不足' in task['error']
