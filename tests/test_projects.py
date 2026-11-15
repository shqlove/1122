import json
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image


def make_project(tmp_path):
    from dialogue_tool.projects import ProjectRepository
    source = tmp_path / 'source.mp4'
    if not shutil.which('ffmpeg'):
        pytest.skip('ffmpeg missing')
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=red:s=160x90:r=25:d=3',
                    '-c:v', 'libx264', str(source)], check=True)
    repository = ProjectRepository(tmp_path / 'data')
    project = repository.create(source)
    frames = [{'id': i, 'shot_id': i, 'seconds': i + .1, 'file': f'frame-{i}.jpg'} for i in range(3)]
    scan = Path(project['directory']) / 'scan'
    scan.mkdir()
    for f in frames:
        Image.new('RGB', (100, 60), (f['id'] * 80, 0, 0)).save(scan / f['file'])
    project.update(scan={'frames': frames}, scan_directory=str(scan), groups=[
        {'id': 'g1', 'frame_a': 0, 'frame_b': 1, 'prompt': '台词', 'reason': '候选',
         'needs_review': True, 'selected': True, 'repaired_a': 'stale.png', 'repaired_b': None}])
    repository.save(project)
    return repository, project


def test_original_frames_reuse_metadata_and_match_direct_extraction(tmp_path, monkeypatch):
    from dialogue_tool import projects
    from dialogue_tool.media import extract_frame

    _, project = make_project(tmp_path)
    original_probe = projects.probe
    calls = []

    def counted_probe(*args):
        calls.append(args)
        return original_probe(*args)

    monkeypatch.setattr(projects, 'probe', counted_probe)
    ffmpeg, ffprobe = shutil.which('ffmpeg'), shutil.which('ffprobe')
    for identifier in (0, 1):
        actual = projects.original_frame(project, identifier, ffmpeg, ffprobe)
        expected = tmp_path / f'expected-{identifier}.png'
        extract_frame(Path(project['source']), identifier + .1, expected, ffmpeg, ffprobe)
        with Image.open(actual) as first, Image.open(expected) as second:
            assert first.size == second.size
            assert first.tobytes() == second.tobytes()
    assert len(calls) == 1


def test_projects_persist_and_replacing_frame_invalidates_old_repair(tmp_path):
    from dialogue_tool.projects import ProjectRepository
    repository, project = make_project(tmp_path)
    replacement = dict(project['groups'][0], frame_a=2)
    saved = repository.update_groups(project['id'], [replacement])
    assert saved['groups'][0]['repaired_a'] is None
    assert ProjectRepository(tmp_path / 'data').get(project['id'])['groups'][0]['frame_a'] == 2


def test_invalid_group_update_does_not_corrupt_saved_project(tmp_path):
    repository, project = make_project(tmp_path)
    invalid = dict(project['groups'][0], frame_b=0)
    with pytest.raises(ValueError):
        repository.update_groups(project['id'], [invalid])
    assert repository.get(project['id'])['groups'][0]['frame_b'] == 1


def test_visual_description_survives_ui_save_and_clears_with_replaced_image(tmp_path):
    from dialogue_tool.projects import ProjectRepository
    source = tmp_path / 'source.mp4'
    source.write_bytes(b'video')
    repository = ProjectRepository(tmp_path / 'data')
    project = repository.create(source)
    project['scan'] = {'frames': [{'id': value, 'seconds': value} for value in (1, 2, 3)]}
    project['groups'] = [{'id': 'g', 'frame_a': 1, 'frame_b': 2, 'prompt': '台词',
                          'person_a': '旧描述', 'person_b': '聆听者', 'selected': True}]
    repository.save(project)
    edited = {**project['groups'][0], 'person_a': ' 中央黑衣男子 '}
    saved = repository.update_groups(project['id'], [edited])
    assert saved['groups'][0]['person_a'] == '中央黑衣男子'
    replaced = {**saved['groups'][0], 'frame_a': 3}
    result = repository.update_groups(project['id'], [replaced])['groups'][0]
    assert result['person_a'] == '' and result['person_b'] == '聆听者'


