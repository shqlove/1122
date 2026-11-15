import hashlib
import json
import time
from pathlib import Path

import pytest

from dialogue_tool.desktop import DesktopAPI, workbench_html
from dialogue_tool.service import Service
from dialogue_tool.workbench import save_json, read_json


@pytest.fixture
def wb(tmp_path):
    root = tmp_path / 'post'
    source = tmp_path / 'archive' / 'batch' / '第01组_优云智算H3_one.mp4'
    source.parent.mkdir(parents=True)
    source.write_bytes(b'original video')
    service = Service({'data': str(tmp_path / 'data'), 'postprocess_root': str(root)})
    p = service.projects.create(source)
    p['scan'] = {'frames': [{'id': n, 'seconds': n} for n in (1, 2, 3)]}
    p['groups'] = [{'id': 'g1', 'frame_a': 1, 'frame_b': 2, 'prompt': 'keep',
                    'selected': True, 'needs_review': False, 'person_a': '豪哥'}]
    p['generation'] = {'model': 'MiniMax-H3', 'use_context_ir': True, 'tasks': {
        'g1': {'status': 'downloaded', 'output': str(source), 'archive_output': str(source)}}}
    service.projects.save(p)
    cfg = {'inputRoot': str(tmp_path / 'archive'), 'audio': {'engine': 'local-spatial'},
           'tools': {'python': 'python'}, 'profileVersion': 'local-spatial-v1'}
    save_json(root / '流程配置.json', cfg)
    output = tmp_path / 'final.mp4'
    output.write_bytes(b'finished video')
    srt = tmp_path / 'captions.srt'
    srt.write_text('1\n00:00:00,000 --> 00:00:01,000\n真实字幕\n', encoding='utf-8')
    manifest = {'id': 'batch1', 'batch': str(source.parent), 'profile': cfg,
                'jobs': [{'id': 'job1', 'source': str(source), 'status': 'complete',
                          'output': str(output), 'outputSha256': hashlib.sha256(output.read_bytes()).hexdigest(),
                          'captions': {'path': str(srt)}, 'updatedAt': time.time()}]}
    save_json(root / 'batches' / 'batch1' / 'manifest.json', manifest)
    api = DesktopAPI(service)
    yield api, p, root, source, output
    api.close()


def test_history_links_and_acceptance_are_distinct(wb):
    api, p, _, _, output = wb
    item = next(i for i in api.workbench_library()['items'] if i['kind'] == 'post')
    assert item['project_id'] == p['id'] and item['group_id'] == 'g1'
    assert item['available'] and not item['accepted']
    api.workbench_review(item['id'], True)
    assert api._library_item(item['id'])['accepted']
    output.write_bytes(b'changed')
    assert not api._library_item(item['id'])['accepted']
    with pytest.raises(ValueError, match='修改'):
        api.workbench_review(item['id'], True)


def test_my_tasks_includes_history_and_uses_submission_snapshot(wb):
    import copy
    api,p,_,source,_=wb
    old=copy.deepcopy(p['generation'])
    old['submitted_at']=10
    old['tasks']['g1'].update(submit_id='old-submit',task_id='old-platform',prompt='旧备用文本',
        request_snapshot={'bound_prompt':'当次提交原文', 'parameters':{'model':'MiniMax-H3','duration':12,'resolution':'480P','ratio':'4:3','use_context_ir':False},'role_bindings':[]})
    p['generation_history']=[old]
    p['generation']={'model':'seedance_2.5','duration':30,'resolution':'4k','global_prompt':'当前新提示词',
                     'submitted_at':20,'tasks':{'g1':{'status':'failed','submit_id':'new-submit','prompt':'新提交原文','error':'平台失败'}}}
    api._service.projects.save(p)
    before=api._service.projects.get(p['id'])
    api.busy=True
    rows=api.workbench_tasks()['items']
    assert api._service.projects.get(p['id'])==before
    historic=next(r for r in rows if r['submit_id']=='old-submit')
    assert historic['category']=='completed' and historic['round']==0
    assert historic['prompt']=='当次提交原文' and historic['duration']==12 and historic['context_ir'] is False
    assert historic['task_id']=='old-platform' and historic['available']
    newest=next(r for r in rows if r['submit_id']=='new-submit')
    assert newest['category']=='failed' and newest['provider']=='dreamina'


