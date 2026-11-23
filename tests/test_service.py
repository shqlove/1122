import json
from pathlib import Path

import pytest
from PIL import Image

from dialogue_tool.projects import ProjectRepository


def test_usage_log_is_read_as_utf8_on_gbk_windows(tmp_path, monkeypatch):
    from dialogue_tool import service as service_module

    source = tmp_path / 'source.mp4'
    source.write_bytes(b'source')
    service = service_module.Service({'data': str(tmp_path / 'data'), 'codex': 'unused'})
    project = service.projects.create(source)
    project['scan_directory'] = str(tmp_path)
    frames = [{'id': i, 'seconds': i, 'file': f'{i}.jpg'} for i in range(2)]

    def analyze(codex, images, output, context):
        output.mkdir()
        event = {'type': 'turn.completed', 'message': '中文日志',
                 'usage': {'input_tokens': 12, 'output_tokens': 3}}
        (output / 'events.jsonl').write_text(json.dumps(event, ensure_ascii=False), encoding='utf-8')
        return {'summary': '无对话', 'pairs': []}

    original_read = Path.read_text

    def gbk_default(path, *args, **kwargs):
        if path.name == 'events.jsonl' and not args and 'encoding' not in kwargs:
            kwargs['encoding'] = 'gbk'
        return original_read(path, *args, **kwargs)

    monkeypatch.setattr(service_module, 'analyze_images', analyze)
    monkeypatch.setattr(Path, 'read_text', gbk_default)
    result = service._cached_request(project, 'discovery', frames, '测试')
    assert result['pairs'] == []
    saved = service.projects.get(project['id'])
    assert next(iter(saved['analysis_usage'].values()))['input_tokens'] == 12
    assert len(saved['analysis_cache']) == 1


def test_frame_batches_do_not_repeat_a_tail_already_covered():
    from dialogue_tool.service import frame_batches
    assert [len(b) for b in frame_batches(list(range(23)))] == [23]
    assert [len(b) for b in frame_batches(list(range(24)))] == [24]
    assert [len(b) for b in frame_batches(list(range(26)))] == [24, 6]


def test_character_filter_is_sent_to_model_and_isolates_cached_results(tmp_path, monkeypatch):
    from dialogue_tool import service as service_module

    source = tmp_path / 'source.mp4'
    source.write_bytes(b'source')
    service = service_module.Service({'data': str(tmp_path / 'data'), 'codex': 'unused'})
    project = service.projects.create(source)
    project['scan_directory'] = str(tmp_path)
    frames = [{'id': i, 'seconds': i, 'file': f'{i}.jpg'} for i in range(2)]
    contexts = []

    def analyze(codex, images, output, context):
        contexts.append(context)
        output.mkdir()
        (output / 'events.jsonl').write_text('', encoding='utf-8')
        return {'summary': '无符合条件的对话', 'pairs': []}

    monkeypatch.setattr(service_module, 'analyze_images', analyze)
    service._cached_request(project, 'discovery', frames, '识别对话')
    project['analysis_filter'] = {'required_character': '张世豪（豪哥）'}
    service._cached_request(project, 'discovery', frames, '识别对话')
    service._cached_request(project, 'discovery', frames, '识别对话')
    service._cached_request(project, 'verify', frames, '复核对话')
    assert len(contexts) == 3
    assert '人物筛选要求' not in contexts[0]
    assert all('张世豪（豪哥）' in context and '仅提到该角色名字不算参与' in context
               for context in contexts[1:])


def test_existing_groups_are_never_discarded_by_scan_only(tmp_path):
    from dialogue_tool.service import Service
    source = tmp_path / 'source.mp4'
    source.write_bytes(b'source')
    service = Service({'data': str(tmp_path / 'data')})
    project = service.projects.create(source)
    project['groups'] = [{'id': 'existing', 'prompt': '用户确认文案'}]
    service.projects.save(project)
    with pytest.raises(ValueError, match='已有配组'):
        service.analyze(project['id'], scan_only=True)
    assert service.projects.get(project['id'])['groups'][0]['prompt'] == '用户确认文案'


