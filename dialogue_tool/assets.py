"""Persistent imported chat files and reusable voice references."""
import base64
import io
import json
import os
import shutil
import uuid
from pathlib import Path

from PIL import Image

from .media import probe
from .projects import file_digest

AUDIO = {'.mp3', '.wav', '.m4a', '.aac', '.flac', '.ogg'}
TEXT = {'.txt', '.md', '.json', '.csv', '.srt', '.vtt'}
IMAGES = {'.png', '.jpg', '.jpeg', '.webp', '.bmp'}


def catalog(folder):
    result = []
    for record in Path(folder).glob('*.json'):
        asset = json.loads(record.read_text(encoding='utf-8'))
        if Path(asset['path']).is_file():
            result.append(asset)
    return sorted(result, key=lambda a: a['name'])


def import_asset(source, folder, config, *, voice=False):
    source = Path(source).resolve(strict=True)
    suffix = source.suffix.lower()
    allowed = AUDIO if voice else AUDIO | TEXT | IMAGES | {'.mp4', '.mov', '.pdf', '.docx'}
    if suffix not in allowed:
        raise ValueError('不支持此文件类型，请添加图片、音频、视频、文本、PDF 或 Word 文件')
    if not 0 < source.stat().st_size <= 200 * 1024 * 1024:
        raise ValueError('文件大小须在 1 字节至 200 MB 之间')
    kind = 'file'
    duration = None
    if suffix in AUDIO:
        info = probe(source, config['ffprobe'])
        if not any(s.get('codec_type') == 'audio' for s in info['streams']):
            raise ValueError('文件中没有音轨')
        duration = float(info['format']['duration'])
        kind = 'audio'
    elif suffix in IMAGES:
        with Image.open(source) as image:
            image.verify()
        kind = 'image'
    elif suffix in TEXT:
        kind = 'text'
    digest = file_digest(source)
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / (digest + suffix)
    record = folder / (digest + '.json')
    if record.exists():
        return json.loads(record.read_text(encoding='utf-8'))
    temporary = folder / (uuid.uuid4().hex + '.tmp')
    try:
        shutil.copyfile(source, temporary)
        os.replace(temporary, target)
        asset = {'asset_id': digest, 'name': source.name, 'path': str(target),
                 'kind': kind, 'size': target.stat().st_size, 'duration': duration}
        temporary.write_text(json.dumps(asset, ensure_ascii=False), encoding='utf-8')
        os.replace(temporary, record)
        return asset
    finally:
        temporary.unlink(missing_ok=True)


def read_attachment(asset):
    result = dict(asset)
    path = Path(asset['path'])
    if asset.get('kind') == 'text':
        raw = path.read_bytes()[:120001]
        try:
            text = raw.decode('utf-8-sig')
        except UnicodeDecodeError:
            text = raw.decode('gb18030', errors='replace')
        result.update(text=text[:30000], truncated=len(raw) > 120000 or len(text) > 30000)
    elif asset.get('kind') == 'image':
        with Image.open(path) as source:
            image = source.convert('RGB')
            image.thumbnail((1600, 1600))
            buffer = io.BytesIO()
            image.save(buffer, format='JPEG', quality=90)
        result['images'] = [{'side': '附件', 'width': image.width, 'height': image.height,
                             'image_url': 'data:image/jpeg;base64,' + base64.b64encode(buffer.getvalue()).decode()}]
    else:
        result['note'] = '文件已保存。音频可用作音色参考；需要识别音频或视频台词时，请明确调用transcribe_audio（阿里云收费识别）。本接口不自动上传识别，也不解析PDF/Word正文。'
    return result
