@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
".tools\dreamina-canvas.exe" auth login
pause
endlocal
