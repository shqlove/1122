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
