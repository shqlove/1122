import base64
import math
import shutil
import wave
from pathlib import Path

import pytest

from test_compshare import setup, prepare
from dialogue_tool import compshare, speech
from dialogue_tool.generation import CanvasError, _sha
from dialogue_tool.h3_errors import H3Error
from dialogue_tool.workflow_api import WorkflowAPI


@pytest.fixture
def audio_setup(setup, monkeypatch):
    api, pid, service, calls = setup
    p = service.projects.get(pid)
    source = Path(p['directory']) / 'voice.wav'; source.write_bytes(b'original voice')
    p['groups'][0]['generation_bindings'] = {'male_side': 'a', 'voice_path': str(source)}
    service.projects.save(p)
    monkeypatch.setattr(compshare, 'probe', lambda *a: {'format': {'duration': 10}})
    api.execute(pid, 'configure_generation', dict(model='MiniMax-H3', duration=15, ratio='16:9',
        resolution='480P', prompt_mode='shared', common_prompt='@男主说出台词：“如果没有天赋，那就不断的重复。” @聆听者闭嘴',
        audio_first=True, use_context_ir=False))
    audio_calls = []
    def request(self, method, path, payload=None, identity=None):
        audio_calls.append((method, path, payload, identity))
        if path == 'pricing':
            return {'enabled': True, 'unit_characters': 100, 'point_precision': 2, 'costs': {'text_to_speech': 1}}
        if method == 'POST':
            saved = service.projects.get(pid)['generation']['tasks']['one']
            assert saved['speech']['request_id'] == identity
            assert saved['status'] == 'audio_submission_unknown'
            return {'task_id': 'speech-1', 'point_cost': .13}
        return {'task': {'task_id': 'speech-1', 'status': 'succeeded',
                         'point_cost': .13, 'point_status': 'consumed', 'output_url': 'https://media.ufileos.com/a.wav'}}
    monkeypatch.setattr(speech.SpeechClient, 'request', request)
    monkeypatch.setattr(speech, 'upload_reference', lambda *a: 'voice-asset')
    monkeypatch.setattr(speech, 'download_audio', lambda url, dest, cfg: dest.write_bytes(b'raw entire dialogue'))
    def fit(src, dest, target, cfg):
        dest.write_bytes(b'fitted entire dialogue')
        return {'output': str(dest), 'raw_output': str(src), 'duration': target,
                'raw_duration': 23., 'tempo_factor': 23./target, 'sha256': _sha(dest)}
    monkeypatch.setattr(speech, 'fit_audio', fit)
    return api, pid, service, calls, audio_calls, source


def test_full_audio_chain_is_single_approval_and_sends_new_audio(audio_setup):
    api, pid, service, calls, audio_calls, source = audio_setup
    card = prepare(api, pid)
    assert card['audio_first'] and card['video_cost'] == 75
    assert card['speech_cost'] == .13 and card['ceiling'] == 75.13
    assert not any(c[0] == 'POST' for c in audio_calls + calls)
    api.approve_submission(pid, card['id'], card['ceiling'])
    assert service.projects.get(pid)['generation']['tasks']['one']['status'] == 'audio_running'
    assert not any(c[0] == 'POST' for c in calls)
    api = WorkflowAPI(service)  # app restart: resume using persisted approval and IDs
    api.execute(pid, 'refresh_results')
    saved = service.projects.get(pid)['generation']['tasks']['one']
    assert saved['status'] == 'submitted'
    body = next(c[2] for c in calls if c[0] == 'POST')
    sent = body['content'][-1]['audio_url']['url'].split(',')[1]
    assert base64.b64decode(sent) == b'fitted entire dialogue'
    assert saved['request_snapshot']['speech_task_id'] == 'speech-1'
    assert saved['request_snapshot']['role_bindings'][-1]['sha256'] == saved['speech']['sha256']
    assert saved['request_snapshot']['role_bindings'][-1]['path'] != str(source)
    assert service.projects.get(pid)['groups'][0]['generation_bindings']['voice_path'] == str(source)
    api.execute(pid, 'refresh_results'); api.execute(pid, 'refresh_results')
    assert len([c for c in calls if c[0] == 'POST']) == 1
    assert len([c for c in audio_calls if c[0] == 'POST']) == 1
    with pytest.raises(ValueError): api.approve_submission(pid, card['id'], card['ceiling'])


