from pathlib import Path

from dialogue_tool.video_archive import archive_videos


def test_archive_batch_is_stable_and_preserves_edited_copy(tmp_path):
    source = tmp_path / 'source.mp4'; source.write_bytes(b'original')
    root = tmp_path / 'desktop' / '豪哥视频'
    p = {'name': '项目/电影', 'groups': [{'id': 'a'}, {'id': 'b'}]}
    cfg = {'model': 'MiniMax-H3', 'submitted_at': 1794061863, 'tasks': {
        'a': {'group_id': 'a', 'submit_id': 'one', 'status': 'downloaded', 'output': str(source)},
        'b': {'group_id': 'b', 'submit_id': 'two', 'status': 'running'}}}
    archive_videos(p, cfg, root)
    first = Path(cfg['tasks']['a']['archive_output']); directory = cfg['archive_directory']
    assert first.read_bytes() == b'original' and '第01组' in first.name
    archive_videos(p, cfg, root)
    assert len(list(root.rglob('*.mp4'))) == 1
    first.write_bytes(b'user edit')
    cfg['tasks']['b'].update(status='downloaded', output=str(source))
    archive_videos(p, cfg, root)
    assert cfg['archive_directory'] == directory
    assert first.read_bytes() == b'user edit'
    assert Path(cfg['tasks']['a']['archive_output']).read_bytes() == b'original'
    assert Path(cfg['tasks']['b']['archive_output']).parent == first.parent
    assert source.read_bytes() == b'original'


def test_separate_submissions_do_not_overwrite(tmp_path):
    source = tmp_path / 'video.mp4'; source.write_bytes(b'video')
    p = {'name': '项目', 'groups': [{'id': 'g'}]}
    paths = []
    for sid in ('first', 'second'):
        cfg = {'model': 'seedance_2.0_fast_vip', 'submitted_at': 1794061863,
               'tasks': {'g': {'group_id': 'g', 'submit_id': sid, 'status': 'downloaded', 'output': str(source)}}}
        archive_videos(p, cfg, tmp_path / 'archive')
        paths.append(cfg['tasks']['g']['archive_output'])
    assert paths[0] != paths[1]
    assert all(Path(p).is_file() for p in paths)
