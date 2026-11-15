"""Official Dreamina Canvas CLI integration: two character references, not first/last frames.

No browser credentials, API keys, credit tokens, or signed URLs are persisted.
Calls are argv-based and require a locally authenticated dreamina-canvas executable.
"""
import hashlib
import json
import math
import re
import subprocess
import uuid
import time
from pathlib import Path

from .projects import original_frame, project_asset, character_filter
from .media import probe
from .video_archive import archive_videos


class CanvasError(RuntimeError):
    pass


def _data(envelope):
    if not isinstance(envelope, dict):
        raise CanvasError("即梦 CLI 未返回有效 JSON")
    if envelope.get("error"):
        err = envelope["error"]
        code = err.get("code", "unknown") if isinstance(err, dict) else "unknown"
        action = err.get("requiredAction", "") if isinstance(err, dict) else ""
        raise CanvasError(f"即梦接口错误：{code}，后续操作：{action}")
    return envelope.get("data", envelope)


def invoke(cli, args, timeout=120):
    """No shell, no credential logging. An uncertain write must not be reissued blindly."""
    if not cli:
        raise CanvasError("未安装官方 dreamina-canvas CLI，请先在本机安装并登录。")
    cmd = [str(cli), "--format", "json", "--non-interactive", *map(str, args)]
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8',
                              errors='replace', timeout=timeout, check=False,
                              creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CanvasError("即梦命令未确认成功；若是写操作，请先核查已有任务，不要直接重提") from exc
    # CLI failures use stderr for JSON; successful responses use stdout.
    payload = done.stdout.strip() or getattr(done, 'stderr', '').strip()
    try:
        envelope = json.loads(payload.lstrip('\ufeff'))
    except ValueError as exc:
        raise CanvasError(f"即梦返回格式不可解析（退出码 {done.returncode}）；请核查命令及已有任务状态") from exc
    # Exit 20 means recover by original identity, never assume a paid request failed.
    if done.returncode not in (0, 20):
        err = envelope.get("error") if isinstance(envelope, dict) else None
        code = err.get("code", "unknown") if isinstance(err, dict) else "unknown"
        action = err.get("requiredAction", "") if isinstance(err, dict) else ""
        raise CanvasError(f"即梦 CLI 退出 {done.returncode}：{code}，后续操作 {action}")
    return _data(envelope)


def _sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for part in iter(lambda: f.read(1024 * 1024), b""):
            h.update(part)
    return h.hexdigest()


def _uuid():
    return str(uuid.uuid4())


def named_prompt(text):
    text = re.sub(r'(?<!@)男主@', '@男主', text)
    text = re.sub(r'(?<!@)聆听者@', '@聆听者', text)
    return re.sub(r'音色参考@(?!音色)', '音色参考@音色', text)


def bind_prompt(text, tokens):
    text = named_prompt(text)
    if '{{' in text:
        raise CanvasError('请使用引用按钮，不要手写即梦素材 ID')
    def replace(match):
        role = match.group(0)[1:]
        if role not in tokens:
            raise CanvasError(f'提示词引用 @{role} 尚未绑定素材')
        return tokens[role]
    # Check unknown @ before substituting the actual resource tokens.
    if '@' in re.sub(r'@(男主|聆听者|音色)', '', text):
        raise CanvasError('存在未绑定的 @，请使用男主、聆听者或音色引用按钮')
    return re.sub(r'@(男主|聆听者|音色)', replace, text)


def _created_node(data):
    if isinstance(data, dict):
        if isinstance(data.get('nodeId'), str):
            return data['nodeId']
        for value in data.values():
            found = _created_node(value)
            if found:
                return found
    elif isinstance(data, list):
        for value in data:
            found = _created_node(value)
            if found:
                return found
    return None


def _result_resource(value):
    """Only resource identities; never save provider URLs or tokens."""
    if isinstance(value, dict):
        for key in ("resourceId", "resultResourceId", "outputResourceId"):
            item = value.get(key)
            if isinstance(item, str) and len(item) == 36:
                return item
        for key in ("resources", "outputs", "result", "items", "assets"):
            if key in value:
                found = _result_resource(value[key])
                if found:
                    return found
    elif isinstance(value, list):
        for item in value:
            found = _result_resource(item)
            if found:
                return found
    return None


