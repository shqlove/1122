import threading
import time
from pathlib import Path

import pytest
from PIL import Image

from dialogue_tool.service import Service
from dialogue_tool.video_queue import VideoQueue
from dialogue_tool.h3_baseline import BASELINE
from dialogue_tool.generation import _sha
from dialogue_tool.project_run import analysis_lock, workflow_lock


@pytest.fixture
def queue(tmp_path, monkeypatch):
    from dialogue_tool.settings import settings
    s = Service({**settings(), 'data': str(tmp_path / 'db')})
    source = tmp_path / 'source.mp4'; source.write_bytes(b'source')
    p = s.projects.create(source)
    g = {'id': 'g', 'frame_a': 0, 'frame_b': 1, 'selected': True, 'needs_review': False,
         'reference_reviews': {}}
    for side in ('a','b'):
        image = Path(p['directory']) / (side + '.png')
        Image.new('RGB', (640,480), 'red').save(image)
        g['repaired_' + side] = str(image)
        g['reference_reviews'][side] = {'sha256': _sha(image), 'clean': True}
    p['groups'] = [g]; s.projects.save(p)
    q = VideoQueue(s)
    monkeypatch.setattr(q, '_start', lambda jid, op: False)
    yield q, p
    q.stop.set()


def config(**changes):
    return dict(model='MiniMax-H3',duration=15,ratio='16:9',resolution='768P',prompt_mode='shared',
                common_prompt='@男主说“你好”，@聆听者听。',baseline_id='custom',preserve_prompt=True,
                use_context_ir=True,**changes)


def test_sources_remain_unchanged_and_inputs_are_independent(queue):
    q,p = queue
    p['generation'] = {'tasks': {'g': {'status':'submission_unknown','submit_id':'old'}}}
    q.source.projects.save(p)
    with analysis_lock(Path(p['directory'])), workflow_lock(Path(p['directory'])):
        one = q.create(p['id'], ['g'], config())['job_ids'][0]
        two = q.create(p['id'], ['g'], config())['job_ids'][0]
    assert one != two
    assert q.source.projects.get(p['id']) == p
    job = q.service.projects.get(one)
    copied = Path(job['groups'][0]['repaired_a'])
    old = copied.read_bytes()
    Path(p['groups'][0]['repaired_a']).write_bytes(b'changed')
    assert copied.read_bytes() == old
    assert job['generation']['global_prompt'] == config()['common_prompt']
    assert job['generation']['verbatim_prompt'] is True
    assert _sha(Path(job['groups'][0]['generation_bindings']['voice_path'])) == BASELINE['audio_sha256']


def test_polling_keeps_media_urls_stable(queue):
    from dialogue_tool.workbench import PostWorkbench
    q,p=queue
    q.create(p['id'],['g'],config())
    host=PostWorkbench(q.source);host._video_queue=q
    first=host.workbench_video_jobs()[0]['references']
    second=host.workbench_video_jobs()[0]['references']
    assert first==second
    assert len(host._media_server.files)==3


def test_default_split_preserves_exact_prompt_voice_and_order(queue):
    from dialogue_tool.dialogue_segments import split_prompts
    q,p = queue
    c = {k:v for k,v in BASELINE['parameters'].items() if k!='mode'}
    c.update(common_prompt=BASELINE['global_prompt'],baseline_id=BASELINE['id'],auto_split_dialogue=True)
    jobs = q.create(p['id'], ['g'], c)['job_ids']
    assert len(jobs)==2
    for i,jid in enumerate(jobs):
        job = q.service.projects.get(jid)
        assert job['generation']['global_prompt']==split_prompts(c['common_prompt'])[i]
        assert job['video_job']['part']==i+1
        assert _sha(Path(job['groups'][0]['generation_bindings']['voice_path']))==BASELINE['audio_sha256']
        sheet = q.api.execute(jid,'execution_sheet',{'group_id':'g'})
        expected = split_prompts(c['common_prompt'])[i].replace('@男主','<Picture 1>').replace('@聆听者','<Picture 2>').replace('@音色','<Audio 1>')
        assert sheet['bound_prompt']==expected


def test_quote_then_submission_is_per_row_and_idempotent(queue, monkeypatch):
    q,p = queue
    from dialogue_tool.compshare import H3Client
    monkeypatch.setattr(H3Client,'request',lambda *a,**k:{'available_points':9999})
    one,two = [q.create(p['id'],['g'],config())['job_ids'][0] for _ in range(2)]
    q._prepare(one); q._prepare(two)
    card=q.service.projects.get(one)['generation']['pending_approval']
    with pytest.raises(ValueError,match='变化'):
        q.approve(two,card['id'],card['ceiling'])
    gate=threading.Event(); started=threading.Event()
    def submit(*a): started.set(); gate.wait(3)
    monkeypatch.setattr(q.api,'approve_submission',submit)
    monkeypatch.setattr(q,'_watch',lambda jid:None)
    monkeypatch.setattr(q,'_start',VideoQueue._start.__get__(q))
    q.approve(one,card['id'],card['ceiling']); assert started.wait(1)
    try:
        assert q.service.projects.get(two)['queue_state']['phase']=='awaiting_approval'
        assert q._start(two,lambda:q._save_state(two,'tracking'))
        with pytest.raises(ValueError):q.approve(one,card['id'],card['ceiling'])
    finally: gate.set()


