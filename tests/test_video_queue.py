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