def test_my_tasks_shows_download_failures_without_retrying(wb,monkeypatch):
    api,p,_,source,_=wb
    q=api._video_queue
    job={'id':'independent','directory':str(source.parent),'name':'job','created_at':30,
         'video_job':{'source_project_id':p['id'],'source_project_name':p['name'],'source_group_id':'g1',
                      'group_number':1,'part':2,'part_count':2,'batch_id':'batch'},
         'groups':[{'repaired_a':str(source),'repaired_b':str(source)}],
         'queue_state':{'phase':'attention','error':'下载连接失败'},
         'generation':{'model':'MiniMax-H3','duration':15,'ratio':'16:9','resolution':'768P',
                       'global_prompt':'已提交提示词','tasks':{'g1':{'status':'completed','submit_id':'download-pending','task_id':'platform-id','download_error':'连接失败'}}}}
    q.service.projects.save(job)
    monkeypatch.setattr(q.api,'execute',lambda *a:pytest.fail('list must not query, retry or submit'))
    monkeypatch.setattr(q,'refresh',lambda *a:pytest.fail('list must remain read only'))
    before=q.service.projects.get('independent')
    row=next(r for r in api.workbench_tasks()['items'] if r['submit_id']=='download-pending')
    assert row['category']=='attention' and row['status_label']=='已生成 · 下载待处理'
    assert not row['available'] and row['segment']=={'index':2,'count':2}
    assert row['error']=='下载连接失败' and row['task_id']=='platform-id'
    assert q.service.projects.get('independent')==before


def test_dialogue_parts_and_assembly_belong_to_original_group(wb):
    api, p, _, source, output = wb
    p['generation']['tasks'] = {f'g1__dialogue_part_{n}': {
        'status': 'downloaded', 'output': str(source), 'source_group_id': 'g1',
        'segment': {'index': n, 'count': 2}} for n in (1, 2)}
    p['generation']['dialogue_plans'] = {'g1': {'source_group_id': 'g1',
        'parts': [{'group_id': gid, 'index': n} for n, gid in enumerate(p['generation']['tasks'], 1)],
        'assembly': {'output': str(output), 'duration': 30}}}
    api._service.projects.save(p)
    items = api.workbench_library()['items']
    assembly = next(i for i in items if i['kind'] == 'assembly')
    assert assembly['group_ids'] == ['g1'] and assembly['group_number'] == 1
    parts = [i for i in items if i['kind'] == 'generation']
    assert len(parts) == 2
    assert all(i['group_id'] == 'g1' and i['assembly_id'] == assembly['id'] for i in parts)
    assert all(i['poster_frame'] == 1 and i['group_number'] == 1 for i in parts)


def test_global_overview_includes_all_projects_history_and_assemblies(wb):
    import copy
    api, p, _, source, _ = wb
    p['generation']['tasks']['g1']['request_snapshot'] = {'bound_prompt': '实际提交原文', 'role_bindings': []}
    p['generation_history'] = [copy.deepcopy(p['generation'])]
    p['generation_history'][0]['use_context_ir'] = False
    p['generation_history'][0]['segment_plan'] = {'parts': [], 'assembly': {
        'output': str(source), 'sha256': hashlib.sha256(source.read_bytes()).hexdigest(), 'duration': 30}}
    api._service.projects.save(p)
    other = api._service.projects.create(source)
    other['groups'] = [{**p['groups'][0], 'person_a': '另一集人物'}]
    api._service.projects.save(other)
    api.current = other['id']
    data = api.workbench_library()
    assert len(data['groups']) == 2
    assert len({g['id'] for g in data['groups']}) == 2
    assert {g['project_id'] for g in data['groups']} == {p['id'], other['id']}
    assert next(g for g in data['groups'] if g['project_id'] == p['id'])['generated'] == 2
    assert next(i for i in data['items'] if i['kind'] == 'assembly')['duration'] == 30
    old = next(i for i in data['items'] if i['kind'] == 'generation' and i['round'] == 0)
    assert old['prompt'] == '实际提交原文' and old['context_ir'] is False
    assert api.current == other['id']