def test_restart_never_replays_paid_request(queue,monkeypatch):
    q,p=queue
    jid=q.create(p['id'],['g'],config())['job_ids'][0]
    q._save_state(jid,'submitting')
    monkeypatch.setattr(q.api,'approve_submission',lambda *a:pytest.fail('must not pay on startup'))
    q.resume()
    assert q.service.projects.get(jid)['queue_state']['phase']=='attention'
    job=q.service.projects.get(jid)
    job['generation']['tasks']={'g':{'submit_id':'existing','status':'submission_unknown'}}
    job['queue_state']['phase']='submitting';q.service.projects.save(job)
    calls=[]
    monkeypatch.setattr(q,'_start',lambda jid,op:(calls.append(jid) or True))
    q.resume();assert calls==[jid]


def test_h3_platform_parameters_reach_payload(queue):
    q,p=queue
    jid=q.create(p['id'],['g'],config(mute_audio=True,aigc_watermark=True,skill_id='example'))['job_ids'][0]
    sheet=q.api.execute(jid,'execution_sheet',{'group_id':'g'})
    assert sheet['mute_audio'] is True
    assert sheet['aigc_watermark'] is True
    assert sheet['skill_id']=='example'
    assert sheet['bound_prompt']=='<Picture 1>说“你好”，<Picture 2>听。'


def test_missing_image_review_stops_before_quote(queue):
    q,p=queue
    p['groups'][0]['reference_reviews']={};q.source.projects.save(p)
    jid=q.create(p['id'],['g'],config())['job_ids'][0]
    q._prepare(jid)
    assert q.service.projects.get(jid)['queue_state']['phase']=='needs_review'
    assert not q.service.projects.get(jid)['generation'].get('tasks')


def test_agent_can_review_existing_job_using_viewed_image_hashes(queue, monkeypatch):
    import json
    from dialogue_tool.agent import AgentManager
    from dialogue_tool.compshare import H3Client

    q, p = queue
    p['groups'][0]['reference_reviews'] = {}
    q.source.projects.save(p)
    jid = q.create(p['id'], ['g'], config())['job_ids'][0]
    q._prepare(jid)
    assert q.service.projects.get(jid)['queue_state']['phase'] == 'needs_review'
    monkeypatch.setattr('dialogue_tool.video_queue.queue_for', lambda service: q)
    monkeypatch.setattr(H3Client, 'request', lambda *args, **kwargs: {'available_points': 9999})
    agent = AgentManager(q.source)
    result = agent._tool(p['id'], {'tool': 'workbench_view_video_job', 'callId': 'view-job',
                                  'arguments': {'job_id': jid}}, ['g'])
    images = json.loads(result['contentItems'][0]['text'])['images']
    hashes = {'sha_' + image['side']: image['sha256'] for image in images}
    agent._tool(p['id'], {'tool': 'workbench_review_video_job', 'callId': 'review-job',
                         'arguments': {'job_id': jid, **hashes}}, ['g'])
    q._prepare(jid)
    job = q.service.projects.get(jid)
    assert job['queue_state']['phase'] == 'awaiting_approval'
    assert job['generation']['pending_approval']
    assert all(not task.get('submit_id') for task in job['generation']['tasks'].values())


def test_bad_source_group_does_not_block_next_group(queue):
    import copy
    q,p=queue
    bad=copy.deepcopy(p['groups'][0]);bad.update(id='bad',needs_review=True)
    p['groups'].insert(0,bad);q.source.projects.save(p)
    result=q.create(p['id'],['bad','g'],config())
    assert result['errors'][0]['group_id']=='bad'
    assert len(result['job_ids'])==1
    assert q.service.projects.get(result['job_ids'][0])['video_job']['source_group_id']=='g'


def test_dreamina_capabilities_and_job_validation(queue,monkeypatch):
    import json
    from dialogue_tool.generation import CanvasWorkflow
    from dialogue_tool.workbench import PostWorkbench
    q,p=queue
    catalog=json.loads((Path(__file__).parent/'fixtures/dreamina-video-models.json').read_text(encoding='utf-8'))
    monkeypatch.setattr(CanvasWorkflow,'models',lambda self:catalog)
    models=PostWorkbench(q.source).workbench_video_models()
    assert len(models)>=6
    assert next(m for m in models if m['model']=='seedance_2.5')['max']==30
    assert not any(m['model']=='seedance_pro_fast' for m in models)
    c=config();c.update(model='seedance_2.5',resolution='1080p');c.pop('use_context_ir')
    jid=q.create(p['id'],['g'],c)['job_ids'][0]
    job=q.service.projects.get(jid)
    sheet=q.api.execute(jid,'execution_sheet',{'group_id':'g'})
    q._validate_dreamina(job,sheet)
    job['generation']['resolution']='768P'
    with pytest.raises(ValueError,match='resolution'):q._validate_dreamina(job,sheet)


