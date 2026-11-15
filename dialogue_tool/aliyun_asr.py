"""Aliyun Qwen ASR for explicitly requested local audio/video transcription."""
import base64
import hashlib
import json
import math
import os
import re
import subprocess
import tempfile
import threading
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, HTTPRedirectHandler

from .media import probe

MODEL = 'qwen3-asr-flash'
_lock = threading.RLock()


class ASRError(ValueError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def config_path(config):
    return Path(config.get('aliyun_asr_config') or Path.home() / '.dialogue-workbench' / 'aliyun-asr.json')


def credentials(config):
    file = config_path(config)
    data = json.loads(file.read_text(encoding='utf-8')) if file.exists() else {}
    key_file = file.with_suffix('.key')
    local_key = key_file.read_text(encoding='utf-8-sig').strip() if key_file.exists() else ''
    key = os.environ.get('DASHSCOPE_API_KEY') or local_key or data.get('api_key', '')
    region = data.get('region', 'beijing')
    workspace = data.get('workspace_id', '')
    if region not in ('beijing', 'singapore') or (workspace and not re.fullmatch(r'[A-Za-z0-9-]+', workspace)):
        raise ASRError('阿里云地域或业务空间 ID 无效，请重新配置')
    if workspace:
        host = workspace + ('.cn-beijing.maas.aliyuncs.com' if region == 'beijing' else '.ap-southeast-1.maas.aliyuncs.com')
    else:
        host = 'dashscope.aliyuncs.com' if region == 'beijing' else 'dashscope-intl.aliyuncs.com'
    return key, 'https://' + host + '/compatible-mode/v1/chat/completions'


def configure(config, key, region='beijing', workspace=''):
    key, workspace = key.strip(), workspace.strip()
    if not key.startswith('sk-') or any(c.isspace() for c in key):
        raise ASRError('请填写百炼 API Key，不是 AccessKey ID/Secret')
    if region not in ('beijing', 'singapore') or (workspace and not re.fullmatch(r'[A-Za-z0-9-]+', workspace)):
        raise ASRError('地域或业务空间 ID 无效')
    file = config_path(config)
    file.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=file.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump({'api_key': key, 'region': region, 'workspace_id': workspace}, f)
        os.replace(tmp, file)
    finally:
        Path(tmp).unlink(missing_ok=True)
    return {'configured': True, 'model': MODEL, 'region': region, 'verified': False}


def status(config):
    key, endpoint = credentials(config)
    return {'configured': bool(key), 'model': MODEL, 'endpoint': endpoint,
            'note': '已配置不代表已联网验证；识别会上传所选音频并消耗阿里云额度。'}


def request_transcription(config, audio):
    key, endpoint = credentials(config)
    if not key:
        raise ASRError('尚未配置阿里云百炼 Key，请运行“配置阿里云语音识别.bat”；不要把密钥发到聊天')
    encoded = base64.b64encode(audio).decode('ascii')
    if len(encoded) > 10 * 1024 * 1024:
        raise ASRError('音频编码超过10MB，请缩短识别范围')
    # Do not feed the expected script to ASR: it could hide recognition errors.
    body = {'model': MODEL, 'messages': [{'role': 'user', 'content': [
        {'type': 'input_audio', 'input_audio': {'data': 'data:audio/wav;base64,' + encoded}}]}],
        'stream': False, 'asr_options': {'enable_itn': False}}
    req = Request(endpoint, data=json.dumps(body).encode(), headers={
        'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'}, method='POST')
    try:
        with build_opener(NoRedirect()).open(req, timeout=120) as response:
            data = json.load(response)
    except HTTPError as e:
        reason = {401: 'Key无效或地域不匹配', 403: '服务未开通或无模型权限',
                  429: '额度不足或请求限流'}.get(e.code, '接口请求失败')
        raise ASRError(f'阿里云识别失败：HTTP {e.code}，{reason}；未自动重试') from None
    except (URLError, TimeoutError, OSError, ValueError):
        raise ASRError('阿里云网络或响应异常，未取得转写；未自动重试，服务端可能已计费') from None
    try:
        choice = data['choices'][0]
        text = choice['message']['content']
        if not isinstance(text, str) or choice.get('finish_reason') != 'stop':
            raise ValueError()
    except (KeyError, IndexError, TypeError, ValueError):
        raise ASRError('阿里云未返回完整转写文本，不将截断结果视为成功') from None
    return {'text': text, 'request_id': data.get('id'), 'usage': data.get('usage', {})}


def transcribe(config, source, cache_dir, start=0, end=None):
    with _lock:
        source = Path(source).resolve(strict=True)
        if source.suffix.lower() not in ('.mp4', '.mov', '.mkv', '.webm', '.mp3', '.wav', '.m4a', '.aac', '.flac', '.ogg'):
            raise ASRError('请选择音频或视频文件')
        info = probe(source, config.get('ffprobe'))
        if not any(s.get('codec_type') == 'audio' for s in info.get('streams', [])):
            raise ASRError('文件没有音轨')
        duration = float(info['format']['duration'])
        start, end = float(start), duration if end is None else float(end)
        if not all(math.isfinite(v) for v in (duration,start,end)) or not 0 <= start < end <= duration + .01 or end-start > 180:
            raise ASRError('单次识别须为文件内的0–180秒片段；较长文件请指定起止秒数')
        key, endpoint = credentials(config)
        if not key:
            raise ASRError('尚未配置阿里云百炼 Key，请运行“配置阿里云语音识别.bat”')
        digest = hashlib.sha256()
        with source.open('rb') as f:
            for part in iter(lambda: f.read(1048576), b''): digest.update(part)
        identity = {'sha256': digest.hexdigest(), 'start': start, 'end': end, 'model': MODEL,
                    'endpoint': endpoint, 'version': 1}
        cache = Path(cache_dir)
        cache.mkdir(parents=True, exist_ok=True)
        record = cache / (hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()+'.json')
        if record.exists():
            return {**json.loads(record.read_text(encoding='utf-8')), 'cached': True}
        with tempfile.TemporaryDirectory(prefix='dialogue-asr-') as tmp:
            wav = Path(tmp) / 'audio.wav'
            try:
                subprocess.run([str(config['ffmpeg']), '-nostdin', '-v', 'error', '-y', '-ss', str(start),
                    '-i', str(source), '-t', str(end-start), '-map', '0:a:0', '-vn', '-ac', '1',
                    '-ar', '16000', '-c:a', 'pcm_s16le', str(wav)], check=True, capture_output=True,
                    timeout=120, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            except (OSError, subprocess.SubprocessError):
                raise ASRError('音频提取失败，尚未向阿里云提交') from None
            result = {**identity, **request_transcription(config, wav.read_bytes()), 'cached': False,
                'timestamp_type': 'clip_bounds_only',
                'note': '起止秒数是截取范围，不是逐句时间戳；ASR可能误识别，不能证明音质或口型合格。'}
        fd, tmp = tempfile.mkstemp(dir=cache)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f: json.dump(result, f, ensure_ascii=False, indent=2)
            os.replace(tmp, record)
        finally:
            Path(tmp).unlink(missing_ok=True)
        return result
