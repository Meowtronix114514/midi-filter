@echo off
chcp 65001 >nul
title 键盘 MIDI 模式
cd /d "%~dp0"
if exist "%~dp0venv\Scripts\pythonw.exe" (
  start "" "%~dp0venv\Scripts\pythonw.exe" "%~dp0keyboard_midi.py"
) else (
  start "" pythonw "%~dp0keyboard_midi.py"
)