def test_queue_assembles_only_its_complete_pair_and_reuses_output(queue):
    import subprocess
    q,p=queue
    c={k:v for k,v in BASELINE['parameters'].items() if k!='mode'}
    c.update(common_prompt=BASELINE['global_prompt'],baseline_id=BASELINE['id'],auto_split_dialogue=True)
    batch=q.create(p['id'],['g'],c)
    for i,jid in enumerate(batch['job_ids']):
        job=q.service.projects.get(jid);out=Path(job['directory'])/'clip.mp4'
        subprocess.run([q.service.config['ffmpeg'],'-v','error','-y','-f','lavfi','-i',
            f"color=c={'red' if i==0 else 'blue'}:s=320x240:r=24:d=1",'-f','lavfi','-i',
            'sine=frequency=440:sample_rate=48000:duration=1','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac',str(out)],check=True,capture_output=True)
        job['generation']['tasks']={'g':{'status':'downloaded','output':str(out),'sha256':_sha(out)}}
        q.service.projects.save(job);q._save_state(jid,'downloaded')
        q._assemble(batch['batch_id'])
        if i==0:assert not any(item['kind']=='assembly' for item in q.library_items())
    assembly=next(item for item in q.library_items() if item['kind']=='assembly')
    assert assembly['group_id']=='g' and 1.9<assembly['duration']<2.2
    output=Path(assembly['path']);stamp=output.stat().st_mtime_ns
    for when,index in [('0.4',0),('1.4',2)]:
        raw=subprocess.run([q.service.config['ffmpeg'],'-v','error','-ss',when,'-i',str(output),'-frames:v','1','-f','rawvideo','-pix_fmt','rgb24','-'],check=True,capture_output=True).stdout
        assert raw[index]>200 and raw[2-index]<40
    q._assemble(batch['batch_id']);assert output.stat().st_mtime_ns==stamp
    assert all(item.get('assembly_id')==assembly['id'] for item in q.library_items() if item['kind']=='generation')
    output.unlink()
    assert not any(item.get('assembly_id') for item in q.library_items() if item['kind']=='generation')



def test_cross_project_batch_isolated_and_idempotent(queue):
    from dialogue_tool.workbench import PostWorkbench
    import copy
    q, p = queue
    other = copy.deepcopy(p)
    other.update(id='other', name='Other', directory=str(q.source.projects.root / 'other'))
    Path(other['directory']).mkdir()
    for side in ('a', 'b'):
        target = Path(other['directory']) / (side + '.png')
        target.write_bytes(Path(p['groups'][0]['repaired_' + side]).read_bytes())
        other['groups'][0]['repaired_' + side] = str(target)
    q.source.projects.save(other)
    host = PostWorkbench(q.source); host._video_queue = q
    scope = [{'project_id': p['id'], 'group_id': 'missing'}, {'project_id': p['id'], 'group_id': 'g'}, {'project_id': 'other', 'group_id': 'g'}]
    request_id = 'batch-regression-0001'
    result = host.workbench_video_create_batch(scope, config(), 'baseline', request_id)
    assert [r['status'] for r in result['items']] == ['failed', 'created', 'created']
    jobs = [q.service.projects.get(r['job_ids'][0]) for r in result['items'][1:]]
    assert [j['video_job']['source_project_id'] for j in jobs] == [p['id'], 'other']
    assert jobs[0]['video_job']['batch_id'] != jobs[1]['video_job']['batch_id']
    assert all(j['queue_state']['phase'] == 'preparing' for j in jobs)
    assert host.workbench_video_create_batch(scope, config(), 'baseline', request_id) == result
    assert len(q.service.projects.list()) == 2
    assert q.source.projects.get(p['id']) == p
    assert q.source.projects.get('other') == other
    with pytest.raises(ValueError, match='已变化'):
        host.workbench_video_create_batch(scope[:1], config(), 'baseline', request_id)


def test_interrupted_batch_does_not_replay_uncertain_group(queue):
    from dialogue_tool.workbench import PostWorkbench, read_json, save_json
    q, p = queue
    host = PostWorkbench(q.source); host._video_queue = q
    scope = [{'project_id': p['id'], 'group_id': 'g'}]
    request_id = 'batch-regression-0002'
    host.workbench_video_create_batch(scope, config(), 'baseline', request_id)
    path = q.source.projects.root / 'video-batches' / (request_id + '.json')
    saved = read_json(path); saved['items'][0]['status'] = 'creating'; save_json(path, saved)
    result = host.workbench_video_create_batch(scope, config(), 'baseline', request_id)
    assert result['items'][0]['status'] == 'creating'
    assert len(q.service.projects.list()) == 1


def test_batch_approval_prevalidates_total_and_stale_cards(queue, monkeypatch):
    from dialogue_tool.workbench import PostWorkbench
    q, p = queue
    host = PostWorkbench(q.source); host._video_queue = q
    rows = [{'id': str(i), 'phase': 'awaiting_approval', 'busy': False,
             'card': {'id': 'card'+str(i), 'ceiling': 60, 'available_credits': 100,
                      'provider': 'compshare', 'sufficient': True}} for i in range(2)]
    monkeypatch.setattr(q, 'rows', lambda: rows)
    seen = []
    monkeypatch.setattr(q, 'approve', lambda *a: seen.append(a) or True)
    cards = [{'job_id': r['id'], 'card_id': r['card']['id'], 'ceiling': 60} for r in rows]
    with pytest.raises(ValueError, match='总费用'):
        host.workbench_video_approve_batch(cards)
    assert not seen
    for row in rows: row['card']['available_credits'] = 200
    cards[1]['card_id'] = 'stale'
    with pytest.raises(ValueError, match='已变化'):
        host.workbench_video_approve_batch(cards)
    assert not seen
    cards[1]['card_id'] = 'card1'
    assert all(r['ok'] for r in host.workbench_video_approve_batch(cards))
    assert len(seen) == 2


def test_batch_workers_bound_preparation_without_blocking_other_lane(queue, monkeypatch):
    q, p = queue
    jobs = [q.create(p['id'], ['g'], config())['job_ids'][0] for _ in range(7)]
    monkeypatch.setattr(q, '_start', VideoQueue._start.__get__(q))
    release = threading.Event()
    started = []
    guard = threading.Lock()
    def hold(jid):
        with guard: started.append(jid)
        release.wait(3)
    try:
        for jid in jobs[:6]: q._start(jid, lambda jid=jid: hold(jid))
        deadline = time.time()+2
        while len(started)<4 and time.time()<deadline: time.sleep(.01)
        assert len(started)==4
        assert len(q.waiting)==2
        q._save_state(jobs[6], 'tracking')
        progressed = threading.Event()
        assert q._start(jobs[6], progressed.set)
        assert progressed.wait(1)
        assert len(started)==4
    finally:
        release.set()


