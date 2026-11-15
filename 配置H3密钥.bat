@echo off
chcp 65001 >nul
cd /d "%~dp0"
".venv\Scripts\python.exe" -m dialogue_tool h3-set-key
if errorlevel 1 goto end
".venv\Scripts\python.exe" -m dialogue_tool h3-check
:end
pause
