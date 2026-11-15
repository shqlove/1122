"""每项目一个进程锁；进程退出由操作系统释放，不用残留文件判断正在运行。"""

import os
import threading
from contextlib import contextmanager
from pathlib import Path


class AnalysisBusy(ValueError):
    pass


_held = threading.local()


@contextmanager
def workflow_lock(directory: Path):
    """One writer across desktop, agent and CLI; reentrant on the same thread."""
    folder = directory / '.workflow'
    folder.mkdir(exist_ok=True)
    key = str(folder.resolve())
    held = getattr(_held, 'paths', set())
    if key in held:
        yield
        return
    with analysis_lock(folder):
        _held.paths = held | {key}
        try:
            yield
        finally:
            _held.paths = held


@contextmanager
def analysis_lock(directory: Path):
    with (directory / 'analysis.lock').open('a+b') as stream:
        if stream.tell() == 0:
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise AnalysisBusy('此项目正在拆解，请等待完成；不要重复启动') from exc
        try:
            yield
        finally:
            if os.name == 'nt':
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def analysis_running(directory: Path):
    try:
        with analysis_lock(directory):
            return False
    except AnalysisBusy:
        return True