def test_partial_creation_failure_is_not_offered_for_automatic_retry(queue, monkeypatch):
    from dialogue_tool.workbench import PostWorkbench
    q, p = queue
    host = PostWorkbench(q.source); host._video_queue = q
    original = host.workbench_video_create
    def create_then_disconnect(*args):
        original(*args)
        raise OSError('simulated result write failure')
    monkeypatch.setattr(host, 'workbench_video_create', create_then_disconnect)
    scope = [{'project_id': p['id'], 'group_id': 'g'}]
    result = host.workbench_video_create_batch(scope, config(), 'baseline', 'partial-creation-0001')
    assert result['items'][0]['status'] == 'creating'
    assert len(result['items'][0]['job_ids']) == 1
    assert host.workbench_video_create_batch(scope, config(), 'baseline', 'partial-creation-0001') == result
    assert len(q.service.projects.list()) == 1


@pytest.mark.parametrize('bad', ['trashed', 'missing'])
def test_removed_group_does_not_block_valid_agent_batch(queue, bad):
    from dialogue_tool.workbench import save_json
    q, p = queue
    save_json(q.source.projects.root / 'workbench-trash.json', {f"group:{p['id']}:trashed": {}})
    result = q.create(p['id'], [bad, 'g'], config())
    assert len(result['errors']) == 1 and result['errors'][0]['group_id'] == bad
    assert len(result['job_ids']) == 1
    assert q.service.projects.get(result['job_ids'][0])['video_job']['source_group_id'] == 'g'


def test_missing_job_does_not_leave_queue_busy(queue, monkeypatch):
    q, _ = queue
    monkeypatch.setattr(q, '_start', VideoQueue._start.__get__(q))
    with pytest.raises((ValueError, KeyError, FileNotFoundError)):
        q._start('missing', lambda: None)
    assert not q.active and not q.waiting


def test_status_write_failure_does_not_kill_workers(queue, monkeypatch):
    q, p = queue
    jobs = [q.create(p['id'], ['g'], config())['job_ids'][0] for _ in range(5)]
    monkeypatch.setattr(q, '_start', VideoQueue._start.__get__(q))
    def fail(*args): raise OSError('simulated disk unavailable')
    monkeypatch.setattr(q, '_save_state', fail)
    for jid in jobs[:4]:
        q._start(jid, fail)
        q.lanes['prepare'].join()
    progressed = threading.Event()
    q._start(jobs[4], progressed.set)
    assert progressed.wait(2), 'A failed status write exhausted the worker pool'

    failed_rows = [r for r in q.rows() if r['id'] in jobs[:4]]
    assert all(r['phase'] == 'attention' and '保存失败' in r['error'] and not r['busy'] for r in failed_rows)
    recovered = threading.Event()
    monkeypatch.setattr(q, '_save_state', VideoQueue._save_state.__get__(q))
    def prepare(jid):
        q._save_state(jid, 'needs_review')
        recovered.set()
    monkeypatch.setattr(q, '_prepare', prepare)
    assert q.refresh(jobs[0]) and recovered.wait(2)
    assert jobs[0] not in q.runtime_errors


def test_partial_split_creation_cannot_publish_incomplete_assembly(queue, monkeypatch):
    q, p = queue
    c = {k: v for k, v in BASELINE['parameters'].items() if k != 'mode'}
    c.update(common_prompt=BASELINE['global_prompt'], baseline_id=BASELINE['id'], auto_split_dialogue=True)
    batch = q.create(p['id'], ['g'], c)
    first = q.service.projects.get(batch['job_ids'][0])
    assert first['video_job']['part_count'] == 2
    monkeypatch.setattr(q.service.projects, 'list', lambda: [first])
    monkeypatch.setattr('dialogue_tool.segment_assembly.assemble_segments', lambda *args: pytest.fail('must not publish missing segment'))
    with pytest.raises(ValueError, match='分段任务记录不完整'):
        q._assemble(batch['batch_id'])


def test_source_checks_follow_current_hash_and_never_claim_repaired_is_clean(queue):
    from dialogue_tool.workbench import PostWorkbench
    q, p = queue
    host = PostWorkbench(q.source); host._video_queue = q
    scope = [{'project_id': p['id'], 'group_id': 'g'}]
    assert host.workbench_video_check(scope, True)['ready']  # No Agent needed for valid reviews.
    Image.new('RGB', (640, 480), 'blue').save(p['groups'][0]['repaired_a'])
    result = host.workbench_video_check(scope)
    assert not result['ready'] and result['missing'] == scope
    assert host.workbench_video_sources()[0]['groups'][0]['cleanup'] == 'pending'
    with pytest.raises(ValueError, match='Agent'):
        host.workbench_video_check(scope, True)
    assert q.source.projects.get(p['id']) == p


