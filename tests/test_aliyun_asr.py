import json
from pathlib import Path
from urllib.error import HTTPError
import pytest
from dialogue_tool import aliyun_asr as asr


@pytest.fixture
def config(tmp_path, monkeypatch):
    monkeypatch.delenv('DASHSCOPE_API_KEY', raising=False)
    return {'aliyun_asr_config': str(tmp_path/'key.json'), 'ffmpeg': 'fake', 'ffprobe': 'fake'}


def test_credentials_and_status_do_not_expose_key(config):
    assert not asr.status(config)['configured']
    asr.configure(config, 'sk-test-secret', 'singapore', 'workspace123')
    assert asr.credentials(config)[1].startswith('https://workspace123.ap-southeast-1.maas.aliyuncs.com/')
    assert 'sk-test-secret' not in json.dumps(asr.status(config))
    with pytest.raises(asr.ASRError):
        asr.configure(config, 'sk-test', 'beijing', 'evil.example/path')


def test_missing_key_fails_before_network(config, monkeypatch):
    monkeypatch.setattr(asr, 'build_opener', lambda *a: pytest.fail('network forbidden'))
    with pytest.raises(asr.ASRError, match='尚未配置'):
        asr.request_transcription(config, b'audio')


def test_request_uses_audio_only_and_handles_auth_error(config, monkeypatch):
    asr.configure(config, 'sk-secret')
    class Opener:
        def open(self, request, timeout):
            body = json.loads(request.data)
            assert body['model'] == 'qwen3-asr-flash'
            assert len(body['messages']) == 1
            assert body['messages'][0]['content'][0]['input_audio']['data'].startswith('data:audio/wav;base64,')
            assert not body['asr_options']['enable_itn']
            raise HTTPError(request.full_url, 401, 'secret response', {}, None)
    monkeypatch.setattr(asr, 'build_opener', lambda *a: Opener())
    with pytest.raises(asr.ASRError, match='401') as exc:
        asr.request_transcription(config, b'audio')
    assert 'secret' not in str(exc.value)


def test_range_cache_and_no_fake_timestamps(config, tmp_path, monkeypatch):
    asr.configure(config, 'sk-secret')
    source = tmp_path/'source.mp4'; source.write_bytes(b'video')
    monkeypatch.setattr(asr, 'probe', lambda *a: {'format':{'duration':'30'},'streams':[{'codec_type':'audio'}]})
    def ffmpeg(args, **kw):
        assert args[args.index('-ss')+1] == '7.0'
        assert args[args.index('-t')+1] == '7.0'
        Path(args[-1]).write_bytes(b'WAV')
    monkeypatch.setattr(asr.subprocess, 'run', ffmpeg)
    calls=[]
    monkeypatch.setattr(asr, 'request_transcription', lambda *a: calls.append(a) or {'text':'真实转写', 'request_id':'req'})
    first=asr.transcribe(config,source,tmp_path/'cache',7,14)
    second=asr.transcribe(config,source,tmp_path/'cache',7,14)
    assert first['text']=='真实转写' and not first['cached'] and second['cached']
    assert len(calls)==1
    assert first['timestamp_type']=='clip_bounds_only'
    with pytest.raises(asr.ASRError,match='片段'):
        asr.transcribe(config,source,tmp_path/'cache',10,40)
    assert len(calls)==1


def test_model_response_content_is_used(config, monkeypatch):
    import io
    asr.configure(config,'sk-secret')
    class Opener:
        def open(self,*args,**kw):
            return io.BytesIO(json.dumps({'id':'r','choices':[{'finish_reason':'stop','message':{'content':'听到的台词'}}],'usage':{'seconds':7}}).encode())
    monkeypatch.setattr(asr,'build_opener',lambda *a: Opener())
    assert asr.request_transcription(config,b'WAV')['text']=='听到的台词'
