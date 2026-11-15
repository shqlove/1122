"""CompShare H3: local drafts, explicit credit approval, durable idempotency."""
import base64
import hashlib
import json
import math
import mimetypes
import os
import re
import shutil
import tempfile
import time
import uuid
from http.client import HTTPException
from pathlib import Path
from urllib.request import Request, urlopen, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse

from PIL import Image
from .generation import CanvasWorkflow, CanvasError, bind_prompt, _sha
from .media import probe
from .projects import character_filter
from .h3_errors import H3Error, failure, network_reason, provider_reason, redact
from .h3_baseline import BASELINE, BASELINE_ID, NO_SUBTITLES, apply_baseline, uses_haoge, validate_baseline, review_valid
from .dialogue_segments import POLICY, enabled, expanded_groups, assembly_plans

MODELS = ('MiniMax-H3', 'minimax-h3-lite')
RATES = {'480P': 5, '768P': 10, '1080P': 19, '2K': 20, '4K': 22}
BASE = 'https://cp.compshare.cn/minimax/v2/'
CHANNELS = {
    'dreamina': {'label': '即梦', 'model': 'seedance_2.0_fast_vip', 'duration': 15, 'ratio': '4:3', 'resolution': '720p'},
    'compshare': {'label': '优云智算 H3', 'model': 'MiniMax-H3', 'duration': 15, 'ratio': '16:9', 'resolution': '768P', 'use_context_ir': True},
}


def channel_for(model):
    return 'compshare' if model in MODELS else 'dreamina'


def h3_reference_bindings(references):
    """Use the same ordered references for the preview and the paid request."""
    counts = {'image': 0, 'audio': 0}
    expected = {'男主': 'image', '聆听者': 'image', '音色': 'audio'}
    bindings, seen = [], set()
    for ref in references:
        role, kind = ref['role'], ref['type']
        if role in seen or expected.get(role) != kind:
            raise CanvasError('H3 人物或音色引用重复、类型不符，请重新核对本组素材绑定')
        seen.add(role)
        counts[kind] += 1
        token = f'<{"Picture" if kind == "image" else "Audio"} {counts[kind]}>'
        bindings.append({**ref, 'token': token})
    if not {'男主', '聆听者'}.issubset(seen):
        raise CanvasError('H3 需要分别绑定本组男主和聆听者参考图')
    return bindings


