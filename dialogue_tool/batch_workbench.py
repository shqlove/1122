"""Explicit cross-project scopes and recoverable library operations."""
import copy
import hashlib
import json
import re
import threading
import time

_batch_lock = threading.RLock()


def normalize_scope(scope):
    if not isinstance(scope, list) or not scope or len(scope) > 500:
        raise ValueError('请选择 1–500 个图片组')
    result, seen = [], set()
    for item in scope:
        pid, gid = item.get('project_id'), item.get('group_id')
        if not isinstance(pid, str) or not pid or not isinstance(gid, str) or not gid:
            raise ValueError('图片组范围无效')
        if (pid, gid) not in seen:
            seen.add((pid, gid))
            result.append({**item, 'project_id': pid, 'group_id': gid})
    return result


def trash_records(service):
    from .workbench import read_json
    path = service.projects.root / 'workbench-trash.json'
    return read_json(path) if path.exists() else {}


def group_key(pid, gid):
    return 'group:' + pid + ':' + gid


class BatchWorkbenchMixin:
    def workbench_export_selected(self, item_ids):
        import shutil
        import uuid
        import webview
        from pathlib import Path
        from .video_archive import digest
        if not isinstance(item_ids, list) or not item_ids or len(item_ids) > 500:
            raise ValueError('请选择 1–500 条视频')
        known = {i['id']: i for i in self.workbench_library()['items']}
        if any(key not in known or known[key].get('trashed') or not known[key].get('available') for key in item_ids):
            raise ValueError('所选视频不存在、已移入回收站或尚未下载')
        chosen = self._window.create_file_dialog(webview.FileDialog.FOLDER)
        if not chosen:
            return []
        destination = Path(chosen[0]).resolve()
        results = []
        for key in dict.fromkeys(item_ids):
            tmp = None
            created = False
            try:
                item = known[key]
                source = Path(item['path'])
                checksum = digest(source)
                label = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', item.get('project_name') or '视频')[:60].strip(' .') or '视频'
                name = f"{label}_第{item.get('group_number', 0)}组_{checksum[:16]}{source.suffix}"
                target = destination / name
                if not target.exists():
                    tmp = destination / (uuid.uuid4().hex + '.tmp')
                    shutil.copy2(source, tmp)
                    if digest(tmp) != checksum:
                        raise ValueError('复制过程中源文件发生变化')
                    # Exclusive creation prevents overwriting a concurrently created export.
                    with target.open('xb') as out:
                        created = True
                        with tmp.open('rb') as incoming:
                            shutil.copyfileobj(incoming, out)
                if digest(target) != checksum:
                    raise ValueError('目标存在不同内容，未覆盖，请更换导出目录')
                results.append({'id': key, 'ok': True, 'path': str(target)})
            except Exception as exc:
                if created:
                    target.unlink(missing_ok=True)
                results.append({'id': key, 'ok': False, 'message': str(exc)})
            finally:
                if tmp:
                    tmp.unlink(missing_ok=True)
        return results

    def workbench_video_create_batch(self, scope, config, voice_source, request_id):
        """Persist per-group outcomes; transport retries reuse the same request."""
        from .workbench import read_json, save_json
        scope = normalize_scope(scope)
        if not isinstance(request_id, str) or not re.fullmatch(r'[a-zA-Z0-9-]{16,80}', request_id):
            raise ValueError('创建请求标识无效')
        signature = hashlib.sha256(json.dumps([scope, config, voice_source], sort_keys=True).encode()).hexdigest()
        path = self._service.projects.root / 'video-batches' / (request_id + '.json')
        from .project_run import workflow_lock
        request_folder = path.parent / request_id
        request_folder.mkdir(parents=True, exist_ok=True)
        with workflow_lock(request_folder):
            state = read_json(path) if path.exists() else {'signature': signature, 'items': [], 'id': request_id}
            if state['signature'] != signature:
                raise ValueError('同一创建请求的范围或参数已变化')
            for source in scope:
                prior = next((r for r in state['items'] if r['project_id'] == source['project_id'] and r['group_id'] == source['group_id']), None)
                if prior:
                    continue
                entry = {**source, 'status': 'creating', 'message': '创建被中断，请核对任务记录；不会自动重复创建'}
                state['items'].append(entry)
                save_json(path, state)
                try:
                    result = self.workbench_video_create(source['project_id'], [source['group_id']], config, voice_source, request_id)
                    entry.update(status='created', job_ids=result['job_ids'], batch_id=result['batch_id'], message='')
                except Exception as exc:
                    partial = [p['id'] for p in self._video_jobs().service.projects.list() if p.get('video_job', {}).get('creation_id') == request_id and p['video_job']['source_project_id'] == source['project_id'] and p['video_job']['source_group_id'] == source['group_id']]
                    entry.update(status='creating' if partial else 'failed', job_ids=partial, message=str(exc) + ('；已有部分记录，请核对任务，不会自动重建' if partial else ''))
                save_json(path, state)
            return copy.deepcopy(state)

    def workbench_group_batch(self, scope, action):
        from .workbench import save_json
        from .workflow_api import WorkflowAPI
        from .project_run import workflow_lock
        from pathlib import Path
        scope = normalize_scope(scope)
        if action not in ('confirm', 'unconfirm', 'trash', 'restore'):
            raise ValueError('不支持的图片组操作')
        results = []
        for source in scope:
            try:
                p = self._service.projects.get(source['project_id'])
                with workflow_lock(Path(p['directory'])):
                    p = self._service.projects.get(source['project_id'])
                    g = next((g for g in p['groups'] if g['id'] == source['group_id']), None)
                    if not g or any(source.get(k) != g.get(k) for k in ('frame_a', 'frame_b')):
                        raise ValueError('图片组已变化，请刷新后重新检查')
                    if action in ('trash', 'restore'):
                        with _batch_lock, workflow_lock(self._service.projects.root):
                            data = trash_records(self._service)
                            key = group_key(p['id'], g['id'])
                            if action == 'trash': data[key] = {'at': time.time()}
                            else: data.pop(key, None)
                            save_json(self._service.projects.root / 'workbench-trash.json', data)
                    else:
                        if group_key(p['id'], g['id']) in trash_records(self._service):
                            raise ValueError('请先从回收站恢复图片组')
                        flow = WorkflowAPI(self._service)
                        flow.execute(p['id'], 'update_groups', {'expected_revision': flow.summary(p['id'])['revision'],
                            'updates': [{'group_id': g['id'], 'confirmed': action == 'confirm'}]})
                results.append({**source, 'ok': True})
            except Exception as exc:
                results.append({**source, 'ok': False, 'message': str(exc)})
        if hasattr(self, 'version'): self.version += 1
        return results

    def workbench_media_batch(self, item_ids, action):
        from .workbench import save_json
        from .project_run import workflow_lock
        if not isinstance(item_ids, list) or not item_ids or len(item_ids) > 500 or action not in ('trash', 'restore', 'accept', 'unaccept'):
            raise ValueError('请选择有效的视频和操作')
        known = {i['id']: i for i in self.workbench_library()['items']}
        results = []
        for key in dict.fromkeys(item_ids):
            try:
                item = known.get(key)
                if not item: raise ValueError('视频记录已变化，请刷新')
                if action in ('accept', 'unaccept'):
                    if item.get('trashed'): raise ValueError('请先从回收站恢复视频')
                    self.workbench_review(key, action == 'accept')
                else:
                    if item['status'] in ('running', 'submitted', 'submitting', 'tracking'):
                        raise ValueError('视频正在处理，请等待完成')
                    with _batch_lock, workflow_lock(self._service.projects.root):
                        data = trash_records(self._service)
                        if action == 'trash': data['media:' + key] = {'at': time.time()}
                        else: data.pop('media:' + key, None)
                        save_json(self._service.projects.root / 'workbench-trash.json', data)
                results.append({'id': key, 'ok': True})
            except Exception as exc:
                results.append({'id': key, 'ok': False, 'message': str(exc)})
        return results

    def workbench_video_approve_batch(self, cards, batch_key=None):
        if not isinstance(cards, list) or not cards or len(cards) > 500:
            raise ValueError('请选择待确认的费用卡')
        queue = self._video_jobs()
        with queue.lock:
            rows = {r['id']: r for r in queue.rows()}
            if batch_key is not None:
                expected = [r for r in rows.values() if (r.get('creation_id') or r['batch_id']) == batch_key and r['phase'] != 'canceled' and not r.get('submit_id')]
                if not expected or {r['id'] for r in expected} != {c['job_id'] for c in cards}:
                    raise ValueError('本次批次范围已变化，请重新核对完整批次')
                for row in expected:
                    siblings = [r for r in rows.values() if r['batch_id'] == row['batch_id'] and r['phase'] != 'canceled']
                    if sorted(r['part'] for r in siblings) != list(range(1, row['part_count'] + 1)):
                        raise ValueError('本组分段不完整，不能提交部分视频')
            if len({c['job_id'] for c in cards}) != len(cards):
                raise ValueError('费用卡重复')
            totals, balances = {}, {}
            for c in cards:
                row = rows.get(c['job_id'], {})
                card = row.get('card') or {}
                if row.get('phase') != 'awaiting_approval' or row.get('busy') or card.get('id') != c['card_id'] or card.get('ceiling') != c['ceiling'] or card.get('sufficient') is not True:
                    raise ValueError('费用卡或任务状态已变化，请刷新后重新核对')
                provider = card['provider']
                totals[provider] = totals.get(provider, 0) + card['ceiling']
                balances[provider] = min(balances.get(provider, float('inf')), card['available_credits'])
            if any(total > balances[provider] for provider, total in totals.items()):
                raise ValueError('所选任务总费用超过可用积分，请减少选择或充值后刷新报价')
            results = []
            for c in cards:
                try:
                    if queue.approve(c['job_id'], c['card_id'], c['ceiling']) is not True:
                        raise ValueError('本条未提交，请检查任务状态')
                    results.append({'id': c['job_id'], 'ok': True})
                except Exception as exc:
                    results.append({'id': c['job_id'], 'ok': False, 'message': str(exc)})
            return results
