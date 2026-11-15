"""Project-linked media library and explicit, resumable postproduction jobs."""
from __future__ import annotations

from .dialogue_segments import has_tasks

import hashlib
import base64
import copy
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
import uuid

from .settings import ROOT

_post_launch_lock = threading.RLock()


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def save_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
        for attempt in range(8):
            try:
                tmp.replace(path)
                break
            except PermissionError:
                if attempt == 7:
                    raise
                time.sleep(.1 * (attempt + 1))
    finally:
        tmp.unlink(missing_ok=True)


class WorkbenchMixin:
    def _video_jobs(self):
        from .video_queue import queue_for
        with self.lock:
            if not hasattr(self, '_video_queue'):
                self._video_queue = queue_for(self._service)
            return self._video_queue

    def workbench_video_sources(self):
        return [{'id': p['id'], 'name': p['name'], 'groups': [
            {'id': g['id'], 'number': i, 'needs_review': bool(g.get('needs_review')),
             'voice': g.get('generation_bindings', {}).get('voice_name'),
             'selected': g.get('selected', False)} for i, g in enumerate(p.get('groups', []), 1)]}
            for p in self._service.projects.list()]

    def workbench_video_preset(self):
        from .h3_baseline import BASELINE
        params = {k: v for k, v in BASELINE['parameters'].items() if k != 'mode'}
        return {**params, 'baseline_id': BASELINE['id'], 'auto_split_dialogue': True,
                'common_prompt': BASELINE['global_prompt']}

    def workbench_video_models(self):
        from .compshare import GenerationWorkflow
        models = GenerationWorkflow(self._service).canvas.models()['items']
        result = []
        for model in models:
            mode = next((m for m in model.get('modes', []) if m['name'] == 'm2v'), None)
            if not model['model'].startswith('seedance_') or not mode:
                continue
            flags = {f['flag']: f for f in mode['flags']}
            if not {'image', 'audio'}.issubset({t['mediaType'] for t in mode['references']['types']}):
                continue
            result.append({'model': model['model'], 'label': model.get('displayName', model['model']),
                           'min': flags['--duration']['min'], 'max': flags['--duration']['max'],
                           'ratios': flags['--ratio']['values'], 'resolutions': flags['--resolution']['values']})
        return result

    def workbench_video_create(self, project_id, group_ids, config, voice_source='baseline'):
        return self._video_jobs().create(project_id, group_ids, config, voice_source)

    def workbench_video_jobs(self):
        from .media_server import MediaServer
        if not hasattr(self, '_media_server'):
            self._media_server = MediaServer()
        if not hasattr(self, '_video_media_urls'):
            self._video_media_urls = {}
        def media_url(path):
            # Polling must not change the source URL and restart video/audio playback.
            key = str(Path(path).resolve())
            if key not in self._video_media_urls:
                self._video_media_urls[key] = self._media_server.register(path)
            return self._video_media_urls[key]
        rows = self._video_jobs().rows()
        for row in rows:
            for ref in row['references']:
                if Path(ref['path']).is_file():
                    ref['url'] = media_url(ref['path'])
            if row['output'] and Path(row['output']).is_file():
                row['url'] = media_url(row['output'])
        return rows

    def workbench_video_approve(self, job_id, card_id, ceiling):
        return self._video_jobs().approve(job_id, card_id, ceiling)

    def workbench_video_review(self, job_id):
        return self._video_jobs().review(job_id)

    def workbench_video_refresh(self, job_id):
        return self._video_jobs().refresh(job_id)

    def workbench_add_group(self, project_id, frame_a, frame_b):
        from .project_run import workflow_lock
        with self.lock:
            self._idle()
            if self.current != project_id:
                raise ValueError('项目已切换')
            p = self._service.projects.get(project_id)
            with workflow_lock(Path(p['directory'])):
                p = self._service.projects.get(project_id)
                group = {'id': uuid.uuid4().hex, 'frame_a': frame_a, 'frame_b': frame_b,
                         'prompt': '', 'selected': False, 'needs_review': True}
                self._service.projects.update_groups(project_id, [*p['groups'], group])
                self.version += 1
                return group['id']

    def workbench_confirm_hero(self, project_id, group_id, side):
        return self.workbench_update_group(project_id, group_id, {'confirmed_character_side': side})

    def workbench_replace_frame(self, project_id, group_id, side, expected_frame, frame_id):
        from .project_run import workflow_lock
        with self.lock:
            self._idle()
            if self.current != project_id or side not in ('a', 'b'):
                raise ValueError('项目或人物侧已变化')
            p = self._service.projects.get(project_id)
            with workflow_lock(Path(p['directory'])):
                p = self._service.projects.get(project_id)
                groups = copy.deepcopy(p['groups'])
                group = next((g for g in groups if g['id'] == group_id), None)
                if not group or group['frame_' + side] != expected_frame:
                    raise ValueError('图片已变化，请重新打开后选择')
                if has_tasks(p, group_id):
                    raise ValueError('本组已有生成草稿或任务，请先新建轮次再换图')
                group['frame_' + side] = frame_id
                self._service.projects.update_groups(project_id, groups)
                self.version += 1
        return True

    def workbench_poster(self, item_id):
        item = self._library_item(item_id)
        path = Path(item.get('path') or '')
        if not item.get('available'):
            return None
        stat = path.stat()
        key = (str(path), stat.st_size, stat.st_mtime_ns)
        cache = getattr(self, '_poster_cache', {})
        if key not in cache:
            result = subprocess.run([self._service.config['ffmpeg'], '-v', 'error', '-ss', '0.5', '-i', str(path),
                                     '-frames:v', '1', '-vf', 'scale=480:-2', '-f', 'image2pipe', '-vcodec', 'mjpeg', '-'],
                                    capture_output=True, timeout=25,
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if result.returncode or not result.stdout:
                return None
            if len(cache) >= 128:
                cache.pop(next(iter(cache)))
            cache[key] = 'data:image/jpeg;base64,' + base64.b64encode(result.stdout).decode('ascii')
            self._poster_cache = cache
        return cache[key]

    def _post_root(self):
        return Path(self._service.config.get('postprocess_root') or ROOT.parent / '视频后期处理').resolve()

    def _post_run_root(self):
        return self._post_root() / 'workbench-runs'

    def _post_manifests(self):
        result = []
        for path in (self._post_root() / 'batches').glob('*/manifest.json'):
            try:
                result.append((path, read_json(path)))
            except (OSError, ValueError):
                continue
        return result

    def workbench_library(self):
        items, source_links, projects, all_groups = [], {}, [], []
        for listed in self._service.projects.list():
            p = self._service.projects.get(listed['id'])
            groups = {g['id']: i for i, g in enumerate(p.get('groups', []), 1)}
            project_items = []
            for idx, cfg in list(enumerate(p.get('generation_history', []))) + [(-1, p.get('generation', {}))]:
                from .dialogue_segments import assembly_plans
                plans = assembly_plans(cfg)
                def assembly_key(plan):
                    suffix = ':' + plan['source_group_id'] if plan in cfg.get('dialogue_plans', {}).values() else ''
                    return f"assembly:{p['id']}:{idx}{suffix}"
                assembled_groups = {part['group_id']: assembly_key(plan) for plan in plans
                    if plan.get('assembly', {}).get('output') and Path(plan['assembly']['output']).is_file()
                    for part in plan.get('parts', [])}
                for gid, task in cfg.get('tasks', {}).items():
                    key = f"generation:{p['id']}:{idx}:{gid}"
                    params = task.get('request_snapshot', {}).get('parameters', cfg)
                    item = {'id': key, 'kind': 'generation', 'project_id': p['id'], 'project_name': p['name'],
                            'group_id': task.get('source_group_id') or gid, 'task_group_id': gid,
                            'group_number': groups.get(task.get('source_group_id') or gid, 0), 'round': idx,
                            'segment': task.get('segment'),
                            'status': task.get('status', 'draft'), 'model': params.get('model'),
                            'duration': params.get('duration'), 'resolution': params.get('resolution'),
                            'ratio': params.get('ratio'), 'context_ir': params.get('use_context_ir'),
                            'submit_id': task.get('submit_id'), 'task_id': task.get('task_id'), 'path': task.get('output'),
                            'archive_path': task.get('archive_output'), 'error': task.get('error') or task.get('download_error') or task.get('query_error') or task.get('submit_error'),
                            'created_at': cfg.get('submitted_at', 0),
                            'prompt': task.get('request_snapshot', {}).get('bound_prompt') or task.get('prompt', ''),
                            'references': task.get('request_snapshot', {}).get('role_bindings', task.get('references', [])),
                            'assembly_id': assembled_groups.get(gid),
                            'poster_frame': next((g.get('frame_a') for g in p.get('groups', []) if g['id'] == (task.get('source_group_id') or gid)), None)}
                    item['available'] = bool(item['path'] and Path(item['path']).is_file())
                    project_items.append(item)
                    for file in (item['path'], item['archive_path']):
                        if file:
                            source_links[str(Path(file).resolve()).casefold()] = item
                for plan in plans:
                    assembly = plan.get('assembly', {})
                    if not assembly.get('output'):
                        continue
                    item = {'id': assembly_key(plan), 'kind': 'assembly',
                            'project_id': p['id'], 'project_name': p['name'], 'round': idx,
                            'group_id': plan.get('source_group_id'),
                            'group_ids': ([plan['source_group_id']] if plan in cfg.get('dialogue_plans', {}).values()
                                          else [part['group_id'] for part in plan.get('parts', [])]),
                            'task_group_ids': [part['group_id'] for part in plan.get('parts', [])],
                            'group_number': groups.get(plan.get('source_group_id'), 0), 'label': '完整拼接', 'status': 'downloaded',
                            'path': assembly['output'], 'archive_path': assembly.get('archive_output'),
                            'available': Path(assembly['output']).is_file(), 'duration': assembly.get('duration'),
                            'model': cfg.get('model'), 'resolution': cfg.get('resolution'), 'ratio': cfg.get('ratio'),
                            'context_ir': cfg.get('use_context_ir'), 'created_at': cfg.get('submitted_at', 0),
                            'sha256': assembly.get('sha256'),
                            'prompt': '', 'references': [], 'review_note': '拼接完成，待核对对白与衔接',
                            'parts': [{'index': part['index'], 'group_id': part['group_id'],
                                       'prompt': cfg.get('tasks', {}).get(part['group_id'], {}).get('request_snapshot', {}).get('bound_prompt', ''),
                                       'references': cfg.get('tasks', {}).get(part['group_id'], {}).get('request_snapshot', {}).get('role_bindings', [])}
                                      for part in plan.get('parts', [])]}
                    project_items.append(item)
            for g in p.get('groups', []):
                generated = sum(i['kind'] == 'generation' and i['group_id'] == g['id'] and
                                i['status'] in ('completed', 'downloaded') for i in project_items)
                all_groups.append({'id': f"{p['id']}:{g['id']}", 'project_id': p['id'], 'project_name': p['name'],
                    'group_id': g['id'], 'group_number': groups[g['id']],
                    'person_a': g.get('person_a', ''), 'person_b': g.get('person_b', ''),
                    'frame_a': g.get('frame_a'), 'frame_b': g.get('frame_b'),
                    'repaired_a': g.get('repaired_a'), 'repaired_b': g.get('repaired_b'),
                    'needs_review': g.get('needs_review', False), 'generated': generated,
                    'clean_reviewed': all(g.get('reference_reviews', {}).get(side, {}).get('clean') for side in ('a', 'b'))})
            items.extend(project_items)
            projects.append({'id': p['id'], 'name': p['name'], 'phase': p.get('phase'),
                             'groups': len(groups), 'generated': sum(i['status'] == 'downloaded' for i in project_items),
                             'running': sum(i['status'] in ('running', 'submitted', 'submission_unknown', 'audio_running') for i in project_items),
                             'poster_frame': next((g.get('frame_a') for g in p.get('groups', [])), None)})
        # Include independent jobs in the same library and postproduction linkage.
        from .video_queue import VideoQueue
        queue = getattr(self, '_video_queue', None) or VideoQueue(self._service)
        for item in queue.library_items():
            items.append(item)
            if item.get('path'):
                source_links[str(Path(item['path']).resolve()).casefold()] = item
            if item['kind'] == 'generation':
                for group in all_groups:
                    if group['project_id'] == item['project_id'] and group['group_id'] == item['group_id'] and item['status'] in ('downloaded', 'completed'):
                        group['generated'] += 1
                for project in projects:
                    if project['id'] == item['project_id']:
                        project['generated'] += int(item['status'] == 'downloaded')
                        project['running'] += int(item['status'] in ('running', 'submitted', 'submission_unknown', 'submitting', 'tracking'))
        review_path = self._service.projects.root / 'workbench-reviews.json'
        reviews = read_json(review_path) if review_path.exists() else {}
        assembly_sources = {i['sha256']: i for i in items if i['kind'] == 'assembly' and i.get('sha256')}
        for path, manifest in self._post_manifests():
            for job in manifest.get('jobs', []):
                parent = source_links.get(str(Path(job['source']).resolve()).casefold()) or assembly_sources.get(job.get('sourceSha256'), {})
                linkage = job.get('workbench', {})
                key = f"post:{manifest['id']}:{job['id']}"
                quality = job.get('quality', {})
                review = reviews.get(key, {})
                accepted = review.get('accepted') and review.get('sha256') == job.get('outputSha256')
                output = Path(job.get('output') or '')
                if accepted:
                    stat = output.stat() if output.is_file() else None
                    accepted = bool(stat and review.get('file_size') == stat.st_size and review.get('mtime_ns') == stat.st_mtime_ns)
                items.append({'id': key, 'kind': 'post', 'project_id': parent.get('project_id') or linkage.get('project_id'),
                              'label': '完整拼接 · 后期' if parent.get('kind') == 'assembly' else None,
                              'project_name': parent.get('project_name') or Path(manifest['batch']).name,
                              'group_id': parent.get('group_id') or linkage.get('group_id'), 'group_number': parent.get('group_number') or
                              int((re.search(r'第(\d+)组', Path(job['source']).name) or [None, '0'])[1]),
                              'group_ids': parent.get('group_ids') or linkage.get('group_ids'),
                              'source_id': parent.get('id'), 'source_path': job['source'],
                              'batch_id': manifest['id'], 'job_id': job['id'], 'status': job.get('status'),
                              'path': job.get('output'), 'available': bool(job.get('output') and Path(job['output']).is_file()),
                              'duration': job.get('media', {}).get('duration'),
                              'created_at': job.get('updatedAt') or job.get('createdAt', 0),
                              'profile': manifest.get('profile', {}).get('profileVersion'),
                              'captions': bool(job.get('captions')), 'accepted': bool(accepted),
                              'review_note': quality.get('speechCaptionReviewNote') or review.get('note', ''),
                              'review_required': not accepted, 'quality': quality, 'error': job.get('error'),
                              'poster_frame': parent.get('poster_frame')})
        runs = []
        for path in self._post_run_root().glob('*/state.json'):
            try:
                run = read_json(path)
                if run.get('status') == 'running' and time.time() - run.get('heartbeat', 0) > 90:
                    run = {**run, 'status': 'interrupted', 'message': '后期进程已中断，重新选择原条目继续即可'}
                runs.append(run)
            except (OSError, ValueError):
                pass
        items.sort(key=lambda i: i.get('created_at') or 0, reverse=True)
        return {'items': items, 'projects': projects, 'groups': all_groups, 'runs': sorted(runs, key=lambda r: r.get('started_at', 0), reverse=True),
                'post_available': (self._post_root() / 'postprocess.py').is_file()}

    def workbench_tasks(self):
        """Read all saved submission rounds, without querying/retrying provider jobs."""
        from .compshare import channel_for
        items, seen = [], set()
        for item in self.workbench_library()['items']:
            if item['kind'] != 'generation':
                continue
            provider = channel_for(item.get('model'))
            identity = (provider, item.get('submit_id') or item.get('task_id') or item['id'])
            if identity in seen:
                continue
            seen.add(identity)
            status, phase = item['status'], item.get('queue_phase')
            if status == 'downloaded' and item['available']:
                category, label = 'completed', '已完成'
            elif status == 'completed':
                category, label = 'attention', '已生成 · 下载待处理' if item.get('error') else '已生成 · 等待下载'
            elif status in ('failed', 'canceled', 'cancelled', 'completed_no_resource'):
                category, label = 'failed', '已取消' if status in ('canceled', 'cancelled') else '失败'
            elif phase == 'attention' or status in ('submission_unknown', 'requires_manual_review', 'downloaded', 'node_create_unknown'):
                category, label = 'attention', '本地视频缺失' if status == 'downloaded' else '待核查'
            elif status in ('draft', 'preparing', 'needs_review', 'awaiting_approval'):
                category, label = 'draft', '待检查图片' if status == 'needs_review' else '准备中' if status == 'preparing' else '待提交'
            else:
                category, label = 'running', '提交中' if status == 'submitting' else '生成中'
            items.append({**item, 'provider': provider, 'provider_label': '优云智算 H3' if provider == 'compshare' else '即梦',
                          'category': category, 'status_label': label,
                          'submitted': bool(item.get('submit_id') or item.get('task_id'))})
        return {'items': items}

    def workbench_group_image(self, project_id, group_id, side):
        """Global browser reads explicit project assets without switching the active project."""
        from .projects import original_frame, project_asset
        from PIL import Image
        import io
        p = self._service.projects.get(project_id)
        g = next((g for g in p['groups'] if g['id'] == group_id), None)
        if not g or side not in ('a', 'b'):
            raise ValueError('图片组不存在')
        path = (project_asset(p, g['repaired_' + side]) if g.get('repaired_' + side) else
                original_frame(p, g['frame_' + side], self._service.config['ffmpeg'], self._service.config['ffprobe']))
        with Image.open(path) as source:
            im = source.convert('RGB'); im.thumbnail((700, 500))
            buf = io.BytesIO(); im.save(buf, format='JPEG', quality=88)
        return {'url': 'data:image/jpeg;base64,' + base64.b64encode(buf.getvalue()).decode('ascii')}

    def workbench_history_reference(self, item_id, sha256):
        item = self._library_item(item_id)
        refs = item.get('references', []) + [r for part in item.get('parts', []) for r in part.get('references', [])]
        ref = next((r for r in refs if r.get('sha256') == sha256), None)
        if not ref or not ref.get('path'):
            raise ValueError('历史记录没有这份参考素材')
        path = Path(ref['path'])
        from .generation import _sha
        if not path.is_file() or _sha(path) != sha256:
            raise ValueError('历史参考文件已移动或变化，不能当作原提交素材展示')
        from .media_server import MediaServer
        if not hasattr(self, '_media_server'):
            self._media_server = MediaServer()
        return {'url': self._media_server.register(path)}

    def workbench_select_global_groups(self, project_id, group_ids):
        from .project_run import workflow_lock
        with self.lock:
            self._idle()
            p = self._service.projects.get(project_id)
            with workflow_lock(Path(p['directory'])):
                p = self._service.projects.get(project_id)
                known = {g['id'] for g in p['groups']}
                if not group_ids or len(set(group_ids)) != len(group_ids) or not set(group_ids).issubset(known):
                    raise ValueError('所选图片组已变化，请刷新')
                for g in p['groups']:
                    g['selected'] = g['id'] in group_ids
                self._service.projects.save(p)
                self.version += 1
        return True

    def _library_item(self, item_id):
        item = next((i for i in self.workbench_library()['items'] if i['id'] == item_id), None)
        if not item:
            raise ValueError('素材记录已变化，请刷新')
        return item

    def workbench_media(self, item_id):
        item = self._library_item(item_id)
        if not item.get('available'):
            raise ValueError('视频尚未下载，或本地文件已移动')
        from .media_server import MediaServer
        if not hasattr(self, '_media_server'):
            self._media_server = MediaServer()
        return {'url': self._media_server.register(item['path']), 'path': item['path']}

    def workbench_reveal(self, item_id):
        path = Path(self._library_item(item_id).get('path') or '').resolve()
        if not path.is_file():
            raise ValueError('文件不存在')
        if os.name == 'nt':
            subprocess.Popen(['explorer.exe', '/select,', str(path)])
        elif sys.platform == 'darwin':
            subprocess.Popen(['open', '-R', str(path)])
        else:
            subprocess.Popen(['xdg-open', str(path.parent)])
        return True

    def workbench_review(self, item_id, accepted):
        from .video_archive import digest
        with self.lock:
            item = self._library_item(item_id)
            if item['kind'] != 'post' or item.get('status') != 'complete' or not item.get('available') or type(accepted) is not bool:
                raise ValueError('只能验收已导出的后期成片')
            manifest = read_json(self._post_root() / 'batches' / item['batch_id'] / 'manifest.json')
            job = next(j for j in manifest['jobs'] if j['id'] == item['job_id'])
            checksum = digest(item['path'])
            if checksum != job.get('outputSha256'):
                raise ValueError('成片已被修改，请先核对，不覆盖历史验收')
            path = self._service.projects.root / 'workbench-reviews.json'
            data = read_json(path) if path.exists() else {}
            stat = Path(item['path']).stat()
            data[item_id] = {'accepted': accepted, 'sha256': checksum, 'at': time.time(),
                             'file_size': stat.st_size, 'mtime_ns': stat.st_mtime_ns}
            save_json(path, data)
            return True

    def workbench_captions(self, item_id):
        item = self._library_item(item_id)
        if item['kind'] != 'post':
            raise ValueError('请选择后期视频')
        manifest = read_json(self._post_root() / 'batches' / item['batch_id'] / 'manifest.json')
        job = next(j for j in manifest['jobs'] if j['id'] == item['job_id'])
        cap = job.get('captions')
        if not cap:
            return {'text': '', 'revision': None}
        content = Path(cap['path']).read_bytes()
        return {'text': content.decode('utf-8-sig'), 'revision': hashlib.sha256(content).hexdigest()}

    def workbench_post_settings(self):
        path = self._post_root() / '流程配置.json'
        if not path.is_file():
            return {'available': False, 'message': '未找到本机后期处理程序'}
        cfg = read_json(path)
        return {'available': True, 'profile': cfg['profileVersion'], 'audio': cfg['audio'],
                'captions': cfg['captions'], 'output_root': cfg['outputRoot'], 'bgm': Path(cfg['bgmPath']).name}

    def workbench_start_post(self, item_ids, recognize=True, caption_edit=None):
        with self.lock, _post_launch_lock:
            if not isinstance(item_ids, list) or not item_ids or len(item_ids) > 100:
                raise ValueError('请选择1–100条视频')
            library = self.workbench_library()
            if any(r['status'] == 'running' for r in library['runs']):
                raise ValueError('已有后期批次正在处理，请等待完成')
            known = {i['id']: i for i in library['items']}
            if len(set(item_ids)) != len(item_ids) or any(i not in known for i in item_ids):
                raise ValueError('所选视频已变化，请刷新')
            root = self._post_root()
            cfg = read_json(root / '流程配置.json')
            if cfg['audio'].get('engine') != 'local-spatial':
                raise ValueError('当前后期默认配置须为本地空间效果')
            requests, sources = [], set()
            allowed = Path(cfg['inputRoot']).resolve()
            for key in item_ids:
                item = known[key]
                source = item.get('source_path') if item['kind'] == 'post' else item.get('archive_path')
                if item['kind'] == 'assembly' and item.get('available'):
                    from .video_archive import digest
                    import shutil
                    original = Path(item['path'])
                    checksum = digest(original)
                    if not item.get('sha256') or checksum != item['sha256']:
                        raise ValueError('拼接视频已变化，请先重新核对')
                    archive = allowed / '工作台拼接原片' / item['project_id'] / ('第00组_优云智算H3_完整拼接_' + checksum[:16] + '.mp4')
                    archive.parent.mkdir(parents=True, exist_ok=True)
                    if not archive.exists():
                        shutil.copy2(original, archive)
                    if digest(archive) != checksum:
                        raise ValueError('拼接原片归档校验失败')
                    source = str(archive)
                if not source or not Path(source).is_file() or not Path(source).resolve().is_relative_to(allowed):
                    raise ValueError('所选视频尚未归档或原片已移动，请先完成下载归档')
                if source in sources:
                    continue
                sources.add(source)
                from .video_archive import digest
                requests.append({'source': source, 'source_sha256': digest(source), 'item_id': key, 'project_id': item.get('project_id'),
                                 'group_id': item.get('group_id'), 'group_ids': item.get('group_ids')})
            if caption_edit:
                if len(item_ids) != 1 or not isinstance(caption_edit.get('text'), str) or len(caption_edit['text']) > 200000:
                    raise ValueError('每次只编辑一条视频的字幕')
                original = self.workbench_captions(item_ids[0])
                if original['revision'] != caption_edit.get('revision'):
                    raise ValueError('字幕已变化，请重新打开后编辑')
            run_id = uuid.uuid4().hex
            folder = self._post_run_root() / run_id
            state = {'id': run_id, 'status': 'running', 'started_at': time.time(), 'heartbeat': time.time(),
                     'count': len(requests), 'message': '正在准备后期', 'item_ids': item_ids}
            save_json(folder / 'state.json', state)
            save_json(folder / 'config.json', cfg)
            save_json(folder / 'request.json', {'items': requests, 'recognize': bool(recognize),
                      'caption_edit': caption_edit, 'runtime': str(root), 'state': state})
            try:
                log = (folder / 'runner.log').open('wb')
                try:
                    subprocess.Popen([cfg['tools']['python'], '-X', 'utf8', str(Path(__file__).with_name('post_runner.py')),
                                      str(folder / 'request.json')], cwd=root, stdout=log, stderr=log,
                                     creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                finally:
                    log.close()
            except Exception as exc:
                save_json(folder / 'state.json', {**state, 'status': 'failed', 'message': str(exc)})
                raise
            return state

    def workbench_enter_generation(self, project_id, group_ids):
        """Explicit 'go generate' prepares a fresh round only after terminal jobs."""
        from .project_run import workflow_lock
        from .compshare import channel_for
        from .h3_baseline import apply_baseline, uses_haoge
        with self.lock:
            self._idle()
            if self.current != project_id:
                raise ValueError('项目已切换')
            p = self._service.projects.get(project_id)
            with workflow_lock(Path(p['directory'])):
                p = self._service.projects.get(project_id)
                known = {g['id']: g for g in p['groups']}
                if not group_ids or len(set(group_ids)) != len(group_ids) or not set(group_ids).issubset(known):
                    raise ValueError('请先选择有效的图片组')
                if any(not known[gid].get('selected') for gid in group_ids):
                    raise ValueError('图片组选择已变化，请刷新后重试')
                cfg = p.get('generation', {})
                tasks = list(cfg.get('tasks', {}).values())
                archived = False
                if tasks and all(t.get('status') in ('downloaded', 'failed', 'canceled') for t in tasks):
                    provider = cfg.get('provider') or channel_for(cfg.get('model'))
                    p = self._generation.new_round(project_id)
                    p = self._generation.select_channel(project_id, provider)
                    archived = True
                elif not tasks and cfg.get('model') in ('MiniMax-H3', 'minimax-h3-lite') and uses_haoge(p) and (not cfg.get('baseline_id') or cfg.get('baseline_id') == 'h3-first-v1' and not cfg.get('dialogue_policy')):
                    apply_baseline(p)
                    self._service.projects.save(p)
                self.version += 1
                return {'ready': True, 'archived_previous_round': archived,
                        'has_tasks': bool(p.get('generation', {}).get('tasks'))}

    def workbench_apply_h3_baseline(self, project_id):
        with self.lock:
            self._idle()
            if self.current != project_id:
                raise ValueError('项目已切换')
            result = self._agent.workflow.execute(project_id, 'apply_h3_baseline')
            self.version += 1
            return result

    def workbench_clean_groups(self, project_id, group_ids):
        """One explicit cleanup action, using the existing visual agent and local repair."""
        with self.lock:
            self._idle()
            if self.current != project_id:
                raise ValueError('项目已切换')
            p = self._service.projects.get(project_id)
            if not group_ids or len(set(group_ids)) != len(group_ids) or not set(group_ids).issubset({g['id'] for g in p['groups']}):
                raise ValueError('请选择有效的图片组')
            if any(has_tasks(p, gid) for gid in group_ids):
                raise ValueError('本组已有生成草稿或任务，请先新建轮次再清理，旧视频保留')
            if not self._agent.connection_state().get('connected'):
                raise ValueError('Agent 已断开，请在右侧重新连接；也可使用手动框选修复')
            state = self._agent._session(project_id)
            model = next((m for m in self._agent.models if m['model'] == state.get('model')), None)
            model = model or next((m for m in self._agent.models if m.get('isDefault')), None) or next(iter(self._agent.models), None)
            if not model:
                raise ValueError('Agent 模型目录尚未就绪，请稍后重试')
            effort = state.get('effort') or model['defaultReasoningEffort']
            if effort not in [e['reasoningEffort'] for e in model['supportedReasoningEfforts']]:
                effort = model['defaultReasoningEffort']
            return self._agent.send(project_id,
                '请只处理本次指定图片组的A、B两图：逐图查看，去除水印和叠加字幕，使用本地repair_region修复，再逐图复看并record_reference_review记录。'
                '没有水印字幕的图直接跳过并记录。场景内招牌不清除。不要生成视频，不修改台词或音色。',
                model['model'], effort, group_ids)

    def workbench_generation_preview(self, project_id, group_ids, config):
        with self.lock:
            self._idle()
            if self.current != project_id:
                raise ValueError('项目已切换')
            flow = self._agent.workflow
            flow.execute(project_id, 'configure_generation', config)
            self.version += 1
            return flow.execute(project_id, 'generation_preflight', {'group_ids': group_ids})

    def workbench_reference(self, project_id, group_id, token, sha256):
        if self.current != project_id:
            raise ValueError('项目已切换')
        sheet = self._agent.workflow.execute(project_id, 'execution_sheet', {'group_id': group_id})
        ref = next((r for r in sheet.get('role_bindings', []) if r['token'] == token and r['sha256'] == sha256), None)
        if ref is None:
            raise ValueError('参考素材已变化，请重新检查实际输入')
        from .media_server import MediaServer
        if not hasattr(self, '_media_server'):
            self._media_server = MediaServer()
        return {'url': self._media_server.register(ref['path'])}

    def workbench_reference_review(self, project_id, group_id, side, sha256, clean):
        with self.lock:
            self._idle()
            if self.current != project_id:
                raise ValueError('项目已切换')
            result = self._agent.workflow.execute(project_id, 'record_reference_review',
                {'group_id': group_id, 'side': side, 'sha256': sha256, 'clean': clean})
            self.version += 1
            return result

    def workbench_prepare(self, project_id, group_ids, config):
        with self.lock:
            self._idle()
            if self.current != project_id:
                raise ValueError('项目已切换')
            if not group_ids:
                raise ValueError('请先选择图片组')
        def prepare():
            flow = self._agent.workflow
            flow.execute(project_id, 'configure_generation', config)
            preflight = flow.execute(project_id, 'generation_preflight', {'group_ids': group_ids})
            if preflight['errors']:
                raise ValueError('；'.join(e['message'] for e in preflight['errors']))
            flow.execute(project_id, 'prepare_batch', {'group_ids': group_ids})
            flow.execute(project_id, 'quote_batch')
            flow.execute(project_id, 'request_submission')
            return '草稿与费用确认卡已准备好，请核对后提交'
        return self._start(prepare)

    def workbench_update_group(self, project_id, group_id, fields):
        with self.lock:
            self._idle()
            if self.current != project_id:
                raise ValueError('项目已切换')
            summary = self._agent.workflow.summary(project_id)
            result = self._agent.workflow.execute(project_id, 'update_groups', {
                'expected_revision': summary['revision'], 'updates': [{'group_id': group_id, **fields}]})
            self.version += 1
            return result


class PostWorkbench(WorkbenchMixin):
    """The same explicit-scope postproduction operations for the embedded Agent."""
    def __init__(self, service):
        self._service = service
        self.lock = threading.RLock()
