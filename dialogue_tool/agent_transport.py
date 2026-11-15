"""Codex App Server stdio transport. No browser, terminal UI, or copied credentials."""
from __future__ import annotations

import json
import queue
import subprocess
import threading
from pathlib import Path


class AgentConnectionError(RuntimeError):
    pass


class AppServerClient:
    def __init__(self, executable, cwd):
        self.events = queue.Queue()
        self._pending = {}
        self._lock = threading.RLock()
        self._next_id = 0
        self.closed = False
        self.stderr_tail = []
        self.process = subprocess.Popen(
            [str(executable), 'app-server', '--listen', 'stdio://'],
            cwd=str(cwd), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding='utf-8', errors='replace', bufsize=1,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=self._read_errors, daemon=True).start()
        try:
            self.request('initialize', {'clientInfo': {'name': 'dialogue_workbench', 'title': '对话视频工作台', 'version': '0.2.0'},
                                       'capabilities': {'experimentalApi': True}}, timeout=45)
            self.notify('initialized', {})
        except Exception:
            self.close()
            raise

    def _write(self, data):
        with self._lock:
            if self.closed or self.process.poll() is not None:
                raise AgentConnectionError('Codex 连接已结束，请重新连接；不会自动重放操作')
            try:
                self.process.stdin.write(json.dumps(data, ensure_ascii=False) + '\n')
                self.process.stdin.flush()
            except (OSError, ValueError) as exc:
                raise AgentConnectionError('Codex 连接中断，原任务记录已保留') from exc

    def _read(self):
        try:
            for line in self.process.stdout:
                try:
                    data = json.loads(line)
                except ValueError:
                    continue
                if 'id' in data and 'method' not in data:
                    with self._lock:
                        pending = self._pending.get(data['id'])
                    if pending:
                        pending.put(data)
                else:
                    self.events.put(data)
        finally:
            self.closed = True
            with self._lock:
                for pending in self._pending.values():
                    try: pending.put_nowait({'error': {'message': 'Codex 进程已断开'}})
                    except queue.Full: pass
            self.events.put({'method': 'connection/closed', 'params': {}})

    def _read_errors(self):
        for line in self.process.stderr:
            # Keep diagnostics bounded in memory. Do not persist auth or raw protocol logs.
            self.stderr_tail = (self.stderr_tail + [line.rstrip()])[-15:]

    def request(self, method, params=None, timeout=60):
        with self._lock:
            self._next_id += 1
            identifier = self._next_id
            response = queue.Queue(maxsize=1)
            self._pending[identifier] = response
        try:
            self._write({'id': identifier, 'method': method, 'params': params or {}})
            try:
                data = response.get(timeout=timeout)
            except queue.Empty as exc:
                raise AgentConnectionError(f'{method} 等待响应超时；未自动重试') from exc
            if 'error' in data:
                raise AgentConnectionError(data['error'].get('message', 'Codex 请求失败'))
            return data.get('result', {})
        finally:
            with self._lock:
                self._pending.pop(identifier, None)

    def notify(self, method, params):
        self._write({'method': method, 'params': params})

    def respond(self, identifier, result):
        self._write({'id': identifier, 'result': result})

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
        self.closed = True
