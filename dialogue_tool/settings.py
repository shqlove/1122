"""Paths for source checkout and the per-user Windows installer.

Do not write user projects into Program Files or PyInstaller's _internal directory.
"""
import json
import os
import shutil
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def desktop_directory():
    if os.name == 'nt':
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders') as key:
                return Path(os.path.expandvars(winreg.QueryValueEx(key, 'Desktop')[0]))
        except OSError:
            pass
    return Path.home() / 'Desktop'


def user_root() -> Path:
    if getattr(sys, "frozen", False) and os.name == "nt":
        base = os.environ.get("LOCALAPPDATA")
        return Path(base) / "DialogueWorkbench" if base else Path.home() / "AppData" / "Local" / "DialogueWorkbench"
    return ROOT


def bundled_tool(name: str) -> str | None:
    if not getattr(sys, "frozen", False):
        return None
    suffix = ".exe" if os.name == "nt" else ""
    path = Path(sys.executable).resolve().parent / "tools" / (name + suffix)
    return str(path) if path.is_file() else None


def resolve_codex(configured=None):
    """Recover versioned desktop CLI paths after a Codex update."""
    if configured and (Path(configured).is_file() or shutil.which(str(configured))):
        return str(configured)
    if configured and not Path(configured).is_absolute():
        return configured
    found = shutil.which('codex')
    if found:
        return found
    local = os.environ.get('LOCALAPPDATA')
    if local:
        candidates = list((Path(local) / 'OpenAI' / 'Codex' / 'bin').glob('*/codex.exe'))
        if candidates:
            return str(max(candidates, key=lambda p: p.stat().st_mtime_ns))
    return configured


def settings() -> dict:
    storage = user_root()
    local = storage / "settings.local.json"
    overrides = json.loads(local.read_text(encoding="utf-8")) if local.exists() else {}
    bundled_codex = Path("/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex")
    default_codex = str(bundled_codex) if os.name != "nt" and bundled_codex.is_file() else shutil.which("codex")
    return {
        "codex": os.environ.get("DIALOGUE_CODEX_PATH") or resolve_codex(overrides.get("codex") or default_codex),
        "dreamina_canvas": os.environ.get("DIALOGUE_CANVAS_CLI") or overrides.get("dreamina_canvas") or shutil.which("dreamina-canvas"),
        "ffmpeg": overrides.get("ffmpeg") or bundled_tool("ffmpeg") or shutil.which("ffmpeg"),
        "ffprobe": overrides.get("ffprobe") or bundled_tool("ffprobe") or shutil.which("ffprobe"),
        "model": overrides.get("model") or str((storage if getattr(sys, "frozen", False) else ROOT / ".tools") / "models" / "big-lama.pt"),
        "data": overrides.get("data") or str(storage / "workspaces"),
        "postprocess_root": overrides.get("postprocess_root") or str(ROOT.parent / '视频后期处理'),
        "analysis_workers": overrides.get("analysis_workers", 3),
        "video_archive": overrides.get("video_archive") or str(desktop_directory() / '豪哥视频'),
        "compshare_h3_key_file": overrides.get("compshare_h3_key_file") or str(Path.home() / ".dialogue-workbench" / "compshare-h3.key"),
    }
