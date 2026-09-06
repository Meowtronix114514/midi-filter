@echo off
chcp 65001 >nul
title MIDI Filter Web Monitor
cd /d "%~dp0"

if exist "venv\Scripts\python.exe" (
  set "PY=venv\Scripts\python.exe"
) else (
  set "PY=python"
)

echo ============================================
echo   MIDI Filter Web Monitor
echo   URL: http://127.0.0.1:8765
echo   Ctrl+C 退出
echo ============================================
%PY% midi_server.py
pause >nul
