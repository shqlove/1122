import copy
import hashlib
import json
import queue
import threading
import time
import uuid
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path

from .projects import ProjectRepository, assert_source_unchanged, character_filter, export_groups, original_frame, normalize_character_groups
from .providers import ANALYSIS_SCHEMA, ProviderError, analyze_images, request_images, validate_analysis
from .dialogue_analysis import (CLUSTER_SCHEMA, consolidate_dialogues, pair_span, refinement_frames,
                                validate_clusters, verified_group)
from .repair import rectangle_mask, repair_image
from .scanner import character_probe_frames, expanded_character_frames, discovery_frames, merge_pair_proposals, scan_video
from .character_probe import PROBE_SCHEMA, validate_probe
from .project_run import analysis_lock


def frame_batches(frames: list) -> list[list]:
    batches, start = [], 0
    while start < len(frames):
        batches.append(frames[start:start + 24])
        if start + 24 >= len(frames):
            break
        start += 20
    return batches


class Service:
    def __init__(self, config: dict):
        self.config = config
        self.projects = ProjectRepository(Path(config['data']))

    def analyze(self, identifier: str, start: float = 0, end=None, *, scan_only=False, progress=None) -> dict:
        directory = Path(self.projects.get(identifier)['directory'])
        with analysis_lock(directory):
            return self._analyze(identifier, start, end, scan_only=scan_only, progress=progress)

    def _analyze(self, identifier: str, start: float = 0, end=None, *, scan_only=False, progress=None) -> dict:
        project = self.projects.get(identifier)
        assert_source_unchanged(project)
        if project['groups']:
            raise ValueError('已有配组，先保留现有结果；如需重新分析，请将视频导入为新项目')
        started = time.monotonic()
        user_progress = progress

        def progress(value, message):
            project['run_status'] = {'progress': value, 'message': message}
            self.projects.save(project)
            if user_progress:
                user_progress(value, message)

        phase_timings = {'scan': 0.0}
        scan_reused = project['scan'] is not None and not scan_only
        try:
            if project['scan'] is None or scan_only:
                scan_started = time.monotonic()
                scan_dir = Path(project['directory']) / f'scan-{uuid.uuid4().hex[:8]}'
                project['phase'], project['error'] = 'scanning', None
                self.projects.save(project)
                report = scan_video(Path(project['source']), scan_dir, self.config['ffmpeg'], self.config['ffprobe'],
                                    start, end, (lambda v, m: progress(v * .15, m)) if progress else None)
                phase_timings['scan'] = round(time.monotonic() - scan_started, 3)
                project.update(scan=report, scan_directory=str(scan_dir), phase='scanned', groups=[])
                self.projects.save(project)
            if scan_only:
                return project
            frames = project['scan']['frames']
            if len(frames) < 2:
                raise ValueError('有效候选图少于两张，请更换时间范围')
            if not self.config.get('codex'):
                raise ValueError('未找到 Codex CLI，请先配置其路径并登录')
            project['phase'] = 'analyzing'
            project['error'], project['error_code'] = None, None
            self.projects.save(project)
            phase_started = time.monotonic()
            proposals = []
            project['analysis_used_keys'] = []
            requests_before = project.get('analysis_requests_started', 0)
            hits_before = project.get('analysis_cache_hits', 0)
            probe_frames = []
            probe_results = []
            discovery = discovery_frames(frames)
            if character_filter(project).get('required_character'):
                probe_frames = character_probe_frames(frames)
                probe_tasks = [(probe_frames[i:i + 24],
                                '角色定位任务：逐张判断目标角色是否出现在画面中，不要求对话或正脸。'
                                '只在能明确排除目标角色时填写not_matched；模糊、遮挡、背影、远景或无法辨认均填uncertain。'
                                '多人画面只要包含目标角色也填matched。此阶段不要以画质或缺少反打排除。')
                               for i in range(0, len(probe_frames), 24)]
                answers = self._parallel_requests(project, 'probe', probe_tasks, progress)
                for (batch, _), answer in zip(probe_tasks, answers):
                    probe_results.extend(dict(item, frame_id=batch[item['frame']]['id'],
                                              shot_id=batch[item['frame']]['shot_id'])
                                         for item in answer['frames'])
                project['character_probe'] = probe_results
                related_frames = expanded_character_frames(
                    frames, {item['frame_id']: item['status'] for item in probe_results})
                discovery = character_probe_frames(related_frames)
                self.projects.save(project)
            phase_timings['character_probe'] = round(time.monotonic() - phase_started, 3)
            phase_started = time.monotonic()
            batches = frame_batches(discovery)
            discovery_tasks = []
            for batch in batches:
                context = ('这是按镜头稀疏取样的初筛，先定位目标角色及其交流对象，再为每个人物组合寻找 A/B 身份锚点。'
                           '初筛锚点只需足以辨认人物和交流关系，最佳正脸和画质由后续全段候选复核选择；'
                           '不要因当前锚点表情、角度欠佳就漏掉整段有交流证据的候选。'
                           '同一场交流中同两个人反复切 A/B 只提议一组；一对多交流须逐个列出不同交流对象。'
                           '例如目标角色分别与乙、丙交流且都有独立镜头，应输出目标角色/乙、目标角色/丙两组，允许共享目标角色锚点。'
                           '必须检查本批全部交流对象，不限制每场或整集的组数；旁观者不算交流对象。'
                           '同场不同人物组合的独立交流可以分开。跨场景不得合并。'
                           '必须有两个不同真人的独立镜头和明确交流证据，不能用同框两张图凑对；'
                           '缺少反打的片段只在summary说明，不要编造A/B。'
                           '用span_start/span_end覆盖本批次可见的整段交流，以便后续在全段选最佳帧。'
                           '人物描述必须结合脸部和服装，不能只靠同色衣服。时间点（秒）、镜头编号：'
                           + json.dumps([{'time': round(f['seconds'], 3), 'shot': f['shot_id']} for f in batch])
                           + '。这是带重叠帧的连续批次，跨批重复由主流程统一合并。')
                discovery_tasks.append((batch, context))
            # Each subagent owns a continuous shard and keeps the previous summary.
            # A preceding batch warms up each shard boundary; its findings also enter dedupe.
            shard_count = max(1, min(self._analysis_workers(), len(discovery_tasks)))
            boundaries = [index * len(discovery_tasks) // shard_count for index in range(shard_count + 1)]
            discovery_shards = [discovery_tasks[max(0, start - 1):end]
                                for start, end in zip(boundaries, boundaries[1:]) if start < end]
            shard_results = self._parallel_requests(project, 'discovery', discovery_shards, progress, chained=True)
            discovered = [(task[0], result) for shard, results in zip(discovery_shards, shard_results)
                          for task, result in zip(shard, results)]
            for batch, result in discovered:
                for pair in result['pairs']:
                    first = pair.get('span_start') if pair.get('span_start') is not None else min(pair['frame_a'], pair['frame_b'])
                    last = pair.get('span_end') if pair.get('span_end') is not None else max(pair['frame_a'], pair['frame_b'])
                    proposals.append(dict(pair, frame_a=batch[pair['frame_a']]['id'],
                                          frame_b=batch[pair['frame_b']]['id'],
                                          scene_start_seconds=batch[first]['seconds'],
                                          scene_end_seconds=batch[last]['seconds']))
            phase_timings['discovery'] = round(time.monotonic() - phase_started, 3)
            phase_started = time.monotonic()
            proposals = merge_pair_proposals(proposals, frames)
            project['analysis_proposals'] = proposals
            self.projects.save(project)
            proposals = self._deduplicate(project, proposals, frames, progress)
            phase_timings['dedupe'] = round(time.monotonic() - phase_started, 3)
            phase_started = time.monotonic()
            # 第一阶段：先确认独立的连续对话片段（同段反复切镜已经去重）。
            # 第二阶段：在每段的所有候选镜头中选择一组最适合图生视频的 A/B。
            groups, excluded, episodes = [], [], []
            verification_tasks = []
            for proposal in proposals:
                choices = refinement_frames(proposal, frames)
                context = ('复核任务：这是已识别的一段连续人物交流；只从本段里选最佳的一组 A/B，不拆成多组。'
                           '附件0和1是初步识别的人物身份锚点，其余来自同段的不同镜头/时刻。'
                           '必须为两个不同真人、同一连续交流的标准正反打，两张图分别是清楚的单人主体，'
                           '脸部可见、无遮挡或少遮挡、接近正脸、画面不模糊、表情自然、无字幕压脸。'
                           '允许替换到本段更清晰的其他镜头，但人物身份不得改变。'
                           '同一人的不同表情、多人合照、背影、严重侧脸/遮挡、机器人、屏幕人物、'
                           '没有独立反打或不能证明交流关系，均不得进入成功结果。'
                           '如果任一侧没有可用于图生视频的清晰正脸，返回空pairs并解释原因；'
                           '不请求人工补选。若仍有疑虑，needs_review=true，系统将自动排除。'
                           '仅返回一组；span_start/span_end填null。'
                           '原识别说明：' + proposal['reason'] + '；时间和镜头：'
                           + json.dumps([{'time': round(f['seconds'], 3), 'shot': f['shot_id']} for f in choices]))
                verification_tasks.append((choices, context))
            verification_results = self._parallel_requests(project, 'verify', verification_tasks, progress)
            for index, (proposal, task, verified) in enumerate(zip(proposals, verification_tasks, verification_results)):
                choices = task[0]
                group = verified_group(proposal, verified, choices)
                episode = {'index': index + 1, 'start_seconds': proposal['scene_start_seconds'],
                           'end_seconds': proposal['scene_end_seconds'],
                           'anchor_a': proposal['frame_a'], 'anchor_b': proposal['frame_b']}
                if group['needs_review']:
                    episode.update(status='excluded', reason=group.get('verification_reason') or '缺少合格的正反打')
                    excluded.append(episode)
                else:
                    episode.update(status='character_uncertain' if group.get('required_character_status') == 'uncertain' else 'ready', group_id=group['id'])
                    groups.append(group)
                episodes.append(episode)
            # 无合格独立反打的片段不放入待确认，也不会要求用户手动筛选。
            project['dialogue_segments'] = episodes
            project['excluded_dialogues'] = excluded
            self.projects.save(project)
            phase_timings['verify'] = round(time.monotonic() - phase_started, 3)
            phase_started = time.monotonic()
            prepared, sizes = 0, set()
            from PIL import Image
            frame_ids = list(dict.fromkeys(group[f'frame_{side}'] for group in groups for side in ('a', 'b')))

            def prepare_original(frame_id):
                image_path = original_frame(project, frame_id, self.config.get('ffmpeg'), self.config.get('ffprobe'))
                with Image.open(image_path) as image:
                    image.load()
                    return image.size

            # Bound full-resolution decoding separately from network-heavy Codex work.
            with ThreadPoolExecutor(max_workers=2, thread_name_prefix='dialogue-original') as executor:
                for index, size in enumerate(executor.map(prepare_original, frame_ids)):
                    sizes.add(size)
                    if progress:
                        progress(.94 + .05 * (index + 1) / max(1, len(frame_ids)),
                                 f'准备原尺寸截图 {index + 1}/{len(frame_ids)}')
            prepared = len(groups) * 2
            phase_timings['originals'] = round(time.monotonic() - phase_started, 3)
            project['groups'] = groups
            normalize_character_groups(project)
            ready = len(groups)
            usage = [project.get('analysis_usage', {})[key] for key in project['analysis_used_keys']
                     if key in project.get('analysis_usage', {})]
            project['analysis_report'] = {
                'protocol_version': 9, 'analysis_workers': self._analysis_workers(), 'phase_timings_seconds': phase_timings, 'scan_reused': scan_reused, 'sampling': 'character_probe_then_sparse_discovery_dense_refinement', 'discovery_batches': len(batches), 'discovery_shards': len(discovery_shards),
                'boundary_warmup_batches': sum(len(shard) for shard in discovery_shards) - len(batches), 'discovery_images': len(discovery),
                'dialogue_segments': len(episodes), 'excluded_segments': len(excluded),
                'character_probe_images': len(probe_frames), 'character_probe_uncertain': sum(item['status'] == 'uncertain' for item in probe_results),
                'candidate_images': len(frames), 'raw_proposals': len(project['analysis_proposals']),
                'ready_groups': ready, 'character_uncertain_groups': sum(g.get('required_character_status') == 'uncertain' for g in groups), 'review_groups': 0, 'prepared_images': prepared, 'unique_prepared_images': len(frame_ids),
                'image_sizes': sorted(sizes), 'codex_requests_started': project.get('analysis_requests_started', 0),
                'cache_hits_this_run': project.get('analysis_cache_hits', 0) - hits_before,
                'new_requests_this_run': project.get('analysis_requests_started', 0) - requests_before,
                'usage': {key: sum(u.get(key, 0) for u in usage) for key in ('input_tokens', 'cached_input_tokens', 'output_tokens')},
                'usage_records': len(usage)}
            project.update(phase='review', error=None, analysis_elapsed_seconds=round(time.monotonic() - started, 3))
            project['analysis_progress'] = {'step': 'complete', 'completed': len(groups), 'total': len(groups)}
            self.projects.save(project)
            if progress:
                progress(1, f'拆解完成：{ready} 组可生成正反打，{len(excluded)} 段不合格已自动排除')
            return project
        except Exception as exc:
            project.update(phase='error', error=str(exc), error_code=getattr(exc, 'code', 'processing_error'))
            self.projects.save(project)
            raise

    def _analysis_workers(self):
        return max(1, min(4, int(self.config.get('analysis_workers', 3))))

    def _parallel_requests(self, project, kind, tasks, progress=None, *, chained=False):
        """Run independent Codex workers with a single owner of project persistence."""
        if not tasks:
            return []
        workers = min(self._analysis_workers(), len(tasks))
        results = [None] * len(tasks)
        completed = 0
        total_requests = sum(len(task) for task in tasks) if chained else len(tasks)
        checkpoints = queue.Queue()
        stop_dispatch = threading.Event()

        def checkpoint(snapshot, success):
            used = snapshot.get('analysis_used_keys', [])
            delta = {'analysis_used_keys': list(used), 'analysis_filter': snapshot.get('analysis_filter', {}),
                     '_request_completed': success}
            for field in ('analysis_cache', 'analysis_usage', 'invalid_analysis_cache'):
                delta[field] = {key: snapshot[field][key] for key in used if key in snapshot.get(field, {})}
            for field in ('analysis_requests_started', 'analysis_cache_hits'):
                delta[field] = snapshot.get(field, 0)
                snapshot[field] = 0
            snapshot['analysis_used_keys'] = []
            checkpoints.put(copy.deepcopy(delta))

        def report_completion():
            project['analysis_progress'] = {
                'step': kind, 'completed': completed, 'total': total_requests, 'workers': workers}
            self.projects.save(project)
            if progress:
                base, span, label = {'probe': (.16, .14, '定位目标人物'),
                                     'discovery': (.30, .35, '补看相关镜头并识别对话')}.get(
                                         kind, (.75, .18, '二次检查与选图'))
                progress(base + span * completed / total_requests,
                         f'{label} {completed}/{total_requests}，{workers} 个子任务并行')

        def perform_request(snapshot, frames, context, persist):
            if persist:
                return self._cached_request(snapshot, kind, frames, context)
            success = False
            try:
                answer = self._cached_request(snapshot, kind, frames, context, persist=False)
                success = True
                return answer
            finally:
                checkpoint(snapshot, success)

        def run_task(snapshot, task, persist):
            nonlocal completed
            if not chained:
                frames, context = task
                return perform_request(snapshot, frames, context, persist)
            previous_summary = ''
            answers = []
            for frames, context in task:
                if stop_dispatch.is_set():
                    break
                context += '。上一批场景摘要（只辅助，不可虚构当前图片）：' + previous_summary
                answer = perform_request(snapshot, frames, context, persist)
                answers.append(answer)
                previous_summary = answer['summary'][-1200:]
                if persist:
                    completed += 1
                    report_completion()
            return answers

        report_completion()
        if workers == 1:
            for index, task in enumerate(tasks):
                results[index] = run_task(project, task, True)
                if not chained:
                    completed += 1
                    report_completion()
            return results

        def execute(snapshot, task):
            try:
                result = run_task(snapshot, task, False)
                return snapshot, result, None
            except Exception as exc:
                stop_dispatch.set()
                return snapshot, None, exc

        def merge(snapshot):
            nonlocal completed
            project.setdefault('analysis_filter', snapshot.get('analysis_filter', {}))
            for key in snapshot.get('analysis_used_keys', []):
                if key not in project.setdefault('analysis_used_keys', []):
                    project['analysis_used_keys'].append(key)
                for field in ('analysis_cache', 'analysis_usage', 'invalid_analysis_cache'):
                    if key in snapshot.get(field, {}):
                        project.setdefault(field, {})[key] = snapshot[field][key]
                if key in snapshot.get('invalid_analysis_cache', {}) and key not in snapshot.get('analysis_cache', {}):
                    project.setdefault('analysis_cache', {}).pop(key, None)
            for field in ('analysis_requests_started', 'analysis_cache_hits'):
                project[field] = project.get(field, 0) + snapshot.get(field, 0)
            if snapshot.get('_request_completed'):
                completed += 1
                report_completion()
            else:
                self.projects.save(project)

        first_error = None
        next_index = 0
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix='dialogue-codex') as executor:
            pending = {}

            def submit(index):
                snapshot = copy.deepcopy(project)
                snapshot['analysis_used_keys'] = []
                snapshot['analysis_requests_started'] = 0
                snapshot['analysis_cache_hits'] = 0
                pending[executor.submit(execute, snapshot, tasks[index])] = index

            while next_index < workers:
                submit(next_index)
                next_index += 1
            while pending:
                done, _ = wait(pending, timeout=.1, return_when=FIRST_COMPLETED)
                while not checkpoints.empty():
                    merge(checkpoints.get_nowait())
                for future in done:
                    index = pending.pop(future)
                    snapshot, result, error = future.result()
                    merge(snapshot)
                    if error is not None:
                        if first_error is None:
                            first_error = error
                    else:
                        results[index] = result
                # Finish already-running workers, but stop dispatching after any failure.
                while first_error is None and not stop_dispatch.is_set() and len(pending) < workers and next_index < len(tasks):
                    submit(next_index)
                    next_index += 1
        if first_error is not None:
            raise first_error
        return results

    def _cached_request(self, project, kind, frames, context, *, persist=True):
        project.setdefault('analysis_filter', character_filter(project))
        required_character = character_filter(project).get('required_character')
        references = []
        if required_character and kind in ('probe', 'discovery', 'verify'):
            library = self.projects.root / 'character-references.json'
            entries = json.loads(library.read_text(encoding='utf-8')) if library.exists() else {}
            for item in entries.get(required_character, []):
                path = self.projects.root / item['file']
                if (item.get('confirmed_by_user') or (item.get('role') == 'supplementary'
                        and item.get('web_researched') and item.get('source_url'))) and path.is_file():
                    references.append(dict(item, path=path, digest=hashlib.sha256(path.read_bytes()).hexdigest()))
            labels = [{'attachment': len(frames) + i, 'role': r['role']} for i, r in enumerate(references)]
            profile_path = self.projects.root / 'character-profiles.json'
            profile = json.loads(profile_path.read_text(encoding='utf-8')).get(required_character, {}) if profile_path.exists() else {}
            context = ('本项目人物筛选要求（最高优先级）：先与用户确认的角色参考图对照，再判断对话。目标角色：'
                       + required_character + '。候选图索引仅为0至' + str(len(frames)-1)
                       + '；其后附件仅用于角色对照，禁止选为frame或span。参考图：'
                       + json.dumps(labels, ensure_ascii=False)
                       + '。positive是用户确认的目标角色，negative是用户确认的易混淆其他角色。'
                       'supplementary是有来源的网络辅助图，并非用户确认正例；与正例冲突时标uncertain，不能仅凭辅助图确认。'
                       + '角色资料和辅助观察：' + json.dumps(profile, ensure_ascii=False) + '。'
                       '比较稳定的角色外貌特征，不能仅凭服装、字幕称呼或剧情常识猜测；仅提到该角色名字不算参与。'
                       'required_character_status填写matched、not_matched或uncertain。'
                       'matched必须填写required_character_side为a或b及required_character_evidence对照依据。'
                       '只有明确不匹配才排除；完全判断不准也必须保留合格对话pair，填写uncertain和原因。'
                       '没有可用正例时一律uncertain，不能凭角色名字确认。'
                       '角色不确定不要设needs_review；该字段仅表示画质或正反打关系不合格。'
                       '换图后重新对照参考；参考图只用于识别，绝不能替代当前视频截图。后续任务：' + context)
        if kind == 'probe':
            context += ('。本轮只返回frames数组，逐一覆盖全部候选索引，每项frame/status/reason；'
                        '前述pair字段规则仅供后续阶段使用，本轮以角色定位schema为准。')
        key = hashlib.sha256(json.dumps({'kind': kind, 'frames': [(f['id'], f['seconds']) for f in frames],
                                         'source': project['source_sha256'], 'context': context,
                                         'references': [(r['role'], r['digest']) for r in references], 'version': 4}, sort_keys=True).encode()).hexdigest()
        cache = project.setdefault('analysis_cache', {})
        used = project.setdefault('analysis_used_keys', [])
        if key not in used:
            used.append(key)
        if key in cache:
            try:
                if kind == 'probe':
                    validate_probe(cache[key], len(frames))
                elif kind == 'dedupe':
                    validate_clusters(cache[key], len(frames) // 2)
                else:
                    validate_analysis(cache[key], len(frames))
            except ProviderError:
                project.setdefault('invalid_analysis_cache', {})[key] = cache.pop(key)
                if persist:
                    self.projects.save(project)
            else:
                project['analysis_cache_hits'] = project.get('analysis_cache_hits', 0) + 1
                return cache[key]
        folder = Path(project['directory']) / f'{kind}-{uuid.uuid4().hex[:10]}'
        project['analysis_requests_started'] = project.get('analysis_requests_started', 0) + 1
        if persist:
            self.projects.save(project)
        images = [Path(project['scan_directory']) / f['file'] for f in frames]
        if kind == 'probe':
            result = validate_probe(request_images(
                self.config['codex'], images + [r['path'] for r in references], folder,
                context, PROBE_SCHEMA), len(frames))
            if not any(r['role'] == 'positive' for r in references):
                result = {'frames': [dict(item, status='uncertain', reason='缺少用户确认正例，保留补看。')
                                     for item in result['frames']]}
        elif kind == 'dedupe':
            result = validate_clusters(request_images(self.config['codex'], images, folder, context, CLUSTER_SCHEMA), len(frames) // 2)
        else:
            if references:
                result = validate_analysis(request_images(
                    self.config['codex'], images + [r['path'] for r in references], folder,
                    context, ANALYSIS_SCHEMA), len(frames))
            else:
                result = analyze_images(self.config['codex'], images, folder, context)
            if required_character:
                accepted = []
                has_positive = any(r['role'] == 'positive' for r in references)
                for pair in result['pairs']:
                    pair = dict(pair)
                    status = pair.get('required_character_status')
                    evidence = (pair.get('required_character_evidence') or '').strip()
                    if not has_positive or not evidence or status not in ('matched', 'not_matched', 'uncertain'):
                        status = 'uncertain'
                    if status == 'matched' and pair.get('required_character_side') not in ('a', 'b'):
                        status = 'uncertain'
                    if status == 'not_matched':
                        continue
                    pair['required_character_status'] = status
                    if status == 'uncertain':
                        pair['required_character_side'] = None
                        pair['required_character_evidence'] = evidence or '缺少可靠的角色匹配依据，请人工检查。'
                    accepted.append(pair)
                result = dict(result, pairs=accepted)
        cache[key] = result
        for line in (folder / 'events.jsonl').read_text(encoding='utf-8').splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get('type') == 'turn.completed' and isinstance(event.get('usage'), dict):
                project.setdefault('analysis_usage', {})[key] = event['usage']
        if persist:
            self.projects.save(project)
        return result

    def _deduplicate(self, project, proposals, frames, progress):
        if len(proposals) < 2:
            return proposals
        known = {f['id']: f for f in frames}
        calls = 0

        def compare(window):
            nonlocal calls
            calls += 1
            if progress:
                progress(.7, f'合并同段重复对话，第 {calls} 组比较')
            chosen = [known[proposals[i][side]] for i in window for side in ('frame_a', 'frame_b')]
            descriptions = [{'group': n, 'images': [n * 2, n * 2 + 1], 'range': pair_span(proposals[i], frames),
                             'reason': proposals[i]['reason']} for n, i in enumerate(window)]
            context = ('去重任务：每组恰好两张图片，不生成新配对，只将同一连续对话、同两个真人的重复组归并。'
                       '一对多中的不同交流对象分别保留，即使共享目标角色锚点或时间段也不能合并。'
                       '不同人物组合、不同地点或时间段再次出现不能因服装相同就合并。短暂插入无关镜头仍在继续的对话可以合并。'
                       '不确定是否重复时分开保留。每个输入组必须且只属于一个cluster，members使用组索引，'
                       'representative从members选画面较好的一组。组信息：' + json.dumps(descriptions, ensure_ascii=False))
            return self._cached_request(project, 'dedupe', chosen, context)
        return consolidate_dialogues(proposals, frames, compare)

    def repair(self, identifier: str, group_id: str, side: str, rect: list[int], *, apply_all=False, progress=None) -> dict:
        if side not in ('a', 'b'):
            raise ValueError('图片位置无效')
        project = self.projects.get(identifier)
        group = next((g for g in project['groups'] if g['id'] == group_id), None)
        if group is None:
            raise ValueError('配组不存在')
        from PIL import Image
        anchor = original_frame(project, group[f'frame_{side}'], self.config['ffmpeg'], self.config['ffprobe'])
        with Image.open(anchor) as image:
            w, h = image.size
            rectangle_mask(image.size, tuple(rect))
        targets = [(g, s) for g in project['groups'] if g['selected'] for s in ('a', 'b')] if apply_all else [(group, side)]
        from .dialogue_segments import has_tasks
        if any(has_tasks(project, target['id']) for target, _ in targets):
            raise ValueError('已有生成草稿或任务，请先新建轮次再修复图片')
        folder = Path(project['directory']) / f'repair-{uuid.uuid4().hex[:10]}'
        folder.mkdir()
        for index, (target, position) in enumerate(targets):
            if progress:
                progress(index / len(targets), f'本地修复 {index + 1}/{len(targets)}，CPU 处理中')
            original = original_frame(project, target[f'frame_{position}'], self.config['ffmpeg'], self.config['ffprobe'])
            with Image.open(original) as image:
                tw, th = image.size
            scaled = (round(rect[0] / w * tw), round(rect[1] / h * th), round(rect[2] / w * tw), round(rect[3] / h * th))
            mask = folder / f'{target["id"]}-{position}-mask.png'
            rectangle_mask((tw, th), scaled).save(mask)
            output = folder / f'{target["id"]}-{position}.png'
            report = repair_image(original, mask, output, Path(self.config['model']))
            (folder / f'{target["id"]}-{position}.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            target[f'repaired_{position}'], target[f'mask_{position}'] = str(output), str(mask)
            self.projects.save(project)
        project['phase'] = 'review'
        self.projects.save(project)
        return project

    def export(self, identifier: str, destination: Path) -> Path:
        project = self.projects.get(identifier)
        result = export_groups(project, destination, self.config['ffmpeg'], self.config['ffprobe'])
        project['exports'].append(str(result))
        project['phase'] = 'exported'
        self.projects.save(project)
        return result