def test_check_starts_only_scoped_cleanup_and_tracks_the_exact_turn(queue):
    from dialogue_tool.workbench import PostWorkbench
    q, p = queue
    host = PostWorkbench(q.source)
    p['groups'][0]['reference_reviews'] = {}; q.source.projects.save(p)
    class Agent:
        lock = threading.RLock()
        models = [{'model': 'test', 'defaultReasoningEffort': 'low', 'supportedReasoningEfforts': [{'reasoningEffort':'low'}]}]
        session = {}
        def connection_state(self): return {'connected': True}
        def _session(self, pid): return self.session
        def send(self, *args):
            self.sent = args
            self.session['execution'] = {'started_at': 123, 'phase': '看图'}
        def state(self, pid): return {**self.session, 'busy': True}
    host._agent = Agent()
    result = host.workbench_video_check([{'project_id': p['id'], 'group_id': 'g'}], True)
    assert result['agent'] == {'project_id': p['id'], 'started_at': 123}
    assert host._agent.sent[-1] == {'projects': {p['id']: ['g']}, 'cleanup_only': True}
    assert host.workbench_video_check_status(p['id'], 123)['busy']
    assert not host.workbench_video_check_status(p['id'], 122)['busy']
    assert not q.service.projects.list()  # Checking cannot create a generation job.


def test_edit_invalidates_every_segment_preserves_snapshots_and_does_not_submit(queue, monkeypatch):
    from dialogue_tool.workbench import PostWorkbench
    from dialogue_tool.compshare import H3Client
    q, p = queue
    host = PostWorkbench(q.source); host._video_queue = q
    monkeypatch.setattr(H3Client, 'request', lambda *a, **k: {'available_points':9999})
    cfg = host.workbench_video_preset()
    batch = q.create(p['id'], ['g'], cfg, creation_id='edit-this-batch')
    for jid in batch['job_ids']: q._prepare(jid)
    before = [q.service.projects.get(jid) for jid in batch['job_ids']]
    q.runtime_errors[batch['job_ids'][0]] = 'earlier status-write failure'
    result = host.workbench_video_draft(batch['batch_id'], 'edit')
    assert not q.runtime_errors
    assert not q.refresh(batch['job_ids'][0])
    assert result['config'] == cfg
    assert result['scope'] == [{'project_id': p['id'], 'group_id': 'g'}]
    for old in before:
        new = q.service.projects.get(old['id'])
        assert new['queue_state']['phase'] == 'canceled'
        assert 'pending_approval' not in new['generation']
        assert new['groups'] == old['groups']
        assert new['generation']['global_prompt'] == old['generation']['global_prompt']
        for key, task in old['generation']['tasks'].items():
            assert new['generation']['tasks'][key] == {**task, 'status': 'canceled'}
        card = old['generation']['pending_approval']
        with pytest.raises(ValueError, match='变化'):
            q.approve(old['id'], card['id'], card['ceiling'])
    assert all(r['category'] == 'failed' and r['status_label'] == '已取消' for r in host.workbench_tasks()['items'])
    assert q.source.projects.get(p['id']) == p


@pytest.mark.parametrize('blocker', ['active', 'submitting', 'submit_id', 'task_id', 'unknown'])
def test_draft_operations_prevalidate_whole_video_before_any_change(queue, blocker):
    from dialogue_tool.workbench import PostWorkbench
    q, p = queue
    host = PostWorkbench(q.source); host._video_queue = q
    batch = q.create(p['id'], ['g'], host.workbench_video_preset())
    last = q.service.projects.get(batch['job_ids'][-1])
    if blocker == 'active': q.active.add(last['id'])
    elif blocker == 'submitting': last['queue_state']['phase'] = 'submitting'
    else: last['generation']['tasks'] = {'g': {'status':'submission_unknown' if blocker=='unknown' else 'draft', **({blocker:'original'} if blocker!='unknown' else {})}}
    q.service.projects.save(last)
    before = q.service.projects.list()
    with pytest.raises(ValueError, match='不能修改'):
        host.workbench_video_draft(batch['batch_id'], 'remove')
    assert q.service.projects.list() == before


def test_scoped_payment_rejects_other_batch_missing_segments_and_incomplete_cards(queue, monkeypatch):
    from dialogue_tool.workbench import PostWorkbench
    q, p = queue
    host = PostWorkbench(q.source); host._video_queue = q
    rows = [dict(id=str(i), creation_id='current' if i<2 else 'other',batch_id='group1' if i<2 else 'group2',part=i+1 if i<2 else 1,part_count=2 if i<2 else 1,phase='awaiting_approval',busy=False,
                 card=dict(id='card'+str(i),ceiling=155,available_credits=9999,provider='compshare',sufficient=True)) for i in range(3)]
    monkeypatch.setattr(q, 'rows', lambda: rows)
    seen=[];monkeypatch.setattr(q,'approve',lambda *a:seen.append(a) or True)
    cards=[dict(job_id=r['id'],card_id=r['card']['id'],ceiling=155) for r in rows]
    for invalid in (cards,cards[:1],cards[1:2]):
        with pytest.raises(ValueError,match='批次范围'):
            host.workbench_video_approve_batch(invalid,'current')
    rows[1]['phase']='canceled'
    with pytest.raises(ValueError,match='分段不完整'):
        host.workbench_video_approve_batch(cards[:1],'current')
    assert not seen
    rows[1]['phase']='awaiting_approval'
    assert all(r['ok'] for r in host.workbench_video_approve_batch(cards[:2],'current'))
    assert [v[0] for v in seen] == ['0','1']


def paid_job(q, p, status='running'):
    jid = q.create(p['id'], ['g'], config())['job_ids'][0]
    job = q.service.projects.get(jid)
    job['generation']['tasks'] = {'g': {'group_id':'g', 'submit_id':'original-submit', 'task_id':'original-platform', 'status':status}}
    job['queue_state']['phase'] = 'tracking'
    q.service.projects.save(job)
    return jid


