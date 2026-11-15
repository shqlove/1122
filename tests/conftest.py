"""Run registered Python CLI doubles with an interpreter on every platform."""
import sys
from pathlib import Path

import pytest


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