@pytest.mark.parametrize('kind', ['discovery', 'verify'])
def test_haoge_filter_without_references_retains_uncertain_pairs(tmp_path, monkeypatch, kind):
    from dialogue_tool import service as service_module
    from dialogue_tool.projects import character_filter

    source = tmp_path / '插翅难逃01.mp4'
    source.write_bytes(b'source')
    service = service_module.Service({'data': str(tmp_path / 'data'), 'codex': 'unused'})
    project = service.projects.create(source)
    assert project['analysis_filter']['required_character'] == '张世豪（豪哥）'
    del project['analysis_filter']  # 旧项目也使用同一默认规则。
    assert character_filter(project)['required_character'] == '张世豪（豪哥）'
    project['scan_directory'] = str(tmp_path)
    frames = [{'id': i, 'seconds': i, 'file': f'{i}.jpg'} for i in range(2)]
    base = dict(frame_a=0, frame_b=1, reason='交流', needs_review=False)
    confirmed = dict(base, required_character_side='b', required_character_evidence='B为豪哥，连续反打与A交流')

    def analyze(codex, images, output, context):
        assert context.startswith('本项目人物筛选要求')
        output.mkdir()
        (output / 'events.jsonl').write_text('', encoding='utf-8')
        return {'summary': '候选', 'pairs': [base, confirmed,
                dict(confirmed, needs_review=True), dict(confirmed, required_character_evidence='')]}

    monkeypatch.setattr(service_module, 'analyze_images', analyze)
    result = service._cached_request(project, kind, frames, '对话任务')
    assert len(result['pairs']) == 4
    assert all(p['required_character_status'] == 'uncertain' for p in result['pairs'])
    assert service._cached_request(project, kind, frames, '对话任务') == result
    assert character_filter(dict(project, analysis_filter={})) == {}


def test_resume_does_not_repeat_successful_codex_batches(tmp_path, register_python_cli):
    from dialogue_tool.service import Service
    source = tmp_path / 'source.mp4'
    source.write_bytes(b'source')
    counter = tmp_path / 'calls.txt'
    executable = tmp_path / 'fake-codex'
    executable.write_text('#!/usr/bin/env python3\n' + f'''import sys,json,pathlib
assert sys.argv[sys.argv.index('--model') + 1] == 'gpt-6.2-sol'
assert 'model_reasoning_effort="medium"' in sys.argv
counter=pathlib.Path({str(counter)!r})
n=int(counter.read_text())+1 if counter.exists() else 1
counter.write_text(str(n))
if n==2: sys.exit(1)
path=pathlib.Path(sys.argv[sys.argv.index('--output-last-message')+1])
path.write_text(json.dumps({{"summary":"测试批次","pairs":[]}}))
''', encoding='utf-8')
    executable.chmod(0o700)
    register_python_cli(executable)
    config = {'data': str(tmp_path / 'data'), 'codex': str(executable), 'analysis_workers': 1}
    service = Service(config)
    project = service.projects.create(source)
    folder = Path(project['directory']) / 'scan'
    folder.mkdir()
    frames = []
    for i in range(26):
        Image.new('RGB', (16, 16), 'red').save(folder / f'{i}.jpg')
        frames.append({'id': i, 'shot_id': i, 'seconds': i, 'file': f'{i}.jpg'})
    project.update(scan={'frames': frames}, scan_directory=str(folder), phase='scanned')
    service.projects.save(project)
    with pytest.raises(Exception):
        service.analyze(project['id'])
    assert counter.read_text(encoding='utf-8') == '2'
    service.analyze(project['id'])
    assert counter.read_text(encoding='utf-8') == '3'


