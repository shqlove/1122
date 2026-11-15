"""IndexTTS speech preparation for H3; no audition gate or silent truncation."""
import hashlib
import math
import re
import shutil
import subprocess
from decimal import Decimal, ROUND_CEILING
from pathlib import Path
from urllib.request import Request, build_opener
from urllib.parse import urlparse

from .compshare import H3Client, NoRedirect
from .generation import CanvasError, _sha
from .media import probe


class SpeechClient(H3Client):
    base_url = 'https://cp.compshare.cn/audio/v1/'


def speech_plan(payload, config):
    # Only the explicitly introduced dialogue is spoken, never other quoted directions.
    matches = re.findall(r'(?:说出)?台词\s*[：:]\s*[“「](.*?)[”」]', payload['prompt'], re.S)
    if len(matches) != 1 or not matches[0].strip():
        raise CanvasError('先配音模式需要一段明确的台词，请用 台词：“完整原文” 标注；镜头要求放在引号外')
    text = matches[0].strip()
    count = sum(c.isalpha() or c.isdecimal() for c in text)
    if not count or len(text) > 2000:
        raise CanvasError('配音台词须包含文字，且不超过2000个字符')
    voice = next((r for r in payload['references'] if r['type'] == 'audio'), None)
    if not voice:
        raise CanvasError('先配音模式需要先绑定主角参考音色')
    if not 4 <= payload['duration'] <= 15:
        raise CanvasError('完整配音直传 H3 的单条视频须为4–15秒；长台词请分段或调整视频时长')
    target = payload['duration'] - .2
    return {'text': text, 'billable_characters': count, 'target_seconds': target,
            'speed': round(min(2., max(1., count / (5 * target))), 2),
            'source_path': voice['path'], 'source_sha256': voice['sha256'],
            'emotion_description': config.get('speech_emotion', ''),
            'fit_policy': 'whole_audio_atempo_no_trimming'}


def estimate(plan, pricing):
    try:
        unit = Decimal(str(pricing['unit_characters']))
        rate = Decimal(str(pricing['costs']['text_to_speech']))
        precision = pricing['point_precision']
        if pricing.get('enabled') is not True or unit <= 0 or rate < 0 or not unit.is_finite() or not rate.is_finite() or type(precision) is not int or not 0 <= precision <= 2:
            raise ValueError()
        return float((Decimal(plan['billable_characters']) * rate / unit).quantize(
            Decimal(1).scaleb(-precision), rounding=ROUND_CEILING))
    except (KeyError, TypeError, ValueError, ArithmeticError):
        raise CanvasError('配音服务未提供有效报价或暂未启用，尚未提交生成') from None


def safe_media_url(url):
    parsed = urlparse(url)
    if parsed.scheme != 'https' or parsed.username or parsed.password or not parsed.hostname or not any(
            parsed.hostname == host or parsed.hostname.endswith('.' + host)
            for host in ('ufileos.com', 'compshare.cn', 'ucloud.cn')):
        raise CanvasError('配音服务返回了无法核验的素材地址，已停止上传或下载')
    return url


def upload_reference(client, plan, config):
    source = Path(plan['source_path'])
    if _sha(source) != plan['source_sha256']:
        raise CanvasError('配音参考音色已变化，请重新准备并确认费用')
    mime = {'.wav': 'audio/wav', '.mp3': 'audio/mpeg', '.flac': 'audio/flac',
            '.ogg': 'audio/ogg', '.m4a': 'audio/mp4'}.get(source.suffix.lower())
    duration = audio_duration(source, config)
    if not mime or not .5 <= duration <= 30 or not 0 < source.stat().st_size <= 15 * 1024**2:
        raise CanvasError('配音参考须为0.5–30秒、15 MiB以内的 WAV/MP3/FLAC/OGG/M4A 音频')
    metadata = {'file_name': source.name, 'content_type': mime, 'file_size': source.stat().st_size}
    upload = client.request('POST', 'upload_urls', metadata)
    with build_opener(NoRedirect()).open(Request(safe_media_url(upload['upload_url']),
            data=source.read_bytes(), headers={'Content-Type': mime}, method='PUT'), timeout=60) as response:
        if not 200 <= response.status < 300:
            raise CanvasError('参考音色上传未完成，请稍后继续原任务')
    asset = client.request('POST', 'assets', {**metadata, 'object_key': upload['object_key'],
                                            'duration_ms': round(duration * 1000)}).get('asset', {})
    if not asset.get('asset_id'):
        raise CanvasError('配音服务没有返回参考素材编号，尚未提交配音')
    return asset['asset_id']


def audio_duration(source, config):
    data = probe(Path(source), config.get('ffprobe'))
    duration = float(data.get('format', {}).get('duration', 0))
    if not math.isfinite(duration) or duration <= 0 or not any(s.get('codec_type') == 'audio' for s in data.get('streams', [])):
        raise CanvasError('配音文件没有可读取的有效音轨，已停止视频生成')
    return duration


def download_audio(url, destination, config):
    partial = destination.with_suffix('.partial')
    with build_opener(NoRedirect()).open(safe_media_url(url), timeout=60) as response, partial.open('wb') as output:
        shutil.copyfileobj(response, output)
    audio_duration(partial, config)
    partial.replace(destination)


def fit_audio(source, destination, target, config):
    """Keep the entire utterance. Speed, never cut; retain the original WAV too."""
    original = audio_duration(source, config)
    factor = max(1., original / target)
    if factor > 2:
        raise CanvasError('配音过长，完整压入目标时长需再次超过2倍加速，已停止视频生成；请缩短台词或分段')
    filters = f'atempo={factor:.9f},apad=whole_dur=2' if factor > 1 else 'apad=whole_dur=2'
    result = subprocess.run([config['ffmpeg'], '-hide_banner', '-loglevel', 'error', '-y',
        '-i', str(source), '-vn', '-af', filters, '-ac', '1', '-ar', '24000', '-c:a', 'pcm_s16le',
        str(destination)], capture_output=True, timeout=120)
    if result.returncode:
        raise CanvasError('本机未能完成配音时长适配，原始配音已保留，尚未生成视频')
    duration = audio_duration(destination, config)
    if not 2 <= duration <= target + .1:
        raise CanvasError('配音时长适配未达到目标，已停止视频生成，没有截断台词')
    # Decoding is also an integrity and non-silence check, not a subjective quality claim.
    decoded = subprocess.run([config['ffmpeg'], '-v', 'error', '-i', str(destination),
        '-f', 's16le', '-ac', '1', '-'], capture_output=True, timeout=60)
    import array
    samples = array.array('h', decoded.stdout)
    if decoded.returncode or not samples or max(abs(v) for v in samples) < 32:
        raise CanvasError('配音文件无法完整解码或接近静音，已停止视频生成')
    return {'raw_duration': original, 'duration': duration, 'tempo_factor': factor,
            'sha256': _sha(destination), 'output': str(destination), 'raw_output': str(source)}
