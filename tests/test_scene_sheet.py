import json
from zipfile import ZipFile

import pytest
from PIL import Image

from dialogue_tool.assets import import_asset
from dialogue_tool.projects import file_digest
from dialogue_tool.scene_sheet import SLOTS, SceneSheets, plan_segments


def sheet():
    return {'name': '茶楼', 'revision': 0, 'setting': '窗边桌子，A左B右', 'a_name': '甲', 'b_name': '乙',
            'ratio': '4:3', 'limit': 15, 'assets': {}, 'shots': [
                {'speaker': 'A', 'focus': 'B', 'seconds': 9, 'text': '你听我说。', 'action': '乙闭口点头'},
                {'speaker': 'B', 'focus': 'AB', 'seconds': 8, 'text': '后来呢？', 'action': '顺接点头动作'}]}


def test_plan_respects_speaker_reaction_and_limit():
    draft = sheet()
    parts = plan_segments(draft)
    assert [(s['speaker'], s['focus'], s['text']) for p in parts for s in p['shots']] == [
        ('A', 'B', '你听我说。'), ('B', 'AB', '后来呢？')]
    assert [p['seconds'] for p in parts] == [9, 8]
    draft['limit'] = 30
    assert len(plan_segments(draft)) == 1
    draft['shots'][0]['seconds'] = 31
    with pytest.raises(ValueError, match='镜头 1'):
        plan_segments(draft)


def test_save_reopen_and_stale_update(tmp_path):
    store = SceneSheets(tmp_path)
    saved = store.save(sheet())
    assert SceneSheets(tmp_path).sheets() == [saved]
    assert store.save(saved)['revision'] == 2
    with pytest.raises(ValueError, match='重新载入'):
        store.save(saved)


def test_export_validates_materials_and_writes_a_portable_bundle(tmp_path):
    store = SceneSheets(tmp_path)
    draft = sheet()
    target = tmp_path / '成品.zip'
    target.write_bytes(b'previous')
    with pytest.raises(ValueError, match='三视图'):
        store.export(draft, target)
    assert target.read_bytes() == b'previous'
    for idx, (slot, (_, kind)) in enumerate(SLOTS.items()):
        if kind == 'image':
            path = tmp_path / (slot + '.png')
            Image.new('RGB', (8, 8), (idx, 10, 30)).save(path)
            record = import_asset(path, store.assets, {})
        else:
            import wave
            path = store.assets / (slot + '.wav')
            with wave.open(str(path), 'wb') as stream:
                stream.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
                stream.writeframes(bytes([idx, 0]) * 1600)
            digest = file_digest(path)
            record = {'asset_id': digest, 'kind': 'audio', 'name': path.name, 'path': str(path)}
            (store.assets / (digest + '.json')).write_text(json.dumps(record), encoding='utf8')
        draft['assets'][slot] = record['asset_id']
    store.export(draft, target)
    with ZipFile(target) as z:
        manifest = json.loads(z.read('制作单.json'))
        assert all(name in z.namelist() for name in manifest['files'].values())
        assert len(manifest['segments']) == 2
        prompts = z.read('分段视频提示词.txt').decode()
        assert prompts.count('你听我说。') == prompts.count('后来呢？') == 1
        assert '拍 B · 仅 A 说' in prompts