@pytest.mark.parametrize('failed_command', ['get', 'download'])
def test_dreamina_download_timeout_survives_full_error_chain(queue, monkeypatch, tmp_path, failed_command):
    import json
    import subprocess
    from types import SimpleNamespace
    from dialogue_tool import generation
    q, p = queue
    jid = paid_job(q, p, 'completed')
    job = q.service.projects.get(jid)
    job['generation'].update(model='seedance_2.0_fast_vip', project_id='original-project')
    job['generation']['tasks']['g']['resource_id'] = 'original-resource'
    q.service.projects.save(job)
    monkeypatch.setitem(q.service.config, 'dreamina_canvas', 'test-cli')
    monkeypatch.setitem(q.service.config, 'video_archive', str(tmp_path / 'archive'))
    monkeypatch.setattr(q, '_ensure_scheduler', lambda: None)
    monkeypatch.setattr(q, '_assemble', lambda _: None)
    calls = []
    failed = [False]
    def command(argv, **kwargs):
        assert 'resource' in argv and not any(v in argv for v in ('node', 'run', 'confirm'))
        verb = argv[argv.index('resource') + 1]
        calls.append(verb)
        if verb == failed_command and not failed[0]:
            failed[0] = True
            raise subprocess.TimeoutExpired(argv, 120)
        data = {}
        if verb == 'download':
            output = Path(argv[argv.index('--output') + 1]) / 'video.mp4'
            output.write_bytes(b'downloaded-video')
            data = {'path': str(output), 'size': output.stat().st_size, 'sha256': _sha(output)}
        return SimpleNamespace(returncode=0, stdout=json.dumps(data), stderr='')
    monkeypatch.setattr(generation.subprocess, 'run', command)
    monkeypatch.setattr(generation, 'probe', lambda *a: {'format': {'duration': 15}, 'streams': [
        {'codec_type': 'video', 'width': 128, 'height': 72}, {'codec_type': 'audio'}]})
    q._watch(jid)
    retry = q.service.projects.get(jid)
    assert retry['queue_state']['phase'] == 'retry_wait'
    assert retry['tracking']['auto_follow'] and retry['tracking']['retry_counts']['download'] == 1
    monkeypatch.setattr(q, '_start', lambda jid, op: op() or True)
    monkeypatch.setattr('dialogue_tool.video_queue.time.time', lambda: retry['tracking']['next_attempt_at'] + 1)
    q._run_due()
    done = q.service.projects.get(jid)
    assert done['queue_state']['phase'] == 'downloaded' and not done['tracking']['auto_follow']
    assert done['generation']['tasks']['g']['submit_id'] == 'original-submit'
    assert Path(done['generation']['tasks']['g']['archive_output']).is_file()


def test_temporary_query_failure_is_scheduled_and_success_downloads_without_resubmission(queue, monkeypatch):
    from dialogue_tool.h3_errors import H3Error
    q,p=queue; jid=paid_job(q,p)
    now=[1000.0];monkeypatch.setattr('dialogue_tool.video_queue.time.time',lambda:now[0])
    monkeypatch.setattr(q,'_ensure_scheduler',lambda:None)
    monkeypatch.setattr(q.api,'approve_submission',lambda *a:pytest.fail('no new paid request'))
    calls=[]
    def execute(identifier, action):
        calls.append(action)
        if calls==['refresh_results']: raise H3Error('timeout','temporary network failure',retryable=True)
        job=q.service.projects.get(identifier);task=job['generation']['tasks']['g']
        if action=='refresh_results':task['status']='completed'
        elif action=='download_results':
            output=Path(job['directory'])/'result.mp4';output.write_bytes(b'downloaded')
            task.update(status='downloaded',output=str(output))
        else:pytest.fail(action)
        q.service.projects.save(job)
    monkeypatch.setattr(q.api,'execute',execute)
    monkeypatch.setattr(q,'_assemble',lambda bid:None)
    q._watch(jid)
    job=q.service.projects.get(jid)
    assert job['queue_state']['phase']=='retry_wait'
    assert job['tracking']['next_attempt_at']==1005 and job['tracking']['retry_counts']['query']==1
    assert q.rows()[0]['tracking']['stale'] and not q.rows()[0]['platform_complete']
    starts=[]
    monkeypatch.setattr(q,'_start',lambda jid,op: starts.append(jid) or op() or True)
    q._run_due();assert not starts
    now[0]=1005;q._run_due()
    assert starts==[jid] and calls==['refresh_results','refresh_results','download_results']
    job=q.service.projects.get(jid)
    assert job['queue_state']['phase']=='downloaded' and not job['tracking']['auto_follow']
    assert job['tracking']['last_checked_at']==1005
    assert job['generation']['tasks']['g']['submit_id']=='original-submit'
    assert q.rows()[0]['complete_ready'] and q.rows()[0]['local_available']