def test_global_images_and_selection_use_explicit_project(wb, monkeypatch):
    from PIL import Image
    api, p, _, source, _ = wb
    other = api._service.projects.create(source)
    other['groups'] = [{**p['groups'][0], 'selected': False}]
    api._service.projects.save(other)
    frame = Path(p['directory']) / 'frame.png'; Image.new('RGB', (80, 60), 'red').save(frame)
    seen = []
    def original(project, number, *args):
        seen.append(project['id']); return frame
    monkeypatch.setattr('dialogue_tool.projects.original_frame', original)
    api._service.config.update(ffmpeg='ffmpeg', ffprobe='ffprobe')
    api.current = other['id']
    assert api.workbench_group_image(p['id'], 'g1', 'a')['url'].startswith('data:image/jpeg;base64,')
    assert seen == [p['id']] and api.current == other['id']
    api.workbench_select_global_groups(p['id'], ['g1'])
    assert not api._service.projects.get(other['id'])['groups'][0]['selected']
    with pytest.raises(ValueError):
        api.workbench_select_global_groups(p['id'], ['missing'])


def test_history_reference_refuses_changed_file(wb):
    api, p, _, source, _ = wb
    sha = hashlib.sha256(source.read_bytes()).hexdigest()
    p['generation']['tasks']['g1']['request_snapshot'] = {'role_bindings': [{'path': str(source), 'sha256': sha}]}
    api._service.projects.save(p)
    source.write_bytes(b'changed')
    with pytest.raises(ValueError, match='变化'):
        api.workbench_history_reference(next(i["id"] for i in api.workbench_library()["items"] if i["kind"] == "generation"), sha)


def test_assembly_post_archives_verified_source_only(wb, monkeypatch):
    api, p, root, _, _ = wb
    assembled = Path(p['directory']) / 'joined.mp4'
    assembled.write_bytes(b'joined segments')
    checksum = hashlib.sha256(assembled.read_bytes()).hexdigest()
    p['generation']['segment_plan'] = {'parts': [], 'assembly': {
        'output': str(assembled), 'sha256': checksum, 'duration': 30}}
    api._service.projects.save(p)
    monkeypatch.setattr('dialogue_tool.workbench.subprocess.Popen', lambda *a, **kw: None)
    key = next(i["id"] for i in api.workbench_library()["items"] if i["kind"] == "assembly")
    result = api.workbench_start_post([key])
    request = read_json(root / 'workbench-runs' / result['id'] / 'request.json')
    selected = request['items'][0]
    assert selected['item_id'] == key and selected['source_sha256'] == checksum
    assert Path(selected['source']).read_bytes() == assembled.read_bytes()
    assert Path(selected['source']).name.startswith('第00组_优云智算H3_完整拼接_')
    assert assembled.is_file()
    manifest_path = root / 'batches' / 'assembly-post' / 'manifest.json'
    save_json(manifest_path, {'id': 'assembly-post', 'batch': str(Path(selected['source']).parent), 'profile': {}, 'jobs': [
        {'id': 'joined-post', 'source': selected['source'], 'sourceSha256': checksum, 'status': 'complete'}]})
    rendered = next(i for i in api.workbench_library()['items'] if i.get('job_id') == 'joined-post')
    assert rendered['project_name'] == p['name'] and rendered['label'] == '完整拼接 · 后期'