def test_new_groups_default_confirmed_and_replacement_keeps_confirmation(tmp_path):
    repository, project = make_project(tmp_path)
    group = {'id': 'new', 'frame_a': 0, 'frame_b': 1, 'prompt': 'keep'}
    saved = repository.update_groups(project['id'], [group])['groups'][0]
    assert not saved['needs_review'] and saved['selected']
    saved = repository.update_groups(project['id'], [dict(saved, frame_a=2)])['groups'][0]
    assert not saved['needs_review'] and saved['selected']
    saved = repository.update_groups(project['id'], [dict(saved, needs_review=True)])['groups'][0]
    assert saved['needs_review'] and not saved['selected']
    saved = repository.update_groups(project['id'], [dict(saved, frame_a=0)])['groups'][0]
    assert saved['needs_review'] and not saved['selected']


def test_confirmed_group_retains_source_span_and_verification(tmp_path):
    repository, project = make_project(tmp_path)
    group = project['groups'][0]
    group.update(scene_start_seconds=0, scene_end_seconds=2.5, verification_reason='不同人物正反打', validation_state='uncertain')
    repository.save(project)
    confirmed = dict(group, needs_review=False, validation_state='confirmed')
    saved = repository.update_groups(project['id'], [confirmed])['groups'][0]
    assert saved['needs_review'] is False
    assert saved['validation_state'] == 'confirmed'
    assert saved['scene_end_seconds'] == 2.5
    assert saved['verification_reason'] == '不同人物正反打'


def test_replacing_actor_clears_only_that_actors_old_description(tmp_path):
    repository, project = make_project(tmp_path)
    group = project['groups'][0]
    group.update(person_a='旧人物A', person_b='原人物B')
    repository.save(project)
    saved = repository.update_groups(project['id'], [dict(group, frame_a=2)])['groups'][0]
    assert not saved.get('person_a')
    assert saved['person_b'] == '原人物B'


def test_another_analysis_process_prevents_manual_group_overwrite(tmp_path):
    from dialogue_tool.project_run import analysis_lock, AnalysisBusy
    repository, project = make_project(tmp_path)
    with analysis_lock(Path(project['directory'])):
        with pytest.raises(AnalysisBusy):
            repository.update_groups(project['id'], [])
    assert repository.get(project['id'])['groups']


def test_export_uses_recorded_frames_and_never_overwrites_source(tmp_path):
    from dialogue_tool.projects import export_groups
    repository, project = make_project(tmp_path)
    # 失效修复必须先清除；导出未经修复的图片时明确记录 original。
    project['groups'][0]['repaired_a'] = None
    source = Path(project['source'])
    before = source.read_bytes()
    output = export_groups(project, tmp_path / '交付')
    manifest = json.loads((output / 'manifest.json').read_text(encoding='utf-8'))
    assert manifest['groups'][0]['image_a_variant'] == 'original'
    assert (output / '组001' / 'A_用于生成.png').is_file()
    with Image.open(output / '组001' / 'A_用于生成.png') as image:
        assert image.size == (160, 90)
    assert (output / '组001' / '提示词.txt').read_text(encoding='utf-8') == '台词'
    assert source.read_bytes() == before


def test_original_cache_changes_when_frame_time_changes(tmp_path):
    from dialogue_tool.projects import original_frame
    repository, project = make_project(tmp_path)
    a = original_frame(project, 0, shutil.which('ffmpeg'), shutil.which('ffprobe'))
    project['scan']['frames'][0]['seconds'] = 1.5
    b = original_frame(project, 0, shutil.which('ffmpeg'), shutil.which('ffprobe'))
    assert a != b


def test_corrupt_existing_cache_is_not_exported_as_success(tmp_path):
    from dialogue_tool.projects import original_frame
    repository, project = make_project(tmp_path)
    cached = original_frame(project, 0, shutil.which('ffmpeg'), shutil.which('ffprobe'))
    cached.write_bytes(b'incomplete png')
    recovered = original_frame(project, 0, shutil.which('ffmpeg'), shutil.which('ffprobe'))
    with Image.open(recovered) as image:
        image.verify()
def test_repository_closes_database_handles(tmp_path):
    import shutil
    from dialogue_tool.projects import ProjectRepository

    source = tmp_path / 'source.mp4'
    source.write_bytes(b'test')
    repository = ProjectRepository(tmp_path / 'projects')
    project = repository.create(source)
    assert repository.get(project['id'])['id'] == project['id']
    assert len(repository.list()) == 1
    # Windows rejects removing a SQLite file if a connection is still open.
    shutil.rmtree(repository.root)
    assert not repository.root.exists()

