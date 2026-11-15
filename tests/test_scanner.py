import hashlib
import shutil
import subprocess

import pytest


def test_discovery_frames_preserve_short_shots_and_stable_ids():
    from dialogue_tool.scanner import discovery_frames

    frames = [{'id': 40, 'shot_id': 2, 'seconds': 5.1},
              {'id': 41, 'shot_id': 2, 'seconds': 5.2},
              {'id': 42, 'shot_id': 2, 'seconds': 5.3},
              {'id': 50, 'shot_id': 3, 'seconds': 5.4}]
    assert discovery_frames(list(reversed(frames))) == frames
    assert discovery_frames([]) == []


def test_discovery_frames_sparsify_long_shots_without_mutating_candidates():
    from dialogue_tool.scanner import discovery_frames

    frames = [{'id': i, 'shot_id': 0, 'seconds': i * 2} for i in range(14)]
    original = [dict(frame) for frame in frames]
    selected = discovery_frames(frames)
    assert [frame['seconds'] for frame in selected] == [0, 6, 12, 18, 24, 26]
    assert len({frame['id'] for frame in selected}) == len(selected)
    assert frames == original
    assert all(frame is frames[frame['id']] for frame in selected)


def test_scanner_detects_cut_without_dumping_all_frames(tmp_path):
    from dialogue_tool.scanner import scan_video
    ffmpeg, ffprobe = shutil.which('ffmpeg'), shutil.which('ffprobe')
    if not ffmpeg or not ffprobe:
        pytest.skip('ffmpeg missing')
    source = tmp_path / '镜头测试.mp4'
    subprocess.run([ffmpeg, '-v', 'error', '-f', 'lavfi', '-i', 'color=red:s=320x180:r=25:d=1',
                    '-f', 'lavfi', '-i', 'color=blue:s=320x180:r=25:d=1',
                    '-filter_complex', '[0:v][1:v]concat=n=2:v=1:a=0[v]', '-map', '[v]', '-c:v', 'libx264', str(source)], check=True)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    report = scan_video(source, tmp_path / 'scan', ffmpeg, ffprobe)
    assert len(report['shots']) == 2
    assert 2 <= len(report['frames']) <= 8
    assert [f['id'] for f in report['frames']] == list(range(len(report['frames'])))
    assert report['read_mode'] == 'all_frames'
    assert hashlib.sha256(source.read_bytes()).hexdigest() == digest
    assert all((tmp_path / 'scan' / f['file']).is_file() for f in report['frames'])


def test_batch_pair_merge_keeps_separate_scene_occurrences():
    from dialogue_tool.scanner import merge_pair_proposals
    frames = [{'id': i, 'seconds': i * 2} for i in range(8)]
    pair = lambda a,b: {'frame_a': a, 'frame_b': b, 'reason': '连续场景', 'needs_review': False}
    # 重叠批次相同配组去重；后面相似但时间不同的组合不得仅因衣服相似被合并。
    result = merge_pair_proposals([pair(0, 1), pair(1, 0), pair(6, 7)], frames)
    assert len(result) == 2
    assert result[0]['frame_a'] == 0
    assert result[1]['frame_a'] == 6


def test_integer_range_is_seconds_not_frame_numbers(tmp_path):
    from dialogue_tool.scanner import scan_video
    ffmpeg, ffprobe = shutil.which('ffmpeg'), shutil.which('ffprobe')
    if not ffmpeg or not ffprobe:
        pytest.skip('ffmpeg missing')
    source = tmp_path / 'range.mp4'
    subprocess.run([ffmpeg, '-v', 'error', '-f', 'lavfi', '-i', 'color=green:s=320x180:r=25:d=3',
                    '-c:v', 'libx264', str(source)], check=True)
    report = scan_video(source, tmp_path / 'range-scan', ffmpeg, ffprobe, start=1, end=2)
    assert report['decoded_frames'] == 25
    assert len(report['shots']) == 1
    assert all(1 <= frame['seconds'] < 2 for frame in report['frames'])


