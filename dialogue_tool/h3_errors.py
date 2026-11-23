"""H3 failure reasons for users, with redacted local diagnostics."""
import errno
import json
import re
import socket
import ssl
import threading
import uuid
from datetime import datetime, timezone
from http.client import HTTPException, IncompleteRead, RemoteDisconnected
from pathlib import Path
from urllib.error import URLError

from .generation import CanvasError


_log_lock = threading.Lock()
HTTP_FAILURES = {
    400: ('invalid_request', '平台拒绝了这次请求，请检查视频参数和参考素材', False),
    401: ('invalid_key', '优云智算密钥已失效或填写错误，请在“模型与连接设置”中更新 H3 密钥', False),
    402: ('insufficient_credits', '优云智算账户积分不足，请充值后再继续', False),
    403: ('access_denied', '当前优云智算账户或密钥无权使用此服务，请检查账户权限', False),
    404: ('not_found', '平台找不到这条任务或请求地址，请核对原任务记录', False),
    408: ('timeout', '平台等待请求超时，请稍后重试查询', True),
    413: ('request_too_large', '参考素材总量超过平台限制，请减小素材文件', False),
    422: ('invalid_request', '平台无法使用这次提交的参数或素材，请检查输入内容', False),
    429: ('rate_limited', '查询过于频繁或账户并发已满，请稍后再试', True),
}


class H3Error(CanvasError):
    def __init__(self, code, message, *, retryable=False, diagnostic_id=None):
        self.code = code
        self.retryable = retryable
        self.diagnostic_id = diagnostic_id
        super().__init__(message)

    def details(self):
        return {'code': self.code, 'message': str(self), 'retryable': self.retryable,
                'diagnostic_id': self.diagnostic_id}


def redact(value, secrets=()):
    text = str(value)
    for secret in secrets:
        if secret:
            text = text.replace(secret, '[密钥已隐藏]')
    text = re.sub(r'sk-[A-Za-z0-9_-]+', '[密钥已隐藏]', text)
    text = re.sub(r'(?i)Bearer\s+\S+', 'Bearer [已隐藏]', text)
    text = re.sub(r'https?://[^\s<>"\']+', '[网址已隐藏]', text)
    text = re.sub(r'data:[^\s]+', '[素材已隐藏]', text)
    return text[:2000]


def failure(config, code, message, *, retryable=False, method='GET', path='',
            cause=None, status=None, request_id=None, elapsed=None, secrets=()):
    identity = uuid.uuid4().hex[:12]
    record = {'time': datetime.now(timezone.utc).isoformat(), 'id': identity,
              'code': code, 'message': redact(message, secrets), 'retryable': retryable,
              'method': method, 'path': path.split('?', 1)[0], 'http_status': status,
              'request_id': redact(request_id or '', secrets), 'elapsed_seconds': elapsed,
              'cause_type': type(cause).__name__ if cause is not None else None,
              'cause': redact(cause or '', secrets)}
    try:
        log = Path(config['data']) / 'diagnostics' / 'h3-errors.jsonl'
        with _log_lock:
            log.parent.mkdir(parents=True, exist_ok=True)
            with log.open('a', encoding='utf-8') as output:
                output.write(json.dumps(record, ensure_ascii=False) + '\n')
    except (KeyError, OSError):
        identity = None
    public = redact(message, secrets)
    if identity:
        public += f'。排查编号 {identity}'
    return H3Error(code, public, retryable=retryable, diagnostic_id=identity)


def network_reason(exc):
    reason = exc.reason if isinstance(exc, URLError) else exc
    text = str(reason).lower()
    if 'tunnel connection failed' in text or 'proxy connection' in text:
        return 'proxy_failed', '系统代理未能连接优云智算，请检查代理程序和代理线路', True
    if isinstance(reason, (TimeoutError, socket.timeout)):
        return 'timeout', '等待优云智算回复超时，请检查网络后重试查询', True
    if isinstance(reason, socket.gaierror):
        return 'dns_failed', '无法找到优云智算服务器地址，请检查网络或代理的域名解析', True
    if isinstance(reason, ssl.SSLCertVerificationError):
        return 'certificate_failed', '无法验证优云智算服务器的安全证书，请检查系统时间和代理证书', False
    if isinstance(reason, ssl.SSLError):
        return 'secure_connection_failed', '与优云智算建立安全连接失败，请检查网络和代理设置', True
    if isinstance(reason, ConnectionRefusedError):
        return 'connection_refused', '连接被拒绝，请检查网络、系统代理和代理程序是否正常运行', True
    if isinstance(reason, (ConnectionResetError, ConnectionAbortedError, RemoteDisconnected,
                           IncompleteRead, BrokenPipeError)):
        return 'connection_interrupted', '与优云智算的连接中途断开，未收到完整回复', True
    if isinstance(reason, OSError) and reason.errno == errno.ENOSPC:
        return 'disk_full', '本地磁盘空间不足，无法保存视频，请清理空间后重试下载', False
    if isinstance(reason, OSError) and (reason.errno == errno.ENAMETOOLONG or getattr(reason, 'winerror', None) == 206):
        return 'path_too_long', '本地视频保存路径过长，无法写入文件，请缩短工作台数据目录路径', False
    if isinstance(reason, PermissionError):
        return 'file_permission', '工作台无法写入视频保存目录，请检查目录权限或文件占用', False
    if isinstance(reason, (FileNotFoundError, NotADirectoryError, IsADirectoryError)):
        return 'file_path_invalid', '本地视频保存目录不可用，请检查目录是否存在及路径是否有效', False
    if isinstance(reason, HTTPException):
        return 'incomplete_response', '优云智算的回复未完整传回，请稍后重试查询', True
    return 'connection_failed', '无法连接优云智算，请检查网络和系统代理后重试', True


def provider_reason(result, status=None):
    error = result.get('error')
    if isinstance(error, dict):
        detail = error.get('message', '')
        code = error.get('type') or error.get('code')
        try:
            status = int(error.get('http_code', status or 0)) or status
        except (TypeError, ValueError):
            pass
    else:
        detail = error or result.get('Message') or result.get('message') or ''
        code = result.get('RetCode')
    text = str(detail).lower()
    if 'invalid api key' in text or 'invalid_api_key' in text:
        return HTTP_FAILURES[401]
    if any(word in text for word in ('insufficient', 'not enough points', '积分不足', '余额不足')):
        return HTTP_FAILURES[402]
    if code == 8039 or 'job not found' in text:
        return HTTP_FAILURES[404]
    if status in HTTP_FAILURES:
        reason, message, retryable = HTTP_FAILURES[status]
        if status in (400, 422) and detail:
            message += '。平台说明为 ' + redact(detail)
        return reason, message, retryable
    if status and 500 <= status <= 599:
        return 'service_unavailable', f'优云智算服务暂时无法处理请求（服务状态 {status}），请稍后再试', True
    if status and 300 <= status <= 399:
        return 'unexpected_redirect', '优云智算要求跳转到其他地址，工作台已停止此次连接，请检查网络或平台地址', False
    if detail:
        return 'provider_rejected', '优云智算未接受这次请求。平台说明为 ' + redact(detail), False
    return 'provider_rejected', '优云智算未接受这次请求，平台未提供具体原因，请凭排查编号查询记录', False