def test_download_retries_keep_platform_success_and_growing_budget(queue,monkeypatch):
    from dialogue_tool.h3_errors import H3Error
    q,p=queue;jid=paid_job(q,p)
    monkeypatch.setattr(q,'_ensure_scheduler',lambda:None)
    calls=[]
    def execute(identifier,action):
        calls.append(action)
        if action=='refresh_results':
            job=q.service.projects.get(identifier);job['generation']['tasks']['g']['status']='completed';q.service.projects.save(job)
        else:raise H3Error('connection_failed','download interrupted',retryable=True)
    monkeypatch.setattr(q.api,'execute',execute)
    q._watch(jid);q._watch(jid);q._watch(jid)
    assert calls==['refresh_results','download_results','download_results','download_results']
    row=q.rows()[0]
    assert row['platform_complete'] and not row['local_available']
    assert row['tracking']['last_checked_at'] and not row['tracking']['stale']
    assert row['tracking']['retry_counts']['download']==3 and row['phase']=='retry_wait'
    from dialogue_tool.workbench import PostWorkbench
    host=PostWorkbench(q.source);host._video_queue=q
    item=host.workbench_tasks()['items'][0]
    assert item['category']=='running' and item['status_label']=='正在自动重试下载'
    assert len(calls)==4  # History listing never queries or retries.


def test_retry_budget_and_deadline_survive_restart_and_exhaustion(queue,monkeypatch):
    from dialogue_tool.h3_errors import H3Error
    from dialogue_tool.video_queue import MAX_RETRIES
    q,p=queue;jid=paid_job(q,p)
    monkeypatch.setattr(VideoQueue,'_ensure_scheduler',lambda self:None)
    exc=H3Error('timeout','network unavailable',retryable=True)
    for _ in range(MAX_RETRIES):q._follow_failure(jid,'query',exc)
    before=q.service.projects.get(jid)['tracking']
    other=VideoQueue(q.source)
    starts=[];monkeypatch.setattr(other,'_start',lambda *a:starts.append(a[0]) or True)
    other.resume()
    assert not starts and other.service.projects.get(jid)['tracking']==before
    other._follow_failure(jid,'query',exc)
    after=other.service.projects.get(jid)
    assert after['queue_state']['phase']=='attention' and after['tracking']['retry_exhausted']
    assert not after['tracking']['auto_follow'] and after['tracking']['next_attempt_at'] is None
    other.resume();other._run_due();assert not starts
    other.stop.set()


def test_permanent_error_and_unknown_h3_submission_do_not_auto_retry(queue,monkeypatch):
    from dialogue_tool.h3_errors import H3Error
    q,p=queue;jid=paid_job(q,p)
    monkeypatch.setattr(q,'_ensure_scheduler',lambda:None)
    monkeypatch.setattr(q.api,'execute',lambda *a:(_ for _ in ()).throw(H3Error('invalid_key','update key',retryable=False)))
    q._watch(jid)
    assert q.service.projects.get(jid)['queue_state']['phase']=='attention'
    assert not q.service.projects.get(jid)['tracking']['auto_follow']
    job=q.service.projects.get(jid);job['generation']['tasks']['g'].pop('task_id');q.service.projects.save(job)
    monkeypatch.setattr(q.api,'execute',lambda *a:pytest.fail('unknown H3 identity cannot advance audio or submit'))
    q._watch(jid)
    assert '任务 ID' in q.service.projects.get(jid)['queue_state']['error']


def test_healthy_slow_generation_keeps_polling_without_wait_limit(queue,monkeypatch):
    q,p=queue;jid=paid_job(q,p)
    monkeypatch.setattr(q,'_ensure_scheduler',lambda:None)
    now=[1000.0];monkeypatch.setattr('dialogue_tool.video_queue.time.time',lambda:now[0])
    calls=[];monkeypatch.setattr(q.api,'execute',lambda jid,action:calls.append(action))
    q._watch(jid);now[0]+=7200;q._watch(jid)
    job=q.service.projects.get(jid)
    assert job['queue_state']['phase']=='tracking' and job['tracking']['next_attempt_at']==8210
    assert calls==['refresh_results','refresh_results']
    assert not q.rows()[0]['tracking']['stale']
    now[0]+=100;assert q.rows()[0]['tracking']['stale']


def test_retry_wait_does_not_occupy_worker_slot(queue,monkeypatch):
    from dialogue_tool.h3_errors import H3Error
    q,p=queue;jid=paid_job(q,p)
    monkeypatch.setattr(q,'_ensure_scheduler',lambda:None)
    monkeypatch.setattr(q,'_start',VideoQueue._start.__get__(q))
    monkeypatch.setattr(q.api,'execute',lambda *a:(_ for _ in ()).throw(H3Error('timeout','temporary timeout',retryable=True)))
    q._start(jid,lambda:q._watch(jid));q.lanes['tracking'].join()
    assert jid not in q.active and jid not in q.waiting
    assert q.service.projects.get(jid)['queue_state']['phase']=='retry_wait'


def wait_for_queue(q, timeout=15):
    deadline = time.monotonic() + timeout
    while q.active and time.monotonic() < deadline:
        time.sleep(.01)
    assert not q.active, f'Workers did not finish: {q.active}'