def test_speech_timeout_reuses_exact_idempotency_key(audio_setup, monkeypatch):
    api, pid, service, calls, audio_calls, source = audio_setup
    original = speech.SpeechClient.request
    attempts = []
    def timeout(self, method, path, payload=None, identity=None):
        if method == 'POST':
            attempts.append((identity, payload.copy()))
            if len(attempts) == 1: raise H3Error('timeout', '配音超时', retryable=True)
        return original(self, method, path, payload, identity)
    monkeypatch.setattr(speech.SpeechClient, 'request', timeout)
    card = prepare(api, pid)
    with pytest.raises(H3Error): api.approve_submission(pid, card['id'], card['ceiling'])
    with pytest.raises(CanvasError): api.execute(pid, 'new_round')
    api.execute(pid, 'refresh_results')
    assert len(attempts) == 2 and attempts[0] == attempts[1]
    api.execute(pid, 'refresh_results')
    assert len([c for c in calls if c[0] == 'POST']) == 1


def test_changed_prompt_during_speech_prevents_video_charge(audio_setup):
    api, pid, service, calls, audio_calls, _ = audio_setup
    card = prepare(api, pid); api.approve_submission(pid, card['id'], card['ceiling'])
    p = service.projects.get(pid); p['generation']['global_prompt'] += '改台词'; service.projects.save(p)
    with pytest.raises(CanvasError, match='改变'): api.execute(pid, 'refresh_results')
    assert not any(c[0] == 'POST' for c in calls)


def test_speech_failure_never_submits_video(audio_setup, monkeypatch):
    api, pid, service, calls, _, _ = audio_setup
    card = prepare(api, pid); api.approve_submission(pid, card['id'], card['ceiling'])
    monkeypatch.setattr(speech.SpeechClient, 'request', lambda *a, **kw: {'task': {
        'task_id': 'speech-1', 'status': 'failed', 'error_message': '生成失败'}})
    api.execute(pid, 'refresh_results')
    assert service.projects.get(pid)['generation']['tasks']['one']['status'] == 'failed'
    assert not any(c[0] == 'POST' for c in calls)


def test_price_increase_blocks_both_jobs(audio_setup, monkeypatch):
    api, pid, _, calls, audio_calls, _ = audio_setup
    card = prepare(api, pid)
    monkeypatch.setattr(speech.SpeechClient, 'request', lambda *a, **kw: {
        'enabled': True, 'unit_characters': 100, 'point_precision': 2, 'costs': {'text_to_speech': 10}})
    with pytest.raises(CanvasError, match='费用已变化'): api.approve_submission(pid, card['id'], card['ceiling'])
    assert not any(c[0] == 'POST' for c in calls + audio_calls)


def test_speech_plan_rejects_ambiguous_dialogue_and_long_video():
    payload = {'prompt': '没有台词', 'duration': 15, 'references': [{'type': 'audio', 'path': 'v', 'sha256': 'h'}]}
    with pytest.raises(CanvasError, match='明确'): speech.speech_plan(payload, {})
    payload['prompt'] = '台词：“完整的原文” 镜头在“原文”处切换'
    assert speech.speech_plan(payload, {})['text'] == '完整的原文'
    payload['duration'] = 30
    with pytest.raises(CanvasError, match='4–15'): speech.speech_plan(payload, {})


def test_real_audio_fit_preserves_tail_and_target_duration(tmp_path):
    from dialogue_tool.settings import settings
    config = settings()
    if not config.get('ffmpeg') or not config.get('ffprobe'): pytest.skip('ffmpeg unavailable')
    source, output = tmp_path / 'source.wav', tmp_path / 'fitted.wav'
    import array
    rate = 24000
    # Two distinct tones: losing the ending would lose the 880 Hz tail.
    samples = array.array('h', (int(10000*math.sin(2*math.pi*(440 if i < 3*rate else 880)*i/rate)) for i in range(6*rate)))
    with wave.open(str(source), 'wb') as f:
        f.setnchannels(1); f.setsampwidth(2); f.setframerate(rate); f.writeframes(samples.tobytes())
    result = speech.fit_audio(source, output, 4., config)
    assert 3.8 <= result['duration'] <= 4.1 and result['tempo_factor'] == 1.5
    with wave.open(str(output), 'rb') as f:
        fitted = array.array('h', f.readframes(f.getnframes())); sr = f.getframerate()
    tail = fitted[-sr:]
    crossings = sum(a <= 0 < b for a,b in zip(tail, tail[1:]))
    assert 840 <= crossings <= 920  # original tail and pitch survived, not a cut