def test_nearby_candidate_reads_match_seek_based_scan(tmp_path, monkeypatch):
    from dialogue_tool import scanner
    import cv2

    ffmpeg, ffprobe = shutil.which('ffmpeg'), shutil.which('ffprobe')
    if not ffmpeg or not ffprobe:
        pytest.skip('ffmpeg missing')
    source = tmp_path / 'moving-bframes.mp4'
    subprocess.run([ffmpeg, '-v', 'error', '-f', 'lavfi', '-i',
                    'testsrc2=s=320x180:r=25:d=7', '-c:v', 'libx264',
                    '-g', '125', '-bf', '3', str(source)], check=True)
    fast_dir, old_dir = tmp_path / 'sequential', tmp_path / 'seek'
    optimized = scanner.scan_video(source, fast_dir, ffmpeg, ffprobe, start=.5, end=6.5)

    def seek_read(capture, number, max_forward_frames):
        capture.set(cv2.CAP_PROP_POS_FRAMES, number)
        return capture.read()

    monkeypatch.setattr(scanner, 'read_candidate_frame', seek_read)
    baseline = scanner.scan_video(source, old_dir, ffmpeg, ffprobe, start=.5, end=6.5)
    assert optimized['decoded_frames'] == baseline['decoded_frames']
    assert optimized['shots'] == baseline['shots']
    assert optimized['frames'] == baseline['frames']
    for frame in baseline['frames']:
        assert (fast_dir / frame['file']).read_bytes() == (old_dir / frame['file']).read_bytes()


def test_candidate_reader_seeks_for_far_or_backward_targets_and_recovers_failed_grab():
    from dialogue_tool.scanner import read_candidate_frame

    class Capture:
        def __init__(self):
            self.position = 10
            self.seeks = []
            self.grabs = 0
            self.fail_grab = False

        def get(self, prop):
            return self.position

        def set(self, prop, number):
            self.seeks.append(number)
            self.position = number

        def grab(self):
            self.grabs += 1
            if self.fail_grab:
                return False
            self.position += 1
            return True

        def read(self):
            number = self.position
            self.position += 1
            return True, number

    capture = Capture()
    assert read_candidate_frame(capture, 12, 5) == (True, 12)
    assert capture.grabs == 2 and capture.seeks == []
    assert read_candidate_frame(capture, 30, 5) == (True, 30)
    assert read_candidate_frame(capture, 20, 5) == (True, 20)
    capture.fail_grab = True
    assert read_candidate_frame(capture, 24, 5) == (True, 24)
    assert capture.seeks == [30, 20, 24]


def test_character_probe_covers_short_shots_and_long_windows():
    from dialogue_tool.scanner import character_probe_frames

    frames = [{'id': i, 'shot_id': 0, 'seconds': i * 2} for i in range(19)]
    frames += [{'id': 30, 'shot_id': 7, 'seconds': 37}]
    original = [dict(frame) for frame in frames]
    probes = character_probe_frames(list(reversed(frames)))
    assert [frame['seconds'] for frame in probes] == [6, 18, 30, 37]
    assert frames == original
    assert character_probe_frames([]) == []


def test_character_probe_prefers_usable_local_candidate():
    from dialogue_tool.scanner import character_probe_frames

    frames = [{'id': i, 'shot_id': 0, 'seconds': i, 'sharpness': 10,
               'mean_brightness': 128} for i in range(11)]
    frames[5].update(sharpness=1000, mean_brightness=250)
    frames[4]['sharpness'] = 50
    frames[0]['sharpness'] = 5000
    assert [frame['id'] for frame in character_probe_frames(frames)] == [4]


def test_character_expansion_preserves_uncertain_missing_and_neighbours():
    from dialogue_tool.scanner import character_probe_frames, expanded_character_frames

    frames = [{'id': i, 'shot_id': (i // 3) * 10, 'seconds': i} for i in range(27)]
    probes = character_probe_frames(frames)
    results = {frame['id']: 'not_matched' for frame in probes}
    assert expanded_character_frames(frames, results) == []
    results[probes[4]['id']] = 'matched'
    expanded = expanded_character_frames(frames, results)
    assert expanded == frames[6:21]
    results[probes[4]['id']] = 'uncertain'
    assert expanded_character_frames(frames, results) == expanded
    del results[probes[4]['id']]
    assert expanded_character_frames(frames, results) == expanded
    assert expanded_character_frames(frames, {}) == frames