class CanvasWorkflow:
    def __init__(self, service):
        self.service = service

    @property
    def cli(self):
        return self.service.config.get("dreamina_canvas")

    def _load(self, identifier):
        return self.service.projects.get(identifier)

    def _save(self, project):
        self.service.projects.save(project)

    def configure(self, identifier, model, duration, global_prompt, ratio='4:3', resolution='720p', prompt_mode='combined'):
        project = self._load(identifier)
        settings = project.setdefault("generation", {})
        if any(task.get("submit_id") for task in settings.get("tasks", {}).values()):
            # Updating generation configuration after running would invalidate paid task identity.
            raise CanvasError("已有提交记录，请先新建生成轮次；旧成片会保留在本组。")
        model = str(model).strip()
        if not model:
            raise CanvasError("请先通过 CLI 查询当前可用视频模型，并填写其准确 model 标识")
        duration = int(duration)
        if not 1 <= duration <= 60:
            raise CanvasError("时长须为 1–60 秒且必须符合所选模型的实际约束")
        incoming_prompt = str(global_prompt).strip()
        if prompt_mode not in ('combined', 'shared'):
            raise CanvasError('提示词模式必须为 combined 或 shared')
        if prompt_mode == 'shared' and not incoming_prompt:
            raise CanvasError('共用提示词不能为空')
        if settings.get("tasks") and (settings.get("model") != model or
                settings.get("duration") != duration or settings.get("global_prompt") != incoming_prompt or
                settings.get('ratio', '4:3') != ratio or settings.get('resolution', '720p') != resolution or
                settings.get('prompt_mode', 'combined') != prompt_mode):
            raise CanvasError("已有即梦草稿，请先新建生成轮次再调整模型、时长或全局提示词。")
        settings.update(model=model, duration=duration, global_prompt=incoming_prompt,
                        mode="m2v", ratio=ratio, resolution=resolution, prompt_mode=prompt_mode)
        settings.pop("quote", None)
        settings.pop('pending_approval', None)
        self._save(project)
        return project

    def models(self):
        try:
            return invoke(self.cli, ["model", "list", "--type", "video"])
        except CanvasError as exc:
            if 'cli.authentication_required' not in str(exc):
                raise
            invoke(self.cli, ['auth', 'refresh'])
            return invoke(self.cli, ["model", "list", "--type", "video"])

    def auth_status(self):
        state = invoke(self.cli, ['auth', 'status'])
        if state.get('loggedIn'):
            models = self.models()
            return {'loggedIn': True, 'expiresAt': state.get('expiresAt'),
                    'message': '即梦已连接，视频模型查询成功', 'model_count': len(models.get('items', []))}
        return {'loggedIn': False, 'message': '即梦尚未登录'}

    def login(self):
        result = invoke(self.cli, ['auth', 'login'], timeout=180)
        state = self.auth_status()
        if state['loggedIn']:
            return state
        challenge = result.get('challenge')
        if not challenge or not challenge.get('deviceCode'):
            raise CanvasError('即梦未返回有效授权入口，请重新连接')
        return {**state, 'challenge': challenge}

    def wait_login(self, device_code):
        invoke(self.cli, ['auth', 'wait', '--device-code', device_code, '--timeout', '10m'], timeout=620)
        return self.auth_status()

    def new_round(self, identifier):
        project = self._load(identifier)
        cfg = project.get('generation', {})
        tasks = cfg.get('tasks', {})
        if any(t.get('status') not in ('downloaded', 'failed', 'canceled', 'draft') for t in tasks.values()):
            raise CanvasError('请先查询并下载当前轮次结果，状态不确定或生成中的任务不能归档')
        if tasks:
            project.setdefault('generation_history', []).append(cfg)
        project['generation'] = {k: cfg[k] for k in ('model', 'duration', 'global_prompt', 'mode', 'ratio', 'resolution', 'prompt_mode', 'use_context_ir', 'audio_first', 'speech_emotion', 'preserve_prompt', 'baseline_id', 'dialogue_policy', 'mute_audio', 'aigc_watermark', 'skill_id', 'verbatim_prompt') if k in cfg}
        self._save(project)
        return project

    def inputs(self, project, group):
        cfg = project.get('generation', {})
        if character_filter(project).get('required_character'):
            if group.get('required_character_status') != 'matched':
                raise CanvasError('目标角色身份尚未确认，请先人工确认后生成')
            if group.get('required_character_side') != 'a' or group.get('generation_bindings', {}).get('male_side', 'a') != 'a':
                raise CanvasError('请先将目标角色整理到人物A，并将男主绑定A')
        prompt = (cfg.get('global_prompt', '').strip() if cfg.get('prompt_mode') == 'shared' else
                  '\n'.join(x for x in (cfg.get('global_prompt', ''), group.get('prompt', '').strip()) if x).strip())
        if not prompt:
            raise CanvasError('请填写本组提示词')
        if character_filter(project).get('required_character') and not all(role in named_prompt(prompt) for role in ('@男主', '@聆听者')):
            raise CanvasError('对话提示词须引用 @男主 和 @聆听者，避免人物引用错位')
        bindings = group.get('generation_bindings', {})
        male = bindings.get('male_side', 'a')
        if male not in ('a', 'b'):
            raise CanvasError('男主必须绑定 A 或 B 图片')
        refs = []
        for side in ('a', 'b'):
            path = (project_asset(project, group[f'repaired_{side}']) if group.get(f'repaired_{side}') else
                    original_frame(project, group[f'frame_{side}'], self.service.config['ffmpeg'], self.service.config['ffprobe']))
            refs.append({'role': '男主' if side == male else '聆听者', 'type': 'image',
                         'path': str(path), 'sha256': _sha(path), 'side': side})
        if bindings.get('voice_path'):
            voice = project_asset(project, bindings['voice_path'])
            refs.append({'role': '音色', 'type': 'audio', 'path': str(voice), 'sha256': _sha(voice)})
        bind_prompt(prompt, {r['role']: r['role'] for r in refs})
        payload = dict(prompt=named_prompt(prompt), references=refs, model=cfg.get('model'),
                       duration=cfg.get('duration'), mode='m2v', ratio=cfg.get('ratio', '4:3'),
                       resolution=cfg.get('resolution', '720p'))
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        return payload, fingerprint

    def preflight(self, identifier, group_ids):
        project = self._load(identifier)
        known = {g['id']: g for g in project['groups']}
        if not group_ids or len(set(group_ids)) != len(group_ids) or not set(group_ids).issubset(known):
            raise CanvasError('生成检查范围为空、重复或不存在')
        errors, warnings, sheets = [], [], []
        if (len(group_ids) > 1 and character_filter(project).get('required_character')
                and project.get('generation', {}).get('prompt_mode') != 'shared'):
            errors.append({'group_id': None, 'message': '目标角色批量生成须使用shared共用提示词模式，请先配置本批共用提示词'})
        for gid in group_ids:
            group = known[gid]
            try:
                if group.get('needs_review') or not group.get('selected', True):
                    raise CanvasError('该图片组已取消确认或未勾选')
                if project.get('generation', {}).get('tasks', {}).get(gid, {}).get('submit_id'):
                    raise CanvasError('该组已有提交记录，请查询原任务并下载结果')
                payload, fingerprint = self.inputs(project, group)
                if not payload['model'] or not payload['duration']:
                    raise CanvasError('请先配置模型和时长')
                sheets.append(dict(group_id=gid, fingerprint=fingerprint, **payload))
                speech = ''.join(re.findall(r'[“「](.*?)[”」]', payload['prompt'], flags=re.S))
                if len(re.findall(r'[\u4e00-\u9fffA-Za-z0-9]', speech)) > payload['duration'] * 5:
                    warnings.append({'group_id': gid, 'message': '台词长度可能超过所选时长，请核对语速、台词和时长后确认费用'})
                if '@音色' not in payload['prompt']:
                    warnings.append({'group_id': gid, 'message': '提示词未引用音色，生成声音可能与豪哥参考音色不同'})
            except (CanvasError, ValueError, OSError) as exc:
                errors.append({'group_id': gid, 'message': str(exc)})
        return {'ready': not errors, 'errors': errors, 'warnings': warnings, 'execution_sheets': sheets}

    def execution_sheet(self, identifier, group_id):
        project = self._load(identifier)
        group = next(g for g in project['groups'] if g['id'] == group_id)
        data, _ = self.inputs(project, group)
        return {'project_id': identifier, 'project_name': project['name'], 'group_id': group_id,
                'group_number': project['groups'].index(group)+1, 'mode_label': '全能参考', **data,
                'task': project.get('generation', {}).get('tasks', {}).get(group_id)}

    def prepare(self, identifier, group_id=None, group_ids=None):
        """Create non-billable draft nodes. Network calls may write canvas metadata."""
        project = self._load(identifier)
        config = project.get("generation", {})
        if not config.get("model"):
            raise CanvasError("先配置视频模型和提示词")
        targets = list(group_ids) if group_ids is not None else ([group_id] if group_id else None)
        if targets is not None and (not targets or len(set(targets)) != len(targets) or
                not set(targets).issubset({g['id'] for g in project['groups']})):
            raise CanvasError('批次图片组范围为空、重复或不存在')
        selected = [g for g in project["groups"] if (g['id'] in targets if targets is not None else g.get("selected")) and not g.get("needs_review")]
        if targets is not None and len(selected) != len(targets):
            raise CanvasError('批次含待确认图片组，请先确认图片')
        if not selected:
            raise CanvasError("至少选择一组已通过自动复核的 A/B 截图")
        if any(task.get("submit_id") for task in config.get("tasks", {}).values()):
            raise CanvasError("本轮已有执行任务；请先查询和下载结果，再新建生成轮次")
        check = self.preflight(identifier, [g['id'] for g in selected])
        if check['errors']:
            raise CanvasError('；'.join(item['message'] for item in check['errors']))
        prepared = {g['id']: self.inputs(project, g) for g in selected}
        if config.get('tasks') and set(config['tasks']) != set(prepared):
            raise CanvasError('本轮已有其他组的草稿，请新建生成轮次后再准备当前组')
        if not config.get("project_id"):
            config["project_id"] = _uuid()
            self._save(project)
        canvas_id = config["project_id"]
        # Repeated create with the same project-id is idempotent.
        actual = invoke(self.cli, ["canvas", "create", project["name"][:45], "--project-id", canvas_id])
        if isinstance(actual, dict) and actual.get("projectId") not in (None, canvas_id):
            raise CanvasError("画布返回的 projectId 与本地登记不一致")
        tasks = config.setdefault("tasks", {})
        for group in selected:
            gid = group["id"]
            payload, fingerprint = prepared[gid]
            refs, prompt = payload['references'], payload['prompt']
            task = tasks.setdefault(gid, {})
            if task.get("status") == "node_create_unknown":
                raise CanvasError("节点创建结果不确定；请先在即梦画布核对，不自动创建第二个节点")
            if task.get("node_id"):
                if task.get("fingerprint") != fingerprint:
                    raise CanvasError(f"图片组 {gid} 的提示词或素材发生变化，请新建生成轮次")
                continue
            task.update(group_id=gid, fingerprint=fingerprint, source_prompt=prompt,
                        images=[r['sha256'] for r in refs if r['type']=='image'], references=refs,
                        status="preparing", resource_ids=task.get("resource_ids") or [_uuid() for r in refs])
            task['prompt'] = bind_prompt(prompt, {r['role']: '{{res:' + rid + '}}' for r, rid in zip(refs, task['resource_ids'])})
            self._save(project)  # Identities must be persisted BEFORE uploads.
            for ref, rid in zip(refs, task["resource_ids"]):
                response = invoke(self.cli, ["resource", "upload", "--file", ref['path'], '--type', ref['type'],
                                             "--project-id", canvas_id, "--resource-id", rid,
                                             "--import-kind", "local_upload"], timeout=180)
                if isinstance(response, dict) and response.get("resourceId") not in (None, rid):
                    raise CanvasError("资源上传返回的 identity 与预留 ID 不同")
            # Node create has no caller-provided idempotency key: mark unknown before the write.
            task["status"] = "node_create_unknown"
            self._save(project)
            command = ["node", "create", "video", "--project-id", canvas_id,
                                     "--mode", "m2v", "--model", config["model"],
                                     "--duration", config["duration"], '--ratio', config.get('ratio', '4:3'),
                                     '--resolution', config.get('resolution', '720p'), '--prompt', task['prompt']]
            for rid in task['resource_ids']:
                command += ['--ref', 'res:' + rid]
            data = invoke(self.cli, command, timeout=180)
            node_id = _created_node(data)
            if not isinstance(node_id, str) or not node_id.startswith("node_"):
                raise CanvasError("即梦未返回 nodeId，草稿创建结果不确定；禁止盲目重提")
            task.update(node_id=node_id, status="draft")
            self._save(project)
        config.pop("quote", None)
        config.pop('pending_approval', None)
        self._save(project)
        return project

    def credit_balance(self, ceiling):
        account = invoke(self.cli, ['auth', 'account'])
        value = account.get('credits', {}).get('available', account.get('availableCredits'))
        try:
            available = float(value)
        except (TypeError, ValueError):
            raise CanvasError('即梦未返回有效可用积分，请刷新后再提交')
        if isinstance(value, bool) or not math.isfinite(available) or available < 0:
            raise CanvasError('即梦可用积分异常，请刷新后再提交')
        return {'available_credits': available, 'shortfall': max(0, ceiling - available),
                'sufficient': available >= ceiling, 'checked_at': time.time()}

    def quote(self, identifier):
        project = self._load(identifier)
        config = project.get("generation", {})
        tasks = list(config.get("tasks", {}).values())
        if not tasks or any(x.get("status") != "draft" for x in tasks):
            raise CanvasError("请先准备好所有未付费的视频草稿")
        config.pop('quote', None)
        config.pop('pending_approval', None)
        self._save(project)
        args = ["node", "quote", "--project-id", config["project_id"]]
        for t in tasks:
            args += ["--node-id", t["node_id"]]
        q = invoke(self.cli, args)
        if not isinstance(q, dict) or q.get("confirmable") is not True or not isinstance(q.get("totalMaxCredits"), (int, float)):
            raise CanvasError("即梦未返回可确认的积分上限，禁止提交")
        if not math.isfinite(q['totalMaxCredits']) or q["totalMaxCredits"] < 0:
            raise CanvasError("报价异常")
        balance = self.credit_balance(q['totalMaxCredits'])
        config.pop('pending_approval', None)
        config["quote"] = {"ceiling": q["totalMaxCredits"], **balance,
                           "group_ids": [t["group_id"] for t in tasks],
                           "node_ids": [t["node_id"] for t in tasks]}
        self._save(project)
        return project

    def run_approved(self, identifier, approved_ceiling):
        """Only call after an explicit UI approval with displayed amount."""
        project = self._load(identifier)
        config = project.get("generation", {})
        q = config.get("quote") or {}
        if not q or approved_ceiling != q.get("ceiling"):
            raise CanvasError("授权报价不匹配；必须重新查看积分报价")
        tasks = [config["tasks"][gid] for gid in q["group_ids"]]
        selected = {g["id"]: g for g in project["groups"] if g['id'] in q['group_ids'] and not g.get("needs_review") and g.get('selected', True)}
        if set(selected) != set(q["group_ids"]):
            raise CanvasError("选中的图片组已改变，请重新准备并报价")
        for task in tasks:
            g = selected[task["group_id"]]
            current_prompt = self.inputs(project, g)[0]['prompt']
            if named_prompt(current_prompt) != task.get("source_prompt", task["prompt"]):
                raise CanvasError("图片组提示词已变化，拒绝使用旧草稿扣费")
            files = [project_asset(project, g[f"repaired_{side}"]) if g.get(f"repaired_{side}") else
                     original_frame(project, g[f"frame_{side}"], self.service.config["ffmpeg"],
                                    self.service.config["ffprobe"]) for side in ("a", "b")]
            if [_sha(file) for file in files] != task["images"]:
                raise CanvasError("图片组图片已变化，拒绝使用旧草稿扣费")
            if task.get('fingerprint') != self.inputs(project, g)[1]:
                raise CanvasError('人物绑定、音色或生成参数已变化，请新建轮次重新准备草稿')
        if any(t.get("status") != "draft" or t.get("submit_id") for t in tasks):
            raise CanvasError("任务已提交或状态改变，不能重复扣费")
        # Second quote immediately before approval, bounded by the amount shown in UI.
        q['sufficient'] = False
        if config.get('pending_approval'):
            config['pending_approval']['sufficient'] = False
        self._save(project)
        ids = q["node_ids"]
        args = ["node", "quote", "--project-id", config["project_id"]]
        for nid in ids:
            args += ["--node-id", nid]
        refreshed = invoke(self.cli, args)
        if (not isinstance(refreshed, dict) or refreshed.get("confirmable") is not True or
                not isinstance(refreshed.get("totalMaxCredits"), (int, float)) or
                not math.isfinite(refreshed['totalMaxCredits']) or refreshed['totalMaxCredits'] < 0 or
                refreshed["totalMaxCredits"] > approved_ceiling):
            raise CanvasError("最新报价超出用户批准的积分上限，请重新确认")
        balance = self.credit_balance(refreshed['totalMaxCredits'])
        q.update(balance)
        if config.get('pending_approval'):
            config['pending_approval'].update(balance)
        self._save(project)
        if not balance['sufficient']:
            raise CanvasError(f"积分不足：本批最多需要 {refreshed['totalMaxCredits']:g}，可用 {balance['available_credits']:g}，还差 {balance['shortfall']:g}。请充值或调整批次后重新报价")
        confirm = ["node", "confirm", "--project-id", config["project_id"],
                   "--credit-ceiling", approved_ceiling]
        for nid in ids:
            confirm += ["--node-id", nid]
        token_data = invoke(self.cli, confirm)
        token = token_data.get("creditConfirmationToken") if isinstance(token_data, dict) else None
        if not token:
            raise CanvasError("即梦未签发积分确认凭证")
        for t in tasks:
            t["submit_id"] = _uuid()
            t["status"] = "submission_unknown"
        config['submitted_at'] = time.time()
        config.pop("quote", None)
        config.pop("pending_approval", None)
        self._save(project)  # Durable identity BEFORE any paid operation.
        command = ["node", "run", "--project-id", config["project_id"], "--credit-token", token]
        for nid in ids:
            command += ["--node-id", nid]
        for t in tasks:
            command += ["--submit-id", t["submit_id"]]
        try:
            invoke(self.cli, command, timeout=180)
        except CanvasError:
            # Unknown even if CLI exits nonzero. Reconcile by original submit_ids.
            self._save(project)
            raise
        for t in tasks:
            t["status"] = "submitted"
        self._save(project)
        return project

    def refresh(self, identifier):
        project = self._load(identifier)
        cfg = project.get("generation", {})
        for t in cfg.get("tasks", {}).values():
            if not t.get("submit_id") or t.get("status") == "downloaded":
                continue
            data = invoke(self.cli, ["operation", "status", t["submit_id"],
                                     "--project-id", cfg["project_id"]])
            if not isinstance(data, dict):
                continue
            submission = data.get("submission")
            state = submission.get("state") if isinstance(submission, dict) else None
            if state == "absent":
                t["status"] = "requires_manual_review"
            elif data.get('state') in ('failed', 'canceled'):
                t['status'] = data['state']
                error = data.get('submissionError') or data.get('error') or (submission.get('error') if isinstance(submission, dict) else None)
                if error:
                    if isinstance(error, dict):
                        message = str(error.get('message') or error.get('code') or '即梦返回失败')
                        code = error.get('serviceCode')
                        t['error'] = ('积分不足' if str(code) == '20005' or 'insufficient credits' in message.lower() else message) + (f'（{code}）' if code else '')
                    else:
                        t['error'] = str(error)
            elif data.get('state') in ('success', 'succeeded'):
                rid = _result_resource(data)
                if rid:
                    t["resource_id"] = rid
                    t["status"] = "completed"
                else:
                    t["status"] = "completed_no_resource"
            elif state == 'completed' and not data.get('state'):
                # Compatibility with older CLI contracts that omitted operation state.
                rid = _result_resource(data)
                t.update(status='completed' if rid else 'completed_no_resource', resource_id=rid)
            else:
                t["status"] = "running"
            self._save(project)
        return project

    def download(self, identifier):
        project = self._load(identifier)
        cfg = project.get("generation", {})
        failures = []
        causes = []
        for task in cfg.get("tasks", {}).values():
            if task.get('status') not in ('completed', 'downloaded'):
                continue
            try:
                if task['status'] == 'completed':
                    self._download_task(project, cfg, task)
                if not task.get('output') or not Path(task['output']).is_file():
                    raise CanvasError('项目成片文件缺失；原任务记录已保留')
                archive_videos(project, cfg, self.service.config.get('video_archive'), group_ids=[task['group_id']])
                task.pop('download_error', None)
                task.pop('download_error_details', None)
            except Exception as exc:
                task['download_error'] = str(exc)
                if hasattr(exc, 'details'):
                    task['download_error_details'] = exc.details()
                causes.append(exc)
                failures.append({'group_id': task['group_id'], 'error': str(exc)})
            # A later failure must never hide a successful download or desktop copy.
            self._save(project)
        cfg['download_errors'] = failures
        self._save(project)
        if failures:
            error = CanvasError('部分视频下载或归档未完成，成功项已保存。' +
                                '。'.join(item['error'] for item in failures))
            error.retryable = all(getattr(exc, 'retryable', False) for exc in causes)
            raise error
        return project

    def _download_task(self, project, cfg, task):
        if not task.get('resource_id'):
            raise CanvasError('成片缺少资源ID，请继续查询原任务')
        rid = task["resource_id"]
        invoke(self.cli, ["resource", "get", rid, "--project-id", cfg["project_id"]])
        folder = Path(project["directory"]) / "generated-videos" / task["group_id"]
        if task.get('submit_id'):
            folder = folder / task['submit_id']
        folder.mkdir(parents=True, exist_ok=True)
        response = invoke(self.cli, ["resource", "download", rid,
                                     "--project-id", cfg["project_id"], "--output", str(folder)],
                          timeout=240)
        if not isinstance(response, dict):
            raise CanvasError("下载未返回校验结果")
        local = response.get("path") or response.get("localPath") or response.get("outputPath")
        if not isinstance(local, str):
            raise CanvasError("下载响应缺少本地文件路径")
        file = Path(local).resolve()
        if not file.is_file() or not file.is_relative_to(folder.resolve()):
            raise CanvasError("即梦下载路径不在项目输出目录内")
        count, checksum = response.get("byteCount", response.get('size')), response.get("sha256")
        if (not isinstance(count, int) or count <= 0 or
                file.stat().st_size != count or not isinstance(checksum, str) or _sha(file) != checksum):
            raise CanvasError("即梦下载的文件大小或 SHA256 校验失败")
        try:
            media = probe(file, self.service.config.get('ffprobe'))
            streams = media.get('streams', [])
            duration = float(media.get('format', {}).get('duration', 0))
        except (ValueError, OSError, RuntimeError, TypeError) as exc:
            raise CanvasError('成片媒体检查失败，请检查已下载文件后继续查询原任务') from exc
        videos = [stream for stream in streams if stream.get('codec_type') == 'video']
        if (not videos or not math.isfinite(duration) or duration <= 0 or
                not videos[0].get('width') or not videos[0].get('height')):
            raise CanvasError('成片缺少有效视频画面或时长，尚未标记下载完成')
        expected = float(cfg.get('duration', 0))
        if expected and abs(duration - expected) > max(1, expected * .1):
            raise CanvasError('成片时长与生成配置不符，请检查原任务结果')
        requires_audio = any(ref.get('role') == '音色' for ref in task.get('references', []))
        if requires_audio and not any(stream.get('codec_type') == 'audio' for stream in streams):
            raise CanvasError('成片缺少音轨，请检查原任务结果')
        task.update(status="downloaded", output=str(file), sha256=checksum,
                    media_info={'duration': duration, 'streams': [
                        {key: stream[key] for key in ('codec_type', 'codec_name', 'width', 'height')
                         if key in stream} for stream in streams]})