def test_only_existing_assembly_parts_are_hidden(wb):
    api, p, _, source, _ = wb
    p['generation']['segment_plan'] = {'parts': [{'index': 1, 'group_id': 'g1'}], 'assembly': {
        'output': str(source), 'duration': 15}}
    p['generation']['tasks']['unrelated'] = {'status': 'draft'}
    api._service.projects.save(p)
    items = api.workbench_library()['items']
    assert next(i for i in items if i.get('group_id') == 'g1' and i['kind'] == 'generation')['assembly_id']
    assert not next(i for i in items if i.get('group_id') == 'unrelated')['assembly_id']
    source.unlink()
    assert not any(i.get('assembly_id') for i in api.workbench_library()['items'])


def test_post_exact_selection_snapshot_and_restart(wb, monkeypatch):
    api, p, root, source, _ = wb
    calls = []
    monkeypatch.setattr('dialogue_tool.workbench.subprocess.Popen', lambda *a, **k: calls.append((a, k)))
    key = next(i["id"] for i in api.workbench_library()["items"] if i["kind"] == "generation")
    state = api.workbench_start_post([key])
    folder = root / 'workbench-runs' / state['id']
    request = read_json(folder / 'request.json')
    assert [i['source'] for i in request['items']] == [str(source)]
    assert request['items'][0]['source_sha256'] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert read_json(folder / 'config.json')['audio']['engine'] == 'local-spatial'
    assert len(calls) == 1
    with pytest.raises(ValueError, match='正在处理'):
        api.workbench_start_post([key])
    restarted = DesktopAPI(api._service)
    assert restarted.workbench_library()['runs'][0]['status'] == 'running'
    save_json(folder / 'state.json', {**state, 'heartbeat': time.time() - 100})
    assert restarted.workbench_library()['runs'][0]['status'] == 'interrupted'
    restarted.close()


def test_reject_unknown_and_unarchived_paths(wb, monkeypatch):
    api, p, _, _, _ = wb
    monkeypatch.setattr('dialogue_tool.workbench.subprocess.Popen', lambda *a, **k: pytest.fail('must not spawn'))
    with pytest.raises(ValueError):
        api.workbench_start_post(['not-an-item'])
    key = next(i["id"] for i in api.workbench_library()["items"] if i["kind"] == "generation")
    with pytest.raises(ValueError):
        api.workbench_start_post([key, key])
    p['generation']['tasks']['g1']['archive_output'] = None
    api._service.projects.save(p)
    with pytest.raises(ValueError, match='归档'):
        api.workbench_start_post([key])


def test_caption_edit_stale_revision_cannot_start(wb, monkeypatch):
    api, _, _, _, _ = wb
    monkeypatch.setattr('dialogue_tool.workbench.subprocess.Popen', lambda *a, **k: pytest.fail('must not spawn'))
    with pytest.raises(ValueError, match='字幕已变化'):
        api.workbench_start_post(['post:batch1:job1'], False, {'text': 'edited', 'revision': 'stale'})


def test_replace_frame_guards_task_and_stale_frame(wb):
    api, p, _, _, _ = wb
    with pytest.raises(ValueError, match='生成'):
        api.workbench_replace_frame(p['id'], 'g1', 'a', 1, 3)
    p['generation']['tasks'] = {}
    api._service.projects.save(p)
    with pytest.raises(ValueError, match='图片已变化'):
        api.workbench_replace_frame(p['id'], 'g1', 'a', 99, 3)
    api.workbench_replace_frame(p['id'], 'g1', 'a', 1, 3)
    group = api._service.projects.get(p['id'])['groups'][0]
    assert group['frame_a'] == 3 and group['frame_b'] == 2
    assert group['required_character_status'] == 'uncertain' and group['person_a'] == ''


def test_add_group_keeps_existing_and_requires_two_valid_frames(wb):
    api, p, _, _, _ = wb
    with pytest.raises(ValueError):
        api.workbench_add_group(p['id'], 1, 1)
    key = api.workbench_add_group(p['id'], 2, 3)
    groups = api._service.projects.get(p['id'])['groups']
    assert groups[0]['id'] == 'g1' and groups[1]['id'] == key
    assert groups[1]['needs_review'] and not groups[1]['selected']


