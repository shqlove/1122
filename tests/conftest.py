"""Run registered Python CLI doubles with an interpreter on every platform."""
import sys
from pathlib import Path

import pytest


@pytest.fixture(scope='session', autouse=True)
def isolated_ci_voice(tmp_path_factory):
    """Explicit CI-only audio fixture; never ship or replace the user's voice."""
    import os
    if os.environ.get('WORKBENCH_TEST_SYNTHETIC_VOICE') != '1':
        yield
        return
    import hashlib
    import wave
    from dialogue_tool import h3_baseline
    root = tmp_path_factory.mktemp('synthetic-voice')
    # A valid silent WAV container suffices for hash/copy/duration tests. The
    # production filename is retained because the tested code expects it.
    with wave.open(str(root / 'voice.mp3'), 'wb') as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b'\0\0' * 141280)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(h3_baseline, 'ROOT', root)
        patch.setitem(h3_baseline.BASELINE, 'audio_sha256', hashlib.sha256((root / 'voice.mp3').read_bytes()).hexdigest())
        yield


@pytest.fixture
def register_python_cli(monkeypatch):
    from dialogue_tool import media, providers

    scripts = set()
    original_run = providers.run

    def run(args, *positional, **kwargs):
        if Path(args[0]).resolve() in scripts:
            args = [sys.executable, '-X', 'utf8', *args]
        return original_run(args, *positional, **kwargs)

    monkeypatch.setattr(providers, 'run', run)
    monkeypatch.setattr(media, 'run', run)
    return lambda path: scripts.add(Path(path).resolve())