def test_verification_resume_keeps_dedupe_and_prepares_full_images(tmp_path, monkeypatch, register_python_cli):
    from dialogue_tool.service import Service
    source = tmp_path / 'source.mp4'
    source.write_bytes(b'source')
    counter = tmp_path / 'calls.txt'
    executable = tmp_path / 'fake-codex'
    executable.write_text('#!/usr/bin/env python3\n' + f'''import sys,json,pathlib
counter=pathlib.Path({str(counter)!r})
n=int(counter.read_text())+1 if counter.exists() else 1
counter.write_text(str(n))
prompt=sys.stdin.buffer.read().decode('utf-8')
pair=lambda a,b:dict(frame_a=a,frame_b=b,reason='测试对话',needs_review=False)
if '去重任务' in prompt:
 result={{'clusters':[{{'members':[0,1],'representative':0}}]}}
elif '复核任务' in prompt:
 if n==3: sys.exit(1)
 result={{'summary':'复核清晰画面','pairs':[pair(2,3)]}}
else:
 result={{'summary':'两个重复候选','pairs':[pair(0,1),pair(2,3)]}}
path=pathlib.Path(sys.argv[sys.argv.index('--output-last-message')+1])
path.write_text(json.dumps(result))
''', encoding='utf-8')
    executable.chmod(0o700)
    register_python_cli(executable)
    service = Service({'data': str(tmp_path / 'data'), 'codex': str(executable)})
    project = service.projects.create(source)
    folder = Path(project['directory']) / 'scan'
    folder.mkdir()
    frames = []
    for i in range(4):
        Image.new('RGB', (40, 24), 'red').save(folder / f'{i}.jpg')
        frames.append({'id': i, 'shot_id': i % 2, 'seconds': i + 10, 'file': f'{i}.jpg', 'sharpness': i})
    project.update(scan={'frames': frames}, scan_directory=str(folder), phase='scanned')
    service.projects.save(project)
    full = tmp_path / 'full.png'
    Image.new('RGB', (400, 240), 'blue').save(full)
    monkeypatch.setattr('dialogue_tool.service.original_frame', lambda *args: full)
    with pytest.raises(Exception):
        service.analyze(project['id'])
    assert counter.read_text(encoding='utf-8') == '3'
    result = service.analyze(project['id'])
    assert counter.read_text(encoding='utf-8') == '4'
    assert len(result['groups']) == 1
    assert (result['groups'][0]['frame_a'], result['groups'][0]['frame_b']) == (2, 3)
    assert result['groups'][0]['selected'] is True
    assert result['analysis_report']['prepared_images'] == 2


def test_invalid_dedupe_is_not_cached_as_a_successful_stage(tmp_path, register_python_cli):
    from dialogue_tool.service import Service
    from dialogue_tool.providers import ProviderError
    source = tmp_path / 'source.mp4'
    source.write_bytes(b'source')
    executable = tmp_path / 'fake-codex'
    executable.write_text('''#!/usr/bin/env python3
import json,pathlib,sys
path=pathlib.Path(sys.argv[sys.argv.index('--output-last-message')+1])
path.write_text(json.dumps({'clusters':[{'members':[0],'representative':0}]}))
''', encoding='utf-8')
    executable.chmod(0o700)
    register_python_cli(executable)
    service = Service({'data': str(tmp_path / 'data'), 'codex': str(executable)})
    project = service.projects.create(source)
    folder = Path(project['directory']) / 'scan'
    folder.mkdir()
    frames = []
    for i in range(4):
        Image.new('RGB', (10, 10), 'red').save(folder / f'{i}.jpg')
        frames.append({'id': i, 'seconds': i, 'file': f'{i}.jpg'})
    project['scan_directory'] = str(folder)
    with pytest.raises(ProviderError):
        service._cached_request(project, 'dedupe', frames, '去重任务')
    assert not project.get('analysis_cache')


