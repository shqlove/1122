"""Local scene preparation. Produces a portable reference package for a chosen video channel."""
import json
import math
import os
import re
import tempfile
import uuid
from pathlib import Path
from zipfile import ZipFile

from .assets import catalog
from .projects import file_digest
from .project_run import workflow_lock

SLOTS = {
    'a_image': ('A三视图', 'image'), 'b_image': ('B三视图', 'image'),
    'scene_image': ('场景图', 'image'), 'a_audio': ('A音色', 'audio'), 'b_audio': ('B音色', 'audio'),
}


def validate_sheet(data):
    name = data['name'].strip()
    if not name:
        raise ValueError('请填写制作单名称')
    if data['limit'] not in (15, 30) or data['ratio'] not in ('4:3', '9:16', '16:9'):
        raise ValueError('片段上限或画幅无效')
    for shot in data['shots']:
        if shot['speaker'] not in ('A', 'B') or shot['focus'] not in ('A', 'B', 'AB'):
            raise ValueError('请为每个镜头选择说话人和画面人物')
        if type(shot['seconds']) is not int or not 0 <= shot['seconds'] <= 120:
            raise ValueError('单镜时长应为 0 至 120 的整数，0 表示自动估时')
    if any(k not in SLOTS or not re.fullmatch('[0-9a-f]{64}', v) for k, v in data['assets'].items()):
        raise ValueError('素材引用无效')


def plan_segments(sheet):
    segments = []
    for number, shot in enumerate(sheet['shots'], 1):
        speech = shot['text'].strip()
        if not speech:
            raise ValueError(f'镜头 {number} 缺少台词')
        length = len(re.findall(r'[\w]', speech, re.UNICODE))
        seconds = shot['seconds'] or max(1, math.ceil(length / 5))
        if seconds > sheet['limit']:
            raise ValueError(f'镜头 {number} 预计 {seconds} 秒，超过 {sheet["limit"]} 秒，请在自然停顿处拆开')
        if not segments or segments[-1]['seconds'] + seconds > sheet['limit']:
            segments.append({'seconds': 0, 'shots': []})
        segments[-1]['shots'].append({**shot, 'number': number, 'seconds': seconds})
        segments[-1]['seconds'] += seconds
    if not segments:
        raise ValueError('请至少添加一镜台词')
    return segments


class SceneSheets:
    def __init__(self, root):
        self.folder = Path(root) / 'scene-sheets'
        self.folder.mkdir(parents=True, exist_ok=True)
        self.assets = self.folder / 'assets'

    def sheets(self):
        return [json.loads(p.read_text('utf8')) for p in sorted(self.folder.glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True)]

    def save(self, data):
        validate_sheet(data)
        key = data.get('id') or uuid.uuid4().hex
        if not re.fullmatch('[0-9a-f]{32}', key):
            raise ValueError('制作单编号无效')
        target = self.folder / (key + '.json')
        with workflow_lock(self.folder):
            old = json.loads(target.read_text('utf8')) if target.exists() else {}
            revision = old.get('revision', 0)
            if revision != data['revision'] or (data.get('id') and not old):
                raise ValueError('制作单已在其他窗口更改，请重新载入')
            saved = {**data, 'id': key, 'revision': revision + 1}
            temp = target.with_suffix('.tmp')
            temp.write_text(json.dumps(saved, ensure_ascii=False, indent=2), 'utf8')
            os.replace(temp, target)
        return saved

    def export(self, sheet, target):
        validate_sheet(sheet)
        segments = plan_segments(sheet)
        assets = {a['asset_id']: a for a in catalog(self.assets)}
        records = {}
        for key, (label, kind) in SLOTS.items():
            asset = assets.get(sheet['assets'].get(key))
            if not asset or asset['kind'] != kind:
                raise ValueError(f'请先选择{label}')
            path = Path(asset['path'])
            if file_digest(path) != asset['asset_id']:
                raise ValueError(f'{label}文件已变化，请重新导入')
            records[key] = (path, label + path.suffix)
        if not all(sheet[k].strip() for k in ('a_name', 'b_name', 'setting')):
            raise ValueError('请补全两个角色名和场景设定')
        text = []
        for index, segment in enumerate(segments, 1):
            text.extend([f'第 {index} 段 · {segment["seconds"]} 秒 · {sheet["ratio"]}',
                         f'统一场景：{sheet["setting"]}，参考场景图。',
                         f'A {sheet["a_name"]} 参考 A三视图、A音色；B {sheet["b_name"]} 参考 B三视图、B音色。',
                         '音频仅参考音色。按指定台词说话，不加字幕或背景音乐。'])
            for shot in segment['shots']:
                focus = '双人同框' if shot['focus'] == 'AB' else f'拍 {shot["focus"]}'
                text.append(f'镜头 {shot["number"]} · {shot["seconds"]} 秒 · {focus} · 仅 {shot["speaker"]} 说：“{shot["text"]}”')
                if shot['action'].strip():
                    text.append(shot['action'])
            text.append('')
        manifest = {**sheet, 'segments': segments, 'files': {k: n for k, (_, n) in records.items()}}
        target = Path(target)
        with tempfile.NamedTemporaryFile(dir=target.parent, suffix='.tmp', delete=False) as f:
            temp = Path(f.name)
        try:
            with ZipFile(temp, 'w') as z:
                for path, name in records.values():
                    z.write(path, name)
                z.writestr('制作单.json', json.dumps(manifest, ensure_ascii=False, indent=2))
                z.writestr('分段视频提示词.txt', '\n'.join(text))
            os.replace(temp, target)
        finally:
            temp.unlink(missing_ok=True)