def h3_character_prompt(references, target_name):
    by_role = {ref['role']: ref for ref in references}
    speaker = by_role['男主'].get('description') or '作为主体的人物'
    listener = by_role['聆听者'].get('description') or '作为主体的人物'
    name = f'，身份为{target_name}' if target_name else ''
    lines = [
        '本组人物身份与发声分工。',
        f'@男主参考图中的【{speaker}】是本片主角和唯一说话人{name}。'
        '该图其他人物只作背景，安静旁观。',
        f'@聆听者参考图中的【{listener}】是聆听者。'
        '聆听者全程嘴唇闭合，只做自然的眼神和表情反应，不念台词、不做说话口型或口播手势。',
        '两人的脸型、五官、发型、服装和身份分别对应各自参考图，跨镜头保持一致。'
        '全部台词与说话手势归属于@男主，嘴部动作随男主自己的发声同步。'
        '切到聆听者时继续保持聆听者闭嘴，不能将对白转交给聆听者。',
    ]
    if '音色' in by_role:
        lines.append('@音色仅对应@男主的声音特征，全部对白保持同一声线。')
    return '\n'.join(lines)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class H3Client:
    base_url = BASE
    def __init__(self, config, api_key=None):
        self.config = config
        self._api_key = api_key

    def key(self):
        key = self._api_key if self._api_key is not None else os.environ.get('COMPSHARE_H3_API_KEY', '')
        keyfile = self.config.get('compshare_h3_key_file')
        try:
            if self._api_key is None and not key and keyfile and Path(keyfile).is_file():
                key = Path(keyfile).read_text(encoding='utf-8').strip()
        except (OSError, UnicodeError) as exc:
            raise failure(self.config, 'key_unreadable', '工作台无法读取 H3 密钥文件，请在“模型与连接设置”中重新保存密钥', cause=exc) from None
        if not key.startswith('sk-ml-') or len(key) <= 6 or any(c.isspace() for c in key):
            raise H3Error('key_missing', '请在“模型与连接设置”中填写优云智算视频工作台专属密钥（sk-ml- 开头）')
        return key

    def connection_state(self):
        try:
            self.key()
            configured, message = True, 'H3 密钥已配置，点击“测试连接”检查服务和积分'
        except H3Error as exc:
            configured, message = False, str(exc)
        environment = bool(os.environ.get('COMPSHARE_H3_API_KEY'))
        return {'configured': configured, 'message': message, 'model': 'MiniMax-H3',
                'endpoint': BASE, 'key_source': 'environment' if environment else 'file',
                'can_save': not environment}

    def save_key(self, key):
        if os.environ.get('COMPSHARE_H3_API_KEY'):
            raise H3Error('environment_key', '当前 H3 密钥由环境变量设置，请更新 COMPSHARE_H3_API_KEY 后重启工作台')
        H3Client(self.config, api_key=key).key()
        target = Path(self.config['compshare_h3_key_file'])
        temporary = None
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=target.parent,
                                             prefix='.h3-key-', delete=False) as output:
                temporary = Path(output.name)
                output.write(key.strip())
            temporary.replace(target)
        except OSError as exc:
            raise failure(self.config, 'key_save_failed', '无法保存 H3 密钥，请检查本机密钥目录的写入权限', cause=exc, secrets=(key,)) from None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def response_error(self, path, message, code='invalid_response'):
        return failure(self.config, code, message, retryable=True, path=path)

    def request(self, method, path, payload=None, identity=None):
        key = self.key()
        headers = {'Authorization': 'Bearer ' + key, 'Accept': 'application/json'}
        body = None
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode()
            if len(body) > 72 * 1024 * 1024:
                raise CanvasError('H3 请求超过 72 MiB，请缩小参考素材')
            headers['Content-Type'] = 'application/json'
        if identity:
            headers['Idempotency-Key'] = identity
        started = time.monotonic()
        request_id = None
        try:
            timeout = 30 if method == 'GET' else 120
            with build_opener(NoRedirect()).open(Request(self.base_url + path, data=body, headers=headers, method=method), timeout=timeout) as response:
                response_headers = getattr(response, 'headers', {})
                request_id = response_headers.get('X-Request-ID') or response_headers.get('Request-ID')
                raw = response.read()
            result = json.loads(raw)
        except HTTPError as exc:
            try:
                error_body = json.loads(exc.read(8192))
            except (ValueError, OSError, HTTPException):
                error_body = {}
            if not isinstance(error_body, dict):
                error_body = {}
            code, message, retryable = provider_reason(error_body, exc.code)
            request_id = error_body.get('request_id') or error_body.get('request_uuid')
            if method == 'POST':
                message += '。提交记录已保存，请先核对平台原任务的提交结果'
            raise failure(self.config, code, message, retryable=retryable, method=method, path=path,
                          cause=error_body or exc, status=exc.code, request_id=request_id,
                          elapsed=round(time.monotonic()-started, 3), secrets=(key,)) from None
        except (ValueError, UnicodeError) as exc:
            message = '优云智算返回的内容无法读取，可能是回复不完整或返回了网页，请稍后重试查询'
            if method == 'POST':
                message += '。提交记录已保存，请先核对平台原任务的提交结果'
            raise failure(self.config, 'invalid_json', message, retryable=True, method=method,
                          path=path, cause=exc, request_id=request_id,
                          elapsed=round(time.monotonic()-started, 3), secrets=(key,)) from None
        except (URLError, TimeoutError, OSError, HTTPException) as exc:
            code, message, retryable = network_reason(exc)
            if method == 'POST':
                message += '。提交记录已保存，请先核对平台原任务的提交结果'
            raise failure(self.config, code, message, retryable=retryable, method=method,
                          path=path, cause=exc, elapsed=round(time.monotonic()-started, 3), secrets=(key,)) from None
        if not isinstance(result, dict):
            raise failure(self.config, 'invalid_response', '优云智算回复缺少必要信息，请稍后重试查询',
                          retryable=True, method=method, path=path, cause=type(result).__name__)
        if result.get('error') or result.get('RetCode', 0):
            code, message, retryable = provider_reason(result)
            raise failure(self.config, code, message, retryable=retryable, method=method, path=path,
                          cause=result, request_id=result.get('request_id') or result.get('request_uuid'), secrets=(key,))
        return result