def test_default_discovery_samples_every_shot_and_keeps_dense_pool(tmp_path, register_python_cli):
    from dialogue_tool.service import Service
    source = tmp_path / 'source.mp4'
    source.write_bytes(b'source')
    seen = tmp_path / 'seen.jsonl'
    executable = tmp_path / 'fake-codex'
    executable.write_text('#!/usr/bin/env python3\n' + f'''import json,pathlib,sys
images=[sys.argv[i+1] for i,arg in enumerate(sys.argv[:-1]) if arg=='--image']
with pathlib.Path({str(seen)!r}).open('a') as stream:stream.write(json.dumps(images)+'\\n')
pathlib.Path(sys.argv[sys.argv.index('--output-last-message')+1]).write_text(json.dumps({{'summary':'没有对话','pairs':[]}}))
''', encoding='utf-8')
    executable.chmod(0o700)
    register_python_cli(executable)
    service = Service({'data': str(tmp_path / 'data'), 'codex': str(executable)})
    project = service.projects.create(source)
    folder = Path(project['directory']) / 'scan'
    folder.mkdir()
    frames = []
    for i in range(30):
        Image.new('RGB', (10, 10), 'red').save(folder / f'{i}.jpg')
        frames.append({'id': i, 'seconds': i, 'shot_id': i//15, 'file': f'{i}.jpg'})
    project.update(scan={'frames':frames},scan_directory=str(folder))
    service.projects.save(project)
    service.analyze(project['id'])
    supplied = {Path(image).name for line in seen.read_text(encoding='utf-8').splitlines() for image in json.loads(line)}
    assert supplied == {f'{i}.jpg' for i in (0, 6, 7, 12, 14, 15, 21, 22, 27, 29)}
    saved = service.projects.get(project['id'])
    assert saved['scan']['frames'] == frames
    assert saved['analysis_report']['discovery_images'] == 10
    assert saved['analysis_report']['candidate_images'] == 30


@pytest.mark.parametrize('kind', ['discovery', 'verify'])
def test_reference_matching_and_reference_cache_invalidation(tmp_path, monkeypatch, kind):
    from dialogue_tool import service as module
    service = module.Service({'data': str(tmp_path / 'data'), 'codex': 'fake'})
    source = tmp_path / '插翅难逃.mp4'
    source.write_bytes(b'video')
    project = service.projects.create(source)
    project['scan_directory'] = str(tmp_path)
    root = service.projects.root
    reference = root / 'positive.jpg'
    Image.new('RGB', (20, 20), 'red').save(reference)
    (root / 'character-references.json').write_text(json.dumps({'张世豪（豪哥）': [
        {'file': 'positive.jpg', 'role': 'positive', 'confirmed_by_user': True}]}), encoding='utf-8')
    frames = [{'id': i, 'seconds': i, 'file': f'{i}.jpg'} for i in range(2)]
    calls = []
    def request(codex, images, folder, context, schema):
        calls.append(images)
        assert images[-1] == reference and len(images) == 3
        folder.mkdir()
        (folder / 'events.jsonl').write_text('', encoding='utf-8')
        base = dict(frame_a=0, frame_b=1, reason='对话', needs_review=False,
                    required_character_side='b', required_character_evidence='参考图对照结果')
        return {'summary': '对照', 'pairs': [dict(base, required_character_status=status)
                for status in ('matched', 'not_matched', 'uncertain')]}
    monkeypatch.setattr(module, 'request_images', request)
    result = service._cached_request(project, kind, frames, '测试')
    assert [p['required_character_status'] for p in result['pairs']] == ['matched', 'uncertain']
    assert result['pairs'][1]['required_character_side'] is None
    service._cached_request(project, kind, frames, '测试')
    assert len(calls) == 1
    Image.new('RGB', (20, 20), 'blue').save(reference)
    service._cached_request(project, kind, frames, '测试')
    assert len(calls) == 2


def test_parallel_workers_are_bounded_and_commit_on_main_thread(tmp_path, monkeypatch):
    import threading
    import time
    from dialogue_tool import service as module
    service = module.Service({'data': str(tmp_path / 'data'), 'codex': 'fake'})
    source = tmp_path / 'movie.mp4'
    source.write_bytes(b'video')
    project = service.projects.create(source)
    project['scan_directory'] = str(tmp_path)
    frames = [{'id': i, 'seconds': i, 'file': f'{i}.jpg'} for i in range(2)]
    tasks = [(frames, str(i)) for i in range(6)]
    lock = threading.Lock()
    initial_workers = threading.Barrier(3)
    active = maximum = 0
    main_thread = threading.get_ident()
    original_save = service.projects.save
    def save(document):
        assert threading.get_ident() == main_thread
        original_save(document)
    def analyze(codex, images, output, context):
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
        if int(context) < 3:
            initial_workers.wait(timeout=5)
        time.sleep(.01 * (3 - int(context) % 3))
        output.mkdir()
        (output / 'events.jsonl').write_text(json.dumps({'type': 'turn.completed', 'usage': {'input_tokens': 7}}), encoding='utf-8')
        with lock:
            active -= 1
        return {'summary': context, 'pairs': []}
    monkeypatch.setattr(service.projects, 'save', save)
    monkeypatch.setattr(module, 'analyze_images', analyze)
    results = service._parallel_requests(project, 'verify', tasks)
    assert [r['summary'] for r in results] == list(map(str, range(6)))
    assert maximum == 3
    saved = service.projects.get(project['id'])
    assert saved['analysis_requests_started'] == 6
    assert len(saved['analysis_cache']) == 6
    assert len(saved['analysis_usage']) == 6
    assert len(saved['analysis_used_keys']) == 6
    service._parallel_requests(project, 'verify', tasks)
    assert project['analysis_requests_started'] == 6
    assert project['analysis_cache_hits'] == 6


def test_parallel_failure_stops_dispatch_and_keeps_successful_cache(tmp_path, monkeypatch):
    import threading
    import time
    from dialogue_tool import service as module
    service = module.Service({'data': str(tmp_path / 'data'), 'codex': 'fake'})
    source = tmp_path / 'movie.mp4'
    source.write_bytes(b'video')
    project = service.projects.create(source)
    project['scan_directory'] = str(tmp_path)
    frames = [{'id': i, 'seconds': i, 'file': f'{i}.jpg'} for i in range(2)]
    tasks = [(frames, str(i)) for i in range(6)]
    initial_workers = threading.Barrier(3)
    calls = []
    should_fail = True
    def analyze(codex, images, output, context):
        calls.append(context)
        if should_fail:
            initial_workers.wait(timeout=5)
            if context == '0':
                raise module.ProviderError('限流', 'rate_limited')
            time.sleep(.05)
        output.mkdir()
        (output / 'events.jsonl').write_text('', encoding='utf-8')
        return {'summary': context, 'pairs': []}
    monkeypatch.setattr(module, 'analyze_images', analyze)
    with pytest.raises(module.ProviderError, match='限流'):
        service._parallel_requests(project, 'verify', tasks)
    assert set(calls) == {'0', '1', '2'}
    saved = service.projects.get(project['id'])
    assert len(saved['analysis_cache']) == 2
    assert saved['analysis_requests_started'] == 3
    should_fail = False
    results = service._parallel_requests(saved, 'verify', tasks)
    assert [r['summary'] for r in results] == list(map(str, range(6)))
    assert calls.count('1') == calls.count('2') == 1
    assert saved['analysis_requests_started'] == 7


def test_chained_discovery_passes_previous_summary(tmp_path, monkeypatch):
    from dialogue_tool import service as module
    service = module.Service({'data': str(tmp_path / 'data'), 'codex': 'fake'})
    source = tmp_path / 'movie.mp4'
    source.write_bytes(b'video')
    project = service.projects.create(source)
    project['scan_directory'] = str(tmp_path)
    frames = [{'id': i, 'seconds': i, 'file': f'{i}.jpg'} for i in range(2)]
    contexts = []
    def analyze(codex, images, output, context):
        contexts.append(context)
        output.mkdir()
        (output / 'events.jsonl').write_text('', encoding='utf-8')
        return {'summary': 'previous-' + context[0], 'pairs': []}
    monkeypatch.setattr(module, 'analyze_images', analyze)
    tasks = [[(frames, 'A'), (frames, 'B')], [(frames, 'B'), (frames, 'C')]]
    service._parallel_requests(project, 'discovery', tasks, chained=True)
    assert any(context.startswith('B') and context.endswith('previous-A') for context in contexts)
    assert any(context.startswith('C') and context.endswith('previous-B') for context in contexts)
