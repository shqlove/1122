"""Scoped generation preparation and reversible, unsubmitted draft operations."""
import copy
from contextlib import ExitStack
from pathlib import Path

from .agent_model import AGENT_EFFORT, AGENT_MODEL
from .generation import _sha
from .h3_baseline import review_valid
from .projects import original_frame, project_asset
from .project_run import workflow_lock


class StudioWorkbenchMixin:
    def _studio_group_check(self, project, group):
        try:
            for side in ('a', 'b'):
                path = (project_asset(project, group['repaired_' + side]) if group.get('repaired_' + side) else
                        original_frame(project, group['frame_' + side], self._service.config['ffmpeg'], self._service.config['ffprobe']))
                if not review_valid(group, {'side': side, 'sha256': _sha(path)}):
                    return 'pending'
            return 'clean'
        except Exception:
            return 'unavailable'

    def workbench_video_check(self, scope, start=False):
        from .batch_workbench import normalize_scope, trash_records, group_key
        scope = normalize_scope(scope)
        missing, errors = [], []
        trash = trash_records(self._service)
        for source in scope:
            try:
                p = self._service.projects.get(source['project_id'])
                g = next(g for g in p['groups'] if g['id'] == source['group_id'])
                if group_key(p['id'], g['id']) in trash or g.get('needs_review'):
                    raise ValueError('图片组已移除或人物尚未确认')
                status = self._studio_group_check(p, g)
                if status == 'unavailable':
                    raise ValueError('参考图片无法读取，请恢复素材')
                if status != 'clean':
                    missing.append(source)
            except Exception as exc:
                errors.append({**source, 'message': str(exc) or '图片组不存在'})
        result = {'ready': not missing and not errors, 'missing': missing, 'errors': errors}
        if start and missing and not errors:
            if not hasattr(self, '_agent'):
                raise ValueError('图片需要 Agent 检查，请在桌面工作台连接助手后重试')
            agent = self._agent
            # The turn identity is captured under the same lock used by send().
            with agent.lock:
                if not agent.connection_state()['connected']:
                    raise ValueError('请在右侧助手连接 Agent 后重新准备；已通过检查的图片不会重复清理')
                pid = missing[0]['project_id']
                projects = {}
                for item in missing:
                    projects.setdefault(item['project_id'], []).append(item['group_id'])
                agent.send(pid, '为本次生成准备参考图片。仅处理 allowed_scopes：逐组 view_group 实际看 A/B 图片；'
                           '有水印或叠加字幕则 repair_region 清理并重新 view_group 检查修复质量；无残留且自然后，'
                           '用刚查看文件的 SHA256 调用 record_reference_review。场景实物文字保留。'
                           '不要创建任务、报价、提交生成或后期。失败时保留检查结果并说明具体原因。',
                           AGENT_MODEL, AGENT_EFFORT, {'projects': projects, 'cleanup_only': True})
                result['agent'] = {'project_id': pid, 'started_at': agent._session(pid)['execution']['started_at']}
        return result

    def workbench_video_check_status(self, project_id, started_at):
        state = self._agent.state(project_id)
        execution = state.get('execution', {})
        same = execution.get('started_at') == started_at
        return {'busy': bool(same and state['busy']), 'phase': execution.get('phase') if same else '检查会话已结束',
                'error': state.get('error') if same else None}

    def workbench_video_voice(self):
        from .h3_baseline import ROOT
        from .media_server import MediaServer
        if not hasattr(self, '_media_server'):
            self._media_server = MediaServer()
        return {'url': self._media_server.register(ROOT / 'voice.mp3')}

    def workbench_video_draft(self, batch_id, action):
        """Invalidate a complete video's drafts; never mutate a submitted snapshot."""
        if action not in ('edit', 'remove'):
            raise ValueError('不支持的草稿操作')
        queue = self._video_jobs()
        with queue.lock, ExitStack() as locks:
            jobs = [p for p in queue.service.projects.list() if p.get('video_job', {}).get('batch_id') == batch_id]
            if not jobs:
                raise ValueError('草稿不存在')
            for job in sorted(jobs, key=lambda p: p['id']):
                locks.enter_context(workflow_lock(Path(job['directory'])))
            jobs = [queue.service.projects.get(p['id']) for p in jobs]
            for job in jobs:
                tasks = job.get('generation', {}).get('tasks', {}).values()
                if job['id'] in queue.active or job['queue_state']['phase'] not in ('needs_review', 'attention', 'awaiting_approval', 'preparing', 'canceled') or any(t.get('submit_id') or t.get('task_id') or t.get('status', 'draft') not in ('draft', 'canceled') for t in tasks):
                    raise ValueError('本组正在处理或已有提交记录，不能修改或移除')
            meta = jobs[0]['video_job']
            result = {'scope': [{'project_id': meta['source_project_id'], 'group_id': meta['source_group_id']}],
                      'config': copy.deepcopy(meta['requested_config']), 'voice_source': meta['voice_source']}
            for job in jobs:
                job['generation'].pop('pending_approval', None)
                job['generation'].pop('quote', None)
                for task in job['generation'].get('tasks', {}).values():
                    task['status'] = 'canceled'
                job['queue_state'] = {'phase': 'canceled', 'error': '已转入编辑，需重新报价' if action == 'edit' else '已移除草稿'}
                queue.service.projects.save(job)
                queue.runtime_errors.pop(job['id'], None)
            return result
