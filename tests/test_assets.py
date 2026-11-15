import json
from pathlib import Path

import pytest
from urllib.request import Request, urlopen
from PIL import Image

from dialogue_tool.assets import catalog, import_asset, read_attachment
from dialogue_tool.desktop import DesktopAPI
from dialogue_tool.service import Service
from dialogue_tool.workflow_api import WorkflowAPI


def test_voice_preview_uses_allowed_local_media_url(tmp_path, monkeypatch):
    monkeypatch.setattr('dialogue_tool.assets.probe', lambda *args: {
        'streams': [{'codec_type': 'audio'}], 'format': {'duration': '8.8'}})
    service = Service({'data': str(tmp_path / 'data'), 'ffprobe': 'fake'})
    source = tmp_path / 'voice.mp3'
    source.write_bytes(b'ID3' + b'audio' * 100)
    asset = import_asset(source, service.projects.root / 'voice-library', service.config, voice=True)
    api = DesktopAPI(service)
    try:
        url = api.agent_voice_preview(asset['asset_id'])
        assert url.startswith('http://127.0.0.1:')
        html = (Path(__file__).parents[1] / 'dialogue_tool/ui/index.html').read_text(encoding='utf-8')
        assert "media-src 'self' http://127.0.0.1:*" in html
        with urlopen(Request(url, headers={'Range': 'bytes=0-9'})) as response:
            assert response.status == 206
            assert response.headers['Content-Type'] == 'audio/mpeg'
            assert response.read() == source.read_bytes()[:10]
    finally:
        api._media_server.server.shutdown()
        api._media_server.server.server_close()


def test_attachments_survive_source_removal_and_images_are_readable(tmp_path):
    source = tmp_path / 'prompt.txt'
    source.write_text('豪哥说话', encoding='utf-8')
    folder = tmp_path / 'attachments'
    asset = import_asset(source, folder, {})
    assert import_asset(source, folder, {}) == asset
    source.unlink()
    assert read_attachment(catalog(folder)[0])['text'] == '豪哥说话'
    picture = tmp_path / 'picture.png'
    Image.new('RGB', (30, 20)).save(picture)
    result = read_attachment(import_asset(picture, folder, {}))
    assert result['images'][0]['image_url'].startswith('data:image/jpeg;base64,')


def test_library_voice_is_copied_into_project_and_tasks_protect_binding(tmp_path, monkeypatch):
    monkeypatch.setattr('dialogue_tool.assets.probe', lambda *args: {
        'streams': [{'codec_type': 'audio'}], 'format': {'duration': '8.8'}})
    service = Service({'data': str(tmp_path / 'data'), 'ffprobe': 'fake'})
    source = tmp_path / 'movie.mp4'
    source.write_bytes(b'video')
    project = service.projects.create(source)
    project['groups'] = [{'id': 'one', 'frame_a': 1, 'frame_b': 2}]
    service.projects.save(project)
    voice = tmp_path / '豪哥音色.mp3'
    voice.write_bytes(b'audio')
    asset = import_asset(voice, service.projects.root / 'voice-library', service.config, voice=True)
    voice.unlink()
    workflow = WorkflowAPI(service)
    summary = workflow.summary(project['id'])
    workflow.execute(project['id'], 'update_groups', {
        'expected_revision': summary['revision'],
        'updates': [{'group_id': 'one', 'voice_asset_id': asset['asset_id']}]})
    saved = service.projects.get(project['id'])
    bindings = saved['groups'][0]['generation_bindings']
    assert bindings['voice_name'] == '豪哥音色.mp3'
    assert Path(bindings['voice_path']).is_relative_to(Path(project['directory']))
    assert Path(bindings['voice_path']).read_bytes() == b'audio'
    saved['generation'] = {'tasks': {'one': {'status': 'prepared'}}}
    service.projects.save(saved)
    with pytest.raises(ValueError, match='草稿'):
        workflow.execute(project['id'], 'update_groups', {
            'expected_revision': workflow.summary(project['id'])['revision'],
            'updates': [{'group_id': 'one', 'voice_asset_id': asset['asset_id']}]})


def test_chat_attachment_ids_are_validated_and_forwarded(tmp_path, monkeypatch):
    service = Service({'data': str(tmp_path / 'data')})
    source = tmp_path / 'movie.mp4'
    source.write_bytes(b'video')
    project = service.projects.create(source)
    api = DesktopAPI(service)
    text = tmp_path / 'prompt.txt'
    text.write_text('hello', encoding='utf-8')
    asset = import_asset(text, Path(project['directory']) / 'chat-attachments', {})
    sent = []
    monkeypatch.setattr(api._agent, 'send', lambda *args: sent.append(args) or True)
    api.agent_send('', 'model', 'low', [], project['id'], [asset['asset_id']])
    assert asset['asset_id'] in sent[0][1]
    with pytest.raises(ValueError, match='失效'):
        api.agent_send('hello', 'model', 'low', [], project['id'], ['foreign-id'])
    assert len(sent) == 1
    with pytest.raises(ValueError, match='不存在'):
        api._agent.workflow.execute(project['id'], 'read_attachment', {'asset_id': '../private'})