@pytest.mark.parametrize('count', [24, 48])
def test_batch_stress_mixed_failures_and_restart(queue, monkeypatch, count):
    """Real workers/storage; simulated provider failures, never paid requests."""
    from collections import Counter
    from concurrent.futures import ThreadPoolExecutor
    from dialogue_tool.h3_errors import H3Error
    q, p = queue
    jobs = [paid_job(q, p) for _ in range(count)]
    modes = {jid: i % 8 for i, jid in enumerate(jobs)}
    for jid in jobs:
        job = q.service.projects.get(jid)
        job['generation']['tasks']['g'].update(submit_id='submit-' + jid, task_id='task-' + jid)
        q.service.projects.save(job)
    calls, inflight, peak = Counter(), set(), [0]
    guard = threading.Lock()

    def execute(jid, action):
        assert action in ('refresh_results', 'download_results'), 'Unexpected mutation/payment'
        with guard:
            assert jid not in inflight, 'Same job ran twice concurrently'
            inflight.add(jid)
            peak[0] = max(peak[0], len(inflight))
            calls[jid, action] += 1
            attempt = calls[jid, action]
        try:
            time.sleep(.004)
            job = q.service.projects.get(jid)
            task = job['generation']['tasks']['g']
            mode = modes[jid]
            if action == 'refresh_results':
                if mode == 0 and attempt <= 2:
                    raise H3Error('timeout', 'simulated query timeout', retryable=True)
                if mode == 3:
                    raise H3Error('invalid_request', 'simulated permanent failure')
                task['status'] = ('failed' if mode == 2 else
                                  'running' if mode == 4 and attempt <= 2 else 'completed')
            else:
                if mode == 1 and attempt <= 2:
                    raise H3Error('connection_interrupted', 'simulated download interruption', retryable=True)
                output = Path(job['directory']) / 'test-download.mp4'
                output.write_bytes(jid.encode())
                task.update(status='downloaded', output=str(output), sha256=_sha(output))
            q.service.projects.save(job)
        finally:
            with guard:
                inflight.remove(jid)

    monkeypatch.setattr('dialogue_tool.video_queue.RETRY_DELAYS', (.01, .02))
    monkeypatch.setattr('dialogue_tool.video_queue.POLL_SECONDS', .01)
    monkeypatch.setattr(VideoQueue, '_ensure_scheduler', lambda self: None)
    monkeypatch.setattr(q, '_start', VideoQueue._start.__get__(q))
    monkeypatch.setattr(q.api, 'execute', execute)
    monkeypatch.setattr(q.api, 'approve_submission', lambda *a: pytest.fail('No paid replay'))
    monkeypatch.setattr(q, '_assemble', lambda bid: None)
    for jid in jobs:
        q._start(jid, lambda jid=jid: q._watch(jid))
    wait_for_queue(q)
    saved = {jid: q.service.projects.get(jid)['tracking'] for jid in jobs}
    q.stop.set()

    resumed = VideoQueue(q.source)
    monkeypatch.setattr(resumed.api, 'execute', execute)
    monkeypatch.setattr(resumed.api, 'approve_submission', lambda *a: pytest.fail('No paid replay'))
    monkeypatch.setattr(resumed, '_assemble', lambda bid: None)
    try:
        assert all(resumed.service.projects.get(jid)['tracking'] == saved[jid] for jid in jobs)
        resumed.resume()
        deadline = time.monotonic() + 15
        with ThreadPoolExecutor(max_workers=3) as pool:
            while time.monotonic() < deadline:
                # Overlapping timer ticks must still dispatch each job at most once.
                for future in [pool.submit(resumed._run_due) for _ in range(3)]:
                    future.result()
                wait_for_queue(resumed)
                if all(r['phase'] in ('attention', 'downloaded') for r in resumed.rows()):
                    break
                time.sleep(.02)
        rows = resumed.rows()
        assert len(rows) == count and 1 < peak[0] <= 8
        assert sum(r['phase'] == 'downloaded' for r in rows) == count * 6 // 8
        for row in rows:
            jid = row['id']
            assert row['submit_id'] == 'submit-' + jid and row['task_id'] == 'task-' + jid
            assert not row['busy'] and not row['tracking']['auto_follow']
            if modes[jid] in (2, 3):
                assert row['phase'] == 'attention' and calls[jid, 'refresh_results'] == 1
                assert calls[jid, 'download_results'] == 0
            else:
                assert row['phase'] == 'downloaded' and row['complete_ready']
                assert Path(row['output']).read_bytes() == jid.encode()
                assert calls[jid, 'download_results'] == (3 if modes[jid] == 1 else 1)
        assert q.source.projects.get(p['id']) == p
        before = calls.copy()
        resumed.resume()
        wait_for_queue(resumed)
        resumed.rows(); resumed.library_items()
        assert calls == before, 'Restart/listing replayed a completed provider operation'
    finally:
        resumed.stop.set()


def test_batch_approval_concurrent_double_click_submits_each_job_once(queue, monkeypatch):
    from collections import Counter
    from concurrent.futures import ThreadPoolExecutor
    from dialogue_tool.workbench import PostWorkbench
    q, p = queue
    host = PostWorkbench(q.source); host._video_queue = q
    cards = []
    for i in range(24):
        jid = q.create(p['id'], ['g'], config())['job_ids'][0]
        job = q.service.projects.get(jid)
        job['generation']['pending_approval'] = {'id': 'card-' + jid, 'ceiling': 1,
            'provider': 'compshare', 'available_credits': 100, 'sufficient': True}
        job['queue_state']['phase'] = 'awaiting_approval'
        q.service.projects.save(job)
        cards.append({'job_id': jid, 'card_id': 'card-' + jid, 'ceiling': 1})
    calls, gate, guard = [], threading.Event(), threading.Lock()
    def submit(jid, *args):
        with guard: calls.append(jid)
        assert gate.wait(10)
    monkeypatch.setattr(q.api, 'approve_submission', submit)
    monkeypatch.setattr(q, '_watch', lambda jid: None)
    monkeypatch.setattr(q, '_start', VideoQueue._start.__get__(q))
    def click():
        try:
            return host.workbench_video_approve_batch(cards)
        except ValueError:
            return None
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: click(), range(4)))
        successes = [r for r in results if r is not None]
        assert len(successes) == 1 and all(item['ok'] for item in successes[0])
    finally:
        gate.set()
    wait_for_queue(q)
    assert Counter(calls) == Counter(c['job_id'] for c in cards)
