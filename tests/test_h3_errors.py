import io
import json
import socket
import ssl
from urllib.error import HTTPError, URLError

import pytest

from dialogue_tool import compshare
from dialogue_tool.h3_errors import H3Error


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv('COMPSHARE_H3_API_KEY', raising=False)
    key = tmp_path / 'h3.key'
    key.write_text('sk-ml-private-test-key', encoding='utf-8')
    return compshare.H3Client({'data': str(tmp_path), 'compshare_h3_key_file': str(key)})


def respond(monkeypatch, response):
    calls = []

    class Opener:
        def open(self, request, timeout):
            calls.append(request.method)
            if isinstance(response, Exception):
                raise response
            return io.BytesIO(response)

    monkeypatch.setattr(compshare, 'build_opener', lambda *args: Opener())
    return calls


@pytest.mark.parametrize('reason,code,words,retryable', [
    (TimeoutError('timed out'), 'timeout', '超时', True),
    (socket.gaierror(-2, 'name not found'), 'dns_failed', '服务器地址', True),
    (ConnectionRefusedError('refused'), 'connection_refused', '连接被拒绝', True),
    (ConnectionResetError('reset'), 'connection_interrupted', '中途断开', True),
    (ssl.SSLCertVerificationError('certificate failed'), 'certificate_failed', '安全证书', False),
])
def test_connection_failures_have_distinct_reasons_and_diagnostics(client, monkeypatch, reason, code, words, retryable):
    respond(monkeypatch, URLError(reason))
    with pytest.raises(H3Error) as caught:
        client.request('GET', 'query/point_usage_summary')
    error = caught.value
    assert error.code == code and error.retryable is retryable
    assert words in str(error) and error.diagnostic_id
    log = client.config['data'] + '/diagnostics/h3-errors.jsonl'
    record = json.loads(open(log, encoding='utf-8').read())
    assert record['id'] == error.diagnostic_id
    assert record['cause_type'] == 'URLError'
    assert str(reason) in record['cause']


@pytest.mark.parametrize('status,code,words,retryable', [
    (401, 'invalid_key', '密钥已失效', False),
    (403, 'access_denied', '无权使用', False),
    (402, 'insufficient_credits', '积分不足', False),
    (429, 'rate_limited', '过于频繁', True),
    (503, 'service_unavailable', '服务暂时', True),
])
def test_http_errors_are_explained_without_exposing_credentials(client, monkeypatch, status, code, words, retryable):
    body = json.dumps({'error': {'message': 'test sk-ml-private-test-key'}, 'request_id': 'provider-123'}).encode()
    response = HTTPError('https://example.com', status, 'failure', {}, io.BytesIO(body))
    respond(monkeypatch, response)
    with pytest.raises(H3Error) as caught:
        client.request('GET', 'query/point_usage_summary')
    assert caught.value.code == code
    assert caught.value.retryable is retryable
    assert words in str(caught.value)
    log = open(client.config['data'] + '/diagnostics/h3-errors.jsonl', encoding='utf-8').read()
    assert 'sk-ml-private-test-key' not in log + str(caught.value)
    assert json.loads(log)['request_id'] == 'provider-123'


def test_unreadable_reply_and_write_timeout_never_resubmit(client, monkeypatch):
    calls = respond(monkeypatch, b'<html>temporary gateway page</html>')
    with pytest.raises(H3Error, match='无法读取') as caught:
        client.request('POST', 'video_generation', {'prompt': 'hello'}, 'original-key')
    assert caught.value.code == 'invalid_json'
    assert calls == ['POST']
    assert '提交记录已保存' in str(caught.value)
    calls = respond(monkeypatch, URLError(TimeoutError('timed out')))
    with pytest.raises(H3Error, match='超时'):
        client.request('POST', 'video_generation', {}, 'original-key')
    assert calls == ['POST']


def test_provider_error_retains_its_request_reference(client, monkeypatch):
    respond(monkeypatch, b'{"RetCode":8039,"Message":"job not found","request_uuid":"req-8039"}')
    with pytest.raises(H3Error, match='找不到') as caught:
        client.request('GET', 'query/video_generation/task-1')
    assert caught.value.code == 'not_found'
    assert not caught.value.retryable


def test_key_state_is_private_and_blank_key_cannot_replace_existing_key(client):
    state = client.connection_state()
    assert state['configured'] and state['can_save']
    assert 'sk-ml-private-test-key' not in json.dumps(state)
    with pytest.raises(H3Error):
        client.save_key('')
    assert client.key() == 'sk-ml-private-test-key'
    client.save_key('sk-ml-replacement')
    assert client.key() == 'sk-ml-replacement'


def test_environment_key_cannot_be_silently_overridden(client, monkeypatch):
    monkeypatch.setenv('COMPSHARE_H3_API_KEY', 'sk-ml-environment')
    assert client.connection_state()['can_save'] is False
    with pytest.raises(H3Error, match='环境变量'):
        client.save_key('sk-ml-replacement')