class CompShareWorkflow(CanvasWorkflow):
    def download(self, identifier):
        project = super().download(identifier)
        if assembly_plans(project.get('generation', {})):
            from .segment_assembly import assemble_segments
            assemble_segments(self.service, project)
        return project

    def new_round(self, identifier):
        project = super().new_round(identifier)
        project['generation']['use_context_ir'] = True
        project['generation']['preserve_prompt'] = True
        if uses_haoge(project) or project['generation'].get('baseline_id') == BASELINE_ID:
            apply_baseline(project)
        self._save(project)
        return project

    @property
    def client(self):
        return H3Client(self.service.config)

    def models(self):
        return {'items': [{'model': 'MiniMax-H3', 'provider': 'compshare', 'durations': [4, 30],
                           'resolutions': list(RATES), 'mode': 'm2v'}]}

    def configure(self, identifier, model, duration, global_prompt, ratio='4:3', resolution='480P', prompt_mode='combined', use_context_ir=None):
        if model not in MODELS or type(duration) is not int or not 4 <= duration <= 30:
            raise CanvasError('H3 模型须为 MiniMax-H3，时长须为 4–30 整数秒')
        if resolution not in RATES or ratio not in ('4:3', '16:9', '9:16', '1:1', '3:4', '21:9', 'adaptive'):
            raise CanvasError('H3 分辨率须为 480P / 768P / 1080P / 2K / 4K（区分大小写）')
        cfg = self._load(identifier).get('generation', {})
        optimize = cfg.get('use_context_ir', True) if use_context_ir is None else use_context_ir
        if type(optimize) is not bool:
            raise CanvasError('提示词优化开关须为布尔值')
        if cfg.get('tasks') and optimize != cfg.get('use_context_ir', True):
            raise CanvasError('已有草稿或任务，请先新建轮次再调整提示词优化')
        project = super().configure(identifier, model, duration, global_prompt, ratio, resolution, prompt_mode)
        project['generation']['use_context_ir'] = optimize
        self._save(project)
        return project

    def inputs(self, project, group):
        cfg = project.get('generation', {})
        dialogue_part = group.get('_dialogue_part')
        segment_plan = cfg.get('segment_plan')
        if segment_plan:
            if cfg.get('baseline_id') != 'custom' or cfg.get('prompt_mode') != 'shared':
                raise CanvasError('分段实测须显式使用自定义配置和共用画面要求')
            part = next((part for part in segment_plan['parts'] if part['group_id'] == group['id']), None)
            if part is None or not part.get('prompt'):
                raise CanvasError('图片组不在已编排的台词分段中')
            project = {**project, 'generation': {**cfg,
                'global_prompt': (part['prompt'] if segment_plan.get('prompt_format') == 'verbatim'
                                  else cfg['global_prompt'].strip() + '\n\n' + part['prompt'])}}
        validate_baseline(cfg)
        if dialogue_part:
            project = {**project, 'generation': {**cfg, 'global_prompt': dialogue_part['prompt']}}
        if cfg.get('baseline_id') == BASELINE_ID:
            if not cfg.get('baseline_voice_path'):
                raise CanvasError('首次任务参考音频缺失，请重新应用基准')
            group = {**group, 'generation_bindings': {**group.get('generation_bindings', {}),
                     'voice_path': cfg['baseline_voice_path']}}
        payload, _ = super().inputs(project, group)
        if segment_plan:
            actual = {ref['role']: ref['sha256'] for ref in payload['references']}
            if actual != segment_plan['reference_sha256']:
                raise CanvasError('分段任务的图片或音色发生变化，须与原图片组保持一致')
            payload['segment'] = {'index': part['index'], 'count': len(segment_plan['parts']),
                                  'source_group_id': segment_plan['source_group_id'],
                                  'timeline': part['timeline']}
        if payload['resolution'] not in RATES or type(payload['duration']) is not int or not 4 <= payload['duration'] <= 30:
            raise CanvasError('H3 参数无效，请重新配置分辨率和时长')
        if re.search(r'<(?:Picture|Audio)\s+\d+>', payload['prompt']):
            raise CanvasError('请用 @男主、@聆听者、@音色 引用素材，图片和音频编号由本组绑定自动生成')
        if project.get('generation', {}).get('audio_first'):
            from .speech import speech_plan
            payload['speech_plan'] = speech_plan(payload, project['generation'])
        for ref in payload['references']:
            if ref['type'] == 'image':
                ref['description'] = str(group.get('person_' + ref['side']) or '').strip()
                ref['variant'] = 'repaired' if group.get('repaired_' + ref['side']) else 'original'
                ref['clean_reviewed'] = review_valid(group, ref)
        if cfg.get('baseline_id') == BASELINE_ID:
            if group.get('generation_bindings', {}).get('male_side', 'a') != 'a':
                raise CanvasError('首次任务基准要求男主绑定A、聆听者绑定B，请先整理人物位置')
            audio = [r for r in payload['references'] if r['type'] == 'audio']
            if len(audio) != 1 or audio[0]['sha256'] != BASELINE['audio_sha256']:
                raise CanvasError('参考音频偏离首次任务基准，请重新应用首次任务基准')
            payload['baseline_id'] = BASELINE_ID
            payload['baseline_name'] = BASELINE['name']
        bindings = h3_reference_bindings(payload['references'])
        target_name = character_filter(project).get('required_character', '')
        if not project.get('generation', {}).get('preserve_prompt', True) and (target_name or all(role in payload['prompt'] for role in ('@男主', '@聆听者'))):
            payload['prompt'] = h3_character_prompt(payload['references'], target_name) + '\n\n创作要求。\n' + payload['prompt']
        verbatim_segment = bool(segment_plan and segment_plan.get('prompt_format') == 'verbatim'
                                and cfg.get('preserve_prompt', True))
        if not cfg.get('verbatim_prompt') and not verbatim_segment and not dialogue_part and not payload['prompt'].endswith(NO_SUBTITLES):
            payload['prompt'] += '\n' + NO_SUBTITLES
        if payload.get('speech_plan'):
            payload['prompt'] += ('\n@音色将绑定本段完整台词的已生成配音。全部对白由@男主说出，'
                                  '按这段音频的完整台词、语速、停顿和情绪安排说话口型，'
                                  '在视频时长内说完整，不漏句、不加词。聆听者保持闭嘴。')
        payload['use_context_ir'] = project.get('generation', {}).get('use_context_ir', True)
        if type(payload['use_context_ir']) is not bool:
            raise CanvasError('提示词优化开关须为布尔值')
        from .h3_options import validate_options
        validate_options(cfg)
        payload['mute_audio'] = cfg.get('mute_audio', False)
        for key in ('aigc_watermark', 'skill_id'):
            if cfg.get(key):
                payload[key] = cfg[key]
        total = 0
        for ref in payload['references']:
            file = Path(ref['path'])
            size = file.stat().st_size
            limit = 30 if ref['type'] == 'image' else 15
            if size > limit * 1024 * 1024:
                raise CanvasError(f'H3 {ref["role"]}素材超过 {limit} MiB')
            total += 4 * ((size + 2) // 3)
            if ref['type'] == 'image':
                with Image.open(file) as im:
                    w, h = im.size
                if not (256 <= w <= 5760 and 256 <= h <= 5760 and .4 <= w / h <= 2.5):
                    raise CanvasError('H3 参考图宽高须为256–5760，宽高比须为0.4–2.5')
            else:
                info = probe(file, self.service.config.get('ffprobe'))
                duration = float(info.get('format', {}).get('duration', 0))
                if not math.isfinite(duration) or not 2 <= duration <= 15.08:
                    raise CanvasError('H3 参考音频须为 2–15.08 秒')
        if total > 71 * 1024 * 1024:
            raise CanvasError('参考素材过大，请压缩后再准备')
        if len(payload['prompt']) > 7000:
            raise CanvasError('H3 提示词超过7000字')
        payload['bound_prompt'] = bind_prompt(payload['prompt'], {ref['role']: ref['token'] for ref in bindings})
        payload['role_bindings'] = bindings
        if dialogue_part:
            payload['segment'] = {k: dialogue_part[k] for k in ('source_group_id', 'index', 'count')}
            payload['source_group_id'] = dialogue_part['source_group_id']
            payload['baseline_name'] = '实测流程 · 按台词自动分段'
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        return payload, fingerprint

    def preflight(self, identifier, group_ids):
        project = self._load(identifier)
        cfg = project.get('generation', {})
        if not enabled(cfg):
            check = super().preflight(identifier, group_ids)
            groups = {g['id']: g for g in project['groups']}
        else:
            check = {'ready': False, 'errors': [], 'warnings': [], 'execution_sheets': []}
            try:
                groups = {g['id']: g for g in expanded_groups(project, group_ids)}
            except CanvasError as exc:
                check['errors'].append({'group_id': None, 'message': str(exc)})
                return check
            from .dialogue_segments import count, dialogue_span
            for gid, group in groups.items():
                try:
                    if group.get('needs_review') or not group.get('selected', True):
                        raise CanvasError('该图片组已取消确认或未勾选')
                    if cfg.get('tasks', {}).get(gid, {}).get('submit_id'):
                        raise CanvasError('该组已有提交记录，请查询原任务并下载结果')
                    payload, fingerprint = self.inputs(project, group)
                    check['execution_sheets'].append(dict(group_id=gid, fingerprint=fingerprint, **payload))
                    a, b = dialogue_span(payload['prompt'])
                    if count(payload['prompt'][a:b]) > 75:
                        check['warnings'].append({'group_id': gid, 'message': '本段台词偏长，15秒内语速可能偏快；分段仅按文本估算，需检查实际对白'})
                except (CanvasError, ValueError, OSError) as exc:
                    check['errors'].append({'group_id': gid, 'message': str(exc)})
        if uses_haoge(project) and not cfg.get('baseline_id'):
            check['errors'].append({'group_id': None, 'message':
                '本轮尚未采用首次任务基准，请应用基准；已有任务先查询，完成后新建轮次'})
        for sheet in check['execution_sheets']:
            if cfg.get('baseline_id') == BASELINE_ID:
                for ref in sheet['references']:
                    if ref['type'] == 'image' and not review_valid(groups[sheet['group_id']], ref):
                        check['errors'].append({'group_id': sheet['group_id'], 'message':
                            f'人物{ref["side"].upper()}参考图尚未完成字幕/水印检查；先看图，需清理则修复，再记录检查结果'})
            if sheet.get('speech_plan'):
                check['warnings'].append({'group_id': sheet['group_id'], 'message':
                    '先生成完整配音并自动适配时长，再送入H3；不设试听确认。台词较长时语速会加快。H3仍会重新生成对白，成片需验收。'})
            missing = [ref['side'].upper() for ref in sheet['references']
                       if ref['type'] == 'image' and not ref.get('description')]
            if missing:
                check['warnings'].append({'group_id': sheet['group_id'],
                    'message': f'人物{"、".join(missing)}缺少外貌与位置描述，请看图补充，帮助H3区分说话人和聆听者'})
        check['ready'] = not check['errors']
        return check

    def execution_sheet(self, identifier, group_id):
        p = self._load(identifier)
        if not enabled(p.get('generation', {})):
            return super().execution_sheet(identifier, group_id)
        groups = expanded_groups(p, [group_id])
        parts = [dict(group_id=g['id'], **self.inputs(p, g)[0]) for g in groups]
        return {'project_id': identifier, 'project_name': p['name'], **parts[0], 'parts': parts}

    def body(self, payload):
        bindings = h3_reference_bindings(payload['references'])
        prompt = bind_prompt(payload['prompt'], {ref['role']: ref['token'] for ref in bindings})
        if payload.get('bound_prompt', prompt) != prompt:
            raise CanvasError('最终提示词与素材绑定不一致，请重新准备本组任务')
        content = [{'type': 'text', 'text': prompt}]
        for ref in bindings:
            kind = ref['type']
            file = Path(ref['path'])
            mime = mimetypes.guess_type(file.name)[0] or 'application/octet-stream'
            raw = file.read_bytes()
            if hashlib.sha256(raw).hexdigest() != ref['sha256']:
                raise CanvasError('参考素材在核对后发生变化，请重新准备本组任务')
            data = 'data:' + mime + ';base64,' + base64.b64encode(raw).decode('ascii')
            content.append({'type': kind + '_url', kind + '_url': {'url': data}, 'role': 'reference_' + kind})
        return {'model': 'MiniMax-H3', 'content': content, 'duration': payload['duration'],
                'resolution': payload['resolution'], 'ratio': payload['ratio'],
                'use_context_ir': payload['use_context_ir'], 'mute_audio': payload.get('mute_audio', False), 'aigc_watermark': payload.get('aigc_watermark', False),
                **({'skill_id': payload['skill_id']} if payload.get('skill_id') else {})}

    def prepare(self, identifier, group_id=None, group_ids=None):
        p = self._load(identifier); cfg = p['generation']
        ids = group_ids if group_ids is not None else ([group_id] if group_id else [g['id'] for g in p['groups'] if g.get('selected')])
        if not set(ids).issubset({g['id'] for g in p['groups']}):
            raise CanvasError('准备生成须选择原图片组，不能单独准备某个台词分段')
        check = self.preflight(identifier, ids)
        if check['errors']:
            raise CanvasError('；'.join(e['message'] for e in check['errors']))
        task_ids = [s['group_id'] for s in check['execution_sheets']]
        tasks = cfg.setdefault('tasks', {})
        if any(t.get('submit_id') for t in tasks.values()) or (tasks and set(tasks) != set(task_ids)):
            raise CanvasError('本轮已有其他草稿或提交记录，请先处理原轮次')
        for sheet in check['execution_sheets']:
            gid = sheet['group_id']; old = tasks.get(gid)
            if old and old['fingerprint'] != sheet['fingerprint']:
                raise CanvasError('素材或参数已变化，请新建轮次')
            tasks[gid] = old or {'group_id': gid, 'node_id': 'h3_' + uuid.uuid4().hex,
                                 'fingerprint': sheet['fingerprint'], 'references': sheet['references'],
                                 'status': 'draft', 'source_group_id': sheet.get('source_group_id', gid),
                                 'segment': sheet.get('segment'), 'prompt': sheet['bound_prompt']}
        if enabled(cfg):
            plans = {}
            for sheet in check['execution_sheets']:
                seg = sheet['segment']
                if seg['count'] == 1:
                    continue
                plan = plans.setdefault(seg['source_group_id'], {'source_group_id': seg['source_group_id'],
                    'parts': [], 'assembly': {'order': []}})
                plan['parts'].append({'group_id': sheet['group_id'], 'index': seg['index']})
                plan['assembly']['order'].append(sheet['group_id'])
            cfg['dialogue_plans'] = plans
        cfg.pop('quote', None); cfg.pop('pending_approval', None)
        self._save(p)
        return p

    def credit_balance(self, ceiling):
        value = self.client.request('GET', 'query/point_usage_summary').get('available_points')
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise CanvasError('H3 未返回有效可用积分')
        return {'available_credits': value, 'shortfall': max(0, ceiling-value),
                'sufficient': value >= ceiling, 'checked_at': time.time()}

    def quote(self, identifier):
        p = self._load(identifier); cfg = p['generation']; tasks = cfg.get('tasks', {})
        cfg.pop('quote', None); cfg.pop('pending_approval', None); self._save(p)
        if not tasks or any(t['status'] != 'draft' for t in tasks.values()):
            raise CanvasError('请先准备未提交的 H3 草稿')
        check = self.preflight(identifier, list(tasks))
        if check['errors']:
            raise CanvasError('；'.join(e['message'] for e in check['errors']))
        if {s['group_id'] for s in check['execution_sheets']} != set(tasks):
            raise CanvasError('分段范围已变化，请重新准备')
        for sheet in check['execution_sheets']:
            if sheet['fingerprint'] != tasks[sheet['group_id']]['fingerprint']:
                raise CanvasError('素材或参数已变化，请重新准备')
        optimization_fee = 5 if cfg.get('use_context_ir', True) else 0
        cost = len(tasks) * (RATES[cfg['resolution']] * cfg['duration'] + optimization_fee)
        speech_costs = {}
        if cfg.get('audio_first'):
            from .speech import SpeechClient, estimate
            pricing = SpeechClient(self.service.config).request('GET', 'pricing')
            for g in p['groups']:
                if g['id'] in tasks:
                    speech_costs[g['id']] = estimate(self.inputs(p, g)[0]['speech_plan'], pricing)
        video_cost = cost
        cost = round(cost + sum(speech_costs.values()), 2)
        cfg['quote'] = {'ceiling': cost, **self.credit_balance(cost), 'group_ids': list(tasks),
                        'node_ids': [t['node_id'] for t in tasks.values()], 'provider': 'compshare',
                        'basis': '官方文档费率，' + ('含每条5积分提示词优化' if optimization_fee else '提示词优化已关闭') + '；图片与音频免费'}
        if speech_costs:
            cfg['quote'].update(speech_costs=speech_costs, video_cost=video_cost,
                                speech_cost=round(sum(speech_costs.values()), 2))
            cfg['quote']['basis'] += '；先配音费用按IndexTTS实时计价，配音与视频一次确认后连续执行'
        self._save(p)
        return p

    def run_approved(self, identifier, approved_ceiling):
        p = self._load(identifier); cfg = p['generation']; q = cfg.get('quote')
        if not q or q['ceiling'] != approved_ceiling:
            raise CanvasError('积分授权已失效，请重新报价')
        check = self.preflight(identifier, q['group_ids'])
        if check['errors']:
            raise CanvasError('；'.join(e['message'] for e in check['errors']))
        sheets = {s['group_id']: s for s in check['execution_sheets']}
        tasks = cfg['tasks']
        for gid in q['group_ids']:
            if tasks[gid]['status'] != 'draft' or tasks[gid].get('submit_id') or tasks[gid]['fingerprint'] != sheets[gid]['fingerprint']:
                raise CanvasError('草稿已改变或已提交，拒绝重复扣费')
        cost = len(tasks) * (RATES[cfg['resolution']] * cfg['duration'] + (5 if cfg.get('use_context_ir', True) else 0))
        if cost > approved_ceiling:
            raise CanvasError('积分超过批准上限')
        speech_costs = {}
        if cfg.get('audio_first'):
            from .speech import SpeechClient, estimate
            pricing = SpeechClient(self.service.config).request('GET', 'pricing')
            speech_costs = {gid: estimate(sheets[gid]['speech_plan'], pricing) for gid in q['group_ids']}
            if any(value > q.get('speech_costs', {}).get(gid, -1) for gid, value in speech_costs.items()):
                raise CanvasError('配音费用已变化，请重新报价；尚未提交')
            cost = round(cost + sum(speech_costs.values()), 2)
            if cost > approved_ceiling:
                raise CanvasError('配音与视频合计超过批准上限，请重新报价')
        balance = self.credit_balance(cost)
        q.update(balance)
        if cfg.get('pending_approval'):
            cfg['pending_approval'].update(balance)
        self._save(p)
        if not balance['sufficient']:
            raise CanvasError('H3 可用积分不足，请重新查询积分')
        cfg['submitted_at'] = time.time()
        if cfg.get('audio_first'):
            # Persist the complete approved chain before any paid request, so restarting
            # can continue speech -> video without an audition or a second approval.
            for gid in q['group_ids']:
                t = tasks[gid]
                t.update(submit_id=str(uuid.uuid4()), status='audio_pending',
                         approved_payload=sheets[gid], approved_video_cost=RATES[cfg['resolution']] * cfg['duration'] + (5 if cfg.get('use_context_ir', True) else 0),
                         speech={'request_id': str(uuid.uuid4()), 'ceiling': q['speech_costs'][gid]})
            cfg.pop('quote', None); cfg.pop('pending_approval', None); self._save(p)
            for t in tasks.values():
                try:
                    self._advance_audio(p, t)
                except (OSError, HTTPException) as exc:
                    raise failure(self.service.config, 'speech_transfer', '配音参考上传未完成，原请求已保存，可继续原任务',
                                  retryable=True, path='audio/transfer', cause=exc) from None
            return p
        for gid in q['group_ids']:
            # Persist a stable key before any paid request. Never retry automatically.
            t = tasks[gid]
            body = self.body(sheets[gid])
            snapshot = {'bound_prompt': body['content'][0]['text'],
                        'dialogue_policy': cfg.get('dialogue_policy'),
                        'segment': sheets[gid].get('segment'),
                        'source_group_id': sheets[gid].get('source_group_id', gid),
                        'baseline_id': sheets[gid].get('baseline_id'),
                        'role_bindings': sheets[gid]['role_bindings'],
                        'parameters': {key: value for key, value in body.items() if key != 'content'}}
            t.update(submit_id=str(uuid.uuid4()), status='submission_unknown', request_snapshot=snapshot)
            self._save(p)
            try:
                result = self.client.request('POST', 'video_generation', body, t['submit_id'])
                task_id = result.get('task_id')
                if not isinstance(task_id, str) or not task_id:
                    raise failure(self.service.config, 'missing_task_id',
                                  '平台回复中缺少任务编号，暂时无法确认提交结果，请在平台核对这次提交',
                                  method='POST', path='video_generation')
            except H3Error as exc:
                t['submit_error'] = exc.details()
                self._save(p)
                raise
            t.update(task_id=task_id, status='submitted')
            self._save(p)
        cfg.pop('quote', None); cfg.pop('pending_approval', None); self._save(p)
        return p

    def _advance_audio(self, p, t):
        """Advance one durable stage; never create another paid job on polling."""
        from .speech import SpeechClient, estimate, upload_reference, download_audio, fit_audio
        import copy
        gid = t['group_id']; group = next(g for g in p['groups'] if g['id'] == gid)
        if self.inputs(p, group)[1] != t['fingerprint']:
            raise CanvasError('配音期间素材或提示词已改变，已停止后续视频提交；请恢复原配置后查询')
        sheet = copy.deepcopy(t['approved_payload']); plan = sheet['speech_plan']; speech = t['speech']
        client = SpeechClient(self.service.config)
        if not speech.get('task_id'):
            # IndexTTS documents same-key retries as the same resource and same charge.
            if not speech.get('input'):
                pricing = client.request('GET', 'pricing')
                if estimate(plan, pricing) > speech['ceiling']:
                    raise CanvasError('配音费用超过批准上限，已停止提交')
                if not speech.get('asset_id'):
                    speech['asset_id'] = upload_reference(client, plan, self.service.config)
                    self._save(p)
                speech['input'] = {'text': plan['text'], 'language': 'ZH',
                    'voice': {'source': 'asset', 'asset_id': speech['asset_id']},
                    'speed': plan['speed'], 'auto_emotion': False, 'save_voice': False}
                if plan['emotion_description']:
                    speech['input']['emotion_description'] = plan['emotion_description']
            t['status'] = 'audio_submission_unknown'; self._save(p)
            result = client.request('POST', 'speech/jobs', speech['input'], speech['request_id'])
            if not result.get('task_id'):
                raise client.response_error('speech/jobs', '配音提交未返回任务编号，将用原请求标识核对，勿新建任务')
            speech.update(task_id=result['task_id'], point_cost=result.get('point_cost'))
            t['status'] = 'audio_running'; self._save(p)
            return
        if not speech.get('output'):
            result = client.request('GET', 'speech/jobs/' + quote(speech['task_id'], safe='')).get('task', {})
            if result.get('task_id') != speech['task_id']:
                raise client.response_error('speech/jobs', '配音查询未返回原任务，请稍后重试')
            state = result.get('status'); speech['provider_status'] = state
            if state in ('failed', 'cancelled'):
                t.update(status='failed', error='配音未完成，未提交视频。' + redact(result.get('error_message') or '平台未提供原因'))
                self._save(p); return
            if state in ('queued', 'generating'):
                t['status'] = 'audio_running'; self._save(p); return
            if state != 'succeeded':
                raise client.response_error('speech/jobs', '配音平台进度暂时无法读取，请稍后重试')
            folder = Path(p['directory']) / 'generated-audio' / gid / t['submit_id']
            folder.mkdir(parents=True, exist_ok=True)
            raw, fitted = folder / 'original.wav', folder / 'speech.wav'
            if not raw.exists():
                download_audio(result['output_url'], raw, self.service.config)
            speech.update(fit_audio(raw, fitted, plan['target_seconds'], self.service.config))
            speech.update(point_cost=result.get('point_cost'), point_status=result.get('point_status'), text=plan['text'])
            t['status'] = 'audio_ready'; self._save(p)
        if _sha(Path(speech['output'])) != speech['sha256']:
            raise CanvasError('已生成的配音文件发生变化，已停止视频提交')
        # Replace the voice sample with the WHOLE generated utterance. Keep the
        # group's source voice intact for future rounds.
        for ref in sheet['references']:
            if ref['type'] == 'audio':
                ref.update(path=speech['output'], sha256=speech['sha256'])
        sheet['role_bindings'] = h3_reference_bindings(sheet['references'])
        body = self.body(sheet)
        cost = RATES[sheet['resolution']] * sheet['duration'] + (5 if sheet['use_context_ir'] else 0)
        if cost > t['approved_video_cost']:
            raise CanvasError('视频费用超过批准上限，配音已保留，未提交视频')
        if not self.credit_balance(cost)['sufficient']:
            raise CanvasError('配音已完成，但视频积分不足；充值后查询原任务即可继续')
        t['request_snapshot'] = {'bound_prompt': body['content'][0]['text'],
            'role_bindings': sheet['role_bindings'], 'speech_task_id': speech['task_id'],
            'parameters': {k: v for k, v in body.items() if k != 'content'}}
        t['status'] = 'submission_unknown'; self._save(p)
        result = self.client.request('POST', 'video_generation', body, t['submit_id'])
        if not result.get('task_id'):
            raise self.client.response_error('video_generation', '视频提交未返回任务编号，请核查原提交；配音已保留')
        t.update(task_id=result['task_id'], status='submitted'); self._save(p)

    def refresh(self, identifier):
        p = self._load(identifier)
        errors = []
        for t in p.get('generation', {}).get('tasks', {}).values():
            if t.get('status', '').startswith('audio_'):
                try:
                    self._advance_audio(p, t)
                    t.pop('query_error', None)
                    if t['status'] != 'failed':
                        t.pop('error', None)
                    self._save(p)
                except H3Error as exc:
                    t['query_error'] = exc.details(); self._save(p); errors.append(exc)
                except (OSError, HTTPException) as exc:
                    error = failure(self.service.config, 'speech_transfer', '配音素材传输未完成，已保留原任务，将继续查询或下载',
                                    retryable=True, path='audio/transfer', cause=exc)
                    t['query_error'] = error.details(); self._save(p); errors.append(error)
                except CanvasError as exc:
                    t['error'] = str(exc); self._save(p)
                    raise
                continue
            if t.get('status') in ('downloaded', 'failed', 'canceled') or not t.get('submit_id'):
                continue
            if not t.get('task_id'):
                errors.append(H3Error('submission_unknown',
                                     '这次提交尚未取得平台任务编号，请在优云智算任务列表核对提交结果'))
                continue
            path = 'query/video_generation/' + quote(t['task_id'], safe='')
            try:
                task = self.client.request('GET', path).get('task')
                if not isinstance(task, dict) or task.get('id') != t['task_id']:
                    raise self.client.response_error(path, '平台回复未包含这条任务的有效状态，请稍后重试查询')
                state = task.get('status')
                if state not in ('queued', 'pending', 'moderating', 'running', 'processing', 'succeeded',
                                 'failed', 'blocked', 'cancelled'):
                    raise self.client.response_error(path, '平台尚未返回可识别的任务进度，请稍后重试查询')
            except H3Error as exc:
                t['query_error'] = exc.details()
                errors.append(exc)
                self._save(p)
                continue
            t.pop('query_error', None)
            t['provider_status'] = state
            t['status'] = {'succeeded': 'completed', 'failed': 'failed', 'blocked': 'failed', 'cancelled': 'canceled'}.get(state, 'running')
            t['actual_resolution'] = task.get('resolution')
            if state in ('failed', 'blocked'):
                detail = task.get('error') or {}
                reason = detail.get('message') if isinstance(detail, dict) else str(detail)
                message = '视频未通过平台审核，请检查参考素材和提示词' if state == 'blocked' else '平台未能生成这条视频'
                if reason:
                    message += '。平台说明为 ' + redact(reason)
                t['error'] = message
            self._save(p)
        if errors:
            first = next((error for error in errors if not error.retryable), errors[0])
            raise H3Error(first.code, str(first), retryable=all(e.retryable for e in errors),
                          diagnostic_id=first.diagnostic_id)
        return p

    def _download_task(self, p, cfg, t):
        path = 'query/video_generation/' + quote(t['task_id'], safe='')
        task = self.client.request('GET', path).get('task', {})
        if not isinstance(task, dict) or task.get('id') != t['task_id'] or task.get('status') != 'succeeded':
            raise self.client.response_error(path, '平台尚未提供这条任务的完成状态，请稍后重试查询')
        content = task.get('content') or {}
        url = content.get('url', '') if isinstance(content, dict) else ''
        if urlparse(url).scheme != 'https':
            raise self.client.response_error(path, '视频已生成，但平台尚未提供可下载的文件地址，请稍后重试下载')
        folder = Path(p['directory']) / 'generated-videos' / t['group_id'] / t['submit_id']
        file = folder / 'video.mp4'; partial = folder / 'video.partial'
        try:
            folder.mkdir(parents=True, exist_ok=True)
            with urlopen(url, timeout=120) as response, partial.open('wb') as output:
                shutil.copyfileobj(response, output)
            info = probe(partial, self.service.config.get('ffprobe'))
            duration = float(info.get('format', {}).get('duration', 0)); streams = info.get('streams', [])
            if not math.isfinite(duration) or abs(duration-cfg['duration']) > max(1, cfg['duration']*.1):
                raise CanvasError('H3 成片时长异常，未标记下载完成')
            if not any(s.get('codec_type') == 'video' and s.get('width') and s.get('height') for s in streams):
                raise CanvasError('H3 成片缺少有效视频画面')
            if not t.get('request_snapshot', {}).get('parameters', {}).get('mute_audio', cfg.get('mute_audio', False)) and not any(s.get('codec_type') == 'audio' for s in streams):
                raise CanvasError('H3 成片缺少对白音轨')
            partial.replace(file)
        except HTTPError as exc:
            message = f'视频文件服务器暂时无法提供下载（服务状态 {exc.code}），将重新查询原任务的下载地址'
            raise failure(self.service.config, 'download_unavailable', message,
                          retryable=exc.code in (403, 404, 408, 429) or exc.code >= 500,
                          path='download/video', cause=exc, status=exc.code) from None
        except (URLError, TimeoutError, OSError, HTTPException) as exc:
            code, message, retryable = network_reason(exc)
            raise failure(self.service.config, code, '下载视频时未能完成文件传输。' + message,
                          retryable=retryable, path='download/video', cause=exc) from None
        finally:
            partial.unlink(missing_ok=True)
        t.update(status='downloaded', output=str(file), sha256=_sha(file), media_info={'duration': duration}, actual_resolution=task.get('resolution'))


class GenerationWorkflow:
    """Route by saved model so historical Dreamina jobs remain queryable."""
    def __init__(self, service):
        self.service = service
        self.canvas = CanvasWorkflow(service)
        self.h3 = CompShareWorkflow(service)

    def models(self):
        result = self.h3.models()
        if self.service.config.get('dreamina_canvas'):
            try:
                result['dreamina'] = self.canvas.models()
            except CanvasError:
                result['dreamina_error'] = '即梦暂不可用'
        return result

    def select_channel(self, identifier, channel):
        if channel not in CHANNELS:
            raise CanvasError('请选择即梦或优云智算 H3')
        p = self.service.projects.get(identifier)
        cfg = p.setdefault('generation', {})
        if cfg.get('tasks'):
            raise CanvasError('本轮已有草稿或任务，请先处理原轮次，再新建轮次选择渠道')
        cfg.update({k: v for k, v in CHANNELS[channel].items() if k != 'label'})
        if channel == 'compshare':
            cfg['preserve_prompt'] = True
            if uses_haoge(p) or cfg.get('baseline_id') == BASELINE_ID:
                apply_baseline(p)
        else:
            cfg.pop('baseline_id', None)
            cfg.pop('dialogue_policy', None)
            cfg.pop('dialogue_plans', None)
            for key in ('mute_audio', 'aigc_watermark', 'skill_id', 'verbatim_prompt'):
                cfg.pop(key, None)
        cfg['provider'] = channel
        cfg['channel_selected'] = True
        cfg.pop('channel_request', None)
        cfg.pop('quote', None)
        cfg.pop('pending_approval', None)
        self.service.projects.save(p)
        return p

    def require_channel(self, identifier, model=None):
        cfg = self.service.projects.get(identifier).get('generation', {})
        if not cfg.get('channel_selected'):
            raise CanvasError('本轮尚未选择生成渠道，请先询问用户并选择“即梦”或“优云智算 H3”')
        if model and channel_for(model) != cfg.get('provider'):
            raise CanvasError('模型与用户选择的生成渠道不符，请使用已选渠道的模型')

    def balance(self, channel):
        if channel not in CHANNELS:
            raise CanvasError('未知生成渠道')
        flow = self.h3 if channel == 'compshare' else self.canvas
        return {'provider': channel, 'provider_label': CHANNELS[channel]['label'], **flow.credit_balance(0)}

    def __getattr__(self, name):
        if name in ('auth_status', 'login', 'wait_login'):
            return getattr(self.canvas, name)
        def routed(identifier, *args, **kwargs):
            model = (args[0] if args else kwargs.get('model')) if name == 'configure' else self.service.projects.get(identifier).get('generation', {}).get('model')
            return getattr(self.h3 if model in MODELS else self.canvas, name)(identifier, *args, **kwargs)
        return routed
