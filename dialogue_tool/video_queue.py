"""Independent video jobs: immutable inputs, one quote and submission identity per clip."""
import copy
import logging
import queue
import shutil
import threading
import time
import uuid
from pathlib import Path

from .service import Service
from .workflow_api import WorkflowAPI
from .compshare import channel_for
from .generation import _sha
from .projects import original_frame, project_asset
from .project_run import workflow_lock
from .h3_baseline import apply_baseline, review_valid
from .dialogue_segments import enabled, split_prompts

_queues = {}
_queues_lock = threading.RLock()


def queue_for(service):
    key = str(service.projects.root)
    with _queues_lock:
        queue = _queues.get(key)
        if queue is None or queue.stop.is_set():
            queue = _queues[key] = VideoQueue(service)
            queue.resume()
        return queue


class VideoQueue:
    def __init__(self, source_service):
        self.source = source_service
        self.service = Service({**source_service.config, 'data': str(source_service.projects.root / 'video-jobs')})
        self.api = WorkflowAPI(self.service)
        self.lock = threading.RLock()
        self.active = set()
        self.waiting = set()
        self.runtime_errors = {}
        self.lanes = {}
        self.stop = threading.Event()
        self.assembly_lock = threading.Lock()

    def _save_state(self, jid, phase, error=None):
        p = self.service.projects.get(jid)
        p['queue_state'] = {'phase': phase, 'error': error, 'updated_at': time.time()}
        self.service.projects.save(p)
        self.runtime_errors.pop(jid, None)

    def _start(self, jid, operation):
        with self.lock:
            if jid in self.active:
                return False
            job = self.service.projects.get(jid)
            self.active.add(jid)
            self.waiting.add(jid)
            phase = job.get('queue_state', {}).get('phase')
            lane = 'tracking' if phase in ('submitting', 'tracking') or any(t.get('submit_id') for t in job.get('generation', {}).get('tasks', {}).values()) else 'prepare'
            if lane not in self.lanes:
                pending = self.lanes[lane] = queue.Queue()
                def worker():
                    while not self.stop.is_set():
                        try: task = pending.get(timeout=.2)
                        except queue.Empty: continue
                        try: task()
                        except Exception:
                            # A failed status write must not permanently remove a worker.
                            logging.getLogger(__name__).exception('Video worker could not record task failure')
                        finally: pending.task_done()
                for _ in range(8 if lane == 'tracking' else 4):
                    threading.Thread(target=worker, daemon=True).start()
        def work():
            with self.lock:
                self.waiting.discard(jid)
            try:
                if not self.stop.is_set():
                    operation()
            except Exception as exc:
                try:
                    self._save_state(jid, 'attention', str(exc))
                except Exception as save_error:
                    self.runtime_errors[jid] = f'{exc}；错误状态保存失败：{save_error}'
                    raise
            finally:
                with self.lock:
                    self.active.discard(jid)
        self.lanes[lane].put(work)
        return True

    def _copy_group(self, source, group, folder, voice=None):
        result = copy.deepcopy(group)
        result.update(selected=True)
        for side in ('a', 'b'):
            original = project_asset(source, group['repaired_' + side]) if group.get('repaired_' + side) else original_frame(source, group['frame_' + side], self.source.config['ffmpeg'], self.source.config['ffprobe'])
            digest = _sha(original)
            target = folder / (side + '-' + digest + original.suffix)
            shutil.copy2(original, target)
            if _sha(target) != digest:
                raise ValueError('参考图片在复制期间变化，请重新选择')
            result['repaired_' + side] = str(target)
            result.pop('mask_' + side, None)
        bindings = result.setdefault('generation_bindings', {})
        source_voice = voice or bindings.get('voice_path')
        if source_voice:
            source_voice = Path(source_voice)
            digest = _sha(source_voice)
            target = folder / ('voice-' + digest + source_voice.suffix)
            shutil.copy2(source_voice, target)
            if _sha(target) != digest:
                raise ValueError('音色在复制期间变化，请重新选择')
            bindings.update(voice_path=str(target), voice_name=source_voice.name)
        return result

    def create(self, source_id, group_ids, config, voice_source='baseline', creation_id=None):
        if voice_source not in ('baseline', 'group'):
            raise ValueError('请选择成功模板音色或图片组音色')
        if config.get('baseline_id') == 'h3-first-v1' and voice_source != 'baseline':
            raise ValueError('成功模板使用原始参考音色，请先切换手动配置')
        from .batch_workbench import trash_records, group_key
        trash = trash_records(self.source)
        source = self.source.projects.get(source_id)
        groups = {g['id']: g for g in source['groups']}
        if not group_ids or len(set(group_ids)) != len(group_ids):
            raise ValueError('请选择有效的来源图片组')
        if len(group_ids) > 1:
            result = {'job_ids': [], 'batch_ids': [], 'errors': []}
            for gid in group_ids:
                try:
                    created = self.create(source_id, [gid], config, voice_source, creation_id)
                    result['job_ids'].extend(created['job_ids'])
                    result['batch_ids'].append(created['batch_id'])
                except Exception as exc:
                    result['errors'].append({'group_id': gid, 'message': str(exc)})
            return result
        if group_key(source_id, group_ids[0]) in trash:
            raise ValueError('图片组已移入回收站，请先恢复')
        if group_ids[0] not in groups:
            raise ValueError('请选择有效的来源图片组')
        batch_id = uuid.uuid4().hex
        batch_folder = self.service.projects.root / batch_id
        batch_folder.mkdir()
        jobs = []
        # No source-project write lock: analysis and older submissions own other state.
        for gid in group_ids:
            group = groups[gid]
            if group.get('needs_review'):
                raise ValueError('来源图片组尚未确认')
            staging_id = uuid.uuid4().hex
            folder = batch_folder / staging_id
            folder.mkdir()
            p = {k: copy.deepcopy(source.get(k)) for k in ('source','source_sha256','source_size','source_mtime_ns','scan','analysis_filter')}
            p.update(id=staging_id, name=source['name'], directory=str(folder), phase='review', groups=[], generation={
                'provider': channel_for(config['model']), 'channel_selected': True, 'model': config['model'], 'baseline_id': 'custom'})
            if config.get('baseline_id') == 'h3-first-v1':
                apply_baseline(p)
            from .h3_baseline import ROOT as baseline_root, BASELINE
            voice = baseline_root / 'voice.mp3' if voice_source == 'baseline' else None
            if voice and _sha(voice) != BASELINE['audio_sha256']:
                raise ValueError('成功模板音色校验失败')
            p['groups'] = [self._copy_group(source, group, folder, voice)]
            self.service.projects.save(p)
            self.api.execute(staging_id, 'configure_generation', config)
            p = self.service.projects.get(staging_id)
            cfg = p['generation']
            prompts = split_prompts(cfg['global_prompt']) if enabled(cfg) else [cfg['global_prompt']]
            for index, prompt in enumerate(prompts, 1):
                jid = staging_id if index == 1 else uuid.uuid4().hex
                job = copy.deepcopy(p)
                job_folder = batch_folder / jid
                if index != 1:
                    job_folder.mkdir()
                    job['groups'] = [self._copy_group(p, p['groups'][0], job_folder)]
                job.update(id=jid, directory=str(job_folder), created_at=time.time(), video_job={
                    'batch_id': batch_id, 'creation_id': creation_id, 'source_project_id': source_id, 'source_project_name': source['name'],
                    'source_group_id': gid, 'group_number': next(i for i,g in enumerate(source['groups'],1) if g['id']==gid),
                    'part': index, 'part_count': len(prompts), 'requested_config': config, 'voice_source': voice_source})
                job['generation'].update(global_prompt=prompt, baseline_id='custom')
                job['generation'].pop('dialogue_policy', None)
                job['generation'].pop('dialogue_plans', None)
                job['generation'].pop('baseline_voice_path', None)
                if channel_for(config['model']) == 'compshare':
                    job['generation']['verbatim_prompt'] = config.get('verbatim_prompt', bool(config.get('preserve_prompt', True)))
                job['queue_state'] = {'phase': 'preparing', 'updated_at': time.time()}
                self.service.projects.save(job)
                jobs.append(jid)
        for jid in jobs:
            self._start(jid, lambda jid=jid: self._prepare(jid))
        return {'job_ids': jobs, 'batch_id': batch_id}

    def _prepare(self, jid):
        with workflow_lock(Path(self.service.projects.get(jid)['directory'])):
            p = self.service.projects.get(jid)
            gid = p['groups'][0]['id']
            check = self.api.execute(jid, 'generation_preflight', {'group_ids': [gid]})
            if not check['ready']:
                raise ValueError('；'.join(e['message'] for e in check['errors']))
            refs = check['execution_sheets'][0]['references']
            if any(r['type'] == 'image' and not review_valid(p['groups'][0], r) for r in refs):
                self._save_state(jid, 'needs_review', '请检查本条参考图片是否已清理水印和叠加字幕')
                return
            if channel_for(p['generation']['model']) == 'dreamina':
                self._validate_dreamina(p, check['execution_sheets'][0])
            self.api.execute(jid, 'prepare_batch', {'group_ids': [gid]})
            self.api.execute(jid, 'quote_batch')
            self.api.execute(jid, 'request_submission')
            self._save_state(jid, 'awaiting_approval')

    def _validate_dreamina(self, p, sheet):
        cfg = p['generation']
        models = self.api.flow.canvas.models()['items']
        model = next((m for m in models if m['model'] == cfg['model']), None)
        mode = next((m for m in (model or {}).get('modes', []) if m['name'] == 'm2v'), None)
        if not mode:
            raise ValueError('即梦当前模型不支持全能参考，请从可用模型中选择')
        for flag in mode['flags']:
            key = flag['flag'].removeprefix('--')
            if key not in ('ratio', 'resolution', 'duration'):
                continue
            value = cfg[key]
            if 'values' in flag and value not in flag['values'] or 'min' in flag and not flag['min'] <= value <= flag['max']:
                raise ValueError('所选即梦模型不支持该' + key + '参数')
        limit = next((f['maxLength'] for f in mode['flags'] if f['flag']=='--prompt'), 4000)
        if len(sheet['prompt']) > limit:
            raise ValueError(f'所选即梦模型的提示词超过{limit}字限制')
        supported = {t['mediaType'] for t in mode['references']['types']}
        if any(r['type'] not in supported for r in sheet['references']):
            raise ValueError('所选即梦模型不支持当前参考素材类型')

    def review(self, jid):
        with self.lock:
            if jid in self.active:
                raise ValueError('本条正在处理，请稍后操作')
        p = self.service.projects.get(jid)
        if p['queue_state']['phase'] != 'needs_review':
            raise ValueError('本条不在图片检查阶段')
        gid = p['groups'][0]['id']
        sheet = self.api.execute(jid, 'execution_sheet', {'group_id': gid})
        for ref in sheet['references']:
            if ref['type'] == 'image':
                self.api.execute(jid, 'record_reference_review', dict(group_id=gid,side=ref['side'],sha256=ref['sha256'],clean=True))
        self._save_state(jid, 'preparing')
        return self._start(jid, lambda: self._prepare(jid))

    def approve(self, jid, card_id, ceiling):
        with self.lock:
            if jid in self.active:
                raise ValueError('本条已经在处理，请勿重复提交')
            p = self.service.projects.get(jid)
            card = p.get('generation', {}).get('pending_approval', {})
            if p.get('queue_state', {}).get('phase') != 'awaiting_approval' or card.get('id') != card_id or card.get('ceiling') != ceiling:
                raise ValueError('本条费用卡已变化，请刷新后核对')
            self._save_state(jid, 'submitting')
            def submit():
                try:
                    self.api.approve_submission(jid, card_id, ceiling)
                except Exception:
                    # A persisted submit_id remains query-only. Never replay a paid request.
                    if not any(t.get('submit_id') for t in self.service.projects.get(jid)['generation'].get('tasks', {}).values()):
                        raise
                self._watch(jid)
            return self._start(jid, submit)

    def refresh(self, jid):
        p = self.service.projects.get(jid)
        tasks = p.get('generation', {}).get('tasks', {})
        if any(t.get('submit_id') for t in tasks.values()):
            return self._start(jid, lambda: self._watch(jid))
        if jid in self.runtime_errors or p['queue_state']['phase'] in ('attention', 'awaiting_approval'):
            return self._start(jid, lambda: self._prepare(jid))
        return False

    def _watch(self, jid):
        self._save_state(jid, 'tracking')
        for _ in range(180):
            if self.stop.is_set():
                return
            with workflow_lock(Path(self.service.projects.get(jid)['directory'])):
                self.api.execute(jid, 'refresh_results')
                self.api.execute(jid, 'download_results')
                p = self.service.projects.get(jid)
                states = [t.get('status') for t in p['generation']['tasks'].values()]
            if states and all(s == 'downloaded' for s in states):
                self._save_state(jid, 'downloaded')
                self._assemble(p['video_job']['batch_id'])
                return
            if not states or any(s in ('failed', 'canceled', 'completed_no_resource', 'requires_manual_review') for s in states):
                self._save_state(jid, 'attention', '平台任务未成功完成，请查看原任务；不会自动重新付费')
                return
            if self.stop.wait(10):
                return
        self._save_state(jid, 'attention', '自动等待已结束，可查询本条原任务继续')

    def _assemble(self, batch_id):
        from .segment_assembly import assemble_segments
        with self.assembly_lock:
            jobs = [p for p in self.service.projects.list() if p.get('video_job', {}).get('batch_id') == batch_id]
            parent_id = 'assembly-' + batch_id
            try:
                parent = self.service.projects.get(parent_id)
            except ValueError:
                parent = {'id': parent_id, 'name': '完整原片', 'directory': str(self.service.projects.root / batch_id),
                          'phase': 'review', 'groups': [], 'generation': {'tasks': {}, 'dialogue_plans': {}}, 'video_assembly': batch_id}
            for job in jobs:
                meta = job['video_job']
                if meta['part_count'] < 2:
                    continue
                task = next(iter(job['generation'].get('tasks', {}).values()), {})
                parent['generation']['tasks'][job['id']] = task
                gid = meta['source_group_id']
                parts = sorted([p for p in jobs if p['video_job']['source_group_id']==gid], key=lambda p:p['video_job']['part'])
                plan = parent['generation']['dialogue_plans'].setdefault(gid, {'source_group_id': gid, 'assembly': {}})
                plan['parts'] = [{'group_id':p['id'], 'index':p['video_job']['part']} for p in parts]
                plan['assembly']['order'] = [p['id'] for p in parts]
                plan['origin'] = meta
                plan['config'] = {k:job['generation'].get(k) for k in ('model','duration','resolution','ratio','use_context_ir')}
            self.service.projects.save(parent)
            assemble_segments(self.service, parent)

    def resume(self):
        for p in self.service.projects.list():
            if not p.get('video_job'):
                continue
            phase = p.get('queue_state', {}).get('phase')
            if phase in ('submitting', 'tracking'):
                if not self.refresh(p['id']):
                    self._save_state(p['id'], 'attention', '提交过程曾中断，未记录提交ID；请重新核对报价，不会自动付费')
            elif phase == 'preparing':
                self._save_state(p['id'], 'attention', '准备过程曾中断，请检查本条后继续报价')
            elif phase == 'downloaded':
                self._start(p['id'], lambda p=p: self._assemble(p['video_job']['batch_id']))

    def rows(self):
        rows = []
        for p in self.service.projects.list():
            if not p.get('video_job'):
                continue
            cfg = p['generation']
            task = next(iter(cfg.get('tasks', {}).values()), {})
            params = task.get('request_snapshot', {}).get('parameters', cfg)
            runtime_error = self.runtime_errors.get(p['id'])
            state = {'phase': 'attention', 'error': runtime_error} if runtime_error else p['queue_state']
            rows.append({'id': p['id'], **p['video_job'], **state, 'created_at': cfg.get('submitted_at') or p['created_at'],
                'model': params['model'], 'duration': params['duration'], 'resolution': params['resolution'], 'ratio': params['ratio'],
                'prompt': task.get('request_snapshot', {}).get('bound_prompt') or cfg['global_prompt'],
                'role_bindings': task.get('request_snapshot', {}).get('role_bindings', task.get('references', [])),
                'queued': p['id'] in self.waiting, 'context_ir': params.get('use_context_ir'), 'card': cfg.get('pending_approval'), 'submit_id': task.get('submit_id'),
                'task_id': task.get('task_id'), 'task_status': task.get('status'), 'output': task.get('output'),
                'archive_path': task.get('archive_output'),
                'task_error': task.get('submit_error') or task.get('error') or task.get('download_error') or task.get('query_error'),
                'busy': p['id'] in self.active,
                'references': [{'side': side, 'path': p['groups'][0]['repaired_' + side]} for side in ('a','b')] +
                    ([{'side':'audio','path':p['groups'][0]['generation_bindings']['voice_path']}] if p['groups'][0].get('generation_bindings',{}).get('voice_path') else [])})
        return sorted(rows, key=lambda r:r['created_at'], reverse=True)

    def library_items(self):
        items, links = [], {}
        for p in self.service.projects.list():
            if not p.get('video_assembly'):
                continue
            for plan in p['generation'].get('dialogue_plans', {}).values():
                out = plan['assembly']
                if not out.get('output'):
                    continue
                meta = plan['origin']
                key = 'video-assembly:' + p['id'] + ':' + meta['source_group_id']
                if Path(out['output']).is_file():
                    links.update({part['group_id']: key for part in plan['parts']})
                items.append({'id':key, 'kind':'assembly', 'project_id':meta['source_project_id'], 'project_name':meta['source_project_name'],
                    'group_id':meta['source_group_id'], 'group_ids':[meta['source_group_id']], 'group_number':meta['group_number'],
                    'label':'完整拼接', 'status':'downloaded', 'path':out['output'], 'available':Path(out['output']).is_file(),
                    **plan['config'], 'context_ir':plan['config'].get('use_context_ir'),
                    'duration':out['duration'], 'sha256':out['sha256'], 'created_at':Path(out['output']).stat().st_mtime if Path(out['output']).is_file() else 0,
                    'parts':[{'index':part['index'], 'group_id':part['group_id'],
                              'prompt':p['generation']['tasks'].get(part['group_id'], {}).get('request_snapshot', {}).get('bound_prompt', ''),
                              'references':p['generation']['tasks'].get(part['group_id'], {}).get('request_snapshot', {}).get('role_bindings', [])}
                             for part in plan['parts']]})
        for row in self.rows():
            items.append({'id':'video-job:'+row['id'], 'kind':'generation', 'project_id':row['source_project_id'],
                'project_name':row['source_project_name'], 'group_id':row['source_group_id'], 'group_number':row['group_number'],
                'status':row['task_status'] or row['phase'], 'path':row['output'], 'archive_path':row['archive_path'],
                'available':bool(row['output'] and Path(row['output']).is_file()),
                'duration':row['duration'], 'model':row['model'], 'resolution':row['resolution'], 'ratio':row['ratio'],
                'created_at':row['created_at'], 'submit_id':row['submit_id'], 'task_id':row['task_id'],
                'queue_phase':row['phase'], 'error':row.get('error') or row['task_error'], 'job_id':row['id'],
                'assembly_id':links.get(row['id']),
                'context_ir':row['context_ir'], 'prompt':row['prompt'], 'references':row['role_bindings'], 'segment':{'index':row['part'],'count':row['part_count']}})
        return items