def test_prepare_only_requests_approval_never_submits(wb, monkeypatch):
    api, p, _, _, _ = wb
    calls = []
    def execute(pid, action, args=None):
        calls.append(action)
        return {'errors': []}
    monkeypatch.setattr(api._agent.workflow, 'execute', execute)
    monkeypatch.setattr(api, '_start', lambda f: f())
    api.workbench_prepare(p['id'], ['g1'], {})
    assert calls == ['configure_generation', 'generation_preflight', 'prepare_batch', 'quote_batch', 'request_submission']


def test_agent_cannot_post_outside_selected_group(wb):
    api, p, _, _, _ = wb
    with pytest.raises(ValueError, match='范围'):
        api._agent._tool(p['id'], {'tool': 'workbench_start_postproduction', 'arguments': {
            'item_ids': [next(i["id"] for i in api.workbench_library()["items"] if i["kind"] == "generation")], 'recognize': True}, 'callId': 'test'}, [])


def test_agent_assembly_checks_all_actual_parts_not_original_source_group(wb, monkeypatch):
    api, p, _, source, _ = wb
    p['generation']['segment_plan'] = {'source_group_id': 'other-project-original',
        'parts': [{'index': 1, 'group_id': 'g1'}, {'index': 2, 'group_id': 'g2'}],
        'assembly': {'output': str(source), 'sha256': hashlib.sha256(source.read_bytes()).hexdigest()}}
    api._service.projects.save(p)
    calls = []
    monkeypatch.setattr(api._agent.workflow, 'execute', lambda *args: calls.append(args) or {'id': 'test-run'})
    params = {'tool': 'workbench_start_postproduction', 'arguments': {'item_ids': [next(i["id"] for i in api.workbench_library()["items"] if i["kind"] == "assembly")], 'recognize': True}, 'callId': 'assembly-test'}
    with pytest.raises(ValueError, match='范围'):
        api._agent._tool(p['id'], params, ['g1'])
    assert not calls
    api._agent._tool(p['id'], params, ['g1', 'g2'])
    assert len(calls) == 1


def test_embedded_ui_has_no_external_script_dependency():
    html = workbench_html()
    assert '<div id="root">' in html and '<script type="module">' in html
    assert 'src="http' not in html and 'src="/src/' not in html


def test_go_generate_archives_finished_round_and_restores_current_h3_defaults(wb):
    import copy
    api, p, _, _, _ = wb
    p['analysis_filter'] = {'required_character': '张世豪（豪哥）'}
    p['generation'].update(provider='compshare', channel_selected=True, duration=30, use_context_ir=False)
    old = copy.deepcopy(p['generation'])
    api._service.projects.save(p)
    result = api.workbench_enter_generation(p['id'], ['g1'])
    saved = api._service.projects.get(p['id'])
    assert result['archived_previous_round'] and not result['has_tasks']
    assert saved['generation_history'][-1] == old
    assert saved['groups'] == p['groups']
    assert saved['generation']['duration'] == 15 and saved['generation']['use_context_ir'] is True
    assert saved['generation']['baseline_id'] == 'h3-first-v1'
    assert saved['generation']['channel_selected'] is True
    assert not saved['generation'].get('pending_approval')
    assert not api.workbench_enter_generation(p['id'], ['g1'])['archived_previous_round']
    assert len(api._service.projects.get(p['id'])['generation_history']) == 1


@pytest.mark.parametrize('status', ['draft', 'running', 'submitted', 'submission_unknown', 'completed'])
def test_go_generate_keeps_unfinished_jobs_and_approvals(wb, status):
    api, p, _, _, _ = wb
    p['generation']['tasks']['g1']['status'] = status
    p['generation']['pending_approval'] = {'id': 'keep'}
    api._service.projects.save(p)
    result = api.workbench_enter_generation(p['id'], ['g1'])
    assert result['has_tasks'] and not result['archived_previous_round']
    assert api._service.projects.get(p['id']) == p


