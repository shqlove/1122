"""Typed workbench operations shared by the embedded agent, desktop and CLI."""
from __future__ import annotations

from .dialogue_segments import has_tasks

import base64
import hashlib
import io
import json
import shutil
import uuid
from pathlib import Path

from PIL import Image
from jsonschema import validate

from .compshare import GenerationWorkflow as CanvasWorkflow, CHANNELS, channel_for
from .projects import original_frame, project_asset, normalize_character_groups
from .project_run import workflow_lock, analysis_running
from .assets import catalog, read_attachment, import_asset
from .h3_baseline import BASELINE, BASELINE_ID, apply_baseline, validate_baseline, uses_haoge
from .generation import _sha


def _schema(properties=None, required=()):
    return {'type': 'object', 'properties': properties or {}, 'required': list(required), 'additionalProperties': False}


STR = {'type': 'string'}
IDS = {'type': 'array', 'items': STR, 'minItems': 1, 'uniqueItems': True}
TOOLS = [
    ('apply_h3_baseline', '应用2026-11-07首次H3任务基准到本项目：原提示词、15秒768P16:9、Context-IR开启、原8.83秒音色、shared、保留原文。逐组绑定实际A/B图片；仅替换台词正文，短台词一条、长台词自动两条15秒，下载后按组拼接。已有任务先处理原任务再new_round；不提交生成。', _schema()),
    ('record_reference_review', '看过view_group的实际图片后记录字幕/水印检查，sha256必须取自刚查看图片。clean=true表示确实无叠加字幕水印且修复自然；有残留设false并先repair_region再复看。无需询问用户。不是自动识别结果，禁止未看图直接标记通过。', _schema({
        'group_id': STR, 'side': {'enum': ['a','b']}, 'sha256': STR,
        'clean': {'type': 'boolean'}}, ['group_id','side','sha256','clean'])),
    ('read_media_library', '读取当前项目的生成原片、后期版本、字幕状态及后期执行记录。先读真实记录，不把已导出当已验收。', _schema()),
    ('start_postproduction', '独立第二步：仅用户另行明确要求对指定视频做后期/chuli时调用；生成、下载、合并或原片检查完成都不授权此工具。第一步在合并原片回填工作台后结束，不能自动衔接后期。传入read_media_library中的确切item_ids；分段任务选assembly合并原片，勿同时选择分段原片。阿里云真实句/词时间轴→本地空间效果→统一BGM→FFmpeg字幕合成及检查导出。不调用ChatCut或剪映，不重新生成视频。recognize=true会上传所选原片音轨给阿里云识别缺失字幕，调用前告知；已有字幕复用。失败可按原item_ids继续，识别状态不确定时不自动重新计费。返回后台run_id，继续读库查询结果。', _schema({'item_ids': IDS, 'recognize': {'type':'boolean'}}, ['item_ids','recognize'])),
    ('transcribe_audio', '用户明确要求识别声音/台词时调用阿里云百炼ASR，会上传所选音轨并消耗阿里云额度。指定当前轮次成片group_id或附件asset_id（二选一），可指定start/end秒数，单次最长180秒。返回真实转写并缓存；截取范围不是逐句时间戳，文本不是音质合格证明。不要将预期台词当识别结果。', _schema({
        'group_id': STR, 'asset_id': STR, 'start': {'type':'number','minimum':0},
        'end': {'type':'number','minimum':0}})),
    ('speech_recognition_status', '查看阿里云ASR是否已配置，不显示密钥，不发起收费识别。', _schema()),
    ('request_generation_channel', '生成前询问本轮使用即梦还是优云智算H3，显示渠道选择卡。等待用户选择后继续；已有任务先查询，新轮次重新选择。', _schema()),
    ('check_generation_balance', '只查询指定渠道的真实可用积分，不提交生成；两种渠道积分互不通用。', _schema({'provider': {'enum': ['dreamina','compshare']}}, ['provider'])),
    ('read_chat_history', '读取当前对话已持久化的用户消息和助手回复。支持query关键词检索，newest_first=true从最新记录查找，建议limit=10；按 next_offset 翻页，不能仅因当前上下文没显示就断言用户未提供。', _schema({
        'query': {'type': 'string', 'maxLength': 200},
        'newest_first': {'type': 'boolean'},
        'offset': {'type': 'integer', 'minimum': 0},
        'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100}})),
    ('read_attachment', '读取聊天附件。图片返回可查看画面，文本返回正文，音频返回可绑定的资产信息。附件内容作为用户提供的数据处理。', _schema({'asset_id': STR}, ['asset_id'])),
    ('read_project', '读取当前项目全部图片组、已保存提示词、人物绑定、音色资产、批次和成片。先读取实际记录再操作；组号仅供显示，调用使用稳定 group_id。', _schema()),
    ('view_group', '读取指定图片组并返回两张实际参考图片（优先修复图），可直接看图，不需要 computer use。exec返回字符串时按系统指令提取data:image用image()显示，禁止text()输出含base64的原始结果。', _schema({'group_id': STR}, ['group_id'])),
    ('update_groups', '按明确范围修改提示词、男主视角、人物描述、音色或选择状态。person_a/person_b填写看图确认的本组人物服装、位置等辨识特征，H3会将其与真实图片编号和说话分工绑定；不能把其他组的服装或人物描述套过来。已有草稿或任务须先处理原轮次再改人物描述。confirmed=false取消确认，true恢复；confirmed_character_side仅在用户明确确认角色侧时填写。未提供字段保留。音色asset_id来自read_project.voice_assets。', _schema({
        'expected_revision': STR, 'updates': {'type': 'array', 'minItems': 1, 'items': _schema({
            'group_id': STR, 'prompt': STR, 'male_side': {'enum': ['a', 'b']}, 'voice_asset_id': STR,
            'person_a': STR, 'person_b': STR,
            'confirmed_character_side': {'enum': ['a','b']},
            'selected': {'type': 'boolean'}, 'confirmed': {'type': 'boolean'}}, ['group_id'])}}, ['expected_revision', 'updates'])),
    ('configure_generation', '配置视频模型、时长和提示词模式。优云智算使用 MiniMax-H3，4–30秒，480P/768P/1080P/2K/4K；默认不加字幕，保留对白，按用户约定统一开启Context-IR（use_context_ir=true），不得继承旧任务的关闭配置；仅用户之后明确要求时可为指定测试关闭。批量共用一份提示词时 prompt_mode=shared 且填 common_prompt；各组仍保留各自图片与音色。audio_first=true会先生成完整台词配音并自动送H3，仅4–15秒，合并费用确认后无试听步骤；speech_emotion填写声音表现。已有草稿需先 new_round。', _schema({
        'model': STR, 'duration': {'type': 'integer', 'minimum': 4, 'maximum': 60},
        'ratio': {'enum': ['4:3','16:9','9:16','1:1','3:4','21:9','adaptive']}, 'resolution': STR,
        'prompt_mode': {'enum': ['combined', 'shared']}, 'common_prompt': STR, 'use_context_ir': {'type': 'boolean'},
        'audio_first': {'type': 'boolean'}, 'speech_emotion': {'type': 'string', 'maxLength': 200},
        'verbatim_prompt': {'type': 'boolean'}, 'mute_audio': {'type': 'boolean'}, 'aigc_watermark': {'type': 'boolean'},
        'skill_id': {'type': 'string', 'maxLength': 128},
        'auto_split_dialogue': {'type': 'boolean', 'description': '豪哥H3新默认true，短台词一条、长台词两条15秒，仅替换对白。仅用户要求其他实验时关闭'},
        'preserve_prompt': {'type': 'boolean', 'description': '默认true，保留原文，不追加自动人物前缀；首次任务基准必须true'},
        'baseline_id': {'enum': [BASELINE_ID, 'custom'], 'description': '默认沿用首次任务基准，禁止擅自切custom；仅用户明确要求偏离基准的新方案时使用custom'}},
        ['model','duration','ratio','resolution','prompt_mode','common_prompt'])),
    ('normalize_characters', '将指定组已确认的目标角色放到A，交换对应原图、修复图和人物描述，男主绑定A。身份不确定须先请用户确认；已有草稿或提交需先处理原轮次。', _schema({'group_ids': IDS}, ['group_ids'])),
    ('generation_preflight', '检查指定组人物位置、素材、提示词引用、音色和台词时长，返回问题及最终执行单。H3的bound_prompt是替换成真实编号后的最终文本，role_bindings列出图片编号、人物描述、说话分工与音色。准备草稿前逐组核对。', _schema({'group_ids': IDS}, ['group_ids'])),
    ('prepare_batch', '只为明确列出的图片组创建生成草稿并绑定各组图片/音频；H3仅本地准备，批准后提交素材，不扣生成积分。必须使用 read_project 返回的 group_id。', _schema({'group_ids': IDS}, ['group_ids'])),
    ('quote_batch', '查询本轮所有草稿的积分报价，不提交生成。', _schema()),
    ('request_submission', '用户要求生成时，先准备并报价，再在工作台聊天中展示精确批次和积分上限确认卡。此工具本身不会提交或扣积分；用户点击确认卡后应用提交。', _schema()),
    ('refresh_results', '按原任务ID查询本轮状态。已接受提交不代表视频生成完成；不要新建重复任务。', _schema()),
    ('download_results', '下载本轮已完成的成片，校验文件并接回对应图片组。', _schema()),
    ('new_round', '保留本轮历史与成片，建立空的新轮次。生成中或状态不确定的任务不能归档。不会提交。', _schema()),
    ('execution_sheet', '返回本组最终提示词、图片与音色引用及生成参数，供精确核对。', _schema({'group_id': STR}, ['group_id'])),
    ('list_video_models', '查询支持的视频生成模型和参数约束（不是聊天模型）。', _schema()),
    ('analyze_video', '通过已有分析流程扫描/拆解当前项目视频，会使用 Codex 额度。只在用户明确要求拆解时调用。', _schema({
        'start': {'type':'number','minimum':0}, 'end': {'type':['number','null']}, 'scan_only': {'type':'boolean'}}, ['start','end','scan_only'])),
    ('repair_region', '对指定组、指定视角的原图像素矩形执行本地修复。用户要求去水印默认包括去除叠加字幕，只有字幕也直接处理，无需另问是否去字幕；用户明确要求保留时除外。先 view_group，准确指定原图坐标，修复后再 view_group 核对。', _schema({
        'group_id': STR, 'side': {'enum':['a','b']}, 'rect': {'type':'array','items':{'type':'integer','minimum':0},'minItems':4,'maxItems':4}}, ['group_id','side','rect'])),
    ('export_images', '把本项目勾选的图片组导出到项目内 exports 新目录，保留已有导出。', _schema()),
]
TOOLS.extend([
    ('create_video_jobs', '新生成统一使用独立视频任务。先看图、确认人物并记录清理检查，再传原图片组ID及完整生成配置。复制素材后每条独立准备报价，返回job_ids；在视频生成页逐条点击精确费用卡后才付费。项目旧任务和分析不阻塞本操作，不修改来源项目。不要重复创建已有job；分段和合并由后端完成。',
     _schema({'group_ids': IDS, 'voice_source': {'enum': ['baseline', 'group'], 'description': '默认baseline成功模板原音色；用户明确选图片组绑定音色才用group'}, 'config': next(spec for name, _, spec in TOOLS if name == 'configure_generation')}, ['group_ids', 'config'])),
    ('read_video_jobs', '读取当前来源项目的独立视频任务、每条状态和费用卡；跨项目切换不丢失记录。仅查询，不提交。', _schema()),
    ('refresh_video_job', '继续指定独立视频条目的报价或按原submit_id查询下载；不会付费重提。', _schema({'job_id': STR}, ['job_id'])),
    ('view_video_job', '查看独立视频任务的实际图片副本及SHA，供检查水印字幕。', _schema({'job_id': STR}, ['job_id'])),
    ('review_video_job', '已实际查看view_video_job两图，确认两图无水印字幕后记录SHA并继续本条报价。不得未看图通过，有残留须先回来源清理再新建任务。', _schema({'job_id': STR, 'sha_a': STR, 'sha_b': STR}, ['job_id', 'sha_a', 'sha_b'])),
])
TOOL_SCHEMAS = {name: spec for name, _, spec in TOOLS}


def dynamic_tools():
    return [{'type':'function','name':'workbench_'+name,'description':description,'inputSchema':schema}
            for name,description,schema in TOOLS]


def revision(project):
    snapshot = {k:project.get(k) for k in ('groups','generation','generation_history','phase')}
    return hashlib.sha256(json.dumps(snapshot,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


class WorkflowAPI:
    def __init__(self, service):
        self.service = service
        self.flow = CanvasWorkflow(service)

    def _project(self, identifier):
        return self.service.projects.get(identifier)

    def summary(self, identifier):
        p = self._project(identifier)
        assets = {a['asset_id']: a for a in catalog(self.service.projects.root / 'voice-library')}
        attachments = catalog(Path(p['directory']) / 'chat-attachments')
        assets.update({a['asset_id']: a for a in attachments if a['kind'] == 'audio'})
        groups = []
        for index,g in enumerate(p['groups'],1):
            bindings = g.get('generation_bindings', {})
            voice = bindings.get('voice_path')
            voice_id = None
            if voice:
                voice_id = hashlib.sha256(str(Path(voice).resolve()).encode()).hexdigest()[:20]
                assets[voice_id] = {'asset_id': voice_id, 'name':bindings.get('voice_name',Path(voice).name), 'path':voice}
            groups.append({'group_id':g['id'],'number':index,'selected':g.get('selected',True),
                           'needs_review':g.get('needs_review',False),'prompt':g.get('prompt',''),
                           'male_side':bindings.get('male_side','a'),'voice_asset_id':voice_id,
                           'person_a':g.get('person_a'),'person_b':g.get('person_b'),
                           'required_character_status':g.get('required_character_status'),
                           'required_character_side':g.get('required_character_side'),
                           'frame_a':g['frame_a'],'frame_b':g['frame_b'],
                           'repaired_a':g.get('repaired_a'),'repaired_b':g.get('repaired_b')})
        cfg = p.get('generation',{})
        def jobs(config):
            return [{k:t.get(k) for k in ('group_id','source_group_id','segment','status','node_id','submit_id','resource_id','output','archive_output','download_error','speech','error','query_error')}
                    for t in config.get('tasks',{}).values()]
        return {'project_id':p['id'],'name':p['name'],'phase':p['phase'],'revision':revision(p),
                'groups':groups,'voice_assets':list(assets.values()), 'attachments': attachments,
                'generation_channels':CHANNELS,
                'generation_defaults':{**CHANNELS.get(cfg.get('provider'), {}),'mode':'m2v','prompt_mode':'shared'},
                'h3_baseline': BASELINE,
                'generation':{**{k:cfg.get(k) for k in ('provider','channel_selected','channel_request','model','duration','ratio','resolution','prompt_mode','global_prompt','use_context_ir','audio_first','speech_emotion','preserve_prompt','baseline_id','dialogue_policy','dialogue_plans','segment_plan','mute_audio','aigc_watermark','skill_id','verbatim_prompt','quote','pending_approval')},'tasks':jobs(cfg)},
                'history': [{'model':c.get('model'),'duration':c.get('duration'),'tasks':jobs(c)} for c in p.get('generation_history',[])]}

    def execute(self, identifier, action, arguments=None):
        args = arguments or {}
        if action not in TOOL_SCHEMAS:
            raise ValueError('不支持的工作台操作：'+action)
        validate(args, TOOL_SCHEMAS[action])
        p = self._project(identifier)
        if action in ('create_video_jobs', 'read_video_jobs', 'refresh_video_job', 'view_video_job', 'review_video_job'):
            from .video_queue import queue_for
            queue = queue_for(self.service)
            if action == 'create_video_jobs':
                return queue.create(identifier, args['group_ids'], args['config'], args.get('voice_source', 'baseline'))
            if action in ('refresh_video_job', 'view_video_job', 'review_video_job'):
                job = queue.service.projects.get(args['job_id'])
                if job.get('video_job', {}).get('source_project_id') != identifier:
                    raise ValueError('视频任务不属于当前来源项目')
                if action == 'view_video_job':
                    return queue.api.execute(job['id'], 'view_group', {'group_id': job['groups'][0]['id']})
                if action == 'review_video_job':
                    if any(_sha(Path(job['groups'][0]['repaired_' + side])) != args['sha_' + side] for side in ('a', 'b')):
                        raise ValueError('参考图SHA已变化，请重新查看')
                    queue.review(job['id'])
                else:
                    queue.refresh(args['job_id'])
            return {'jobs': [r for r in queue.rows() if r['source_project_id'] == identifier]}
        if action == 'speech_recognition_status':
            from .aliyun_asr import status
            return status(self.service.config)
        if action == 'transcribe_audio':
            from .aliyun_asr import transcribe
            if bool(args.get('group_id')) == bool(args.get('asset_id')):
                raise ValueError('请只指定一个成片 group_id 或附件 asset_id')
            if args.get('group_id'):
                task = p.get('generation', {}).get('tasks', {}).get(args['group_id'], {})
                source = task.get('output')
                if not source or task.get('status') != 'downloaded':
                    raise ValueError('本组尚无已下载成片，请先查询并下载')
            else:
                summary = self.summary(identifier)
                assets = summary['attachments'] + summary['voice_assets']
                asset = next((a for a in assets if a['asset_id'] == args['asset_id']), None)
                if not asset:
                    raise ValueError('附件不存在')
                source = asset['path']
            with workflow_lock(Path(p['directory'])):
                return transcribe(self.service.config, source, Path(p['directory']) / 'asr-results',
                                  args.get('start', 0), args.get('end'))
        if action == 'read_chat_history':
            path = self.service.projects.root / 'agent-chat.json'
            if not path.exists():
                path = Path(p['directory']) / 'agent-chat.json'
            data = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
            messages = [{k: m.get(k) for k in ('role', 'text', 'time', 'project_id', 'project_name')} for m in data.get('messages', [])
                        if m.get('role') in ('user', 'assistant')]
            query = args.get('query', '').strip().casefold()
            if query:
                messages = [m for m in messages if query in (m.get('text') or '').casefold()]
            if args.get('newest_first'):
                messages.reverse()
            offset, limit = args.get('offset', 0), args.get('limit', 30)
            end = min(offset + limit, len(messages))
            return {'messages': messages[offset:end], 'total': len(messages),
                    'next_offset': end if end < len(messages) else None}
        if action == 'read_attachment':
            assets = self.summary(identifier)['attachments'] + self.summary(identifier)['voice_assets']
            asset = next((a for a in assets if a['asset_id'] == args['asset_id']), None)
            if asset is None:
                raise ValueError('附件不存在')
            return read_attachment(asset)
        if action in ('read_project','view_group','execution_sheet','list_video_models'):
            return self._execute(identifier,action,args)
        if analysis_running(Path(p['directory'])):
            raise ValueError('项目正在拆解，请等待完成')
        with workflow_lock(Path(p['directory'])):
            return self._execute(identifier,action,args)

    def _execute(self, identifier, action, args):
        p = self._project(identifier)
        if action == 'apply_h3_baseline':
            self.flow.require_channel(identifier, 'MiniMax-H3')
            apply_baseline(p)
            self.service.projects.save(p)
            return self.summary(identifier)
        if action == 'record_reference_review':
            group = next(g for g in p['groups'] if g['id'] == args['group_id'])
            if p.get('generation', {}).get('tasks', {}).get(group['id']):
                raise ValueError('已有草稿或任务，请先新建轮次再检查素材')
            side = args['side']
            path = (project_asset(p, group['repaired_' + side]) if group.get('repaired_' + side) else
                    original_frame(p, group['frame_' + side], self.service.config['ffmpeg'], self.service.config['ffprobe']))
            if _sha(path) != args['sha256']:
                raise ValueError('图片已变化，请重新查看实际参考图后记录检查结果')
            group.setdefault('reference_reviews', {})[side] = {'sha256': args['sha256'], 'clean': args['clean']}
            self.service.projects.save(p)
            return {'group_id': group['id'], 'side': side, 'clean': args['clean']}
        if action in ('read_media_library', 'start_postproduction'):
            from .workbench import PostWorkbench
            workbench = PostWorkbench(self.service)
            library = workbench.workbench_library()
            items = [i for i in library['items'] if i.get('project_id') == identifier]
            if action == 'read_media_library':
                ids = {i['id'] for i in items}
                return {'items': items, 'runs': [r for r in library['runs'] if ids.intersection(r.get('item_ids', []))],
                        'post_available': library['post_available']}
            allowed = {i['id'] for i in items}
            if not set(args['item_ids']).issubset(allowed):
                raise ValueError('后期范围必须是当前项目的明确视频记录')
            return workbench.workbench_start_post(args['item_ids'], args['recognize'])
        if action == 'check_generation_balance':
            return self.flow.balance(args['provider'])
        if action == 'request_generation_channel':
            cfg = p.setdefault('generation', {})
            if cfg.get('tasks'):
                raise ValueError('本轮已有任务，请先查询结果或新建轮次')
            cfg['channel_request'] = {'id': str(uuid.uuid4()), 'options': CHANNELS}
            self.service.projects.save(p)
            return {'status': 'awaiting_channel_selection', **cfg['channel_request']}
        if action == 'read_project':
            return self.summary(identifier)
        if action == 'view_group':
            g = next((g for g in p['groups'] if g['id']==args['group_id']),None)
            if not g:
                raise ValueError('图片组不存在')
            images=[]
            for side in ('a','b'):
                path = (project_asset(p,g['repaired_'+side]) if g.get('repaired_'+side) else
                        original_frame(p,g['frame_'+side],self.service.config['ffmpeg'],self.service.config['ffprobe']))
                with Image.open(path) as source:
                    im=source.convert('RGB'); width,height=im.size; im.thumbnail((1024,768))
                    buffer=io.BytesIO(); im.save(buffer,format='JPEG',quality=90)
                images.append({'side':side,'width':width,'height':height,'path':str(path),'sha256':_sha(path),
                               'image_url':'data:image/jpeg;base64,'+base64.b64encode(buffer.getvalue()).decode()})
            return {'group':g,'images':images}
        if action == 'update_groups':
            if revision(p) != args['expected_revision']:
                raise ValueError('项目已变化，请重新读取后再修改')
            assets={a['asset_id']:a for a in self.summary(identifier)['voice_assets']}
            by_id={g['id']:g for g in p['groups']}
            seen=set()
            for update in args['updates']:
                gid=update['group_id']
                if gid not in by_id or gid in seen:
                    raise ValueError('修改范围包含不存在或重复的图片组')
                seen.add(gid); g=by_id[gid]
                for key in ('person_a', 'person_b'):
                    if key in update:
                        description = update[key].strip()
                        if description != (g.get(key) or '') and has_tasks(p, gid):
                            raise ValueError('已有生成草稿或任务，请先处理当前轮次再修改人物描述')
                        g[key] = description
                for key in ('prompt','selected'):
                    if key in update: g[key]=update[key]
                if 'confirmed' in update:
                    g.update(needs_review=not update['confirmed'], selected=update['confirmed'],
                             validation_state='confirmed' if update['confirmed'] else 'rejected')
                    p.get('generation', {}).pop('quote', None)
                    p.get('generation', {}).pop('pending_approval', None)
                if g.get('needs_review'): g['selected'] = False
                if 'confirmed_character_side' in update:
                    if has_tasks(p, gid):
                        raise ValueError('已有生成草稿或任务，请先处理当前轮次')
                    g.update(required_character_status='matched', required_character_side=update['confirmed_character_side'],
                             required_character_evidence='用户明确确认目标角色所在侧')
                    normalize_character_groups(p,[gid])
                b=g.setdefault('generation_bindings',{'male_side':'a'})
                if 'male_side' in update: b['male_side']=update['male_side']
                if 'voice_asset_id' in update:
                    asset=assets.get(update['voice_asset_id'])
                    if not asset: raise ValueError('音色资产不存在，请从界面导入音色后重新读取')
                    if has_tasks(p, gid):
                        raise ValueError('已有生成草稿或任务，请先处理当前轮次再更换音色')
                    if Path(asset['path']).parent == self.service.projects.root / 'voice-library':
                        imported = import_asset(asset['path'], Path(p['directory']) / 'voice-references', self.service.config, voice=True)
                        asset = {**imported, 'name': asset['name']}
                    project_asset(p,asset['path'])
                    b.update(voice_path=asset['path'],voice_name=asset['name'])
            self.service.projects.save(p)
        elif action == 'configure_generation':
            self.flow.require_channel(identifier,args['model'])
            old = p.get('generation', {})
            if channel_for(args['model']) == 'compshare' and uses_haoge(p) and not old.get('baseline_id') and 'baseline_id' not in args:
                raise ValueError('请先应用首次任务基准，不得直接沿用旧模板')
            audio_first = args.get('audio_first', old.get('audio_first', False))
            emotion = args.get('speech_emotion', old.get('speech_emotion', ''))
            preserve = args.get('preserve_prompt', old.get('preserve_prompt', True))
            baseline_id = args.get('baseline_id', old.get('baseline_id', 'custom'))
            auto_split = args.get('auto_split_dialogue', old.get('dialogue_policy') == 'dialogue-two-v1')
            from .h3_options import DEFAULTS, validate_options
            options = {k: args.get(k, old.get(k, v)) for k, v in DEFAULTS.items()}
            if channel_for(args['model']) != 'compshare' and (any(k in args for k in DEFAULTS) or args['ratio'] == 'adaptive'):
                raise ValueError('声音、水印、Skill及自适应比例设置仅适用于优云智算H3')
            validate_options({**old, **args, **options, 'baseline_id': baseline_id, 'audio_first': audio_first,
                              'dialogue_policy': 'dialogue-two-v1' if auto_split else None})
            if old.get('tasks') and any(options[k] != old.get(k, v) for k, v in DEFAULTS.items()):
                raise ValueError('已有草稿或任务，请先新建轮次再修改平台参数')
            if old.get('tasks') and auto_split != (old.get('dialogue_policy') == 'dialogue-two-v1'):
                raise ValueError('已有草稿或任务，请先新建轮次再调整分段策略')
            if old.get('tasks') and baseline_id != old.get('baseline_id', 'custom'):
                raise ValueError('已有草稿或任务，请先新建轮次再切换生成基准')
            validate_baseline({**old, **args, 'baseline_id': baseline_id,
                'dialogue_policy': 'dialogue-two-v1' if auto_split else None,
                'global_prompt': args['common_prompt'], 'mode': 'm2v',
                'preserve_prompt': preserve, 'audio_first': audio_first})
            if auto_split and (channel_for(args['model']) != 'compshare' or args['duration'] != 15
                               or audio_first or not preserve or args['prompt_mode'] != 'shared'):
                raise ValueError('自动分段使用H3、15秒、共用原文和音色参考；其他实验请关闭自动分段')
            if type(preserve) is not bool:
                raise ValueError('保留原提示词设置须为布尔值')
            if old.get('tasks') and preserve != old.get('preserve_prompt', True):
                raise ValueError('已有草稿或任务，请先新建轮次再调整提示词处理方式')
            if audio_first and (channel_for(args['model']) != 'compshare' or args['duration'] > 15):
                raise ValueError('先生成完整配音再传H3模式仅支持4–15秒的H3视频')
            if old.get('tasks') and (audio_first != old.get('audio_first', False) or emotion != old.get('speech_emotion', '')):
                raise ValueError('已有草稿或任务，请先新建轮次再调整配音流程')
            if 'use_context_ir' in args and channel_for(args['model']) != 'compshare':
                raise ValueError('提示词优化开关仅适用于优云智算 H3')
            self.flow.configure(identifier,args['model'],args['duration'],args['common_prompt'],
                                args['ratio'],args['resolution'],args['prompt_mode'],
                                **({'use_context_ir': args['use_context_ir']} if 'use_context_ir' in args else {}))
            p = self._project(identifier)
            p['generation'].update(audio_first=audio_first, speech_emotion=emotion, preserve_prompt=preserve,
                                   baseline_id=baseline_id)
            if channel_for(args['model']) == 'compshare':
                p['generation'].update(options)
            if auto_split:
                p['generation']['dialogue_policy'] = 'dialogue-two-v1'
            else:
                p['generation'].pop('dialogue_policy', None)
            if not p['generation'].get('tasks'):
                p['generation'].pop('dialogue_plans', None)
            self.service.projects.save(p)
        elif action == 'normalize_characters':
            ids=args['group_ids']
            if not set(ids).issubset({g['id'] for g in p['groups']}):
                raise ValueError('图片组范围不存在')
            normalize_character_groups(p,ids)
            self.service.projects.save(p)
        elif action == 'generation_preflight':
            return self.flow.preflight(identifier,args['group_ids'])
        elif action == 'prepare_batch':
            self.flow.require_channel(identifier)
            self.flow.prepare(identifier,group_ids=args['group_ids'])
        elif action == 'quote_batch':
            self.flow.quote(identifier)
            # The approved baseline already fixes full text and duration. A quote
            # must expose its payment card even if the agent stops after this tool.
            if self._project(identifier).get('generation', {}).get('baseline_id') == BASELINE_ID:
                self._execute(identifier, 'request_submission', {})
        elif action == 'request_submission':
            cfg=p.get('generation',{}); quote=cfg.get('quote')
            if not quote or any(t.get('status')!='draft' for t in cfg.get('tasks',{}).values()):
                raise ValueError('请先准备草稿并查询本轮积分')
            check=self.flow.preflight(identifier,quote['group_ids'])
            if check['errors']:
                raise ValueError('；'.join(item['message'] for item in check['errors']))
            pending={'warnings':check['warnings'],'id':str(uuid.uuid4()),'ceiling':quote['ceiling'],'group_ids':quote['group_ids'],
                     **{k:quote.get(k) for k in ('available_credits','shortfall','sufficient','checked_at')},
                     'node_ids':quote['node_ids'],'count':len(quote['node_ids']),
                     'model':cfg['model'],'duration':cfg['duration'], 'resolution':cfg.get('resolution'), 'ratio':cfg.get('ratio'),
                     'provider':channel_for(cfg['model']), 'provider_label':CHANNELS[channel_for(cfg['model'])]['label'],
                     'quote_basis':quote.get('basis','渠道实时报价'), 'quote_kind':'estimate' if channel_for(cfg['model'])=='compshare' else 'live'}
            if channel_for(cfg['model']) == 'compshare':
                pending['use_context_ir'] = cfg.get('use_context_ir', True)
                pending['audio_first'] = cfg.get('audio_first', False)
                from .h3_options import DEFAULTS
                pending.update({k: cfg.get(k, v) for k, v in DEFAULTS.items()})
                if pending['audio_first']:
                    pending.update(speech_cost=quote['speech_cost'], video_cost=quote['video_cost'],
                        speech_previews=[{'group_id': s['group_id'], **s['speech_plan']} for s in check['execution_sheets']])
                pending['character_previews'] = [
                    {'group_id': sheet['group_id'], 'bound_prompt': sheet['bound_prompt'],
                     'source_group_id': sheet.get('source_group_id', sheet['group_id']), 'segment': sheet.get('segment'),
                     'role_bindings': sheet['role_bindings'], 'baseline_name': sheet.get('baseline_name')}
                    for sheet in check['execution_sheets']]
            cfg['pending_approval']=pending
            self.service.projects.save(p)
            return {'status':'awaiting_user_confirmation' if quote.get('sufficient') else 'insufficient_credits','pending_approval':pending,
                    'message':'确认卡已显示。积分不足或余额未知时禁止提交，请充值或调整批次后重新查询。'}
        elif action == 'refresh_results': self.flow.refresh(identifier)
        elif action == 'download_results': self.flow.download(identifier)
        elif action == 'new_round': self.flow.new_round(identifier)
        elif action == 'execution_sheet': return self.flow.execution_sheet(identifier,args['group_id'])
        elif action == 'list_video_models': return self.flow.models()
        elif action == 'analyze_video': self.service.analyze(identifier,args['start'],args['end'],scan_only=args['scan_only'])
        elif action == 'repair_region': self.service.repair(identifier,args['group_id'],args['side'],args['rect'])
        elif action == 'export_images':
            folder=Path(p['directory'])/'exports'; folder.mkdir(exist_ok=True)
            return {'path':str(self.service.export(identifier,folder))}
        return self.summary(identifier)

    def approve_submission(self, identifier, confirmation_id, ceiling):
        """Host/UI only; intentionally absent from the model's tool catalog."""
        p=self._project(identifier)
        with workflow_lock(Path(p['directory'])):
            p=self._project(identifier); cfg=p.get('generation',{}); pending=cfg.get('pending_approval')
            if not pending or pending['id']!=confirmation_id or pending['ceiling']!=ceiling:
                raise ValueError('积分确认卡已失效，请重新报价')
            quote=cfg.get('quote',{})
            if quote.get('node_ids')!=pending['node_ids'] or quote.get('group_ids')!=pending['group_ids']:
                raise ValueError('批次已变化，请重新报价')
            self.flow.run_approved(identifier,ceiling)
            p=self._project(identifier);p['generation'].pop('pending_approval',None);self.service.projects.save(p)
        return self.summary(identifier)

    def import_voice(self, identifier, group_ids, source):
        source=Path(source).resolve(strict=True)
        if source.suffix.lower() not in ('.mp3','.wav','.m4a','.aac','.flac','.ogg'):
            raise ValueError('请选择音频文件')
        p=self._project(identifier)
        if not group_ids or not set(group_ids).issubset({g['id'] for g in p['groups']}):
            raise ValueError('音色绑定范围不正确')
        folder=Path(p['directory'])/'voice-references';folder.mkdir(exist_ok=True)
        target=folder/(hashlib.sha256(source.read_bytes()).hexdigest()+source.suffix.lower())
        if not target.exists():shutil.copy2(source,target)
        for g in p['groups']:
            if g['id'] in group_ids:
                g.setdefault('generation_bindings',{'male_side':'a'}).update(voice_path=str(target),voice_name=source.name)
        self.service.projects.save(p)
        return self.summary(identifier)