def test_go_generate_rejects_stale_selection_before_archiving(wb):
    api, p, _, _, _ = wb
    p['groups'][0]['selected'] = False
    api._service.projects.save(p)
    with pytest.raises(ValueError, match='选择已变化'):
        api.workbench_enter_generation(p['id'], ['g1'])
    assert api._service.projects.get(p['id']) == p


def test_independent_single_video_reaches_post_without_source_mutation(wb, monkeypatch):
    from PIL import Image
    from dialogue_tool.generation import _sha
    api, p, root, source, _ = wb
    q = api._video_queue
    monkeypatch.setattr(q, '_start', lambda *args: False)
    for side in ('a', 'b'):
        image = Path(p['directory']) / (side + '.png')
        Image.new('RGB', (80, 60), 'red').save(image)
        p['groups'][0]['repaired_' + side] = str(image)
    api._service.projects.save(p)
    api.current = p['id']
    api.busy = True
    cfg = api.workbench_video_preset()
    cfg.update(baseline_id='custom', auto_split_dialogue=False, common_prompt='@男主说“你好”。')
    jid = api.workbench_video_create(p['id'], ['g1'], cfg, 'baseline')['job_ids'][0]
    assert api._service.projects.get(p['id']) == p
    job = q.service.projects.get(jid)
    local_output = Path(job['directory']) / 'downloaded.mp4'
    local_output.write_bytes(source.read_bytes())
    job['generation']['tasks'] = {'g1': {'status': 'downloaded', 'output': str(local_output),
                                       'archive_output': str(source), 'sha256': _sha(source)}}
    job['queue_state']['phase'] = 'downloaded'
    q.service.projects.save(job)
    key = 'video-job:' + jid
    item = next(i for i in api.workbench_library()['items'] if i['id'] == key)
    assert item['available'] and item['archive_path'] == str(source)
    assert item['project_id'] == p['id'] and item['group_id'] == 'g1'
    launches = []
    monkeypatch.setattr('dialogue_tool.workbench.subprocess.Popen', lambda *a, **kw: launches.append(a))
    assert launches == []
    result = api.workbench_start_post([key])
    request = read_json(root / 'workbench-runs' / result['id'] / 'request.json')
    assert len(launches) == 1
    assert request['items'][0]['item_id'] == key
    assert request['items'][0]['source'] == str(source)
    assert request['items'][0]['source_sha256'] == _sha(source)
    finished = next(i for i in api.workbench_library()['items'] if i['id'] == 'post:batch1:job1')
    assert finished['source_id'] == key and finished['project_name'] == p['name']
    assert api._service.projects.get(p['id']) == p


def test_global_recycle_restore_preserves_history_files_and_scope(wb):
    api, p, _, source, output = wb
    scope = [{'project_id': p['id'], 'group_id': 'g1', 'frame_a': 1, 'frame_b': 2}]
    before = api._service.projects.get(p['id'])
    assert api.workbench_group_batch(scope, 'trash')[0]['ok']
    assert api.workbench_library()['groups'][0]['trashed']
    assert api.workbench_video_sources()[0]['groups'] == []
    with pytest.raises(ValueError, match='回收站'):
        api.workbench_video_create(p['id'], ['g1'], {})
    assert api._service.projects.get(p['id']) == before
    assert source.is_file() and output.is_file()
    assert api.workbench_tasks()['items']
    assert api.workbench_group_batch(scope, 'restore')[0]['ok']
    assert not api.workbench_library()['groups'][0]['trashed']
    assert len(api.workbench_video_sources()[0]['groups']) == 1
    item = next(i for i in api.workbench_library()['items'] if i['kind'] == 'generation')
    assert api.workbench_media_batch([item['id']], 'trash')[0]['ok']
    assert api._library_item(item['id'])['trashed']
    with pytest.raises(ValueError, match='回收站'):
        api.workbench_start_post([item['id']])
    assert api.workbench_media_batch([item['id']], 'restore')[0]['ok']
    assert source.is_file() and not api._library_item(item['id'])['trashed']


def test_global_confirm_does_not_switch_project_and_failure_continues(wb):
    api, p, *_ = wb
    api.current = 'other-current-project'
    api.busy = True  # Unrelated work is not a global veto.
    scopes = [{'project_id': p['id'], 'group_id': 'g1', 'frame_a': 99, 'frame_b': 2},
              {'project_id': p['id'], 'group_id': 'missing', 'frame_a': 1, 'frame_b': 2}]
    result = api.workbench_group_batch(scopes, 'confirm')
    assert all(not r['ok'] for r in result)
    good = {'project_id': p['id'], 'group_id': 'g1', 'frame_a': 1, 'frame_b': 2}
    assert api.workbench_group_batch([scopes[1], good], 'unconfirm')[1]['ok']
    assert api._service.projects.get(p['id'])['groups'][0]['needs_review']
    assert api.current == 'other-current-project'
    assert api.workbench_group_batch([good], 'confirm')[0]['ok']
    assert not api._service.projects.get(p['id'])['groups'][0]['needs_review']
    assert api._service.projects.get(p['id'])['generation']['tasks']['g1']['status'] == 'downloaded'


def test_batch_export_checks_content_and_never_overwrites(wb, tmp_path, monkeypatch):
    from types import SimpleNamespace
    import sys
    monkeypatch.setitem(sys.modules, 'webview', SimpleNamespace(FileDialog=SimpleNamespace(FOLDER='folder')))
    api, _, _, source, _ = wb
    destination = tmp_path / 'exports'; destination.mkdir()
    api._window = SimpleNamespace(create_file_dialog=lambda *a, **k: [str(destination)])
    item = next(i for i in api.workbench_library()['items'] if i['kind'] == 'generation')
    result = api.workbench_export_selected([item['id']])
    assert result[0]['ok']
    target = Path(result[0]['path'])
    assert target.read_bytes() == source.read_bytes()
    assert api.workbench_export_selected([item['id']])[0]['ok']
    target.write_bytes(b'user modified export')
    assert not api.workbench_export_selected([item['id']])[0]['ok']
    assert target.read_bytes() == b'user modified export'
    assert not list(destination.glob('*.tmp'))


def test_recycled_video_keeps_identity_after_new_round(wb):
    import copy
    api, p, _, source, _ = wb
    before = next(i for i in api.workbench_library()['items'] if i['kind'] == 'generation')
    assert api.workbench_media_batch([before['id']], 'trash')[0]['ok']
    p['generation_history'] = [copy.deepcopy(p['generation'])]
    next_clip = source.with_name('next.mp4'); next_clip.write_bytes(b'new round')
    p['generation']['tasks']['g1'].update(output=str(next_clip), archive_output=str(next_clip))
    api._service.projects.save(p)
    rows = [i for i in api.workbench_library()['items'] if i['kind'] == 'generation']
    old = next(i for i in rows if i['round'] == 0)
    new = next(i for i in rows if i['round'] == -1)
    assert old['id'] == before['id'] and old['trashed']
    assert new['id'] != old['id'] and not new['trashed']
    assert api.workbench_media_batch([old['id']], 'restore')[0]['ok']
    assert not api._library_item(old['id'])['trashed']


def test_legacy_recycle_mark_migrates_on_startup(wb):
    api, p, _, _, _ = wb
    legacy = f"media:generation:{p['id']}:-1:g1"
    save_json(api._service.projects.root / 'workbench-trash.json', {legacy: {'at': 123}})
    restarted = DesktopAPI(api._service)
    try:
        item = next(i for i in restarted.workbench_library()['items'] if i['kind'] == 'generation')
        assert item['trashed']
        records = read_json(api._service.projects.root / 'workbench-trash.json')
        assert legacy not in records and records['media:' + item['id']] == {'at': 123}
    finally:
        restarted.close()
